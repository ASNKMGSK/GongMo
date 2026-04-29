# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL Queue Populator — LangGraph terminal node.

파이프라인 완료 시 `report.evaluation.categories[].items[]` 를 순회해
`human_reviews` 테이블에 `status='pending'` 으로 UPSERT.

운용 모드 (env `QA_HITL_POPULATE_MODE`, 기본 "all"):
  - "all"      : 평가된 모든 항목을 무조건 UPSERT. 사용자 요청 "일단 모든 데이터가
                 다 검토 큐에 들어가도록". reasons=["auto_populate_all"].
  - "flagged"  : 4개 조건 OR 로 필터.
                   1. item.force_t3 == True
                   2. item.confidence.final <= 2
                   3. item.mandatory_human_review == True
                   4. item_number ∈ overrides 영향
                 reasons 에 매칭된 조건 태그 (force_t3 / low_confidence_N /
                 mandatory_human_review / override_applied) 가 들어감.

기타 env:
  QA_HITL_AUTO_POPULATE  (기본 "1") — "0" 이면 전체 no-op.

state update (반환값):
  {"hitl_queue_populated": {"count": N, "item_numbers": [...], "mode": "all"|"flagged"}}

force_t3 / ai_confidence / mandatory_human_review 는 mode 와 무관하게 메타데이터로
계속 저장 — UI 강조/정렬에 사용.

예외 처리: DB 에러는 warning 로그만 남기고 파이프라인 중단 X.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


_CONF_THRESHOLD = 2  # "flagged" 모드에서 confidence.final <= 2 이면 적재


def _resolve_json_root() -> Path:
    """결과 JSON 저장 루트. env QA_RESULT_JSON_ROOT 우선, 기본 ~/Desktop/QA평가결과/JSON."""
    root_str = os.environ.get("QA_RESULT_JSON_ROOT") or str(
        Path.home() / "Desktop" / "QA평가결과" / "JSON"
    )
    return Path(root_str)


def _save_report_json(consultation_id: str, state: dict[str, Any]) -> str | None:
    """검토 큐 풀뷰 렌더용 결과 JSON 파일 저장.

    저장 내용: state 의 report / gt_comparison / gt_evidence_comparison 을 모아
    한 파일로 떨어뜨림. 프론트 /v2/result/full/{id} 가 이 파일을 읽어 평가 결과 탭
    풀뷰를 재현.
    """
    try:
        root = _resolve_json_root()
        root.mkdir(parents=True, exist_ok=True)
        # 파일명 안전화 — 디렉토리 traversal / 경로 구분자 방지
        safe_cid = str(consultation_id).replace("/", "_").replace("\\", "_")
        safe_cid = safe_cid or "unknown"
        target = root / f"{safe_cid}.json"
        payload = {
            "consultation_id": consultation_id,
            "transcript": state.get("transcript"),  # HITL 검토 화면 STT 전문 섹션용
            "report": state.get("report"),
            "gt_comparison": state.get("gt_comparison"),
            "gt_evidence_comparison": state.get("gt_evidence_comparison"),
            "orchestrator": state.get("orchestrator"),
            "preprocessing": state.get("preprocessing"),
            "debates": state.get("debates"),  # Phase 2 — DebateRecord (Dev3 결과 페이지 DebateList 용)
        }
        # default=str: datetime 같은 non-serializable 대비
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("hitl_queue_populator: saved result JSON → %s", target)
        return str(target)
    except Exception as exc:
        logger.warning("hitl_queue_populator: JSON 저장 실패 — %s", exc)
        return None


def _is_disabled() -> bool:
    raw = os.environ.get("QA_HITL_AUTO_POPULATE", "1").strip().lower()
    return raw in {"0", "false", "no", "off"}


def _resolve_mode() -> str:
    raw = (os.environ.get("QA_HITL_POPULATE_MODE") or "all").strip().lower()
    return raw if raw in {"all", "flagged"} else "all"


