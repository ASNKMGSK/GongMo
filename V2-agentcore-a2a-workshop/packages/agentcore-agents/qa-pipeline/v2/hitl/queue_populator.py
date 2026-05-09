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


def _build_full_payload(consultation_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """평가 결과 탭에 표시되는 모든 정보를 한 dict 로 빌드.

    ★ 2026-05-07: kms_evaluation / evaluations 도 포함. 이전 _save_report_json 누락분.
    DB result_payloads 테이블 + JSON 파일 둘 다 같은 페이로드 사용 (단일 정의).
    """
    return {
        "consultation_id": consultation_id,
        "transcript": state.get("transcript"),
        "report": state.get("report"),
        "evaluations": state.get("evaluations"),  # ★ persona_details 등 항목별 풀 결과
        "gt_comparison": state.get("gt_comparison"),
        "gt_evidence_comparison": state.get("gt_evidence_comparison"),
        "orchestrator": state.get("orchestrator"),
        "preprocessing": state.get("preprocessing"),
        "debates": state.get("debates"),
        "kms_evaluation": state.get("kms_evaluation"),  # ★ 이전 누락
        "routing": state.get("routing"),
    }


def _save_report_json(consultation_id: str, state: dict[str, Any]) -> str | None:
    """검토 큐 풀뷰 렌더용 결과 JSON 파일 저장 (백업/이중화).

    DB result_payloads 가 primary source, 이 파일은 백업. 파일 시스템 검색/grep 도 가능.
    """
    try:
        root = _resolve_json_root()
        root.mkdir(parents=True, exist_ok=True)
        safe_cid = str(consultation_id).replace("/", "_").replace("\\", "_") or "unknown"
        target = root / f"{safe_cid}.json"
        payload = _build_full_payload(consultation_id, state)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("hitl_queue_populator: saved result JSON → %s", target)
        return str(target)
    except Exception as exc:
        logger.warning("hitl_queue_populator: JSON 저장 실패 — %s", exc)
        return None


def _save_result_payload_db(consultation_id: str, state: dict[str, Any], model_id: str | None) -> bool:
    """평가 결과 풀 페이로드를 DB result_payloads 테이블에 INSERT/UPDATE.
    ★ 2026-05-07: DB 단일 진실 + JSON 백업 dual storage 패턴.
    검토 큐 상세 (/v2/result/full/{cid}) 가 우선 DB 조회 → 없으면 JSON 파일 fallback.
    """
    try:
        from v2.hitl import db as _hitl_db
        payload = _build_full_payload(consultation_id, state)
        _hitl_db.upsert_result_payload(
            consultation_id=consultation_id,
            payload=payload,
            site_id=state.get("site_id") or state.get("tenant_id"),
            channel=state.get("channel"),
            department=state.get("department"),
            model_id=model_id,
        )
        logger.info("hitl_queue_populator: saved result payload to DB (cid=%s)", consultation_id)
        return True
    except Exception as exc:
        logger.warning("hitl_queue_populator: DB result_payloads 저장 실패 — %s", exc)
        return False


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

    # 검토 큐 풀뷰용 결과 — DB result_payloads 테이블 (primary) + JSON 파일 (백업) 둘 다.
    # ★ 2026-05-07: 평가 결과 탭의 모든 정보 (report + evaluations + debates + persona_details +
    # gt_comparison + kms_evaluation + preprocessing + transcript) 를 단일 dict 로 묶어 저장.
    # 모델 ID 는 첫 평가 항목에서 추출 (loop 진입 전이라 미리 한 번 계산).
    payload_model_id = (
        state.get("bedrock_model_id") or os.environ.get("BEDROCK_MODEL_ID")
    )
    _save_result_payload_db(consult, state, payload_model_id)
    json_path = _save_report_json(consult, state)

    # ★ 2026-05-07: GT 점수 (정답표 xlsx) 를 항목별로 lookup 가능하게 dict 빌드.
    # state.gt_comparison.items[].{item_number, gt_score} 구조. 통계 (/v2/drift/stats) 용.
    gt_score_by_item: dict[int, float] = {}
    gt_comp = state.get("gt_comparison") or {}
    for gi in gt_comp.get("items") or []:
        try:
            inum = int(gi.get("item_number"))
            gs = gi.get("gt_score")
            if gs is not None:
                gt_score_by_item[inum] = float(gs)
        except (TypeError, ValueError):
            continue
    if gt_score_by_item:
        logger.info("hitl_queue_populator: GT scores loaded for %d items", len(gt_score_by_item))

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

        # ★ 2026-05-07: 평가 시 사용된 모델 ID 기록.
        # 우선순위: item.llm_model_id (sub_agent 가 채움) → state.bedrock_model_id (요청 override)
        # → BEDROCK_MODEL_ID env (서버 기본값). 셋 다 없으면 NULL.
        item_model_id = (
            item.get("llm_model_id")
            or state.get("bedrock_model_id")
            or os.environ.get("BEDROCK_MODEL_ID")
        )
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
                model_id=item_model_id,
                gt_score=gt_score_by_item.get(item_number),
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

    # ★ 2026-05-07: KMS 평가도 검토 큐에 동일하게 저장 (사용자 요청 — 평가 결과 모든 것 미러).
    # KMS_INTENT_TABS 7종 (회원정보/환불/교환/반품/수선/배송/취소) 각각 item_number 1001~1007.
    # 신한 dept (901~922) 와 충돌 없도록 1000+ 범위 사용.
    kms_inserted = _populate_kms_evaluations(
        state=state,
        consult=consult,
        item_model_id=item_model_id,
        site_id=state.get("site_id") or state.get("tenant_id"),
        channel=state.get("channel"),
        department=state.get("department"),
    )
    inserted_items.extend(kms_inserted)

    result = {
        "count": len(inserted_items),
        "item_numbers": sorted(set(inserted_items)),
        "mode": mode,
        "json_path": json_path,
        "kms_count": len(kms_inserted),
    }
    logger.info(
        "hitl_queue_populator[%s]: consult=%s enqueued=%d (main=%d + kms=%d)",
        mode, consult, result["count"], len(items), len(kms_inserted),
    )
    return {"hitl_queue_populated": result}


# ---------------------------------------------------------------------------
# KMS 평가 → 검토 큐 적재 (item_number 1001~1007 으로 매핑)
# ---------------------------------------------------------------------------

# KMS 인텐트 7종 → 큐 item_number 매핑. node/kms_node.py 의 KMS_INTENT_TABS 와 동일 순서.
_KMS_INTENT_TO_ITEM: dict[str, int] = {
    "회원정보": 1001,
    "환불": 1002,
    "교환": 1003,
    "반품": 1004,
    "수선": 1005,
    "배송": 1006,
    "취소": 1007,
}


def _populate_kms_evaluations(
    *,
    state: dict[str, Any],
    consult: str,
    item_model_id: str | None,
    site_id: str | None,
    channel: str | None,
    department: str | None,
) -> list[int]:
    """state.kms_evaluation 의 인텐트별 평가를 검토 큐에 적재.

    각 인텐트 = 1행. item_number=1001~1007 (KMS_INTENT_TABS 순서).
    검출 안 된 인텐트는 skip — 평가가 수행되지 않은 상태라 행 의미 없음.
    """
    from v2.hitl import db as _hitl_db

    kms_eval = state.get("kms_evaluation") or {}
    if not isinstance(kms_eval, dict) or not kms_eval.get("available"):
        return []
    evaluations_by_intent = kms_eval.get("evaluations_by_intent") or {}
    if not isinstance(evaluations_by_intent, dict):
        return []

    inserted: list[int] = []
    for intent, intent_eval in evaluations_by_intent.items():
        item_no = _KMS_INTENT_TO_ITEM.get(str(intent))
        if item_no is None:
            logger.warning("hitl_queue_populator: unknown KMS intent '%s' — skip", intent)
            continue
        if not isinstance(intent_eval, dict):
            continue
        try:
            ai_score = intent_eval.get("score")
            ai_score = float(ai_score) if ai_score is not None else None
        except (TypeError, ValueError):
            ai_score = None

        # KMS 의 evidence 는 tab_evaluations 의 applied_branches 같은 구조 → JSON 직렬화 그대로 저장
        evidence_payload: Any = {
            "intent": intent,
            "applied_branches": intent_eval.get("applied_branches"),
            "tab_evaluations": intent_eval.get("tab_evaluations"),
            "summary": intent_eval.get("summary"),
            "mismatches": intent_eval.get("mismatches"),
            "rejected_mismatches": intent_eval.get("rejected_mismatches"),
        }
        try:
            ai_evidence = json.loads(json.dumps(evidence_payload, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            ai_evidence = None

        try:
            _hitl_db.upsert_review(
                consultation_id=consult,
                item_number=item_no,
                ai_score=ai_score,
                human_score=None,
                ai_evidence=ai_evidence,
                ai_judgment=intent_eval.get("reasoning"),
                human_note=None,
                ai_confidence=None,
                reviewer_id=None,
                reviewer_role="senior",
                force_t3=False,
                status="pending",
                site_id=site_id,
                channel=channel,
                department=department,
                model_id=item_model_id,
                gt_score=None,  # KMS 는 GT 매핑 없음 (현재 스키마 기준)
            )
            inserted.append(item_no)
            logger.debug(
                "hitl_queue_populator[KMS]: enqueue consult=%s intent=%s item=%d score=%s",
                consult, intent, item_no, ai_score,
            )
        except Exception as exc:
            logger.warning(
                "hitl_queue_populator[KMS]: upsert 실패 consult=%s intent=%s — %s",
                consult, intent, exc,
            )
            continue
    return inserted
