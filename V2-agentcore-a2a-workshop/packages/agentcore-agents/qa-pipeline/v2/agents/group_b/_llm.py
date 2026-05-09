# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group B Sub Agent 공용 LLM wrapper — V1 `nodes/llm.py` 를 import 로 재활용.

역할:
- V1 `get_chat_model` / `invoke_and_parse` / `LLMTimeoutError` 그대로 재사용 (V1 수정 금지)
- V2 전용 프롬프트 로더 (`v2/prompts/group_b/item_*.sonnet.md` 경로)
- 산술 일관성 + snap 후처리는 각 Sub Agent 에서 `snap_score_v2` + reconciler 로 수행

동시성 제어는 `nodes/llm.py::_get_semaphore` (SAGEMAKER_MAX_CONCURRENT env) 단일
세마포어가 `invoke_and_parse` 안에서 처리. group_b 측 외부 세마포어는 2026-04-30 제거
(이중 sem 으로 group_b 전체 호출이 직렬화되던 문제).

V1 원본 미수정. 순수 import + 경로 wrapper.
"""

from __future__ import annotations

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

    Bedrock 동시성은 `nodes/llm.py::_get_semaphore` (SAGEMAKER_MAX_CONCURRENT) 가
    `invoke_and_parse` 안에서 단독 처리.
    """
    if os.getenv("V2_GROUP_B_SKIP_LLM"):
        raise _SkippedLLMError("V2_GROUP_B_SKIP_LLM=1 — LLM 호출 생략")

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
