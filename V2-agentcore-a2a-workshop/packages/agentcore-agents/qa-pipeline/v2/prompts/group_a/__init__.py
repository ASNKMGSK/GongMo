# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group A 병합 프롬프트 로더 (Phase A2 rubric 확정 후 본문 작성).

현재 단계: skeleton. V1 iter03_clean 프롬프트를 fallback 으로 재사용.
본격 병합 프롬프트는 `v2/prompts/group_a/{greeting,listening_comm,language,needs}.md` 에
Phase A2 확정 rubric 기반으로 Dev2 가 작성.
"""

from __future__ import annotations

from pathlib import Path


_PROMPTS_DIR = Path(__file__).parent


def load_prompt(
    sub_agent: str,
    *,
    item: str | None = None,
    backend: str | None = None,
) -> str:
    """Group A Sub Agent 병합 프롬프트 로드.

    Args:
        sub_agent: "greeting" | "listening_comm" | "language" | "needs"
        item: 항목별 병합 시 확장 슬롯 (예: "item_01" for 인사예절 Sub Agent 내 첫인사 섹션).
              skeleton 단계에서는 item 별 파일을 참조해 V1 프롬프트를 그대로 재사용.
        backend: "bedrock" | "sagemaker" — V1 호환 (sonnet.md 변형 선택용)

    모든 프롬프트 최하단에 xlsx 전 탭 공통 preamble append
    (evaluation_mode 6종 / Override 4종 / 마스킹 / STT 유의사항).
    """
    from v2.prompts._common_preamble import COMMON_PREAMBLE  # circular import 회피용 지연 import

    # 1) V2 Sub Agent 병합 프롬프트 우선 (작성되면 사용)
    sub_path = _PROMPTS_DIR / f"{sub_agent}.md"
    if sub_path.exists():
        body = sub_path.read_text(encoding="utf-8").rstrip()
        return f"{body}\n\n{COMMON_PREAMBLE}"

    # 2) 아직 없으면 FileNotFoundError → 호출측이 V1 프롬프트 import 로 fallback
    raise FileNotFoundError(f"V2 Group A prompt not yet created: {sub_path}")
