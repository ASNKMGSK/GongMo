# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KSQI 평가 9개 항목 메타 (배점 / 영역 / 평가방식).

총 80점 (A 영역 50점 / B 영역 30점). 결함 1건 발생 시 해당 항목 배점 전액 차감.
환산 점수 = (획득점수 / 배점합계) × 100. 각 영역 별도 산출.
우수 콜센터: A 92↑ / B 80↑ (환산 기준).
"""

from __future__ import annotations

from typing import Literal, TypedDict


class KsqiRule(TypedDict):
    item_number: int           # 1~9
    item_name: str             # 항목명 (예: "맞이인사 구성요소")
    area: Literal["A", "B"]    # A: 서비스품질 / B: 공감
    sub_category: str          # "맞이인사" / "상담태도" / "업무처리" / "종료태도"
    max_score: int             # 항목 배점 (5 or 10)
    eval_method: Literal["rule", "llm", "hybrid"]
    description: str           # 판정 포인트 (사람이 읽는 설명)
    node_name: str             # graph 노드명 (ksqi_*)


KSQI_RULES: list[KsqiRule] = [
    # ── A. 서비스 품질 영역 (50점 / 6개 항목) ──
    {
        "item_number": 1,
        "item_name": "맞이인사 구성요소",
        "area": "A",
        "sub_category": "맞이인사",
        "max_score": 10,
        "eval_method": "rule",
        "description": "첫인사 / 소속 / 이름 / 용무문의 4가지 구성요소 모두 포함 여부. 하나라도 누락 시 결함.",
        "node_name": "ksqi_greeting_open",
    },
    {
        "item_number": 2,
        "item_name": "단답형 응대",
        "area": "A",
        "sub_category": "상담태도",
        "max_score": 5,
        "eval_method": "rule",
        "description": "상담사 발화 중 '네' '네네' '맞아요' 등 단답형만으로 구성된 발화 비율이 높으면 결함 (반복 패턴).",
        "node_name": "ksqi_terse_response",
    },
    {
        "item_number": 3,
        "item_name": "거부 후 재안내",
        "area": "A",
        "sub_category": "상담태도",
        "max_score": 5,
        "eval_method": "llm",
        "description": "업셀링/크로스셀링 거부 의사 표현 후 동일 안내 반복 시 결함. 거절 의도 인식 + 후속 발화 검사.",
        "node_name": "ksqi_refusal_followup",
    },
    {
        "item_number": 4,
        "item_name": "쉬운 설명",
        "area": "A",
        "sub_category": "업무처리",
        "max_score": 10,
        "eval_method": "llm",
        "description": "상담사 설명의 논리 전개 / 문장 완결성 / 맥락 일관성. 두서없음 · 중언부언 · 문장 중단 시 결함.",
        "node_name": "ksqi_easy_explain",
    },
    {
        "item_number": 5,
        "item_name": "문의내용 파악도",
        "area": "A",
        "sub_category": "업무처리",
        "max_score": 10,
        "eval_method": "llm",
        "description": "(a) 고객이 같은 내용을 2회 이상 재진술 시 결함, (b) 복합질의에서 일부 답변 누락 시 결함.",
        "node_name": "ksqi_inquiry_grasp",
    },
    {
        "item_number": 6,
        "item_name": "종료인사 구성요소",
        "area": "A",
        "sub_category": "종료태도",
        "max_score": 10,
        "eval_method": "rule",
        "description": "종료인사 / 이름 2가지 구성요소 모두 포함 여부. 하나라도 누락 시 결함.",
        "node_name": "ksqi_greeting_close",
    },
    # ── B. 공감 영역 (30점 / 3개 항목) ──
    {
        "item_number": 7,
        "item_name": "답례표현",
        "area": "B",
        "sub_category": "맞이인사",
        "max_score": 10,
        "eval_method": "rule",
        "description": "고객 인사·감사 표현에 적절한 답례 (정상: '네~ 안녕하세요' / 결함: '네' '말씀하세요' '여보세요').",
        "node_name": "ksqi_acknowledgment",
    },
    {
        "item_number": 8,
        "item_name": "단순 공감 표현",
        "area": "B",
        "sub_category": "상담태도",
        "max_score": 10,
        "eval_method": "hybrid",
        "description": "고객 발화 호응 시 공감 표현 사용 (정상: '아~ 그러셨군요' / 결함: '네네' '아니요' 단답형).",
        "node_name": "ksqi_basic_empathy",
    },
    {
        "item_number": 9,
        "item_name": "고차원 공감 표현",
        "area": "B",
        "sub_category": "상담태도",
        "max_score": 10,
        "eval_method": "llm",
        "description": "불만/어려움/경조사/양해 등 상황 인식 후 맞춤 공감 (사과·위로·조의·축하·감사).",
        "node_name": "ksqi_advanced_empathy",
    },
]


# 노드 이름 순서 (graph fan-out / barrier 용)
KSQI_NODES: tuple[str, ...] = tuple(r["node_name"] for r in KSQI_RULES)


def get_rule(item_number: int) -> KsqiRule:
    for r in KSQI_RULES:
        if r["item_number"] == item_number:
            return r
    raise KeyError(f"KSQI rule not found: item_number={item_number}")


# 영역별 배점 합계 (환산 계산용)
AREA_MAX: dict[str, int] = {
    "A": sum(r["max_score"] for r in KSQI_RULES if r["area"] == "A"),  # 50
    "B": sum(r["max_score"] for r in KSQI_RULES if r["area"] == "B"),  # 30
}

# 우수 콜센터 환산 임계값
EXCELLENT_THRESHOLD: dict[str, int] = {"A": 92, "B": 80}