def _resolve_consultation_id(state: dict[str, Any]) -> str:
    cid = state.get("consultation_id") or state.get("session_id") or ""
    return str(cid) or "unknown"


def _iter_report_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    evaluation = report.get("evaluation") or {}
    categories = evaluation.get("categories") or []
    items: list[dict[str, Any]] = []
    for cat in categories:
        for it in cat.get("items") or []:
            if isinstance(it, dict):
                items.append(it)
    return items


def _iter_evaluation_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    """report 가 비어있을 때 state.evaluations 에서 직접 ItemVerdict 추출.

    신한 dept items (901-922) 또는 layer3/layer4 gate 실패로 report 가 누락된 케이스에서
    HITL 큐가 비지 않도록 보장.
    """
    evals = state.get("evaluations") or []
    items: list[dict[str, Any]] = []
    seen_item_nums: set[int] = set()
    for e in evals:
        if not isinstance(e, dict):
            continue
        ev = e.get("evaluation") if isinstance(e.get("evaluation"), dict) else e
        if not isinstance(ev, dict):
            continue
        item_num_raw = ev.get("item_number")
        if item_num_raw is None:
            continue
        try:
            item_num = int(item_num_raw)
        except (TypeError, ValueError):
            continue
        if item_num in seen_item_nums:
            continue  # dedup — 같은 item_number 가 evaluations 에 중복돼도 1건만
        seen_item_nums.add(item_num)
        items.append(ev)
    return items


def _collect_override_items(report: dict[str, Any]) -> set[int]:
    """report.overrides.reasons[].affected_items 합집합."""
    overrides = report.get("overrides") or {}
    if not overrides.get("applied"):
        return set()
    out: set[int] = set()
    for entry in overrides.get("reasons") or []:
        for n in entry.get("affected_items") or []:
            try:
                out.add(int(n))
            except (TypeError, ValueError):
                continue
    return out


def _flagged_reasons(item: dict[str, Any], override_items: set[int]) -> list[str]:
    reasons: list[str] = []
    if item.get("force_t3") is True:
        reasons.append("force_t3")
    confidence = item.get("confidence") or {}
    final = confidence.get("final")
    try:
        if final is not None and int(final) <= _CONF_THRESHOLD:
            reasons.append(f"low_confidence_{int(final)}")
    except (TypeError, ValueError):
        pass
    if item.get("mandatory_human_review") is True:
        reasons.append("mandatory_human_review")
    item_number = item.get("item_number")
    try:
        if item_number is not None and int(item_number) in override_items:
            reasons.append("override_applied")
    except (TypeError, ValueError):
        pass
    return reasons


