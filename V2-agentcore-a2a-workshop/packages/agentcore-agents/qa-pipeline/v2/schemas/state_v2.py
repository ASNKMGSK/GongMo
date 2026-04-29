# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
QAStateV2 — V2 파이프라인 LangGraph 공유 상태 (Phase A1, Dev5 주관).

V1 `state.QAState` 와 호환성을 유지하면서 V2 4-Layer 구조에 필요한 필드를 확장.

설계 원칙:
 1. V1 필드는 그대로 유지 (transcript / evaluations / parsed_dialogue / ...)
    → V1 노드(레거시) 를 v2 graph 에서도 재사용 가능.
 2. 추가 필드는 Layer 별로 namespacing 된 dict 안에 격납.
    preprocessing / orchestrator / post_processing / routing / confidence_signals.
 3. operator.add 리듀서 필드 (evaluations, completed_nodes, deduction_log 등) 유지.

Dev 간 합의 포인트:
 - Dev1 (Layer1/Layer3): preprocessing / orchestrator 필드 소유
 - Dev2/Dev3 (Sub Agent): evaluations 에 SubAgentResponse.items append
 - Dev4 (RAG): confidence_signals.rag_* 필드에 기여
 - Dev5 (Layer4): post_processing / routing / confidence_signals 소유
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


# ---------------------------------------------------------------------------
# 주의: 순환 import 방지를 위해 enum/io 스키마는 TYPE_CHECKING 블록 안에서만 import.
# 런타임에는 dict[str, Any] 로 느슨하게 보관하고,
# Dev5 의 Layer 4 구현 시에만 qa_output_v2.py 로 변환/검증.
# ---------------------------------------------------------------------------


