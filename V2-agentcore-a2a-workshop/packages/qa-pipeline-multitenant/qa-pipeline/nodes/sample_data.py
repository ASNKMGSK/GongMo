# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# 샘플/목 데이터 모듈
# =============================================================================
# 이 모듈은 QA 평가 파이프라인에서 아직 연결되지 않은 외부 데이터 소스를
# 대체하는 목(mock) 데이터를 제공한다.
#
# [핵심 역할]
# 3가지 외부 데이터 소스의 목 구현:
# 1. 고객 DB (Customer DB) — scope 에이전트가 업무 범위 확인 시 사용
#    - lookup_customer(): 이름/생년월일/전화번호로 고객 정보 조회
#    - 계약 정보, 허가된 발신자, 공개 금지 항목 포함
#
# 2. 벡터 DB / RAG 검색 (Vector DB) — retrieval 에이전트가 유사 사례 검색 시 사용
#    - search_similar_cases(): 키워드 매칭 기반 유사 과거 평가 사례 검색
#    - 우수(S/A), 보통(B/C), 미흡(D/F) 사례가 균형있게 포함
#
# 3. QA 규칙 DB 확장 검색 — retrieval 에이전트가 보충 규칙 검색 시 사용
#    - search_rules_by_context(): 상담 유형+키워드 기반 보충 규칙 검색
#    - 보험 프로세스, 감점 기준, 컴플라이언스, IT 프로세스 등 15개 보충 규칙
#
# [향후 계획]
# 실제 외부 DB/벡터 DB 연결 시 이 모듈의 함수 시그니처는 유지하되
# 내부 구현을 실제 API 호출로 교체할 예정
# =============================================================================

"""
Sample / mock data module for the QA evaluation pipeline.

Provides mock data for three external data sources that are not yet connected:

1. **Customer DB** — ``lookup_customer()`` for the Scope Agent.
2. **Vector DB / RAG Search** — ``search_similar_cases()`` for the Retrieval Agent.
3. **QA Rule DB Enhanced Search** — ``search_rules_by_context()`` for the Retrieval Agent.

All functions perform simple keyword matching against in-memory data structures;
no external dependencies are required.
"""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)

# ============================================================================
# 1. Customer DB (for Scope Agent)
# ============================================================================
# 고객 DB 목 데이터: scope 에이전트가 업무 범위 확인 시 사용.
# 5명의 샘플 고객 정보를 포함하며, 각 고객에 대해 다음을 정의:
# - 기본 정보: 이름, 생년월일, 전화번호
# - 계약 정보: 보험 상품, 상태, 만기일, 환급금 등
# - authorized_callers: 대리 상담 시 허용된 발신자 목록 (본인, 배우자, 자녀 등)
# - prohibited_disclosure: 공개가 금지된 정보 항목 (주소, 주민번호, 계좌번호 등)