def hitl_queue_populator_node(state: dict[str, Any]) -> dict[str, Any]:
    """파이프라인 종료 시 평가 항목을 human_reviews 에 UPSERT.

    state.report 가 없거나 env flag 가 off 이면 no-op.
    """
    if _is_disabled():
        logger.info("hitl_queue_populator: disabled via QA_HITL_AUTO_POPULATE=0")
        return {"hitl_queue_populated": {"count": 0, "item_numbers": [], "skipped": True}}

    report = state.get("report") or {}
    has_report = isinstance(report, dict) and bool(report.get("evaluation"))

    try:
        from v2.hitl import db as _hitl_db

        _hitl_db.init_db()
    except Exception as exc:
        logger.warning("hitl_queue_populator: db.init_db 실패 — skip (%s)", exc)
        return {"hitl_queue_populated": {"count": 0, "item_numbers": []}}

    mode = _resolve_mode()
    consult = _resolve_consultation_id(state)

    # canonical source = state.evaluations (LangGraph operator.add 로 모든 sub-agent 결과 누적).
    # report.evaluation.categories[] 는 CATEGORY_META 기반이라 신한 dept synthetic categories
    # (shinhan_coll_accuracy 등) 가 포함되지 않음 → dept items 가 누락됨. 따라서 evaluations 를
    # 1순위, report 를 2순위로 병합 (report 에만 있는 메타필드 보존).
    eval_items = _iter_evaluation_items(state)
    by_num: dict[int, dict[str, Any]] = {}
    for it in eval_items:
        try:
            by_num[int(it["item_number"])] = dict(it)
        except (TypeError, ValueError, KeyError):
            continue
    if has_report:
        for rit in _iter_report_items(report):
            try:
                k = int(rit.get("item_number"))
            except (TypeError, ValueError):
                continue
            if k in by_num:
                # report 에 있지만 evaluations 에 없는 필드만 보강 (confidence / mandatory_human_review 등)
                for key, val in rit.items():
                    by_num[k].setdefault(key, val)
            else:
                by_num[k] = dict(rit)
    items = list(by_num.values())
    logger.info(
        "hitl_queue_populator: 병합 결과 — evaluations=%d, report=%s, total=%d",
        len(eval_items), "yes" if has_report else "no", len(items),
    )

    if not items:
        logger.info("hitl_queue_populator: 평가 항목 없음 — no-op")
        return {"hitl_queue_populated": {"count": 0, "item_numbers": []}}

    # 검토 큐 풀뷰용 결과 JSON 저장 (report + gt_* 모두 포함)
    json_path = _save_report_json(consult, state)

    override_items: set[int] = set()
    if mode == "flagged":
        override_items = _collect_override_items(report)
        orch = state.get("orchestrator") or {}
        for n in orch.get("items_modified") or []:
            try:
                override_items.add(int(n))
            except (TypeError, ValueError):
                continue

    inserted_items: list[int] = []
    for item in items:
        item_number_raw = item.get("item_number")
        if item_number_raw is None:
            continue
        try:
            item_number = int(item_number_raw)
        except (TypeError, ValueError):
            continue

        if mode == "flagged":
            reasons = _flagged_reasons(item, override_items)
            if not reasons:
                continue
        else:
            reasons = ["auto_populate_all"]

        confidence = item.get("confidence") or {}
        confidence_final = confidence.get("final")
        try:
            confidence_score = float(confidence_final) if confidence_final is not None else None
        except (TypeError, ValueError):
            confidence_score = None

        evidence = item.get("evidence") or []
        try:
            ai_evidence = json.loads(json.dumps(evidence, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            ai_evidence = None

        ai_score_raw = item.get("score")
        try:
            ai_score = float(ai_score_raw) if ai_score_raw is not None else None
        except (TypeError, ValueError):
            ai_score = None

        try:
            _hitl_db.upsert_review(
                consultation_id=consult,
                item_number=item_number,
                ai_score=ai_score,
                human_score=None,
                ai_evidence=ai_evidence,
                ai_judgment=item.get("judgment"),
                human_note=None,
                ai_confidence=confidence_score,
                reviewer_id=None,
                reviewer_role="senior",
                force_t3=bool(item.get("force_t3")),
                status="pending",
                # 3단계 멀티테넌트 — state 로부터 3필드 저장 (없으면 NULL).
                site_id=state.get("site_id") or state.get("tenant_id"),
                channel=state.get("channel"),
                department=state.get("department"),
            )
            inserted_items.append(item_number)
            logger.debug(
                "hitl_queue_populator[%s]: enqueue consult=%s item=%d reasons=%s",
                mode, consult, item_number, reasons,
            )
        except Exception as exc:
            logger.warning(
                "hitl_queue_populator[%s]: upsert 실패 consult=%s item=%s — %s",
                mode, consult, item_number, exc,
            )
            continue

    result = {
        "count": len(inserted_items),
        "item_numbers": sorted(set(inserted_items)),
        "mode": mode,
        "json_path": json_path,
    }
    logger.info(
        "hitl_queue_populator[%s]: consult=%s enqueued=%d/%d",
        mode, consult, result["count"], len(items),
    )
    return {"hitl_queue_populated": result}
