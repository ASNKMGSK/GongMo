# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Runtime evaluation flags — 평가 세션 단위 런타임 상태.

모듈 단위 dict 로 프로세스 전역 상태 보관 (스레드 안전은 보장 X — 단일 평가 세션
기준이므로 동시 평가 시 교차 오염 가능. 현재는 단일 세션 전제).

사용처:
  - `current_sample_id` : 평가 대상 샘플의 ID. RAG 에서 self-exclusion 필터링 용.
    서버(server_v2.py) 가 `/evaluate/stream` 진입 시 body.gt_sample_id 를 주입.
"""

from __future__ import annotations

import re
from typing import Optional


_STATE: dict[str, Optional[str]] = {
    "current_sample_id": None,
}


def set_current_sample_id(sample_id: Optional[str]) -> None:
    """현재 평가 세션의 sample_id 설정. None 이면 self-exclusion 비활성."""
    _STATE["current_sample_id"] = str(sample_id).strip() if sample_id else None


def get_current_sample_id() -> Optional[str]:
    return _STATE.get("current_sample_id")


# example_id 포맷: `GS-02-FULL-668797-T133` / `GS-01-FULL-668437`
# record_id 포맷: `r_02_668797_T133` / `r_02_668797_agg`
_SAMPLE_ID_PATTERN = re.compile(r"\b(\d{6})\b")


def extract_sample_id_from_id(any_id: str) -> Optional[str]:
    """example_id / record_id 에서 6자리 sample_id 추출. 없으면 None."""
    if not any_id:
        return None
    m = _SAMPLE_ID_PATTERN.search(any_id)
    return m.group(1) if m else None


def is_self_sample(example_id: str, record_id: str | None = None) -> bool:
    """현재 sample_id 와 일치하면 True — RAG 결과에서 제외 대상."""
    current = get_current_sample_id()
    if not current:
        return False
    extracted = extract_sample_id_from_id(example_id) or extract_sample_id_from_id(record_id or "")
    return extracted == current
