# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# QA 평가 규칙 데이터셋
# =============================================================================
# 이 모듈은 QA 평가 파이프라인에서 사용하는 18개 평가 항목 규칙을 정의한다.
# 각 평가 에이전트는 이 규칙에 따라 상담 녹취록을 채점한다.
#
# [핵심 역할]
# - 18개 평가 항목의 채점 기준, 감점 규칙, 체크리스트를 정의
# - retrieval 노드가 Wiki에서 규칙을 찾지 못할 때의 폴백 데이터 역할
# - wiki_compiler가 Wiki 페이지를 생성할 때의 원본 데이터(raw source)
#
# [항목 구성] (총 18개, 8개 카테고리, 100점 만점)
# 카테고리 1: 인사 예절 (항목 1-2, 10점) — 첫인사, 끝인사
# 카테고리 2: 경청 및 소통 (항목 3-5, 15점) — 경청/말겹침/말자름, 호응 및 공감, 대기 멘트
# 카테고리 3: 언어 표현 (항목 6-7, 10점) — 정중한 표현, 쿠션어 활용
# 카테고리 4: 니즈 파악 (항목 8-9, 10점) — 문의 파악 및 재확인/복창, 고객정보 확인
# 카테고리 5: 설명력 및 전달력 (항목 10-11, 15점) — 설명의 명확성, 두괄식 답변
# 카테고리 6: 적극성 (항목 12-14, 15점) — 문제 해결 의지, 부연 설명 및 추가 안내, 사후 안내
# 카테고리 7: 업무 정확도 (항목 15-16, 15점) — 정확한 안내, 필수 안내 이행
# 카테고리 8: 개인정보 보호 (항목 17-18, 10점) — 정보 확인 절차, 정보 보호 준수
#
# [공통 감점 규칙]
# - 불친절: 전체 0점 처리
# - 개인정보 유출: 해당 카테고리 0점 + 별도 보고
# - 오안내 미정정: 업무 정확도 카테고리 0점
#
# [각 항목 필드 설명]
# - item_number: 항목 번호 (1-18)
# - category / category_en: 카테고리 (한국어 / 영어)
# - name / name_en: 항목명 (한국어 / 영어)
# - max_score: 만점 배점
# - full_score_criteria: 만점 기준 설명
# - deduction_rules: 감점 규칙 목록 (from_score → to_score 전환 조건)
# - check_items: 체크해야 할 세부 항목 리스트
# - applicable_categories: 이 규칙이 적용되는 상담 유형 리스트
# - notes: 특이사항/예외 조건 (해당하는 경우)
# =============================================================================

"""
Built-in QA evaluation rules dataset.

Contains 18 QA evaluation items organized by 8 categories (100 points total).
"""

from __future__ import annotations