_CUSTOMERS: list[dict[str, Any]] = [
    {
        "customer_id": "C-2024-001",
        "name": "홍길동",
        "dob": "1985-03-15",
        "phone": "010-1234-5678",
        "contracts": [
            {
                "contract_id": "L-2024-001234",
                "product_name": "실손의료보험",
                "product_type": "insurance",
                "status": "active",
                "start_date": "2023-01-15",
                "maturity_date": "2033-01-15",
                "maturity_refund": 5_000_000,
                "current_surrender_value": 3_200_000,
            }
        ],
        "authorized_callers": [{"name": "홍길동", "relationship": "본인"}],
        "prohibited_disclosure": ["주소", "주민등록번호", "계좌번호"],
    },
    {
        "customer_id": "C-2024-002",
        "name": "김철수",
        "dob": "1990-01-01",
        "phone": "010-9876-5432",
        "contracts": [
            {
                "contract_id": "L-2024-001234",
                "product_name": "ABC종합보험",
                "product_type": "insurance",
                "status": "active",
                "start_date": "2022-06-01",
                "maturity_date": "2032-06-01",
                "maturity_refund": 8_000_000,
                "current_surrender_value": 4_500_000,
            }
        ],
        "authorized_callers": [{"name": "김철수", "relationship": "본인"}],
        "prohibited_disclosure": ["주소", "주민등록번호", "계좌번호"],
    },
    {
        "customer_id": "C-2024-003",
        "name": "이영희",
        "dob": "1978-11-22",
        "phone": "010-5555-6789",
        "contracts": [
            {
                "contract_id": "L-2024-003456",
                "product_name": "종합건강보험",
                "product_type": "insurance",
                "status": "active",
                "start_date": "2021-03-10",
                "maturity_date": "2031-03-10",
                "maturity_refund": 10_000_000,
                "current_surrender_value": 6_100_000,
            },
            {
                "contract_id": "L-2024-003457",
                "product_name": "운전자보험",
                "product_type": "insurance",
                "status": "expired",
                "start_date": "2019-05-01",
                "maturity_date": "2024-05-01",
                "maturity_refund": 0,
                "current_surrender_value": 0,
            },
        ],
        "authorized_callers": [
            {"name": "이영희", "relationship": "본인"},
            {"name": "박민수", "relationship": "배우자"},
        ],
        "prohibited_disclosure": ["주소", "주민등록번호", "계좌번호"],
    },
    {
        "customer_id": "C-2024-004",
        "name": "박민수",
        "dob": "1975-07-08",
        "phone": "010-3333-4444",
        "contracts": [
            {
                "contract_id": "L-2024-004567",
                "product_name": "정기보험",
                "product_type": "insurance",
                "status": "active",
                "start_date": "2020-09-01",
                "maturity_date": "2040-09-01",
                "maturity_refund": 0,
                "current_surrender_value": 1_800_000,
            }
        ],
        "authorized_callers": [
            {"name": "박민수", "relationship": "본인"},
            {"name": "이영희", "relationship": "배우자"},
            {"name": "박서연", "relationship": "자녀"},
        ],
        "prohibited_disclosure": ["주소", "주민등록번호", "계좌번호", "연락처"],
    },
    {
        "customer_id": "C-2024-005",
        "name": "최수진",
        "dob": "1995-12-30",
        "phone": "010-7777-8888",
        "contracts": [
            {
                "contract_id": "L-2024-005678",
                "product_name": "저축보험",
                "product_type": "insurance",
                "status": "active",
                "start_date": "2024-01-01",
                "maturity_date": "2034-01-01",
                "maturity_refund": 15_000_000,
                "current_surrender_value": 500_000,
            }
        ],
        "authorized_callers": [{"name": "최수진", "relationship": "본인"}],
        "prohibited_disclosure": ["주소", "주민등록번호", "계좌번호"],
    },
]


def _normalize_dob(raw: str | None) -> str | None:
    """Normalise DOB strings to ``YYYY-MM-DD``.

    Handles 6-digit (YYMMDD) and 8-digit (YYYYMMDD) compact forms as well as
    the canonical dash-separated format.
    """
    # 생년월일 문자열을 YYYY-MM-DD 형식으로 정규화한다.
    # 다양한 입력 형식을 처리:
    #   - 6자리 (YYMMDD): 50 이상이면 19xx, 미만이면 20xx로 간주
    #   - 8자리 (YYYYMMDD): 대시 추가하여 YYYY-MM-DD로 변환
    #   - 이미 YYYY-MM-DD 형식이면 그대로 반환
    if raw is None:
        return None
    cleaned = raw.replace("-", "").replace(".", "").replace("/", "").strip()
    if len(cleaned) == 6:
        yy = int(cleaned[:2])
        prefix = "19" if yy >= 50 else "20"
        cleaned = prefix + cleaned
    if len(cleaned) == 8:
        return f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:8]}"
    return raw.strip()


