# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V2 Sub Agent Group B — 설명력·적극성·업무정확도·개인정보보호.

담당 항목: #10~#18 (9개 항목, 4개 Sub Agent).

- explanation (#10, #11) — 설명명확성 + 두괄식
- proactiveness (#12, #13, #14) — 문제해결의지 + 부연설명 + 사후안내
- work_accuracy (#15, #16) — 정확한안내(RAG) + 필수안내이행
- privacy (#17, #18) — 정보확인절차 + 정보보호준수 (compliance_based + force_t3)

V1 재활용: nodes/scope.py, nodes/proactiveness.py, nodes/work_accuracy.py,
nodes/incorrect_check.py.

V2 신규:
- Sub Agent 공통 응답 스키마 (verdict_mode, routing, flags.patterns_detected)
- #15 업무지식 RAG 분기 (available / coverage / unevaluable)
- #17/#18 패턴 A/B/C 탐지 + force_t3
- iter03_clean 핵심 개선(#10 장황 삭제 + reconciler) 유지
"""

from __future__ import annotations
