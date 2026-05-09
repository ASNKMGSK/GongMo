# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GT xlsx 경로 / 시트 매칭 단일 진실(SoT).

server_v2.py 의 `/v2/gt-scores` 와 layer4/gt_comparison.py 의 `_load_gt_items` 가
두 군데에 동일한 매칭 알고리즘을 인라인으로 들고 있어 668451-A 류 변형 sample_id 가
한쪽만 패치되면 batch ≠ UI 결과 차이가 발생.

2026-04-30 S4 fix: 양쪽이 이 모듈만 import 하도록 통합.

API:
- `resolve_gt_xlsx_path(env_var, explicit)` — 환경변수 → OS 별 후보 → 첫 존재 경로 반환
- `match_sheet(sheet_names, sample_id, explicit_sheet)` — (matched, method) 반환
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

_GT_FILENAMES: tuple[str, ...] = (
    # 2026-05-07: 신규 GT — STT 원문 + 상담유형 추가 버전. 최우선.
    "STT QA 정답표_재채점 및 근거 작성(STT 원문 및 상담유형 추가).xlsx",
    # 2026-04-30: STT 재채점 + 근거 작성 버전.
    "STT QA 정답표_재채점 및 근거 작성.xlsx",
    "QA정답-STT_기반_통합_상담평가표_v3재평가_fixed.xlsx",
    "QA정답-STT_기반_통합_상담평가표_v3재평가.xlsx",
    "코오롱 업무 정확도 auto_qa_criteria.xlsx",
)


def gt_xlsx_candidates() -> list[str]:
    """OS 별 GT xlsx 후보 경로 리스트. 환경변수 우선.

    Windows: `C:\\Users\\META M\\Desktop` (로컬 dev)
    Linux: `~/qa-data`, `/home/ubuntu/qa-data`, `/opt/qa-pipeline/data`,
            `<qa-pipeline>/data/gt`, `<qa-pipeline>/data`
    """
    candidates: list[str] = []
    env_path = os.environ.get("QA_GT_XLSX_PATH")
    if env_path:
        candidates.append(env_path)

    if platform.system() == "Windows":
        base_dirs = [
            Path(r"C:\Users\META M\Desktop\qa테스트 정답"),
            Path(r"C:\Users\META M\Desktop"),
        ]
    else:
        qa_pipeline_root = Path(__file__).resolve().parent.parent.parent
        base_dirs = [
            Path.home() / "qa-data",
            Path("/home/ubuntu/qa-data"),
            Path("/opt/qa-pipeline/data"),
            qa_pipeline_root / "data" / "gt",
            qa_pipeline_root / "data",
        ]
    for b in base_dirs:
        for fn in _GT_FILENAMES:
            candidates.append(str(b / fn))
    return candidates


def resolve_gt_xlsx_path() -> str | None:
    """환경변수 / OS 별 후보 중 첫 존재 경로 반환. 없으면 None."""
    for p in gt_xlsx_candidates():
        if p and Path(p).exists():
            return p
    return None


def match_sheet(
    sheet_names: list[str],
    sample_id: str,
    explicit_sheet: str | None = None,
) -> tuple[list[str], str]:
    """sample_id 로 시트 매칭 — lenient 3단 (suffix → contains → digits).

    Args:
        sheet_names: xlsx 의 sheetnames 리스트
        sample_id: 매칭할 sample id
        explicit_sheet: 사용자 명시 시트명 (있으면 단독 매칭)

    Returns:
        (matched_sheet_list, method)
        method ∈ "explicit" / "suffix" / "contains" / "digits" / "none"
    """
    if explicit_sheet and explicit_sheet in sheet_names:
        return [explicit_sheet], "explicit"

    target_suffix = f"_{sample_id}"
    matched = [s for s in sheet_names if s.endswith(target_suffix)]
    if matched:
        return matched, "suffix"

    matched = [s for s in sheet_names if sample_id in s]
    if matched:
        return matched, "contains"

    sid_digits = re.sub(r"\D", "", sample_id) or sample_id
    sid_int: int | None
    try:
        sid_int = int(sid_digits) if sid_digits else None
    except (ValueError, TypeError):
        sid_int = None
    if sid_int is not None:
        digits_matched: list[str] = []
        for s in sheet_names:
            nums = re.findall(r"\d+", s)
            if any(int(n) == sid_int for n in nums if n):
                digits_matched.append(s)
        if digits_matched:
            return digits_matched, "digits"

    return [], "none"