def lookup_customer(name: str, dob: str | None = None, phone: str | None = None) -> dict[str, Any] | None:
    """Look up a customer by *name* and optional *dob* / *phone*.

    Matching logic (simple keyword match for mock purposes):
    - Name must match exactly (after stripping whitespace).
    - If *dob* is given it is normalised and compared with the stored DOB.
    - If *phone* is given it is compared against the stored phone.
    - A match on name alone is sufficient, but DOB/phone mismatches cause
      the candidate to be rejected.

    Returns the first matching customer record or ``None``.
    """
    # 고객 조회 로직:
    # 1) 이름이 정확히 일치해야 함 (필수)
    # 2) 생년월일이 주어지면 정규화 후 비교 (불일치 시 후보 제외)
    # 3) 전화번호가 주어지면 비교 (불일치 시 후보 제외)
    # 이름만으로도 조회 가능하지만, DOB/전화번호가 주어진 경우 불일치하면 탈락
    norm_dob = _normalize_dob(dob)
    norm_phone = phone.strip() if phone else None

    logger.info("lookup_customer: name=%s dob=%s phone=%s", name, norm_dob, norm_phone)

    for customer in _CUSTOMERS:
        if customer["name"] != name.strip():
            continue

        # 이름 일치 — 추가 필터 확인
        if norm_dob is not None and _normalize_dob(customer["dob"]) != norm_dob:
            logger.debug("lookup_customer: DOB mismatch for '%s'", name)
            continue
        if norm_phone is not None and customer["phone"] != norm_phone:
            logger.debug("lookup_customer: phone mismatch for '%s'", name)
            continue

        logger.info("lookup_customer: found customer_id=%s", customer["customer_id"])
        return customer

    logger.info("lookup_customer: no match found for name='%s'", name)
    return None


# ============================================================================
# 2. Vector DB / RAG Search (for Retrieval Agent)
# ============================================================================
# 벡터 DB 유사 사례 목 데이터: retrieval 에이전트가 과거 평가 사례를 참조할 때 사용.
# 10개의 샘플 사례를 포함하며, 등급별로 균형 있게 구성:
# - 우수 사례 (S/A): 3건 — 모범 응대의 기준점 제공
# - 보통 사례 (B/C): 3건 — 부분적 미흡 사항의 참조점 제공
# - 미흡 사례 (D/F): 4건 — 중대한 위반 패턴 학습용
# 각 사례는 유사도 점수, 상담 유형, 요약, 평가 결과, 핵심 발견사항을 포함

