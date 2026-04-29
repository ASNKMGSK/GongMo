# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group B Sub Agent 공용 LLM wrapper — V1 `nodes/llm.py` 를 import 로 재활용.

역할:
- V1 `get_chat_model` / `invoke_and_parse` / `LLMTimeoutError` 그대로 재사용 (V1 수정 금지)
- V2 전용 프롬프트 로더 (`v2/prompts/group_b/item_*.sonnet.md` 경로)
- `max_concurrent=2` conservative semaphore (Bedrock throttle 대응)
- 산술 일관성 + snap 후처리는 각 Sub Agent 에서 `snap_score_v2` + reconciler 로 수행

V1 원본 미수정. 순수 import + 경로 wrapper.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

# V1 경로를 sys.path 에 추가하여 nodes.llm 을 import (V1 수정 금지 — import only)
_V1_QA_PIPELINE = (
    Path("C:/Users/META M/Desktop/업무/qa/agentcore-a2a-workshop")
    / "packages" / "agentcore-agents" / "qa-pipeline"
)
if _V1_QA_PIPELINE.exists() and str(_V1_QA_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_V1_QA_PIPELINE))

from nodes.llm import (  # noqa: E402 — V1 import after path prepend
    LLMTimeoutError,
    get_chat_model,
    invoke_and_parse,
)

__all__ = [
    "LLMTimeoutError",
    "get_chat_model",
    "invoke_and_parse",
    "load_group_b_prompt",
    "call_bedrock_json",
    "HumanMessage",
    "SystemMessage",
]


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conservative semaphore (Bedrock throttle 대응)
# 2026-04-21: 3-Persona 앙상블 도입으로 하드코딩 2 → env-driven 상향.
# Group B 는 항목 2~3개 × 3 persona = 6~9 동시 호출을 소화해야 함.
# SAGEMAKER_MAX_CONCURRENT env var (config.py 와 동일 키) 로 오버라이드.
# 2026-04-21 임시: 기본값을 200 으로 풀어 사실상 무제한 — Bedrock throttle 실험용.
# 권장값 복귀 시 15 로 변경.
# ---------------------------------------------------------------------------

# 2026-04-27: 사용자 요청으로 기본값 1 (순차 실행) — Bedrock ThrottlingException 방지.
# 환경변수로 상향 조정 가능 (SAGEMAKER_MAX_CONCURRENT=10 등). Bedrock TPM/RPM 쿼터 충분하면 올리기.
_GROUP_B_MAX_CONCURRENT = int(os.environ.get("SAGEMAKER_MAX_CONCURRENT", "1"))

# 세마포어는 생성된 event loop 에 묶이므로 loop 별로 분리해서 캐싱.
# ThreadPoolExecutor 워커가 자체 새 loop 로 async 코드를 돌릴 때 (post-debate judge 등)
# 메인 loop 의 세마포어를 재사용하면 "bound to a different event loop" 에러 발생.
import threading as _threading

_loop_semaphores: dict[int, asyncio.Semaphore] = {}
_semaphore_lock = _threading.Lock()


def _get_semaphore() -> asyncio.Semaphore:
    """현재 running event loop 에 묶인 세마포어 반환. loop 별로 인스턴스 분리.

    각 loop 마다 동일 한도 (_GROUP_B_MAX_CONCURRENT) 의 세마포어를 별도 인스턴스로 보유.
    한 loop 에서 만든 세마포어는 다른 loop 에서 acquire 시 RuntimeError → 분리 필수.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop_id = id(loop)
    sem = _loop_semaphores.get(loop_id)
    if sem is None:
        with _semaphore_lock:
            sem = _loop_semaphores.get(loop_id)
            if sem is None:
                sem = asyncio.Semaphore(_GROUP_B_MAX_CONCURRENT)
                _loop_semaphores[loop_id] = sem
    return sem


# ---------------------------------------------------------------------------
# V2 prompts/group_b/ 로더 (V1 loader 와 독립)
# ---------------------------------------------------------------------------

_V2_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "group_b"


# xlsx 전 탭 + PDF 원칙 반영 공통 preamble (evaluation_mode / Override / 마스킹 / STT)
from v2.prompts._common_preamble import COMMON_PREAMBLE  # noqa: E402


@lru_cache(maxsize=None)
def load_group_b_prompt(name: str) -> str:
    """Load V2 Group B prompt markdown.

    모든 프롬프트 최하단에 xlsx 전 탭 공통 preamble 1회 append.
    PDF 원칙 4 준수: 공통 감점 Override 는 Sub Agent 가 강제하지 않고
    `override_hint` 필드만 기재 (Orchestrator 가 Layer 3 에서 적용).
    """
    sonnet_path = _V2_PROMPTS_DIR / f"{name}.sonnet.md"
    md_path = _V2_PROMPTS_DIR / f"{name}.md"
    path = sonnet_path if sonnet_path.exists() else md_path
    if not path.exists():
        raise FileNotFoundError(f"V2 Group B prompt not found: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    body = text.strip()
    return f"{body}\n\n{COMMON_PREAMBLE}"


# ---------------------------------------------------------------------------
# Bedrock JSON call wrapper (semaphore + LLMTimeoutError passthrough)
# ---------------------------------------------------------------------------


class _SkippedLLMError(Exception):
    """`V2_GROUP_B_SKIP_LLM=1` 환경에서 LLM 호출을 의도적으로 skip 한 것을 나타냄.

    호출자(Sub Agent) 는 generic except 로 포착하여 rule fallback 경로로 진행.
    """


async def call_bedrock_json(
    *,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1024,
    backend: str = "bedrock",
    bedrock_model_id: str | None = None,
) -> dict[str, Any]:
    """V1 `invoke_and_parse` 로 Bedrock 호출 + JSON dict 반환.

    환경변수 `V2_GROUP_B_SKIP_LLM=1` 세팅 시 `_SkippedLLMError` raise 하여
    호출자가 rule fallback 로 진행하도록 유도 (테스트 / offline dev 환경).

    호출자는 반드시 `except LLMTimeoutError: raise` 를 generic except 앞에 배치할 것.
    Bedrock throttle 방지 semaphore 경유.
    """
    import os

    if os.getenv("V2_GROUP_B_SKIP_LLM"):
        raise _SkippedLLMError("V2_GROUP_B_SKIP_LLM=1 — LLM 호출 생략")

    sem = _get_semaphore()
    async with sem:
        llm = get_chat_model(
            max_tokens=max_tokens,
            backend=backend,
            bedrock_model_id=bedrock_model_id,
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        return await invoke_and_parse(llm, messages)
