# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""tracking 모듈 기본 경로 / 임계."""

from __future__ import annotations

import os
from pathlib import Path


# ── 적재 루트 ────────────────────────────────────────────────────
# 환경변수 QA_TRACKING_ROOT 로 오버라이드 가능 (예: 외부 NAS 경로).
TRACKING_ROOT: Path = Path(
    os.environ.get(
        "QA_TRACKING_ROOT",
        str(Path(__file__).parent / "runs"),
    )
).resolve()


# ── 정답 xlsx 경로 ─────────────────────────────────────────────
# gt_comparison.py 와 동일 fallback 체인.
def default_xlsx_path() -> Path:
    env = os.environ.get("QA_GT_XLSX_PATH")
    if env and Path(env).exists():
        return Path(env)
    desktop = Path(r"C:\Users\META M\Desktop")
    for fname in (
        "QA정답-STT_기반_통합_상담평가표_v3재평가_fixed.xlsx",
        "QA정답-STT_기반_통합_상담평가표_v3재평가.xlsx",
    ):
        p = desktop / fname
        if p.exists():
            return p
    return desktop / "QA정답-STT_기반_통합_상담평가표_v3재평가_fixed.xlsx"


# ── 회귀 임계 ────────────────────────────────────────────────────
# direction:
#   'lower_better'  → 직전 run 대비 현재가 +threshold 초과 시 alert
#   'absolute'      → |current - prev| > threshold 시 alert
REGRESSION_THRESHOLDS: dict[str, dict[str, float | str]] = {
    "MAE":   {"direction": "lower_better", "threshold": 0.5},
    "RMSE":  {"direction": "lower_better", "threshold": 0.7},
    "Bias":  {"direction": "absolute",     "threshold": 0.5},
    "MAPE":  {"direction": "lower_better", "threshold": 1.0},
}


# ── 기본 데이터셋 ────────────────────────────────────────────────
# label → (samples_dir, allowed_ids)
DATASETS: dict[str, dict[str, str]] = {
    "training": {
        "samples_dir": r"C:\Users\META M\Desktop\qa 샘플\학습셋",
        "kind": "training",
    },
    "test": {
        "samples_dir": r"C:\Users\META M\Desktop\qa 샘플\테스트셋",
        "kind": "test",
    },
}
