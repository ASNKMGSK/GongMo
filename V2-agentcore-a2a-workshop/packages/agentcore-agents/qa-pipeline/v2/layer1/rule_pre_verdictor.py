# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 (e) — Rule 기반 1차 판정.

설계서 p10 (e):
    첫인사 구성요소 포함 여부, 문의 유형 분류(Intent classification),
    본인 확인 순서 체크 등 LLM 없이 처리 가능한 판정을 미리 수행.

V1 `nodes/skills/pattern_matcher.py::PatternMatcher` 의 8 메서드를
import 로 재활용. 12 항목(#1,2,3,4,5,6,7,8,9,16,17,18) 에 대한 RulePreVerdict 생성.

PL 확정 키명: `rule_pre_verdicts: {"item_01": {...}, "item_02": {...}, ...}`
(zero-padded 2자리 — `item_key()` 헬퍼 사용).

ALLOWED_STEPS 정합성: 점수는 V2 전용 `v2/contracts/rubric.snap_score_v2()` 를 경유해
항목별 허용 단계로 snap. V1 `snap_score` 는 V1 qa_rules.py 테이블 기준이어서
#17/#18 = [5,0] 으로 강제 변환되므로 V2 에서는 쓰지 않는다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# V1 자산 재활용 — import only (수정 금지)
from nodes.skills.constants import (  # type: ignore[import-untyped]
    HOLD_SILENCE_MARKERS,
    IV_PROCEDURE_PATTERNS,
    SPEECH_OVERLAP_PATTERNS,
)
from nodes.skills.pattern_matcher import PatternMatcher  # type: ignore[import-untyped]

from v2.contracts.preprocessing import (
    IVEvidence,
    IVEvidenceTurn,
    RulePreVerdict,
    item_key,
)
from v2.contracts.rubric import snap_score_v2


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent 분류 키워드 — V1 nodes/mandatory.py 의 intent_summary 로직을
# 경량화해 내장. LLM 없이 키워드 매칭만.
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("상품문의",   [r"상품", r"제품", r"가격", r"요금", r"혜택", r"안내받"]),
    ("주문배송",   [r"주문", r"배송", r"배달", r"운송장", r"송장", r"도착"]),
    ("환불취소",   [r"환불", r"취소", r"반품", r"되돌려", r"돌려받"]),
    ("변경해지",   [r"변경", r"해지", r"해약", r"중단", r"정지"]),
    ("장애문의",   [r"안\s*되", r"오류", r"고장", r"작동.*안", r"안\s*나와", r"에러"]),
    ("결제문의",   [r"결제", r"카드", r"입금", r"출금", r"이체"]),
    ("가입상담",   [r"가입", r"신청", r"등록", r"신규"]),
    ("계약문의",   [r"계약", r"약정", r"만기", r"갱신"]),
    ("본인확인",   [r"본인\s*확인", r"성함", r"생년월일"]),
    ("일반문의",   []),  # 기본값 (매칭 실패 시)
]