_SIMILAR_CASES: list[dict[str, Any]] = [
    # --- 우수 사례 (S/A 등급) — 모범적인 상담 응대 ---
    {
        "case_id": "CASE-2024-0001",
        "similarity_score": 0.95,
        "consultation_type": "insurance",
        "summary": "보험금 청구 관련 상담 - 우수 사례. 상담사가 본인확인, 필수 안내, 공감 표현 모두 완벽 수행.",
        "evaluation_result": {"total_score": 95, "grade": "S"},
        "key_findings": ["인사말 정확", "본인확인 완벽", "설명력 우수", "공감 표현 탁월"],
        "source": "vector_db",
    },
    {
        "case_id": "CASE-2024-0002",
        "similarity_score": 0.91,
        "consultation_type": "insurance",
        "summary": "실손보험 해약 상담 - 우수 사례. 해약 절차 안내 정확, 불이익 사항 명확히 고지, 유지 대안 제시.",
        "evaluation_result": {"total_score": 90, "grade": "A"},
        "key_findings": ["해약 절차 안내 정확", "불이익 사항 고지 완료", "대안 제시 우수"],
        "source": "vector_db",
    },
    {
        "case_id": "CASE-2024-0003",
        "similarity_score": 0.89,
        "consultation_type": "IT",
        "summary": "IT 시스템 장애 접수 상담 - 우수 사례. 장애 내용 정확 파악, 에스컬레이션 절차 준수, 고객 안내 명확.",
        "evaluation_result": {"total_score": 92, "grade": "S"},
        "key_findings": ["장애 접수 프로세스 완벽", "에스컬레이션 적시 수행", "고객 안심시킴"],
        "source": "vector_db",
    },
    # --- 보통 사례 (B/C 등급) — 부분적으로 미흡한 상담 ---
    {
        "case_id": "CASE-2024-0004",
        "similarity_score": 0.85,
        "consultation_type": "insurance",
        "summary": "만기 환급금 문의 상담 - 보통 사례. 정보 안내는 정확했으나 본인확인 항목 일부 누락.",
        "evaluation_result": {"total_score": 72, "grade": "B"},
        "key_findings": ["본인확인 일부 누락", "환급금 안내 정확", "끝인사 미흡"],
        "source": "vector_db",
    },
    {
        "case_id": "CASE-2024-0005",
        "similarity_score": 0.82,
        "consultation_type": "general",
        "summary": "일반 문의 상담 - 보통 사례. 고객 문의 대응은 적절했으나 공감 표현 부족, 추가 문의 확인 누락.",
        "evaluation_result": {"total_score": 65, "grade": "C"},
        "key_findings": ["공감 표현 부족", "추가 문의 확인 누락", "기본 응대 적절"],
        "source": "vector_db",
    },
    {
        "case_id": "CASE-2024-0006",
        "similarity_score": 0.80,
        "consultation_type": "insurance",
        "summary": "보험 보장내용 문의 - 보통 사례. 보장내용 설명은 했으나 법적 고지사항 누락, 약관 안내 미흡.",
        "evaluation_result": {"total_score": 68, "grade": "C"},
        "key_findings": ["보장내용 설명 제공", "법적 고지 누락", "약관 안내 미수행"],
        "source": "vector_db",
    },
    # --- 미흡 사례 (D/F 등급) — 중대한 위반이 포함된 상담 ---
    {
        "case_id": "CASE-2024-0007",
        "similarity_score": 0.78,
        "consultation_type": "insurance",
        "summary": "보험금 청구 상담 - 미흡 사례. 첫인사 누락, 본인확인 미수행, 고객 말 끊기 다수 발생.",
        "evaluation_result": {"total_score": 42, "grade": "D"},
        "key_findings": ["첫인사 누락", "본인확인 미수행", "고객 말 반복 끊기", "공감 표현 전무"],
        "source": "vector_db",
    },
    {
        "case_id": "CASE-2024-0008",
        "similarity_score": 0.75,
        "consultation_type": "insurance",
        "summary": "해약 상담 - 불량 사례. 해약 불이익 미고지, 잘못된 환급금 안내, 녹취 안내 미수행.",
        "evaluation_result": {"total_score": 30, "grade": "F"},
        "key_findings": ["해약 불이익 미고지", "환급금 오안내", "녹취 안내 누락", "프로세스 위반"],
        "source": "vector_db",
    },
    {
        "case_id": "CASE-2024-0009",
        "similarity_score": 0.73,
        "consultation_type": "general",
        "summary": "개인정보 유출 사례 - 불량. 제3자에게 고객 주소 및 연락처 안내, 업무범위 초과 정보 제공.",
        "evaluation_result": {"total_score": 20, "grade": "F"},
        "key_findings": ["개인정보 유출", "제3자에게 주소 안내", "업무범위 초과"],
        "source": "vector_db",
    },
    {
        "case_id": "CASE-2024-0010",
        "similarity_score": 0.70,
        "consultation_type": "IT",
        "summary": "IT 지원 상담 - 불량 사례. 업무 회피로 고객 문의 거부, 반말 사용, 끝인사 미수행.",
        "evaluation_result": {"total_score": 25, "grade": "F"},
        "key_findings": ["업무 회피", "반말 사용", "끝인사 누락", "고객 비하 발언"],
        "source": "vector_db",
    },
]


