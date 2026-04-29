# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 전처리 파이프라인 (설계서 p9-10 (a)~(e) 5개 서브 모듈).

실행 순서 (순차 고정):
    (a) quality_gate       — STT 품질 검증
    (b) segment_splitter   — 구간 분리 (opening/body/closing)
    (c) pii_normalizer     — PII 토큰 정규화 (v1_symbolic + v2_categorical 양 경로)
    (d) deduction_trigger_detector — 감점 트리거 사전 탐지
    (e) rule_pre_verdictor — Rule 1차 판정

개별 모듈은 state 독립형(pure function) — 입력 dict/문자열 → 출력 dict.
`run_layer1(...)` 에서 5개 모듈을 순차 호출해 preprocessing 필드 생성.
"""

from v2.layer1.deduction_trigger_detector import detect_triggers  # noqa: F401
from v2.layer1.pii_normalizer import normalize_pii  # noqa: F401
from v2.layer1.quality_gate import quality_gate_check  # noqa: F401
from v2.layer1.rule_pre_verdictor import build_rule_pre_verdicts  # noqa: F401
from v2.layer1.run_layer1 import run_layer1  # noqa: F401
from v2.layer1.segment_splitter import split_sections  # noqa: F401

__all__ = [
    "detect_triggers",
    "normalize_pii",
    "quality_gate_check",
    "build_rule_pre_verdicts",
    "run_layer1",
    "split_sections",
]