def _classify_intent(turns: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """고객 발화 전체를 키워드 매칭해 primary intent 분류.

    Returns
    -------
    (intent_type, intent_detail)
    """
    customer_text = "\n".join(
        t.get("text", "") for t in turns if t.get("speaker") == "customer"
    )

    matched: list[tuple[str, int]] = []  # (intent, 매칭 카운트)
    for intent_type, patterns in _INTENT_PATTERNS:
        if not patterns:
            continue
        count = sum(1 for pat in patterns if re.search(pat, customer_text))
        if count > 0:
            matched.append((intent_type, count))

    matched.sort(key=lambda x: -x[1])  # 많이 매칭된 순

    if not matched:
        primary_intent = "일반문의"
        sub_intents: list[str] = []
    else:
        primary_intent = matched[0][0]
        sub_intents = [i for i, _ in matched[1:3]]  # 2순위까지 sub

    # 복잡도 추정 — 턴 수 기반
    total_turns = len(turns)
    if total_turns < 15:
        complexity = "simple"
    elif total_turns < 40:
        complexity = "moderate"
    else:
        complexity = "complex"

    return primary_intent, {
        "primary_intent": primary_intent,
        "sub_intents": sub_intents,
        "product": "",  # tenant RAG 에서 채움 (Dev4 영역)
        "complexity": complexity,
        "tenant_topic_ref": None,
    }


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------


def build_rule_pre_verdicts(
    turns: list[dict[str, Any]],
    transcript: str,
    turn_pairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """V1 PatternMatcher 결과를 RulePreVerdict 스펙으로 직렬화.

    Parameters
    ----------
    turns : list[dict]
        segment_splitter 출력의 turns (speaker/text/turn_id/segment).
    transcript : str
        원본 전사록 (PatternMatcher 의 count_empathy / detect_inappropriate
        등이 내부 파싱용으로 필요).
    turn_pairs : list[dict] | None
        segment_splitter 출력의 turn_pairs. paraphrase 탐지 등에 사용.

    Returns
    -------
    dict
        {
          "rule_pre_verdicts": dict[str, RulePreVerdict],  # item_01 ~ item_18
          "intent_type": str,
          "intent_detail": IntentDetail,
          "iv_evidence": IVEvidence,
        }
    """
    matcher = PatternMatcher()
    verdicts: dict[str, RulePreVerdict] = {}

    # --- #1 첫인사 ---------------------------------------------------------
    g = matcher.match_greeting(turns, first_n=5)
    verdicts[item_key(1)] = _build_verdict(
        item_number=1,
        score_raw=g.get("score", 0),
        elements=g.get("elements", {}),
        evidence_turn_ids=_extract_turn_ids([g.get("greeting_turn")]),
        evidence_snippets=_extract_snippets([g.get("greeting_text", "")]),
        rationale=_greeting_rationale(g),
        extra={
            "greeting_turn": g.get("greeting_turn"),
            "greeting_text": g.get("greeting_text"),
            "detected_keywords": g.get("detected_keywords", []),
        },
    )

    # --- #2 끝인사 ---------------------------------------------------------
    c = matcher.match_closing(turns, last_n=10)
    verdicts[item_key(2)] = _build_verdict(
        item_number=2,
        score_raw=c.get("score", 0),
        elements=c.get("elements", {}),
        evidence_turn_ids=_extract_turn_ids([c.get("closing_turn")]),
        evidence_snippets=_extract_snippets([c.get("closing_text", "")]),
        rationale=_closing_rationale(c),
        extra={
            "closing_turn": c.get("closing_turn"),
            "closing_text": c.get("closing_text"),
            "customer_ended_first": c.get("customer_ended_first"),
            "detected_keywords": c.get("detected_keywords", []),
        },
    )

    # --- #3 경청/말겹침 — 2026-04-21 평가표에서 제거 (STT 한계) -----------
    # 5점은 #15 정확한 안내 로 이관됨. rule pre-verdict 생성 생략.

    # --- #4 호응/공감 -----------------------------------------------------
    emp = matcher.count_empathy(transcript)
    empathy_count = emp.get("count", 0)
    simple_only = emp.get("simple_only", False)
    empathy_turns = [e.get("turn", 0) for e in emp.get("patterns_found", [])]
    verdicts[item_key(4)] = _build_verdict(
        item_number=4,
        score_raw=0 if empathy_count == 0 else (3 if simple_only else 5),
        elements={
            "empathy_count": empathy_count,
            "simple_response_count": len(emp.get("patterns_found", [])) if simple_only else 0,
        },
        evidence_turn_ids=empathy_turns,
        evidence_snippets=[e.get("text", "") for e in emp.get("patterns_found", [])[:3]],
        rationale=f"empathy {empathy_count}건 / simple_only={simple_only}",
    )

    # --- #5 대기 멘트 -----------------------------------------------------
    h = matcher.detect_hold_mentions(transcript)
    before_count = len(h.get("before", []))
    after_count = len(h.get("after", []))
    silence_count = len(h.get("silence", []))
    hold_detected = h.get("hold_detected", False)
    # 대기 상황 자체가 없으면 만점, 있는데 전후 멘트 없으면 0, 일부만 누락이면 3
    if silence_count == 0 and before_count == 0 and after_count == 0:
        hold_score = 5  # 대기 없음 → 만점 (rule notes 참조)
    elif before_count > 0 and after_count > 0:
        hold_score = 5
    elif before_count > 0 or after_count > 0:
        hold_score = 3
    else:
        hold_score = 0
    verdicts[item_key(5)] = _build_verdict(
        item_number=5,
        score_raw=hold_score,
        elements={
            "hold_detected": hold_detected,
            "before_count": before_count,
            "after_count": after_count,
            "silence_count": silence_count,
        },
        evidence_turn_ids=_extract_turn_ids([
            *(b.get("turn") for b in h.get("before", [])[:2]),
            *(a.get("turn") for a in h.get("after", [])[:2]),
        ]),
        evidence_snippets=[
            *(b.get("text", "") for b in h.get("before", [])[:1]),
            *(a.get("text", "") for a in h.get("after", [])[:1]),
        ],
        rationale=f"before={before_count} after={after_count} silence={silence_count}",
    )

    # --- #6 정중한 표현 ---------------------------------------------------
    ia = matcher.detect_inappropriate(transcript)
    prof = len(ia.get("profanity", []))
    sighs = len(ia.get("sighs", []))
    lang = len(ia.get("language", []))
    mild = len(ia.get("mild", []))
    total_inappropriate = ia.get("total", 0)
    # 점수 규칙: 다수(>=3) → 0, 1~2회 → 3, 없음 → 5
    if prof > 0 or total_inappropriate >= 3:
        polite_score = 0
    elif total_inappropriate >= 1:
        polite_score = 3
    else:
        polite_score = 5
    polite_evidence = (
        ia.get("profanity", []) + ia.get("language", [])
        + ia.get("sighs", []) + ia.get("mild", [])
    )[:3]
    verdicts[item_key(6)] = _build_verdict(
        item_number=6,
        score_raw=polite_score,
        elements={
            "profanity_count": prof,
            "sigh_count": sighs,
            "language_count": lang,
            "mild_count": mild,
            "total_inappropriate": total_inappropriate,
        },
        evidence_turn_ids=[e.get("turn", 0) for e in polite_evidence],
        evidence_snippets=[e.get("text", "") for e in polite_evidence],
        rationale=f"inappropriate total={total_inappropriate} (prof={prof})",
    )

    # --- #7 쿠션어 -------------------------------------------------------
    cw = matcher.detect_cushion_words(transcript)
    cushion_count = cw.get("count", 0)
    refusal_count = len(cw.get("refusal_situations", []))
    # 거절 상황이 없으면 notes 에 따라 만점, 있으면 쿠션어 매칭 필요
    if refusal_count == 0:
        cushion_score = 5
        cushion_mode = "hard"  # 거절 상황 없음 — 만점 확정
    elif cushion_count >= refusal_count:
        cushion_score = 5
        cushion_mode = None
    elif cushion_count > 0:
        cushion_score = 3
        cushion_mode = None
    else:
        cushion_score = 0
        cushion_mode = None
    verdicts[item_key(7)] = _build_verdict(
        item_number=7,
        score_raw=cushion_score,
        elements={
            "refusal_count": refusal_count,
            "cushion_count": cushion_count,
        },
        evidence_turn_ids=[p.get("turn", 0) for p in cw.get("patterns_found", [])[:3]],
        evidence_snippets=[p.get("text", "") for p in cw.get("patterns_found", [])[:3]],
        rationale=f"refusal={refusal_count} cushion={cushion_count}",
        confidence_mode_override=cushion_mode,
    )

    # --- #8 문의 파악/복창 (Rule 초안 — LLM 재검증 권장) -------------------
    paraphrase_count, paraphrase_turns = _count_paraphrase(turns, turn_pairs or [])
    verdicts[item_key(8)] = _build_verdict(
        item_number=8,
        score_raw=5 if paraphrase_count >= 1 else 3,  # 복창 없음도 경계 판정 → LLM verify
        elements={
            "paraphrase_count": paraphrase_count,
            "requery_count": 0,  # V1 규칙상 미구현 — LLM 보강
        },
        evidence_turn_ids=paraphrase_turns[:3],
        evidence_snippets=[],
        rationale=f"paraphrase {paraphrase_count}건 (LLM verify 권장)",
        force_llm_verify=True,
    )

    # --- #9 고객정보 확인 -------------------------------------------------
    iv = matcher.check_identity_verification(turns)
    iv_performed = iv.get("iv_performed", False)
    # #9 는 양해 표현 + 정보 확인 — 양해 표현 rule 없어 LLM verify 필수
    verdicts[item_key(9)] = _build_verdict(
        item_number=9,
        score_raw=5 if iv_performed else 0,
        elements={
            "info_check_count": len(iv.get("iv_details", [])),
            "iv_performed": iv_performed,
        },
        evidence_turn_ids=[d.get("turn", 0) for d in iv.get("iv_details", [])[:3]],
        evidence_snippets=[d.get("text", "") for d in iv.get("iv_details", [])[:3]],
        rationale=f"iv_performed={iv_performed}",
        force_llm_verify=True,
    )

    # --- #16 필수 안내 이행 (tenant 스크립트 필요 — Rule 초안만) -----------
    # Layer 1 단에서는 placeholder (Dev4 tenant mandatory_scripts 합류 시 확장).
    verdicts[item_key(16)] = _build_verdict(
        item_number=16,
        score_raw=5,  # Rule 기반 판정 불가 — 만점 초안, LLM verify 필수
        elements={"mandatory_items_covered": [], "mandatory_items_missing": []},
        evidence_turn_ids=[],
        evidence_snippets=[],
        rationale="tenant 스크립트 미주입 — LLM verify 필수",
        force_llm_verify=True,
    )

    # --- #17 정보 확인 절차 -----------------------------------------------
    preemptive_found = iv.get("preemptive_found", False)
    third_party = iv.get("third_party", False)
    # 3요소 복합 판정 (iv_performed/preemptive_found/third_party)
    if preemptive_found:
        iv_score_raw = 0   # 선언급 = 즉시 0점
    elif not iv_performed:
        iv_score_raw = 0   # 절차 누락 = 0점
    elif third_party:
        iv_score_raw = 3   # 제3자 관여 — 부분 이행 (Q2 확장 시 3점 보존)
    else:
        iv_score_raw = 5
    verdicts[item_key(17)] = _build_verdict(
        item_number=17,
        score_raw=iv_score_raw,
        elements={
            "iv_performed": iv_performed,
            "preemptive_found": preemptive_found,
            "third_party": third_party,
        },
        evidence_turn_ids=[d.get("turn", 0) for d in iv.get("iv_details", [])[:3]],
        evidence_snippets=[d.get("text", "") for d in iv.get("iv_details", [])[:3]],
        rationale=(
            f"iv_performed={iv_performed} preemptive={preemptive_found} third_party={third_party}"
        ),
    )

    # --- #18 정보 보호 준수 -----------------------------------------------
    # 제3자 유출 또는 PII 공개 패턴이면 0점, 아니면 만점
    has_privacy_violation = third_party or _has_privacy_violation(turns)
    verdicts[item_key(18)] = _build_verdict(
        item_number=18,
        score_raw=0 if has_privacy_violation else 5,
        elements={
            "third_party_disclosure": third_party,
            "privacy_violation": has_privacy_violation,
        },
        evidence_turn_ids=[d.get("turn", 0) for d in iv.get("third_party_details", [])[:3]],
        evidence_snippets=[d.get("text", "") for d in iv.get("third_party_details", [])[:3]],
        rationale=f"third_party={third_party} privacy_violation={has_privacy_violation}",
    )

    # --- Intent 분류 ------------------------------------------------------
    intent_type, intent_detail = _classify_intent(turns)

    # --- iv_evidence (Dev3 요청) -----------------------------------------
    iv_evidence: IVEvidence = {
        "iv_procedure_turns": [
            IVEvidenceTurn(turn=d.get("turn", 0), text=d.get("text", ""), pattern=d.get("pattern", ""))
            for d in iv.get("iv_details", [])
        ],
        "preemptive_turns": [
            IVEvidenceTurn(turn=d.get("turn", 0), text=d.get("text", ""), pattern=d.get("pattern", ""))
            for d in iv.get("preemptive_details", [])
        ],
        "third_party_turns": [
            IVEvidenceTurn(turn=d.get("turn", 0), text=d.get("text", ""), pattern=d.get("pattern", ""))
            for d in iv.get("third_party_details", [])
        ],
    }

    logger.info(
        "rule_pre_verdictor: verdicts=%d intent=%s",
        len(verdicts), intent_type,
    )

    return {
        "rule_pre_verdicts": verdicts,
        "intent_type": intent_type,
        "intent_type_primary": intent_type,  # PL 2026-04-20 승인 — 외부 consumer 하위 호환 alias
        "intent_detail": intent_detail,
        "iv_evidence": iv_evidence,
    }


# ---------------------------------------------------------------------------
# RulePreVerdict 빌더
# ---------------------------------------------------------------------------


def _build_verdict(
    *,
    item_number: int,
    score_raw: int,
    elements: dict[str, Any],
    evidence_turn_ids: list[int],
    evidence_snippets: list[str],
    rationale: str,
    extra: dict[str, Any] | None = None,
    confidence_mode_override: str | None = None,
    force_llm_verify: bool = False,
) -> RulePreVerdict:
    """RulePreVerdict TypedDict 생성. snap_score 로 점수 정합 강제."""
    # snap_score_v2 경유 — V2 ALLOWED_STEPS (#17/#18=[5,3,0]) 기준 snap
    score = snap_score_v2(item_number, int(score_raw))

    # confidence / confidence_mode 휴리스틱
    if confidence_mode_override is not None:
        confidence_mode = confidence_mode_override
        confidence = 0.90
    elif force_llm_verify:
        confidence_mode = "soft"
        confidence = 0.55
    elif score == 5 and evidence_snippets:
        # 만점 + 근거 있음 → 높은 신뢰 (hard bypass 가능)
        confidence_mode = "hard"
        confidence = 0.80
    elif score == 0:
        # 0점 판정은 명확한 탐지 결과 → hard
        confidence_mode = "hard"
        confidence = 0.75
    else:
        confidence_mode = "soft"
        confidence = 0.65

    recommended = bool(force_llm_verify or confidence_mode == "soft")

    verdict: RulePreVerdict = {
        "item_number": item_number,
        "score": score,
        "confidence": confidence,
        "confidence_mode": confidence_mode,  # type: ignore[typeddict-item]
        "rationale": rationale,
        "evidence_turn_ids": [tid for tid in evidence_turn_ids if tid],
        "evidence_snippets": [s for s in evidence_snippets if s],
        "elements": elements,  # type: ignore[typeddict-item]
        "recommended_for_llm_verify": recommended,
    }
    if extra:
        verdict.update(extra)  # type: ignore[typeddict-item]
    return verdict


# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------


def _extract_turn_ids(vals: list[Any]) -> list[int]:
    """None 제거 + int 변환."""
    result: list[int] = []
    for v in vals:
        if v is None:
            continue
        try:
            result.append(int(v))
        except (TypeError, ValueError):
            continue
    return result


def _extract_snippets(vals: list[Any]) -> list[str]:
    """빈 문자열 제거."""
    return [str(v) for v in vals if v]


def _has_overlap_or_silence_markers(transcript: str) -> bool:
    """STT 겹침 / 대기 / 묵음 마커가 1건이라도 있는지 빠르게 검사."""
    for pat in SPEECH_OVERLAP_PATTERNS + HOLD_SILENCE_MARKERS:
        if re.search(pat, transcript):
            return True
    return False


# 복창 탐지 — 조사 흡수용 (어간 매칭)
_POSTPOSITIONS = frozenset({
    "이", "가", "은", "는", "을", "를", "로", "도", "만", "에", "와", "과",
    "의", "라", "야", "께", "께서", "에서", "한테", "에게",
})

# 복창 탐지 — 의도 키워드 (1개만 겹쳐도 복창 인정)
_INTENT_KEYWORDS = frozenset({
    "교환", "반품", "취소", "환불", "변경", "해지", "결제", "가입",
    "주문", "배송", "확인", "안내", "문의", "발생", "처리", "신청",
    "사이즈", "색상", "상품", "가격", "요금", "혜택",
})


def _stem_tokens(text: str) -> set[str]:
    """한글 토큰 추출 + 조사 1자 제거 변형 포함.

    예) "교환이" → {"교환이", "교환"}, "될지라고" → {"될지라고"}.
    `_POSTPOSITIONS` 에 해당하는 마지막 1자를 제거한 어간 변형을 추가해
    "교환이" vs "교환이라고" 같은 조사 차이를 흡수한다.
    """
    raw = set(re.findall(r"[가-힣]{2,}", text))
    out = set(raw)
    for w in raw:
        if len(w) >= 3 and w[-1] in _POSTPOSITIONS:
            out.add(w[:-1])  # 마지막 1자 (조사 후보) 제거 변형
    return out


def _count_paraphrase(
    turns: list[dict[str, Any]],
    turn_pairs: list[dict[str, Any]],
) -> tuple[int, list[int]]:
    """복창(paraphrase) 흔적 카운트 (경량 추정).

    판정:
    - 의도 키워드(교환/반품/취소 등)가 1개라도 겹치면 복창 인정,
    - 또는 일반 한글 토큰이 2개 이상 겹치면 복창 인정.
    - 어간 매칭으로 조사 1자 차이 흡수("교환이" ≡ "교환이라고").
    - 전체 turn_pairs 스캔 (이전 [:10] 제한 제거).

    정확도는 여전히 추정 수준 — `force_llm_verify=True` 로 LLM 재검증 권장.
    """
    count = 0
    matched_turns: list[int] = []
    for pair in turn_pairs:
        cust = pair.get("customer_text", "") or ""
        agent = pair.get("agent_text", "") or ""
        cust_tokens = _stem_tokens(cust)
        agent_tokens = _stem_tokens(agent)
        overlap = cust_tokens & agent_tokens
        intent_overlap = overlap & _INTENT_KEYWORDS
        if intent_overlap or len(overlap) >= 2:
            count += 1
            matched_turns.append(pair.get("agent_turn_id", 0))
    return count, matched_turns


def _has_privacy_violation(turns: list[dict[str, Any]]) -> bool:
    """상담사 발화에서 privacy_violation 패턴 탐지 여부 (간이).

    Full 탐지는 deduction_trigger_detector 가 수행 — 여기선 #18 Rule 초안용 경량 체크만.
    """
    from nodes.skills.constants import PRIVACY_VIOLATION_PATTERNS  # type: ignore[import-untyped]
    for t in turns:
        if t.get("speaker") != "agent":
            continue
        text = t.get("text", "")
        for pat in PRIVACY_VIOLATION_PATTERNS:
            if re.search(pat, text):
                return True
    return False


def _greeting_rationale(g: dict[str, Any]) -> str:
    """#1 첫인사 rationale 생성."""
    missing: list[str] = []
    elements = g.get("elements", {})
    if not elements.get("greeting"):
        missing.append("인사말")
    if not elements.get("affiliation"):
        missing.append("소속")
    if not elements.get("agent_name"):
        missing.append("상담사명")
    if missing:
        return f"누락: {', '.join(missing)}"
    return "3요소(인사말/소속/상담사명) 모두 충족"


def _closing_rationale(c: dict[str, Any]) -> str:
    """#2 끝인사 rationale 생성."""
    missing: list[str] = []
    elements = c.get("elements", {})
    if not elements.get("additional_inquiry"):
        missing.append("추가문의 확인")
    if not elements.get("closing_greeting"):
        missing.append("끝인사")
    if not elements.get("agent_name"):
        missing.append("상담사명")
    if missing:
        return f"누락: {', '.join(missing)}"
    return "3요소(추가문의/끝인사/상담사명) 모두 충족"
