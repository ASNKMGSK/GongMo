# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""신한 부서특화 노드 메타데이터 및 sub-agent factory 레지스트리.

xlsx (`부서별_AI_QA_평가표_통합.xlsx`) 의 대분류 단위로 노드가 정의되며,
평가항목은 노드 내부 sub-items 으로 채점된다.

frontend (`lib/pipeline.ts::SHINHAN_DEPT_NODE_IDS`) 와 1:1 정합.

Synthetic item_number 할당 (9XX 대역):
- 901-902: coll_accuracy
- 903-904: coll_debt_compliance
- 905-906: iss_accuracy
- 907-908: iss_terms_compliance
- 909-910: crm_accuracy
- 911-912: crm_tm_compliance
- 913-914: cons_complaint
- 915-916: cons_resolution
- 917-918: cons_protection
- 919-922: comp_unfair_sale_check
"""

from __future__ import annotations

from typing import Callable, TypedDict


class DeptNodeSpec(TypedDict, total=False):
    """부서특화 노드 1개의 메타데이터."""

    node_id: str               # frontend 노드 ID (e.g. "coll_accuracy")
    team_id: str               # 부서 (collection / review / crm / consumer / compliance)
    label_ko: str              # 대분류 라벨 (e.g. "업무 정확도")
    category_key: str          # synthetic CategoryKey (e.g. "shinhan_coll_accuracy")
    max_score: int             # 노드 총 배점
    items: list[dict]          # [{item_number, item_name, max_score, allowed_steps}]
    rubric_focus: str          # LLM 프롬프트의 평가 포커스 한 줄 요약
    # 페르소나 모드 (xlsx 처리방식 정합):
    #   "multi"  — 3-persona ensemble (LLM + Few-shot / RAG / 분류기 등 주관 판정 항목)
    #   "single" — neutral 1회만 (compliance_based / Rule + LLM verify 등 객관 판정)
    mode: str                  # "multi" | "single"


# ===========================================================================
# 10 노드 메타데이터 (xlsx 기준)
# ===========================================================================

DEPT_NODE_REGISTRY: dict[str, DeptNodeSpec] = {
    # ── collection (컬렉션관리부) — xlsx 정합 ──
    "coll_accuracy": {
        "node_id": "coll_accuracy",
        "team_id": "collection",
        "label_ko": "업무 정확도",
        "category_key": "shinhan_coll_accuracy",
        "max_score": 20,
        "mode": "multi",  # 정확한 안내 (★) 가 RAG 기반 주관 판정 → multi
        "items": [
            {"item_number": 901, "item_name": "정확한 안내 ★ (연체경과/원금·수수료/총납부/납부기한/결제계좌)", "max_score": 15, "allowed_steps": [15, 10, 5, 0]},
            {"item_number": 902, "item_name": "필수 안내 이행 (거래정지/블랙리스트/가용한도)", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "연체 정보 정확성 + 연체 구간별 필수 안내 이행",
    },
    "coll_debt_compliance": {
        "node_id": "coll_debt_compliance",
        "team_id": "collection",
        "label_ko": "채권추심 법규 준수",
        "category_key": "shinhan_coll_debt_compliance",
        "max_score": 10,
        "mode": "single",  # 채권추심법 compliance_based + T3 → single
        "items": [
            {"item_number": 903, "item_name": "불공정 채권추심 금지 (위협/공포/수치심)", "max_score": 5, "allowed_steps": [5, 3, 0]},
            {"item_number": 904, "item_name": "정당한 채권추심 절차 고지 (사실/근거/소속/민원방법)", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "채권추심법 제8조의2/제9조 준수",
    },
    # ── review (심사발급부) — xlsx 정합 ──
    "iss_accuracy": {
        "node_id": "iss_accuracy",
        "team_id": "review",
        "label_ko": "업무 정확도",
        "category_key": "shinhan_iss_accuracy",
        "max_score": 20,
        "mode": "multi",
        "items": [
            {"item_number": 905, "item_name": "정확한 안내 ★ (수령지/배송/연회비/이자율/발급조건)", "max_score": 15, "allowed_steps": [15, 10, 5, 0]},
            {"item_number": 906, "item_name": "필수 안내 이행 (연회비/결제일/이용한도/심사절차/배송)", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "심사·발급 정보 정확성 + 카드상품별 필수 안내",
    },
    "iss_terms_compliance": {
        "node_id": "iss_terms_compliance",
        "team_id": "review",
        "label_ko": "약관 및 동의 절차",
        "category_key": "shinhan_iss_terms_compliance",
        "max_score": 10,
        "mode": "single",
        "items": [
            {"item_number": 907, "item_name": "약관 설명 및 동의 절차 (신용조회/개인정보/부가서비스 자동가입)", "max_score": 5, "allowed_steps": [5, 3, 0]},
            {"item_number": 908, "item_name": "가족카드·배우자 기준 동의 확인", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "약관규제법/여신금융감독규정 기반 동의 절차 준수",
    },
    # ── crm (CRM부) — xlsx 정합 ──
    "crm_accuracy": {
        "node_id": "crm_accuracy",
        "team_id": "crm",
        "label_ko": "업무 정확도",
        "category_key": "shinhan_crm_accuracy",
        "max_score": 20,
        "mode": "multi",
        "items": [
            {"item_number": 909, "item_name": "정확한 안내 ★ (혜택/연회비/이자율/수수료/계좌변경 대상)", "max_score": 15, "allowed_steps": [15, 10, 5, 0]},
            {"item_number": 910, "item_name": "필수 안내 이행 (전화목적/녹취고지/청약철회권/개인정보 동의)", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "TM 상품·계좌변경 정확성 + 아웃바운드 필수 스크립트",
    },
    "crm_tm_compliance": {
        "node_id": "crm_tm_compliance",
        "team_id": "crm",
        "label_ko": "TM 준수사항",
        "category_key": "shinhan_crm_tm_compliance",
        "max_score": 10,
        "mode": "single",
        "items": [
            {"item_number": 911, "item_name": "전화 목적·녹취 고지 (도입부 5턴 내)", "max_score": 5, "allowed_steps": [5, 3, 0]},
            {"item_number": 912, "item_name": "청약철회권 안내 및 가입의사 확인", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "방문판매법/여신전문금융업법 TM 준수사항",
    },
    # ── consumer (소비자보호부) ──
    "cons_complaint": {
        "node_id": "cons_complaint",
        "team_id": "consumer",
        "label_ko": "민원 대응",
        "category_key": "shinhan_cons_complaint",
        "max_score": 20,
        "mode": "multi",
        "items": [
            {"item_number": 913, "item_name": "민원 유형 분류 및 원인 파악 ★ (VOC 카테고리 매핑)", "max_score": 15, "allowed_steps": [15, 10, 5, 0]},
            {"item_number": 914, "item_name": "공감·사과 표현 및 감정 관리", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "VOC 카테고리 정확 분류 + 진정성 있는 공감/사과",
    },
    "cons_resolution": {
        "node_id": "cons_resolution",
        "team_id": "consumer",
        "label_ko": "민원 해결 품질",
        "category_key": "shinhan_cons_resolution",
        "max_score": 5,
        "mode": "multi",
        "items": [
            {"item_number": 915, "item_name": "해결책 제시 및 이관·사후 관리", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "구체적 해결책 / 적정 부서 이관 / 사후 연락 안내",
    },
    "cons_protection": {
        "node_id": "cons_protection",
        "team_id": "consumer",
        "label_ko": "소비자보호 준수",
        "category_key": "shinhan_cons_protection",
        "max_score": 5,
        "mode": "single",  # T3 compliance_based
        "items": [
            {"item_number": 916, "item_name": "부당 응대·2차 가해 방지 (책임전가/비난/반론강요)", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "소비자보호기준 기반 2차 가해 패턴 부재 (T3 필수)",
    },
    # ── compliance (준법관리부) ──
    "comp_unfair_sale_check": {
        "node_id": "comp_unfair_sale_check",
        "team_id": "compliance",
        "label_ko": "불완전판매 점검",
        "category_key": "shinhan_comp_unfair_sale_check",
        "max_score": 30,
        "mode": "multi",  # 설명의무 (★15점) RAG 기반 주관 판정
        "items": [
            {"item_number": 919, "item_name": "설명의무 이행 ★ (상품명/연회비/이자율/혜택/위험)", "max_score": 15, "allowed_steps": [15, 10, 5, 0]},
            {"item_number": 920, "item_name": "취약계층 식별 및 보호 조치 (고령/이해곤란)", "max_score": 5, "allowed_steps": [5, 3, 0]},
            {"item_number": 921, "item_name": "부당권유 금지 (허위/과장/단정적/불이익 누락)", "max_score": 5, "allowed_steps": [5, 3, 0]},
            {"item_number": 922, "item_name": "청약철회·이해도 확인 (금소법 제46조)", "max_score": 5, "allowed_steps": [5, 3, 0]},
        ],
        "rubric_focus": "금소법 제19/21/46조 준수 — 설명의무·적합성·청약철회",
    },
}


# 부서 → 노드 ID 리스트 (graph build 시 활용)
DEPT_NODES_BY_TEAM: dict[str, list[str]] = {}
for _node_id, _spec in DEPT_NODE_REGISTRY.items():
    DEPT_NODES_BY_TEAM.setdefault(_spec["team_id"], []).append(_node_id)


def get_dept_nodes_for_tenant(tenant_id: str, team_id: str | None) -> list[str]:
    """tenant_id + team_id 조합으로 활성화할 부서특화 노드 ID 리스트 반환.

    shinhan 외 tenant 또는 team 미지정 시 빈 리스트 (기본 8개 sub-agent 만 동작).
    """
    if tenant_id != "shinhan" or not team_id:
        return []
    return list(DEPT_NODES_BY_TEAM.get(team_id, []))


def get_dept_agent(node_id: str) -> Callable | None:
    """node_id 로 sub-agent callable 반환.

    Lazy import 로 순환 참조 회피.
    """
    if node_id not in DEPT_NODE_REGISTRY:
        return None
    from v2.agents.shinhan_dept._base import make_dept_agent
    return make_dept_agent(node_id)