class QAStateV2(TypedDict, total=False):
    """V2 QA Pipeline 공유 상태.

    V1 QAState 상위 호환. V1 노드에 그대로 전달 가능.
    LangGraph StateGraph(QAStateV2) 로 구동.
    """

    # =====================================================================
    # [V1 호환 — 입력 필드]
    # =====================================================================
    transcript: str
    consultation_type: str      # V1 호환. V2 는 preprocessing.intent_type 이 상위.
    customer_id: str
    session_id: str
    llm_backend: str
    bedrock_model_id: str | None

    # =====================================================================
    # [V2 신규 — 입력 필드]
    # =====================================================================
    consultation_id: str        # 설계서 §11.1 최상위 식별자. session_id 와 별도.

    # 3단계 멀티테넌트 (2026-04-24 도입). 대분류(site) → 중분류(channel) → 소분류(department).
    # site_id   : 업체 코드 (예: "kolon" / "cartgolf" / "generic")
    # channel   : "inbound" | "outbound"  — 원본 JSON 의 SITE_CD 값에서 매핑
    # department: 업체×채널 하위 부서 자유 문자열 (예: "cs" / "retention" / "default")
    # tenant_key: f"{site_id}:{channel}:{department}" — 캐시 키/AOSS 필터용 파생값.
    # tenant_id : 레거시 — site_id 의 alias. 당분간 server 가 site_id 로부터 자동 채움.
    #             새 코드는 site_id 를 읽을 것.
    site_id: str
    channel: str
    department: str
    tenant_key: str
    tenant_id: str

    evaluated_at: str           # ISO-8601 UTC. Layer 1 진입 시 세팅.
    stt_metadata: dict[str, Any]          # STTMetadataBlock 호환
    masking_format: dict[str, Any]        # MaskingFormatBlock 호환
    versions: dict[str, Any]              # VersionsBlock 호환

    # =====================================================================
    # [Layer 1 — Dev1 영역]
    # =====================================================================
    # preprocessing 은 PreprocessingOutput (contracts/phase_a1_interface_draft.py) 구조.
    # - quality / segments / pii / deduction_triggers / rule_pre_verdicts / agent_turn_assignments / turns
    preprocessing: dict[str, Any]

    # V1 호환 (Dev1 이 preprocessing 에서 복사 — 레거시 노드 지원용)
    parsed_dialogue: dict[str, Any]
    agent_turn_assignments: dict[str, Any]

    # =====================================================================
    # [Layer 2 — Dev2/Dev3 영역]
    # =====================================================================
    # evaluations: V1 호환 append-only.
    # V2 에서는 각 원소가 SubAgentResponse.items 풀린 형태 또는 V1 EvaluationResult 포맷 모두 허용.
    # Layer 3 가 정규화.
    evaluations: Annotated[list[dict[str, Any]], operator.add]

    # Sub Agent 응답 원본 (SubAgentResponse 8건). Layer 3 diagnostics 입력.
    sub_agent_responses: Annotated[list[dict[str, Any]], operator.add]

    # V1 호환 — Wiki 공유 메모리
    deduction_log: Annotated[list[dict[str, Any]], operator.add]
    intent_summary: dict[str, Any]
    accuracy_verdict: dict[str, Any]
    flags: dict[str, Any]

    # =====================================================================
    # [Layer 3 — Dev1 영역]
    # =====================================================================
    # orchestrator 는 OrchestratorOutputV2 구조.
    # - category_scores / overrides_applied / consistency_flags / grade / final_evaluations
    orchestrator: dict[str, Any]

    # V1 호환 (Dev1 이 orchestrator 에서 복사)
    verification: dict[str, Any]
    score_validation: dict[str, Any]

    # =====================================================================
    # [Layer 4 — Dev5 영역]
    # =====================================================================
    # confidence_signals: 항목별 4 신호 원천값 저장.
    # 구조: {item_number: {llm_self, rule_llm_agreement, rag_stdev, evidence_quality, weighted}}
    confidence_signals: dict[int, dict[str, Any]]

    # routing: Tier 결정 결과.
    # 구조: RoutingBlock 과 호환 — {decision, hitl_driver, priority_flags, estimated_review_time_min, tier_reasons}
    routing: dict[str, Any]

    # post_processing: evidence 정제 / drift 로깅 등 부가 산출.
    post_processing: dict[str, Any]

    # report: 최종 QAOutputV2 직렬화 결과.
    # report_generator_v2 가 pydantic 모델 → dict 로 덤프해서 저장.
    report: dict[str, Any]

    # GT 비교 (gt_sample_id 가 주입된 경우만 활성).
    # gt_comparison: 점수 비교 — Layer 4 후속, gt_comparison_node 가 채움.
    # gt_evidence_comparison: 근거 LLM 비교 — gt_evidence_comparison_node 가 채움.
    gt_sample_id: str
    gt_comparison: dict[str, Any]
    gt_evidence_comparison: dict[str, Any]

    # HITL Queue Populator — 파이프라인 종료 시 human_reviews 에 적재한 건수/항목 번호.
    # 구조: {"count": int, "item_numbers": list[int], "skipped": bool?}
    hitl_queue_populated: dict[str, Any]

    # =====================================================================
    # [KSQI — 별도 평가 그룹 (2026-04-27 추가)]
    # =====================================================================
    # KSQI (Korean Service Quality Index) — STT 텍스트 기반 9개 항목 자동평가.
    # Layer 2 barrier 직후 layer3 와 병렬로 fan-out 되며 별도 KSQI barrier 에서 수렴.
    # 결과는 기존 evaluations 와 분리 (다른 채점 체계: 결함 1건 = 항목 배점 전액 차감).
    # 구조: 각 원소 = {item_number, item_name, area, max_score, score, defect, evidence, rationale}
    ksqi_evaluations: Annotated[list[dict[str, Any]], operator.add]

    # KSQI 최종 보고서 — 환산 점수 + 우수콜센터 판정.
    # 구조: {
    #   "area_a": {raw, max, scaled, grade},  # 서비스 품질 (50점 → 100점 환산, 92↑ 우수)
    #   "area_b": {raw, max, scaled, grade},  # 공감 (30점 → 100점 환산, 80↑ 우수)
    #   "items": list[ksqi_evaluations 정렬],
    #   "summary": str,
    # }
    ksqi_report: dict[str, Any]

    # 통합 최종 보고서 — 두 분기 (기존 #1~#18 / KSQI 9) 모두 종료 후 한 artifact 로 묶음.
    # 두 보고서는 채점 체계가 다르므로 sub-section 으로 분리 보존, 상위에 메타·요약 추가.
    # 구조: {
    #   "consultation_id": str,
    #   "tenant": {site_id, channel, department},
    #   "evaluated_at": str,
    #   "existing": dict (state.report 의 요약 — grade, total_score, items 일부),
    #   "ksqi": dict (state.ksqi_report — area_a/area_b/overall/items/summary),
    #   "summary": str (양쪽 보고서를 한 문장으로 요약),
    # }
    combined_report: dict[str, Any]

    # =====================================================================
    # [Debate (Phase 2) — Dev4 영역]
    # =====================================================================
    # debates: AG2 3-페르소나 GroupChat 토론 결과. key=item_number (1~18).
    # 값은 v2.debate.schemas.DebateRecord.model_dump() 포맷 — CLAUDE.md 명시.
    # 발동 조건: Layer 3 reconciler 산출 `persona_step_spread >= QA_DEBATE_SPREAD_THRESHOLD`
    # (기본 3). `QA_DEBATE_ENABLED=false` 면 항상 {}.
    # ★ Option A (2026-04-24): sub-agent 별로 inline debate → 각 sub-agent 가 debates dict
    # 부분 반환. LangGraph 기본은 dict override, 병합해야 하므로 `a | b` 리듀서 적용.
    debates: Annotated[dict[int, dict[str, Any]], lambda a, b: {**(a or {}), **(b or {})}]

    # LangGraph state 에 임시 주입되는 SSE 콜백 — server_v2 가 debate_node 진입 직전에 set.
    # 타입상 Callable 이지만 TypedDict 호환 위해 Any 로 보관.
    _debate_on_event: Any

    # debate_node 가 노드 실행 중 발생시킨 SSE 이벤트 버퍼.
    # 각 원소: {"event": "debate_round_start"|"persona_turn"|"moderator_verdict"|"debate_final",
    #           "data": {...CLAUDE.md 명시 페이로드...}}
    # graph.astream 이 노드 완료 delta 로 server_v2 에 전달 → server 가 풀어서 개별 SSE 이벤트로 emit.
    # ★ Option A: inline debate 가 각 sub-agent 에서 events 를 반환 → list concat 리듀서 필요.
    _debate_events: Annotated[list[dict[str, Any]], operator.add]

    # V3 interactive discussion (ensemble 모드 — 2026-04-23 추가).
    # _discussion_auto_start=False 면 discussion_started 송출 후 gate 대기.
    # _discussion_gate_factory(discussion_id) → threading.Event. 프론트의 start 핸들러가 .set().
    _discussion_auto_start: bool
    _discussion_gate_factory: Any

    # =====================================================================
    # [운영/오케스트레이션 — V1 호환]
    # =====================================================================
    plan: dict[str, Any]
    current_phase: str          # "init" | "layer1_done" | "layer2_done" | "layer3_done" | "complete"
    next_node: str
    parallel_targets: list[str]
    completed_nodes: Annotated[list[str], operator.add]
    node_timings: Annotated[list[dict[str, Any]], operator.add]
    node_traces: Annotated[list[dict[str, Any]], operator.add]

    # =====================================================================
    # [에러 전파]
    # =====================================================================
    error: str | None