QA_RULES: list[dict] = [
    # =========================================================================
    # 카테고리 1: 인사 예절 (항목 1-2, 10점)
    # 상담 시작과 종료 시의 인사 절차를 평가한다.
    # =========================================================================
    {
        "item_number": 1,
        "category": "인사 예절",
        "category_en": "Greeting etiquette",
        "name": "첫인사",
        "name_en": "Opening greeting",
        "max_score": 5,
        "full_score_criteria": "인사말, 소속, 상담사명 3가지 요소를 모두 포함하여 첫인사를 진행하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "3가지 요소(인사말/소속/상담사명) 중 1가지 누락"},
            {"from_score": 5, "to_score": 0, "condition": "2가지 이상 누락 또는 첫인사 미진행"},
        ],
        "check_items": ["인사말 포함 여부", "소속(회사/부서) 안내 여부", "상담사 이름 안내 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 2,
        "category": "인사 예절",
        "category_en": "Greeting etiquette",
        "name": "끝인사",
        "name_en": "Closing greeting",
        "max_score": 5,
        "full_score_criteria": "추가 문의 확인, 인사말, 상담사명을 모두 포함하여 끝인사를 진행하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "1가지 누락 또는 추가 문의 확인 누락"},
            {"from_score": 5, "to_score": 0, "condition": "끝인사 미진행 또는 2가지 이상 누락"},
        ],
        "check_items": ["추가 문의 확인 여부", "인사말 포함 여부", "상담사 이름 안내 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    # =========================================================================
    # 카테고리 2: 경청 및 소통 (항목 3-5, 15점)
    # 고객의 말을 경청하고 공감하며 소통하는 능력을 평가한다.
    # =========================================================================
    {
        "item_number": 3,
        "category": "경청 및 소통",
        "category_en": "Listening and communication",
        "name": "경청/말겹침/말자름",
        "name_en": "Listening / speech overlap / interruption",
        "max_score": 5,
        "full_score_criteria": "말겹침이나 말자름 없이 고객의 말을 끝까지 경청하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "말겹침 1회 또는 중간 개입 1회"},
            {"from_score": 5, "to_score": 0, "condition": "말자름 1회 이상 또는 말겹침 2회 이상"},
        ],
        "check_items": ["말겹침 발생 여부 및 횟수", "말자름 발생 여부", "고객 발언 중 개입 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
        "notes": "STT 겹침 구간 미표기 시 만점 처리",
    },
    {
        "item_number": 4,
        "category": "경청 및 소통",
        "category_en": "Listening and communication",
        "name": "호응 및 공감",
        "name_en": "Responsiveness and empathy",
        "max_score": 5,
        "full_score_criteria": "다양한 호응 표현이나 공감 표현을 1회 이상 사용하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "단순 '네' 위주의 호응만 사용"},
            {"from_score": 5, "to_score": 0, "condition": "호응/공감 표현 없음 또는 부적절한 반응"},
        ],
        "check_items": ["다양한 호응 표현 사용 여부", "공감 표현 사용 여부", "고객 감정 인정 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 5,
        "category": "경청 및 소통",
        "category_en": "Listening and communication",
        "name": "대기 멘트",
        "name_en": "Hold announcement",
        "max_score": 5,
        "full_score_criteria": "대기 전 양해 멘트와 대기 후 감사 멘트를 모두 진행하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "대기 전/후 멘트 중 1가지 누락"},
            {"from_score": 5, "to_score": 0, "condition": "양해 없이 대기 발생"},
        ],
        "check_items": ["대기 전 양해 멘트 여부", "대기 후 감사/사과 멘트 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
        "notes": "묵음/대기 구간 미식별 시 멘트 유무로만 판단",
    },
    # =========================================================================
    # 카테고리 3: 언어 표현 (항목 6-7, 10점)
    # 상담 중 사용하는 언어의 정중함과 적절성을 평가한다.
    # =========================================================================
    {
        "item_number": 6,
        "category": "언어 표현",
        "category_en": "Language expression",
        "name": "정중한 표현",
        "name_en": "Polite expression",
        "max_score": 5,
        "full_score_criteria": "부적절한 표현(반말, 비속어, 고압적 표현 등) 없이 정중하게 응대하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "부적절한 표현 1~2회 사용"},
            {"from_score": 5, "to_score": 0, "condition": "부적절한 표현 다수 사용 또는 불친절한 태도"},
        ],
        "check_items": ["반말/비속어 사용 여부", "고압적/무시하는 표현 여부", "전체적 어조의 정중함"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 7,
        "category": "언어 표현",
        "category_en": "Language expression",
        "name": "쿠션어 활용",
        "name_en": "Cushion language usage",
        "max_score": 5,
        "full_score_criteria": "거절/불가/안내 상황에서 쿠션어를 적절히 활용하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "쿠션어 사용이 형식적이거나 일부 상황에서 누락"},
            {"from_score": 5, "to_score": 0, "condition": "통보식 안내(쿠션어 미사용)"},
        ],
        "check_items": ["거절/불가 상황에서 쿠션어 사용 여부", "안내 시 완곡한 표현 사용 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
        "notes": "거절/불가 상황이 없으면 만점 처리",
    },
    # =========================================================================
    # 카테고리 4: 니즈 파악 (항목 8-9, 10점)
    # 고객의 문의 내용을 정확히 파악하고 확인하는 능력을 평가한다.
    # =========================================================================
    {
        "item_number": 8,
        "category": "니즈 파악",
        "category_en": "Needs identification",
        "name": "문의 파악 및 재확인/복창",
        "name_en": "Inquiry identification and confirmation",
        "max_score": 5,
        "full_score_criteria": "고객 문의를 정확히 파악하고 핵심 내용을 재확인(복창)하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "문의 파악은 되었으나 재확인 누락 또는 1회 재질의 필요"},
            {"from_score": 5, "to_score": 0, "condition": "동문서답 또는 반복적 재질의"},
        ],
        "check_items": ["고객 문의 핵심 파악 여부", "재확인/복창 수행 여부", "불필요한 재질의 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 9,
        "category": "니즈 파악",
        "category_en": "Needs identification",
        "name": "고객정보 확인",
        "name_en": "Customer information verification",
        "max_score": 5,
        "full_score_criteria": "양해 표현과 함께 필요한 고객정보를 확인하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "일부 정보만 확인 또는 양해 표현 없이 확인"},
            {"from_score": 5, "to_score": 0, "condition": "고객정보 확인 누락"},
        ],
        "check_items": ["양해 표현 후 정보 확인 여부", "필요 정보 확인 완료 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
        "notes": "고객이 선제적으로 정보를 제공한 경우 복창 확인하면 만점 처리",
    },
    # =========================================================================
    # 카테고리 5: 설명력 및 전달력 (항목 10-11, 15점)
    # 고객 눈높이에 맞는 명확한 설명과 두괄식 전달 능력을 평가한다.
    # =========================================================================
    {
        "item_number": 10,
        "category": "설명력 및 전달력",
        "category_en": "Explanation and delivery",
        "name": "설명의 명확성",
        "name_en": "Clarity of explanation",
        "max_score": 10,
        "full_score_criteria": "고객 눈높이에 맞춰 쉽고 명확하게 설명하였음",
        "deduction_rules": [
            {"from_score": 10, "to_score": 7, "condition": "부분적으로 장황하거나 일부 불명확"},
            {"from_score": 10, "to_score": 5, "condition": "내부 용어 사용, 나열식 설명, 또는 고객 되물음 유발"},
            {"from_score": 10, "to_score": 0, "condition": "설명 불가 수준 또는 고객이 이해하지 못함"},
        ],
        "check_items": [
            "고객 눈높이에 맞춘 설명 여부",
            "내부 전문용어 사용 여부",
            "나열식/장황한 설명 여부",
            "고객 되물음 발생 여부",
        ],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 11,
        "category": "설명력 및 전달력",
        "category_en": "Explanation and delivery",
        "name": "두괄식 답변",
        "name_en": "Conclusion-first response",
        "max_score": 5,
        "full_score_criteria": "결론을 먼저 제시한 후 부연 설명을 하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "장황하지만 핵심은 전달됨"},
            {"from_score": 5, "to_score": 0, "condition": "두서없이 장황하여 핵심 파악 곤란"},
        ],
        "check_items": ["결론 우선 전달 여부", "부연 설명의 간결성", "핵심 메시지 전달 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    # =========================================================================
    # 카테고리 6: 적극성 (항목 12-14, 15점)
    # 문제 해결에 대한 적극적 태도와 선제적 안내를 평가한다.
    # =========================================================================
    {
        "item_number": 12,
        "category": "적극성",
        "category_en": "Proactiveness",
        "name": "문제 해결 의지",
        "name_en": "Problem-solving willingness",
        "max_score": 5,
        "full_score_criteria": "적극적으로 대안을 제시하고 해결 의지를 보여주었음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "기본 안내만 진행하고 대안 미제시"},
            {"from_score": 5, "to_score": 0, "condition": "단순 반복 안내 또는 해결 회피"},
        ],
        "check_items": ["적극적 대안 제시 여부", "해결 의지 표현 여부", "고객 문제에 대한 책임감 표현"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 13,
        "category": "적극성",
        "category_en": "Proactiveness",
        "name": "부연 설명 및 추가 안내",
        "name_en": "Supplementary explanation and additional guidance",
        "max_score": 5,
        "full_score_criteria": "선제적으로 관련 정보를 추가 안내하여 원스톱 처리하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "부연 설명이 부족하여 추가 문의 가능성 있음"},
            {"from_score": 5, "to_score": 0, "condition": "단답형 응대로 고객의 재문의를 유발"},
        ],
        "check_items": ["선제적 추가 안내 여부", "관련 정보 원스톱 제공 여부", "고객 재문의 유발 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 14,
        "category": "적극성",
        "category_en": "Proactiveness",
        "name": "사후 안내",
        "name_en": "Follow-up guidance",
        "max_score": 5,
        "full_score_criteria": "후속 절차, 소요 시간, 연락 수단을 명확히 안내하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "사후 안내가 구체성이 부족함"},
            {"from_score": 5, "to_score": 0, "condition": "사후 안내 누락"},
        ],
        "check_items": ["후속 절차 안내 여부", "예상 소요 시간 안내 여부", "연락 수단/방법 안내 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
        "notes": "즉시 해결되어 사후 안내가 불필요한 경우 만점 처리",
    },
    # =========================================================================
    # 카테고리 7: 업무 정확도 (항목 15-16, 15점)
    # 안내 정보의 정확성과 필수 안내사항 이행을 평가한다.
    # =========================================================================
    {
        "item_number": 15,
        "category": "업무 정확도",
        "category_en": "Work accuracy",
        "name": "정확한 안내",
        "name_en": "Accurate guidance",
        "max_score": 10,
        "full_score_criteria": "오안내 없이 정확한 정보를 안내하였음",
        "deduction_rules": [
            {"from_score": 10, "to_score": 5, "condition": "미미한 오류이거나 즉시 정정한 경우"},
            {"from_score": 10, "to_score": 0, "condition": "오안내가 있으며 정정이 필요한 경우"},
        ],
        "check_items": ["안내 정보의 정확성", "오안내 발생 여부", "오안내 즉시 정정 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 16,
        "category": "업무 정확도",
        "category_en": "Work accuracy",
        "name": "필수 안내 이행",
        "name_en": "Mandatory guidance compliance",
        "max_score": 5,
        "full_score_criteria": "업무별 필수 안내사항을 모두 누락 없이 이행하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 3, "condition": "필수 안내사항 일부 누락"},
            {"from_score": 5, "to_score": 0, "condition": "필수 안내 미진행 또는 다수 누락"},
        ],
        "check_items": ["필수 안내 항목 전달 여부", "안내 내용의 완전성"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    # =========================================================================
    # 카테고리 8: 개인정보 보호 (항목 17-18, 10점)
    # 고객 개인정보의 적절한 확인 절차와 보호 준수를 평가한다.
    # =========================================================================
    {
        "item_number": 17,
        "category": "개인정보 보호",
        "category_en": "Privacy protection",
        "name": "정보 확인 절차",
        "name_en": "Information verification procedure",
        "max_score": 5,
        "full_score_criteria": "개인정보 확인 가이드라인에 따라 절차를 이행하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 0, "condition": "확인 절차 누락 또는 정보 선언급(확인 전 고객정보 먼저 말함)"},
        ],
        "check_items": ["가이드라인에 따른 확인 절차 이행 여부", "정보 선언급 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
    {
        "item_number": 18,
        "category": "개인정보 보호",
        "category_en": "Privacy protection",
        "name": "정보 보호 준수",
        "name_en": "Privacy compliance",
        "max_score": 5,
        "full_score_criteria": "개인정보 보호 가이드라인을 준수하였음",
        "deduction_rules": [
            {"from_score": 5, "to_score": 0, "condition": "제3자에게 개인정보 안내 또는 정보 유출 발생"},
        ],
        "check_items": ["제3자 정보 안내 여부", "개인정보 유출 여부", "정보 보호 가이드 준수 여부"],
        "applicable_categories": ["insurance", "it_support", "e_commerce", "simple_inquiry", "general"],
    },
]


# ---------------------------------------------------------------------------
# 규칙 검색 유틸리티 함수
# ---------------------------------------------------------------------------
# 카테고리명 또는 항목 번호로 QA 규칙을 조회하는 헬퍼 함수들.
# retrieval 노드와 평가 에이전트들이 필요한 규칙을 찾을 때 사용한다.


def get_rules_by_category(category: str) -> list[dict]:
    """Filter QA rules by category name (Korean or English)."""
    # 카테고리명으로 규칙을 필터링한다.
    # "general"이면 전체 규칙을 반환하고, 그 외에는 한국어/영어 카테고리명,
    # 또는 적용 가능 상담 유형(applicable_categories) 중 하나와 일치하는 규칙만 반환
    if category.lower() == "general":
        return QA_RULES
    results = []
    for rule in QA_RULES:
        if (
            category in rule["category"]
            or category.lower() in rule["category_en"].lower()
            or category.lower() in [c.lower() for c in rule["applicable_categories"]]
        ):
            results.append(rule)
    return results


def get_rule_by_item_number(item_number: int) -> dict | None:
    """Get a specific QA rule by its item number."""
    # 항목 번호(1-18)로 특정 규칙을 조회한다.
    # 해당 번호의 규칙이 없으면 None 반환
    for rule in QA_RULES:
        if rule["item_number"] == item_number:
            return rule
    return None
