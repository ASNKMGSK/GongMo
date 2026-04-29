# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 Orchestrator (설계서 p10 Layer 3 (a)-(d)).

실행 순서 (순차 고정):
    (a) aggregator          — 대분류별 점수 집계 + raw_total
    (b) override_rules      — Layer 1/Layer 2 감점 트리거 → OverrideAction 적용
    (c) consistency_checker — Sub Agent 결과 간 Rule 기반 교차 점검 (모순 flag)
    (d) grader              — grade 판정 + 경계 ±3 → T2/T3 라우팅 힌트

`run_layer3(...)` 가 4 모듈을 순차 호출해 orchestrator dict 생성.
`skip_phase_c_and_reporting=True` 플래그 유지 — 프롬프트 튜닝 배치용.
"""

from v2.layer3.aggregator import aggregate_scores  # noqa: F401
from v2.layer3.consistency_checker import check_consistency  # noqa: F401
from v2.layer3.grader import assign_grade  # noqa: F401
from v2.layer3.orchestrator_v2 import run_layer3  # noqa: F401
from v2.layer3.override_rules import apply_overrides  # noqa: F401

__all__ = [
    "aggregate_scores",
    "check_consistency",
    "assign_grade",
    "run_layer3",
    "apply_overrides",
]
