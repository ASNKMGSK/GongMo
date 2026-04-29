# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 3 (a) — 점수 집계.

설계서 p10 Layer 3 (a):
    평가항목별 점수를 대분류별로 합산, 전체 총점 계산.

입력: evaluations (Layer 2 Sub Agent 결과) 리스트. 각 원소는 V1 EvaluationResult
호환 dict — `{"status": str, "agent_id": str, "evaluation": {item_number, score, max_score, ...}}`.

출력: category_scores[CategoryBlock-like] + raw_total / max_possible.

V1 `nodes/skills/scorer.py::Scorer.build_report` 와 동일 역할이나, V2 에서는
Dev5 `v2/schemas/enums.py::CATEGORY_META` 를 단일 진실 소스로 사용.
"""

from __future__ import annotations

import logging
from typing import Any

from v2.contracts.rubric import ALLOWED_STEPS, max_score_of, snap_score_v2
from v2.schemas.enums import CATEGORY_META, CategoryKey, get_category_meta


logger = logging.getLogger(__name__)


# item_number → CategoryKey 역매핑 (CATEGORY_META 로부터 생성)
def _build_item_to_category() -> dict[int, CategoryKey]:
    result: dict[int, CategoryKey] = {}
    for key, meta in CATEGORY_META.items():
        for item_num in meta["items"]:
            result[item_num] = key  # type: ignore[assignment]
    return result


_ITEM_TO_CATEGORY: dict[int, CategoryKey] = _build_item_to_category()


# ---------------------------------------------------------------------------
# 신한 부서특화 dept items (9XX 대역) → synthetic category 매핑
# Backend `v2/agents/shinhan_dept/registry.py` 와 정합 강제.
# 일반 CATEGORY_META 에 등록하지 않는 이유: enums.CategoryKey Literal 을 변경하지 않고
# 옵트인 형태로만 처리하기 위함.
# ---------------------------------------------------------------------------
def _build_dept_item_to_node() -> dict[int, str]:
    """신한 dept items → node_id 역매핑. registry 가 진실 소스.

    Lazy import — registry 미로드 시 빈 dict (회귀 없음).
    """
    try:
        from v2.agents.shinhan_dept.registry import DEPT_NODE_REGISTRY
    except Exception:
        return {}
    out: dict[int, str] = {}
    for nid, spec in DEPT_NODE_REGISTRY.items():
        for it in spec.get("items", []):
            out[int(it["item_number"])] = nid
    return out


_DEPT_ITEM_TO_NODE: dict[int, str] = _build_dept_item_to_node()


def _dept_node_meta(node_id: str) -> dict[str, Any]:
    """node_id 에 해당하는 dept node spec 반환 (label / max_score / items 등)."""
    try:
        from v2.agents.shinhan_dept.registry import DEPT_NODE_REGISTRY
        return DEPT_NODE_REGISTRY.get(node_id, {})  # type: ignore[return-value]
    except Exception:
        return {}


# ===========================================================================
# 메인 함수
# ===========================================================================


def aggregate_scores(
    evaluations: list[dict[str, Any]], *, site_id: str | None = None
) -> dict[str, Any]:
    """evaluations 리스트를 대분류별로 집계하고 총점 계산.

    Parameters
    ----------
    evaluations : list[dict]
        Layer 2 Sub Agent 가 append 한 평가 결과. 각 원소는 V1 EvaluationResult
        포맷 또는 Dev5 SubAgentResponse.items 포맷 허용:
        - V1: {"status", "agent_id", "evaluation": {item_number, score, max_score, ...}}
        - V2: {"item_number", "score", "max_score", ...} (이미 풀린 상태)

    Returns
    -------
    dict
        {
          "category_scores": [
              {"category_key": "greeting_etiquette", "category": "인사 예절",
               "max_score": 10, "achieved_score": 8, "items": [
                   {"item_number": 1, "score": 5, "max_score": 5, ...},
                   {"item_number": 2, "score": 3, "max_score": 5, ...},
               ]},
              ...
          ],
          "raw_total": 82,
          "max_possible": 100,
          "normalized_items": [{...}, ...],  # 플랫 리스트 (18 항목)
          "missing_items": [int, ...],        # 평가 누락 item_number 리스트
        }
    """
    # (1) 평가 dict 를 item_number 별로 정규화
    normalized = _normalize_items(evaluations)
    by_item_num = {item["item_number"]: item for item in normalized}

    # (2) 카테고리별 집계
    category_scores: list[dict[str, Any]] = []
    raw_total = 0
    max_possible = 0
    missing_items: list[int] = []

    # tenant 별 META — 신한 시 #11/#13/#15/#16 제외 + 설명력/적극성 max 조정
    meta_map = get_category_meta(site_id)
    for cat_key, meta in meta_map.items():
        items_in_category: list[dict[str, Any]] = []
        achieved = 0
        cat_max = meta["max_score"]
        for item_num in meta["items"]:
            if item_num in by_item_num:
                item = by_item_num[item_num]
                items_in_category.append(item)
                # unevaluable / SKIPPED_INFRA (score=None) 는 합산 제외
                sc = item.get("score")
                if sc is not None:
                    achieved += int(sc)
            else:
                missing_items.append(item_num)

        category_scores.append({
            "category_key": cat_key,
            "category": meta["label_ko"],
            "category_label_en": meta.get("label_en"),
            "max_score": cat_max,
            "achieved_score": achieved,
            "items": items_in_category,
        })
        raw_total += achieved
        max_possible += cat_max

    # 신한 부서특화 dept categories (있는 경우만 추가) — CategoryKey Literal 영향 없음
    dept_items_by_node: dict[str, list[dict[str, Any]]] = {}
    for item in normalized:
        node_id = _DEPT_ITEM_TO_NODE.get(item["item_number"])
        if node_id:
            dept_items_by_node.setdefault(node_id, []).append(item)

    for node_id, dept_items in dept_items_by_node.items():
        spec = _dept_node_meta(node_id)
        if not spec:
            continue
        achieved = 0
        for it in dept_items:
            sc = it.get("score")
            if sc is not None:
                achieved += int(sc)
        cat_max = int(spec.get("max_score", 0))
        category_scores.append({
            "category_key": spec.get("category_key", f"shinhan_{node_id}"),
            "category": spec.get("label_ko", node_id),
            "category_label_en": None,
            "max_score": cat_max,
            "achieved_score": achieved,
            "items": dept_items,
            # frontend / consistency_check 가 dept 인지 인식하도록 플래그
            "is_dept_specific": True,
            "dept_node_id": node_id,
            "team_id": spec.get("team_id"),
        })
        raw_total += achieved
        max_possible += cat_max

    logger.info(
        "aggregate_scores: raw_total=%d/%d missing=%d dept_categories=%d",
        raw_total, max_possible, len(missing_items), len(dept_items_by_node),
    )

    return {
        "category_scores": category_scores,
        "raw_total": raw_total,
        "max_possible": max_possible,
        "normalized_items": normalized,
        "missing_items": missing_items,
    }


# ===========================================================================
# 내부 헬퍼
# ===========================================================================


def _normalize_items(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """V1 과 V2 두 포맷 모두 지원하는 평가 리스트 정규화.

    반환 형태: `{"item_number", "item_name", "score", "max_score",
                  "evaluation_mode", "deductions", "evidence", "confidence",
                  "agent_id", "status"}` 통일 dict 리스트.
    """
    out: list[dict[str, Any]] = []
    for e in evaluations or []:
        if not isinstance(e, dict):
            continue

        # V1 포맷 (evaluation 중첩)
        if "evaluation" in e and isinstance(e["evaluation"], dict):
            ev = e["evaluation"]
            agent_id = e.get("agent_id", "")
            status = e.get("status", "success")
        else:
            # V2 포맷 (평면)
            ev = e
            agent_id = e.get("agent_id", "")
            status = e.get("status", "success")

        item_number = ev.get("item_number")
        if not isinstance(item_number, int):
            continue
        # 공통 18개 (1-18) 또는 신한 dept (9XX 대역) 만 통과
        is_common = item_number in _ITEM_TO_CATEGORY
        is_dept = item_number in _DEPT_ITEM_TO_NODE
        if not (is_common or is_dept):
            continue

        # score 를 V2 ALLOWED_STEPS 로 다시 snap (방어적)
        # dept items 는 ALLOWED_STEPS_V2 미등록이므로 Sub Agent 가 이미 snap 한 값을 신뢰.
        raw_score_val = ev.get("score")
        if raw_score_val is None:
            snapped = None  # unevaluable / SKIPPED_INFRA — 합산 제외 신호
        elif is_common:
            snapped = snap_score_v2(item_number, int(raw_score_val))
        else:
            snapped = int(raw_score_val)

        # max_score 검증 — common 은 rubric, dept 는 registry 값
        if is_common:
            rubric_max = max_score_of(item_number)
        else:
            # dept items: registry 에서 max 조회
            try:
                from v2.agents.shinhan_dept.registry import DEPT_NODE_REGISTRY
                node_id = _DEPT_ITEM_TO_NODE[item_number]
                spec = DEPT_NODE_REGISTRY[node_id]
                rubric_max = next(
                    int(it["max_score"]) for it in spec["items"]
                    if int(it["item_number"]) == item_number
                )
            except Exception:
                rubric_max = int(ev.get("max_score", 0) or 0)
        ev_max = int(ev.get("max_score", rubric_max) or rubric_max)
        if ev_max != rubric_max:
            logger.warning(
                "aggregate_scores: item #%d max_score=%d != rubric %d — using rubric",
                item_number, ev_max, rubric_max,
            )
            ev_max = rubric_max

        out.append({
            "item_number": item_number,
            "item_name": ev.get("item_name", ""),
            "score": snapped,
            "max_score": ev_max,
            "evaluation_mode": ev.get("evaluation_mode", "full"),
            "deductions": ev.get("deductions", []) or [],
            "evidence": ev.get("evidence", []) or [],
            "confidence": ev.get("confidence"),
            "agent_id": agent_id,
            "status": status,
            "judgment": ev.get("judgment", ""),
            # Optional V2 fields
            "flag": ev.get("flag"),
            "mandatory_human_review": ev.get("mandatory_human_review", False),
            "force_t3": ev.get("force_t3", False),
            "rule_verdict_diff": ev.get("rule_verdict_diff"),
        })

    # item_number 순 정렬
    out.sort(key=lambda x: x["item_number"])
    return out


# ===========================================================================
# 공용 헬퍼 (Layer 3 내부 공유)
# ===========================================================================


def items_in_category(category_scores: list[dict[str, Any]], cat_key: str) -> list[dict[str, Any]]:
    """category_key 로 items 추출."""
    for cat in category_scores:
        if cat.get("category_key") == cat_key:
            return cat.get("items", [])
    return []


def category_of_item(item_number: int) -> CategoryKey:
    """item_number → CategoryKey 조회 (실패 시 KeyError)."""
    key = _ITEM_TO_CATEGORY.get(item_number)
    if key is None:
        raise KeyError(f"item_number={item_number} 가 CATEGORY_META 에 없음")
    return key


def all_item_numbers() -> set[int]:
    """평가 대상 전체 item_number 집합 (CATEGORY_META 기반)."""
    return {
        item_num
        for meta in CATEGORY_META.values()
        for item_num in meta["items"]
    }
