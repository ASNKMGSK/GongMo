# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
18 항목 rubric 메타 — synthetic 골든셋 생성 프롬프트용.

`rubric.md` 의 평가 기준을 구조화. 점수별 조건, 전형적 패턴, V1 iter03_clean 의 특례까지 포함.
"""

from __future__ import annotations


ITEMS: list[dict] = [
    {
        "item_number": 1,
        "slug": "first_greeting",
        "name": "첫인사",
        "category": "인사 예절",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "상담 시작 3~5 발화",
        "full_criteria": "인사말 + 소속 + 상담사명 3요소 모두 포함",
        "partial_criteria": "3요소 중 1개 누락",
        "zero_criteria": "2개 이상 누락 또는 첫인사 자체 미진행",
        "v1_notes": "",
    },
    {
        "item_number": 2,
        "slug": "closing_greeting",
        "name": "끝인사",
        "category": "인사 예절",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "상담 종료 3~5 발화",
        "full_criteria": "추가 문의 확인 + 인사말 + 상담사명 3요소 모두 포함",
        "partial_criteria": "3요소 중 1개 누락 (iter03_clean 완화: 추가 문의 확인 + 인사말 또는 상담사명 중 1개면 3점 가능)",
        "zero_criteria": "끝인사 미진행 또는 2개 이상 누락",
        "v1_notes": "iter03_clean: 2-요소 완화",
    },
    {
        "item_number": 3,
        "slug": "listening_interruption",
        "name": "경청/말겹침/말자름",
        "category": "경청 및 소통",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "에이전트 전 발화 + 말겹침 마커",
        "full_criteria": "말겹침/말자름 없이 고객 발언 끝까지 경청",
        "partial_criteria": "말겹침 1회 또는 중간 개입 1회 ([동시] 마커 1회)",
        "zero_criteria": "말자름 1회 이상 또는 말겹침 2회 이상",
        "v1_notes": "STT 겹침 구간 미표기 시 만점 처리",
    },
    {
        "item_number": 4,
        "slug": "empathy",
        "name": "호응 및 공감",
        "category": "경청 및 소통",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "감정 표현 / 사과 근처 윈도우",
        "full_criteria": "다양한 호응 또는 공감 표현 1회 이상 (감정 인정 + 사과 + 감사 중 2개 이상)",
        "partial_criteria": "단순 '네' 위주 호응만 사용",
        "zero_criteria": "호응/공감 표현 전무 또는 부적절 반응 (감정 호소 무시)",
        "v1_notes": "",
    },
    {
        "item_number": 5,
        "slug": "hold_notice",
        "name": "대기 멘트",
        "category": "경청 및 소통",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "대기 마커 / 사전·사후 안내 근처",
        "full_criteria": "대기 전 양해 + 대기 후 감사/사과 모두 안내",
        "partial_criteria": "사전 또는 사후 안내 중 1개만 있음",
        "zero_criteria": "대기 상황에서 안내 전무",
        "v1_notes": "",
    },
    {
        "item_number": 6,
        "slug": "polite_language",
        "name": "정중한 표현",
        "category": "언어 표현",
        "max_score": 5,
        "allowed_steps": [5, 0],
        "segment_strategy": "에이전트 전 발화",
        "full_criteria": "전 발화 존댓말 + 부적절 표현 전무 (반말/비하/업무회피/비속어 전 없음)",
        "partial_criteria": "(해당 없음 — 2단계 평가)",
        "zero_criteria": "부적절 표현 1회 이상: 반말, 비하('네가 뭔데'), 비속어('아 씨'), 업무회피('직접 하세요'), 혹은 고객 비하",
        "v1_notes": "2단계만 있음. 부적절 표현 단 1회도 즉시 0점",
    },
    {
        "item_number": 7,
        "slug": "cushion_words",
        "name": "쿠션어 활용",
        "category": "언어 표현",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "거절/불가/양해 발화 근처 (refusal-gated)",
        "full_criteria": "거절/불가/요청 상황에서 쿠션어('번거로우시겠지만', '양해 부탁드립니다' 등) 활용",
        "partial_criteria": "거절 상황인데 쿠션어 누락, 단 내용은 정확하게 전달",
        "zero_criteria": "거절 반복 + 쿠션어/양해 전무 + 건조한 응대 톤",
        "v1_notes": "iter03_clean: refusal-gated — 거절/양해 상황이 전제되지 않으면 감점하지 않음",
    },
    {
        "item_number": 8,
        "slug": "needs_identification",
        "name": "문의 파악 / 재확인",
        "category": "니즈 파악",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "에이전트 장문 발화",
        "full_criteria": "고객 문의 요약 복창 + 명확화 질문으로 니즈 재확인",
        "partial_criteria": "문의 파악은 되었으나 복창/재확인 절차 생략",
        "zero_criteria": "고객이 문의를 이미 말했는데 파악 없이 재질문 또는 문의 파악 전무",
        "v1_notes": "",
    },
    {
        "item_number": 9,
        "slug": "customer_info_verification",
        "name": "고객 정보 확인",
        "category": "니즈 파악",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "본인확인 질의응답 근처",
        "full_criteria": "성함 + 생년월일 + 등록 연락처 3-요소 본인확인 질의. 상담사가 정보를 먼저 언급하지 않음",
        "partial_criteria": "본인확인 진행했으나 일부(1~2개) 요소만 확인",
        "zero_criteria": "상담사가 이름/정보를 먼저 발화 (preemptive disclosure) 또는 본인확인 미수행",
        "v1_notes": "",
    },
    {
        "item_number": 10,
        "slug": "explanation_clarity",
        "name": "설명 명확성",
        "category": "설명력 및 전달력",
        "max_score": 10,
        "allowed_steps": [10, 7, 3, 0],
        "segment_strategy": "에이전트 장문 발화(50자 이상) 블록",
        "full_criteria": "항목별 정보 구조화 + 숫자/조건 명시 + 애매 표현 없음",
        "partial_mid_criteria": "7점: 핵심 정보는 있으나 '정도' 완화어 사용 또는 1개 세부 조건 미명시",
        "partial_low_criteria": "3점: 실질적 정보 전달 미흡, 회피성 답변 섞임",
        "zero_criteria": "설명 전무, 업무회피 ('모르겠네요')",
        "v1_notes": "iter03_clean: 장황 조항 삭제 + reconciler. 중간점수 7점 존재 (4단계)",
    },
    {
        "item_number": 11,
        "slug": "top_down_answer",
        "name": "두괄식 답변",
        "category": "설명력 및 전달력",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "답변 첫 3 발화",
        "full_criteria": "결론 먼저 + 세부 설명 뒤 (두괄식 구조)",
        "partial_criteria": "세부 먼저 + 결론 맨 뒤 (미괄식)",
        "zero_criteria": "결론 없이 불명확한 회피성 답변",
        "v1_notes": "",
    },
    {
        "item_number": 12,
        "slug": "problem_solving_attitude",
        "name": "문제 해결 의지",
        "category": "적극성",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "문제 언급 → 해결 제안 구간",
        "full_criteria": "즉시 확인 + 이관 + 콜백 등 다층 해결책 제시",
        "partial_criteria": "해결 의지는 있으나 구체적 제안 없음",
        "zero_criteria": "업무 회피, 이관 노력 전무 ('다른 데로 전화해 주세요')",
        "v1_notes": "",
    },
    {
        "item_number": 13,
        "slug": "additional_guidance",
        "name": "부연 설명 및 추가 안내",
        "category": "적극성",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "답변 직후 에이전트 발화",
        "full_criteria": "핵심 답변 + 선제적 추가 혜택/조건/후속 단계 안내 (2종 이상)",
        "partial_criteria": "핵심 답변만. 부연 설명/추가 안내 없음",
        "zero_criteria": "회피성 답변 + 부연 설명 거부 ('더 자세한 건 홈페이지요')",
        "v1_notes": "",
    },
    {
        "item_number": 14,
        "slug": "follow_up",
        "name": "사후 안내",
        "category": "적극성",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "종료 전 에이전트 발화",
        "full_criteria": "시점(날짜/시간) + 경로(SMS/이메일) + 예외 대응(콜백) 모두 명시",
        "partial_criteria": "사후 안내는 있으나 시점/경로 불명확 ('처리되면 연락드릴게요')",
        "zero_criteria": "사후 안내 전무",
        "v1_notes": "",
    },
    {
        "item_number": 15,
        "slug": "correct_information",
        "name": "정확한 안내",
        "category": "업무 정확도",
        "max_score": 10,
        "allowed_steps": [10, 7, 3, 0],
        "segment_strategy": "고객 핵심 질문 + 상담사 핵심 답변 pair",
        "full_criteria": "업무지식 매뉴얼과 사실 완전 일치, 수치/조건 정확",
        "partial_mid_criteria": "7점: 핵심 사실은 맞으나 세부(1~2개) 누락 또는 모호",
        "partial_low_criteria": "3점: 사실과 부분 불일치, 불완전 안내",
        "zero_criteria": "허위 안내 미정정, 잘못된 사실 전달",
        "v1_notes": "업무지식 RAG 필수. RAG 부재 시 unevaluable (예시 분량에서 제외 — 운영 상 별도 처리)",
    },
    {
        "item_number": 16,
        "slug": "mandatory_notice",
        "name": "필수 안내 이행",
        "category": "업무 정확도",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "intent 기반 script 대조",
        "full_criteria": "intent 별 mandatory_scripts 의 required=true 항목 모두 이행",
        "partial_criteria": "required 항목 일부(1개) 누락",
        "zero_criteria": "필수 안내 전무 또는 핵심 required 항목(사과/본인확인 등) 미이행",
        "v1_notes": "",
    },
    {
        "item_number": 17,
        "slug": "pii_verification",
        "name": "정보 확인 절차",
        "category": "개인정보 보호",
        "max_score": 5,
        "allowed_steps": [5, 3, 0],
        "segment_strategy": "PII 토큰 근처 N 발화",
        "full_criteria": "성함 + 생년월일 + 연락처 3-요소 본인확인 + 확인 완료 멘트",
        "partial_criteria": "2-요소만 확인, 나머지 누락 (iter05 회귀로 3점 허용 확장)",
        "zero_criteria": "본인확인 절차 전무, 즉시 진행",
        "v1_notes": "iter05: ALLOWED_STEPS [5,0] → [5,3,0] 확장",
    },
    {
        "item_number": 18,
        "slug": "privacy_compliance",
        "name": "정보 보호 준수",
        "category": "개인정보 보호",
        "max_score": 5,
        "allowed_steps": [5, 0],
        "segment_strategy": "PII 유출 스캔 전체",
        "full_criteria": "개인정보 유출/제3자 민감정보 제공 없음, 제3자 요청 명시 거절",
        "partial_criteria": "(해당 없음 — 2단계 평가)",
        "zero_criteria": "주소/주민번호/계좌번호 유출, 제3자 민감정보 제공, 타인 계약정보 공개",
        "v1_notes": "2단계만 있음. 유출 1건 발견 즉시 0점 + 별도 컴플라이언스 보고",
    },
]


def get_buckets_for(item: dict) -> list[tuple[str, int, str]]:
    """각 항목의 allowed_steps 를 (bucket_label, score, criteria) 튜플로 반환."""
    steps = item["allowed_steps"]
    buckets = []
    if len(steps) == 4:  # [max, mid, low_partial, 0]
        buckets.append(("full", steps[0], item["full_criteria"]))
        buckets.append(("partial_mid", steps[1], item.get("partial_mid_criteria", item.get("partial_criteria", ""))))
        buckets.append(("partial_low", steps[2], item.get("partial_low_criteria", item.get("partial_criteria", ""))))
        buckets.append(("zero", steps[3], item["zero_criteria"]))
    elif len(steps) == 3:  # [max, partial, 0]
        buckets.append(("full", steps[0], item["full_criteria"]))
        buckets.append(("partial", steps[1], item["partial_criteria"]))
        buckets.append(("zero", steps[2], item["zero_criteria"]))
    elif len(steps) == 2:  # [max, 0]
        buckets.append(("full", steps[0], item["full_criteria"]))
        buckets.append(("zero", steps[1], item["zero_criteria"]))
    return buckets


SUPPORTED_INTENTS = [
    "general_inquiry",
    "complaint",
    "info_change",
    "cancellation",
    "claim",
    "product_inquiry",
    "technical_support",
    "billing",
]

DOMAIN_POOL = ["금융", "통신", "이커머스", "CS 일반"]