def search_similar_cases(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Search for similar past consultation evaluation cases.

    Performs simple keyword matching against stored case summaries and key
    findings. Each result includes a ``similarity_score`` (pre-set mock value),
    a short summary, and the historical evaluation outcome.

    Parameters
    ----------
    query:
        Free-text search query (Korean / English).
    top_k:
        Maximum number of results to return.

    Returns
    -------
    list[dict]
        Matched cases sorted by relevance (keyword overlap), descending.
    """
    # 유사 과거 사례를 키워드 매칭으로 검색한다.
    # 실제 벡터 DB에서는 임베딩 기반 유사도를 사용하지만,
    # 목 구현에서는 간단한 토큰 겹침 비율로 유사도를 계산한다.
    #
    # 점수 계산 공식: (토큰 겹침 비율 * 0.6) + (사전 설정 유사도 * 0.4)
    # → 키워드 매칭과 사전 유사도를 결합하여 합리적인 순위 생성
    logger.info("search_similar_cases: query='%s', top_k=%d", query[:80], top_k)

    query_lower = query.lower()
    query_tokens = set(query_lower.split())

    scored: list[tuple[float, dict[str, Any]]] = []
    for case in _SIMILAR_CASES:
        # 검색 대상 텍스트: 요약 + 핵심 발견사항 + 상담 유형을 하나의 문자열로 결합
        blob = (case["summary"] + " " + " ".join(case["key_findings"]) + " " + case["consultation_type"]).lower()

        # 토큰 겹침 점수: 쿼리 토큰 중 blob에 존재하는 비율 (0.0 ~ 1.0)
        if not query_tokens:
            overlap = 0.0
        else:
            hits = sum(1 for t in query_tokens if t in blob)
            overlap = hits / len(query_tokens)

        # 최종 점수: 키워드 매칭(60%) + 사전 유사도(40%) 가중 평균
        combined = overlap * 0.6 + case["similarity_score"] * 0.4
        scored.append((combined, case))

    # 점수 내림차순 정렬 후 상위 top_k개 반환
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [item for _, item in scored[:top_k]]

    logger.info("search_similar_cases: returning %d results", len(results))
    return results


# ============================================================================
# 3. QA Rule DB Enhanced Search (for Retrieval Agent)
# ============================================================================
# QA 규칙 보충 데이터: qa_rules.py의 21개 기본 규칙을 보완하는 상세 체크리스트.
# 15개의 보충 규칙을 포함하며, 다음 카테고리로 구성:
# - 보험 프로세스 (SUP-001~003, SUP-015): 보험금 청구, 해약, 보장내용, 만기/갱신 체크리스트
# - 감점 기준 (SUP-004~006, SUP-014): 치명적 오류(0점), 부분 감점, 가점, 효율성
# - 컴플라이언스 (SUP-007~009, SUP-013): 보험업법, 개인정보보호법, 제3자 상담, 취약계층
# - IT 프로세스 (SUP-010): IT 장애 접수 절차
# - 상담 기본 (SUP-011~012): 통화 품질, 에스컬레이션
#
# 각 규칙은 checklist(체크리스트 항목)와 keywords(검색 키워드),
# penalty_if_missed(미준수 시 감점 규칙)를 포함한다.

_SUPPLEMENTARY_RULES: list[dict[str, Any]] = [
    # -- 보험 상담 전용 프로세스 체크리스트 --
    {
        "rule_id": "SUP-001",
        "category": "보험 프로세스",
        "consultation_types": ["insurance"],
        "title": "보험금 청구 절차 체크리스트",
        "description": "보험금 청구 상담 시 필수 확인 및 안내 사항.",
        "checklist": [
            "청구 사유 및 발생일 확인",
            "필요 서류 목록 안내 (진단서, 영수증 등)",
            "청구서 작성 방법 안내",
            "처리 예상 소요일 안내 (보통 3~7영업일)",
            "지급 계좌 확인",
            "추가 청구 가능 여부 안내",
        ],
        "keywords": ["보험금", "청구", "지급", "서류", "진단서"],
        "penalty_if_missed": "필수 안내사항 누락 시 항목 13번 감점 (3→1 또는 3→0)",
    },
    {
        "rule_id": "SUP-002",
        "category": "보험 프로세스",
        "consultation_types": ["insurance"],
        "title": "해약 절차 체크리스트",
        "description": "보험 해약 상담 시 반드시 수행해야 할 절차와 고지 사항.",
        "checklist": [
            "해약 의사 재확인",
            "해약 환급금 안내 (현재 해약환급금 정확 금액)",
            "해약 시 불이익 사항 고지 (보장 소멸, 재가입 시 보험료 인상 등)",
            "대안 제시 (감액 완납, 납입 유예 등)",
            "해약 처리 후 환급금 입금 일정 안내",
            "해약 철회 가능 기간 안내",
        ],
        "keywords": ["해약", "해지", "환급금", "해약환급금", "취소"],
        "penalty_if_missed": "해약 불이익 미고지 시 항목 14번(법적 고지) 감점 + 항목 17번(프로세스) 감점",
    },
    {
        "rule_id": "SUP-003",
        "category": "보험 프로세스",
        "consultation_types": ["insurance"],
        "title": "보장내용 안내 체크리스트",
        "description": "보험 보장내용 문의 시 안내해야 할 핵심 항목.",
        "checklist": [
            "계약 기본 정보 확인 (상품명, 계약일, 만기일)",
            "주요 보장 항목 및 한도 안내",
            "면책 사항 안내",
            "보장 제외 사항 안내",
            "특약 보장 범위 안내",
            "갱신 조건 안내 (갱신형의 경우)",
        ],
        "keywords": ["보장", "보장내용", "보험금", "보험료", "특약", "면책"],
        "penalty_if_missed": "보장 관련 오안내 시 항목 18번(정확성) 감점",
    },
    # -- 감점/채점 상세 규칙 --
    {
        "rule_id": "SUP-004",
        "category": "감점 기준",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "즉시 0점 처리 항목 (치명적 오류)",
        "description": "해당 사항 발생 시 관련 평가항목 즉시 0점 처리.",
        "checklist": [
            "개인정보 유출 (주소, 연락처, 계좌번호, 주민번호 → 항목 관련 0점)",
            "허위/거짓 정보 안내 (항목 18번 0점)",
            "본인확인 완전 미수행 (항목 2번 0점)",
            "고객 비하/모욕 발언 (항목 9번, 10번, 11번 모두 0점)",
            "업무 회피 (정당한 사유 없이 처리 거부 → 관련 항목 0점)",
        ],
        "keywords": ["0점", "치명적", "개인정보", "유출", "허위", "비하", "모욕", "업무회피"],
        "penalty_if_missed": "해당 치명적 오류 발견 시 자동 0점 처리",
    },
    {
        "rule_id": "SUP-005",
        "category": "감점 기준",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "부분 감점 기준 (경미한 오류)",
        "description": "완전한 위반은 아니나 미흡한 경우의 감점 기준.",
        "checklist": [
            "인사말 불완전 (소속/이름 중 하나 누락) → 항목 1번 2→0",
            "본인확인 항목 일부 누락 → 항목 2번 3→1",
            "공감 표현 형식적 → 항목 10번 3→1",
            "필수 안내 일부 누락 → 항목 13번 3→1",
            "법적 고지 일부 부정확 → 항목 14번 3→1",
            "사소한 정보 오류 1건 → 항목 18번 3→1",
        ],
        "keywords": ["감점", "부분", "누락", "미흡", "불완전"],
        "penalty_if_missed": "해당 감점 기준 미적용 시 평가 일관성 저하",
    },
    {
        "rule_id": "SUP-006",
        "category": "감점 기준",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "가점/보너스 요소",
        "description": "규정 이상의 우수한 응대에 대한 가점 기준 (점수 상한 내).",
        "checklist": [
            "고객 맞춤형 추가 정보 제공",
            "선제적 불편 사항 해결",
            "후속 조치 구체적 안내 (날짜, 담당자, 방법 명시)",
            "고객 감정 변화에 따른 유연한 응대",
        ],
        "keywords": ["가점", "우수", "추가", "선제적", "맞춤"],
        "penalty_if_missed": "가점 요소이므로 미충족 시 기본 점수 유지",
    },
    # -- 컴플라이언스(법규 준수) 요건 --
    {
        "rule_id": "SUP-007",
        "category": "컴플라이언스",
        "consultation_types": ["insurance"],
        "title": "보험업법 준수 사항",
        "description": "보험 상담 시 보험업법에 따른 필수 준수 사항.",
        "checklist": [
            "불완전판매 방지 의무 (보장내용 정확 설명)",
            "적합성 원칙 (고객 니즈에 맞는 상품 안내)",
            "설명의무 이행 (약관의 주요 내용 설명)",
            "통신판매 시 녹취 의무 준수",
            "청약 철회권 안내 (15일 이내)",
        ],
        "keywords": ["보험업법", "불완전판매", "적합성", "설명의무", "청약철회"],
        "penalty_if_missed": "보험업법 위반 시 항목 14번(법적 고지) 0점 + 별도 컴플라이언스 보고",
    },
    {
        "rule_id": "SUP-008",
        "category": "컴플라이언스",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "개인정보보호법 준수 사항",
        "description": "상담 중 개인정보 처리 관련 법적 요구사항.",
        "checklist": [
            "개인정보 수집 동의 확인",
            "제3자 제공 동의 확인 (필요 시)",
            "최소 수집 원칙 준수",
            "수집 목적 명시",
            "보유 기간 안내",
            "열람/정정/삭제 권리 안내",
        ],
        "keywords": ["개인정보", "보호법", "동의", "수집", "제3자", "열람"],
        "penalty_if_missed": "개인정보보호법 위반 시 항목 15번 0점 + 컴플라이언스 이슈 보고",
    },
    {
        "rule_id": "SUP-009",
        "category": "컴플라이언스",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "제3자 상담 시 정보 제공 범위",
        "description": "본인이 아닌 제3자가 상담 요청 시 정보 제공 기준.",
        "checklist": [
            "제3자 관계 확인 (가족, 대리인 등)",
            "위임장 또는 동의서 확인 (필요 시)",
            "일반 정보만 제공 가능 (상품 개요, 절차 안내)",
            "특정 계약 정보 제공 불가 (본인 인증 필요)",
            "금융거래 관련 정보 제공 절대 불가",
        ],
        "keywords": ["제3자", "대리", "위임", "가족", "본인아닌", "타인"],
        "penalty_if_missed": "제3자에게 계약 세부정보 제공 시 정보유출로 관련 항목 0점",
    },
    # -- IT / 일반 상담 전용 규칙 --
    {
        "rule_id": "SUP-010",
        "category": "IT 프로세스",
        "consultation_types": ["IT"],
        "title": "IT 장애 접수 프로세스",
        "description": "IT 장애/오류 접수 상담 시 필수 수행 절차.",
        "checklist": [
            "장애 현상 상세 확인 (에러 메시지, 발생 시점, 빈도)",
            "영향 범위 파악 (특정 사용자 vs 전체)",
            "긴급도 판단 및 분류",
            "장애 접수 번호 발행 및 안내",
            "예상 처리 시간 안내",
            "에스컬레이션 기준 안내",
        ],
        "keywords": ["장애", "오류", "에러", "IT", "시스템", "접수", "에스컬레이션"],
        "penalty_if_missed": "장애 접수 프로세스 미수행 시 항목 17번(프로세스 준수) 감점",
    },
    {
        "rule_id": "SUP-011",
        "category": "상담 기본",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "통화 품질 기본 요건",
        "description": "모든 상담 유형에 공통 적용되는 통화 품질 기본 요건.",
        "checklist": [
            "명확한 발음과 적절한 속도",
            "전문 용어 사용 시 쉬운 설명 병행",
            "불필요한 보류(hold) 최소화",
            "보류 시 양해 구하기 및 예상 시간 안내",
            "통화 중 타 업무 처리 금지",
        ],
        "keywords": ["통화", "품질", "발음", "속도", "보류", "hold"],
        "penalty_if_missed": "통화 품질 저하 시 항목 5번(경청), 항목 9번(존칭) 등에 간접 영향",
    },
    {
        "rule_id": "SUP-012",
        "category": "상담 기본",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "불만 고객 에스컬레이션 기준",
        "description": "고객 불만 수준에 따른 에스컬레이션 기준 및 절차.",
        "checklist": [
            "1차: 상담사 자체 해결 시도 (사과 + 해결방안 제시)",
            "2차: 팀장/수퍼바이저 연결 (고객 요청 또는 해결 불가 시)",
            "3차: 민원 접수 및 전담부서 이관 (반복 불만 또는 법적 이슈)",
            "에스컬레이션 시 이전 상담 내용 인수인계 필수",
            "고객에게 후속 처리 일정 반드시 안내",
        ],
        "keywords": ["불만", "에스컬레이션", "민원", "이관", "팀장", "수퍼바이저"],
        "penalty_if_missed": "에스컬레이션 미수행 시 항목 12번(불만 고객 응대) 감점 + 항목 17번(프로세스) 감점",
    },
    {
        "rule_id": "SUP-013",
        "category": "컴플라이언스",
        "consultation_types": ["insurance"],
        "title": "고령자/취약계층 상담 특별 요건",
        "description": "고령자 또는 취약계층 고객 상담 시 추가 준수 사항.",
        "checklist": [
            "설명 속도 조절 (천천히, 반복 설명)",
            "핵심 내용 2회 이상 확인",
            "서면 자료 추가 발송 안내",
            "가족/법정대리인 동석 또는 연락 권유 (필요 시)",
            "불완전판매 방지 강화 절차 수행",
        ],
        "keywords": ["고령자", "취약계층", "어르신", "장애", "미성년", "법정대리인"],
        "penalty_if_missed": "취약계층 보호 의무 미이행 시 컴플라이언스 위반 보고 대상",
    },
    {
        "rule_id": "SUP-014",
        "category": "감점 기준",
        "consultation_types": ["insurance", "IT", "general"],
        "title": "상담 시간 및 효율성 기준",
        "description": "상담 시간 관리 및 효율적 진행 관련 평가 기준.",
        "checklist": [
            "불필요한 반복 설명 지양",
            "고객 대기 시간 최소화",
            "핵심 내용 우선 안내 후 세부사항 안내",
            "1회 상담 내 해결 노력 (First Call Resolution)",
            "불가피한 콜백 시 구체적 일정 안내",
        ],
        "keywords": ["시간", "효율", "대기", "반복", "FCR", "콜백"],
        "penalty_if_missed": "효율성 저하 시 직접 감점 없으나 전체 품질 평가에 반영",
    },
    {
        "rule_id": "SUP-015",
        "category": "보험 프로세스",
        "consultation_types": ["insurance"],
        "title": "만기/갱신 상담 체크리스트",
        "description": "보험 만기 또는 갱신 관련 문의 시 안내 사항.",
        "checklist": [
            "만기일 확인 및 안내",
            "만기 환급금 안내 (해당 시)",
            "갱신 조건 안내 (갱신보험료, 보장변경 사항)",
            "갱신 거절 사유 안내 (해당 시)",
            "재가입 절차 안내 (만기 종료 시)",
            "타 상품 전환 안내 (필요 시)",
        ],
        "keywords": ["만기", "갱신", "환급금", "만기환급금", "재가입", "전환"],
        "penalty_if_missed": "만기/갱신 정보 오안내 시 항목 18번(정확성) 감점",
    },
]


def search_rules_by_context(consultation_type: str, keywords: list[str]) -> list[dict[str, Any]]:
    """Search supplementary QA rules relevant to the given context.

    Matching logic:
    - Rules whose ``consultation_types`` list includes *consultation_type*
      (case-insensitive) are candidates.
    - Candidates are ranked by keyword overlap: each keyword from *keywords*
      that appears in the rule's ``keywords``, ``title``, or ``description``
      counts as one hit.
    - Rules with zero keyword hits are included only if *keywords* is empty.

    Parameters
    ----------
    consultation_type:
        The type of consultation (e.g. ``"insurance"``, ``"IT"``, ``"general"``).
    keywords:
        Context keywords extracted from the transcript / query.

    Returns
    -------
    list[dict]
        Matching supplementary rules sorted by relevance (keyword overlap),
        descending. Each dict includes the rule definition, plus an added
        ``match_score`` field indicating keyword overlap count.
    """
    # 상담 유형과 키워드를 기반으로 관련 보충 규칙을 검색한다.
    #
    # 검색 로직:
    # 1단계: 상담 유형(consultation_type) 필터링
    #   - 규칙의 consultation_types에 해당 유형이 포함되거나 "general"이면 후보
    # 2단계: 키워드 겹침 점수 계산
    #   - 규칙의 keywords, title, description에서 쿼리 키워드가 몇 개 매칭되는지 카운트
    # 3단계: 점수 0인 규칙은 키워드가 비어있을 때만 포함 (키워드 있으면 관련성 없는 것으로 판단)
    # 결과에 match_score 필드를 추가하여 매칭 품질을 알려줌
    logger.info("search_rules_by_context: type='%s', keywords=%s", consultation_type, keywords)

    ct_lower = consultation_type.lower().strip()
    kw_lower = [k.lower().strip() for k in keywords if k.strip()]

    results: list[tuple[int, dict[str, Any]]] = []
    for rule in _SUPPLEMENTARY_RULES:
        # 상담 유형 필터: 규칙의 적용 가능 유형에 해당 유형이 포함되는지 확인
        applicable = [t.lower() for t in rule["consultation_types"]]
        if ct_lower not in applicable and "general" not in applicable:
            continue

        # 키워드 겹침 점수 계산: 규칙의 키워드/제목/설명에서 쿼리 키워드 매칭 수
        searchable = (" ".join(rule["keywords"]) + " " + rule["title"] + " " + rule["description"]).lower()

        hits = sum(1 for kw in kw_lower if kw in searchable) if kw_lower else 0

        # 키워드가 제공되었는데 매칭이 0이면 관련 없는 규칙으로 판단하여 제외
        if hits == 0 and kw_lower:
            continue

        result = {**rule, "match_score": hits}
        results.append((hits, result))

    # 키워드 매칭 수 내림차순 정렬
    results.sort(key=lambda x: x[0], reverse=True)
    final = [item for _, item in results]

    logger.info("search_rules_by_context: returning %d rules", len(final))
    return final
