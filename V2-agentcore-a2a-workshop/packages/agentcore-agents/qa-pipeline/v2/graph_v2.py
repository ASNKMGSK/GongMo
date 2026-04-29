# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V2 QA Pipeline — 4-Layer LangGraph (Dev1 주도, PL 검토).

설계서 p9 § 4 4-Layer 구조 준수:
    START → Layer 1 (전처리, 단일 노드)
          → Layer 2 (Sub Agent 8개, Send fan-out 병렬)
          → Layer 2 barrier (8 Sub Agent 완료 대기)
          → Layer 3 (Orchestrator V2, 단일 노드)
          → Layer 4 (Post-processing, 단일 노드)
          → END

Short-circuit 2종:
    A. `preprocessing.quality.unevaluable=True`
        → Layer 1 직후 Layer 2/3 스킵, Layer 4 (T3 라우팅) 직결.
    B. `state.plan.skip_phase_c_and_reporting=True`
        → Layer 3 의 consistency/grade 스킵 + Layer 4 스킵 후 END.
        (V1 orchestrator 의 동명 플래그 의미 유지 — 프롬프트 튜닝 배치용.)

V1 자산 재활용:
    V1 `graph.py::_make_tracked_node` 를 import 해 각 노드에 트레이스 래퍼 적용.
    V1 원본 수정 없음.

Sub Agent optional loading:
    Dev2 Group A + Dev3 Group B 구현 진행 중. 로딩 실패 시 skeleton 모드로 fallback
    — graph 는 compile 되되 해당 Sub Agent 는 mock placeholder 로 동작.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable


# qa-pipeline 루트를 path 에 — V1 nodes 및 v2 import 모두 가능
_QA_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _QA_PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _QA_PIPELINE_DIR)


from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Send  # noqa: E402

# V1 트레이스 래퍼 재활용 (import only)
from graph import _make_tracked_node  # type: ignore[import-untyped]  # noqa: E402

from v2.layer1.node import layer1_node  # noqa: E402
from v2.layer3.node import layer3_node  # noqa: E402
from v2.schemas.state_v2 import QAStateV2  # noqa: E402


logger = logging.getLogger(__name__)


# ===========================================================================
# Sub Agent 레지스트리 (Layer 2) — Dev2/Dev3 구현 optional loading
# ===========================================================================

SUB_AGENT_NAMES: tuple[str, ...] = (
    # Group A (Dev2)
    "greeting",
    "listening_comm",
    "language",
    "needs",
    # Group B (Dev3)
    "explanation",
    "proactiveness",
    "work_accuracy",
    "privacy",
)


# ===========================================================================
# 부서특화 Sub Agent (신한카드 5개 부서 — Phase 4, 2026-04-28)
# ===========================================================================
# tenant_id="shinhan" + team_id 조합 시 base 8개에 추가 fan-out.
# work_accuracy 는 부서특화 *_accuracy 노드로 대체 — frontend 와 정합 (lib/pipeline.ts).
# DEPT_SUB_AGENT_NAMES 는 모든 부서 노드 ID 의 union (graph 등록 시 사용).

try:
    from v2.agents.shinhan_dept.registry import (  # noqa: E402
        DEPT_NODE_REGISTRY,
        get_dept_nodes_for_tenant,
    )
    DEPT_SUB_AGENT_NAMES: tuple[str, ...] = tuple(DEPT_NODE_REGISTRY.keys())
except Exception as _e:  # pragma: no cover — registry 로드 실패 시 비활성
    logger.warning("shinhan_dept registry 로드 실패 — 부서특화 노드 비활성: %s", _e)
    DEPT_NODE_REGISTRY = {}
    DEPT_SUB_AGENT_NAMES = ()

    def get_dept_nodes_for_tenant(tenant_id: str, team_id: str | None) -> list[str]:  # type: ignore[no-redef]
        return []


# 부서특화 노드 활성 시 base 8 중에서 work_accuracy 는 hidden (frontend 와 정합).
# state.tenant_id + state.team_id 에 따라 router 가 동적으로 결정.
def _resolve_active_sub_agents(state: dict[str, Any]) -> list[str]:
    """state 의 site_id (=tenant_id) + department 로 활성화할 sub-agent 이름 리스트 반환.

    - shinhan + 부서 매칭 (collection/review/crm/consumer/compliance)
        → base 7 (work_accuracy 제외) + 부서 노드들
    - 그 외 → base 8 (기존 동작 유지)
    """
    # site_id 가 신 필드, tenant_id 가 레거시 alias — 둘 다 fallback
    tenant_id = state.get("site_id") or state.get("tenant_id") or "generic"
    department = (
        state.get("department")
        or state.get("team_id")  # 레거시
        or (state.get("preprocessing") or {}).get("department")
        or (state.get("metadata") or {}).get("department")
    )
    dept_nodes = get_dept_nodes_for_tenant(tenant_id, department)
    if not dept_nodes:
        return list(SUB_AGENT_NAMES)
    # 부서 노드 활성 시 work_accuracy 는 *_accuracy 로 대체
    base = [n for n in SUB_AGENT_NAMES if n != "work_accuracy"]
    return base + dept_nodes


def _load_sub_agents() -> dict[str, Callable]:
    """Dev2/Dev3 Sub Agent 를 state-dict adapter 로 감싸 로딩.

    각 Sub Agent 는 keyword-only 인자 (`*, preprocessing=..., llm_backend=...`)
    형태라 LangGraph 노드 (state → dict) 로 직접 쓸 수 없다. `_adapt_sub_agent()`
    가 state dict 에서 필요한 필드를 추출해 kwargs 로 전달한다.

    구현 미완료 Sub Agent 는 `_mock_sub_agent_factory(name)` 로 대체.
    """
    registry: dict[str, Callable] = {}
    failures: list[str] = []

    # Group A — keyword-only: preprocessing, llm_backend, bedrock_model_id
    try:
        from v2.agents.group_a import (  # noqa: WPS433
            greeting_sub_agent,
            language_sub_agent,
            listening_comm_sub_agent,
            needs_sub_agent,
        )
        registry["greeting"] = _adapt_group_a("greeting", greeting_sub_agent)
        registry["listening_comm"] = _adapt_group_a("listening_comm", listening_comm_sub_agent)
        registry["language"] = _adapt_group_a("language", language_sub_agent)
        registry["needs"] = _adapt_group_a("needs", needs_sub_agent)
    except Exception as e:  # pragma: no cover — skeleton fallback
        failures.append(f"group_a: {e}")

    # Group B — keyword-only: transcript, assigned_turns, consultation_type, ...
    group_b_imports = {
        "explanation": ("v2.agents.group_b.explanation", "explanation_agent"),
        "proactiveness": ("v2.agents.group_b.proactiveness", "proactiveness_agent"),
        "work_accuracy": ("v2.agents.group_b.work_accuracy", "work_accuracy_agent"),
        "privacy": ("v2.agents.group_b.privacy", "privacy_agent"),
    }
    for name, (module_path, attr) in group_b_imports.items():
        try:
            module = __import__(module_path, fromlist=[attr])
            registry[name] = _adapt_group_b(name, getattr(module, attr))
        except Exception as e:  # pragma: no cover — skeleton fallback
            failures.append(f"{name}: {e}")

    # 미로딩 Sub Agent 는 mock 으로 대체
    for name in SUB_AGENT_NAMES:
        if name not in registry:
            registry[name] = _mock_sub_agent_factory(name)

    # 부서특화 Sub Agent (shinhan) — 동일한 group_b adapter 패턴 사용
    for dept_node_id in DEPT_SUB_AGENT_NAMES:
        try:
            from v2.agents.shinhan_dept import get_dept_agent  # noqa: WPS433
            dept_fn = get_dept_agent(dept_node_id)
            if dept_fn is None:
                registry[dept_node_id] = _mock_sub_agent_factory(dept_node_id)
                continue
            registry[dept_node_id] = _adapt_group_b(dept_node_id, dept_fn)
        except Exception as e:  # pragma: no cover — fallback to mock
            failures.append(f"{dept_node_id}: {e}")
            registry[dept_node_id] = _mock_sub_agent_factory(dept_node_id)

    if failures:
        logger.warning("_load_sub_agents: %d sub-agent(s) using mock — %s", len(failures), failures)
    else:
        logger.info(
            "_load_sub_agents: %d base + %d dept sub-agents loaded with adapters",
            len(SUB_AGENT_NAMES), len(DEPT_SUB_AGENT_NAMES),
        )

    return registry


def _adapt_group_a(name: str, fn: Callable) -> Callable:
    """Group A Sub Agent state-dict adapter.

    Sub Agent 시그니처: `async def X_sub_agent(*, preprocessing, llm_backend, bedrock_model_id, tenant_id?)`
    반환: SubAgentResponse 또는 dict {evaluations, wiki_updates, ...}

    LangGraph 에는 `evaluations: list` append 만 반영.
    Sub Agent 가 tenant_id 인자를 수용하는 경우에만 전달 (시그니처 검사).

    ★ Option A (2026-04-24): sub-agent 평가 완료 직후 inline debate 실행 — layer2_barrier
    대기 없이 per-node 병렬 토론. 각 sub-agent 가 다른 sub-agent 와 병렬 실행되므로,
    sub-agent 자체의 토론도 자동으로 병렬화됨.
    """
    import inspect

    try:
        sig = inspect.signature(fn)
        param_names = set(sig.parameters.keys())
    except (TypeError, ValueError):
        param_names = set()

    async def _wrapped(state: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "preprocessing": state.get("preprocessing") or {},
            "llm_backend": state.get("llm_backend"),
            "bedrock_model_id": state.get("bedrock_model_id"),
        }
        # tenant_id 는 sub agent 가 인자로 받는 경우에만 전달 (구버전 호환).
        if not param_names or "tenant_id" in param_names:
            kwargs["tenant_id"] = state.get("tenant_id", "generic")
        try:
            result = await fn(**kwargs)
        except Exception:  # pragma: no cover — skeleton 회복성
            logger.exception("group_a sub-agent %s failed — emitting mock fallback", name)
            return await _mock_sub_agent_factory(name)(state)
        update = _normalize_sub_agent_output(name, result)
        return await _run_inline_debate(state, update, agent_name=name)

    _wrapped.__name__ = f"{name}_adapted"
    return _wrapped


def _adapt_group_b(name: str, fn: Callable) -> Callable:
    """Group B Sub Agent state-dict adapter.

    Sub Agent 시그니처 변형 다수 — 공통 인자만 전달하고 kwargs 로 흡수.
    """
    import inspect

    try:
        sig = inspect.signature(fn)
        param_names = set(sig.parameters.keys())
    except (TypeError, ValueError):
        param_names = set()

    async def _wrapped(state: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        preprocessing = state.get("preprocessing") or {}

        # 가능한 인자들 — Sub Agent 가 받는 것만 선별 전달
        candidates: dict[str, Any] = {
            "preprocessing": preprocessing,
            "transcript": state.get("transcript", ""),
            "assigned_turns": (preprocessing.get("agent_turn_assignments", {})
                               .get(name, {}).get("turns", [])),
            "consultation_type": state.get("consultation_type", "general"),
            "intent_summary": state.get("intent_summary") or preprocessing.get("intent_detail"),
            "rule_pre_verdicts": preprocessing.get("rule_pre_verdicts"),
            "llm_backend": state.get("llm_backend"),
            "bedrock_model_id": state.get("bedrock_model_id"),
            # tenant_id 누락 시 work_accuracy 의 retrieve_knowledge 가 default "generic" 으로 폴백
            # → kolon manual.md 못 보고 #15 unevaluable 로 떨어지는 버그 방지.
            "tenant_id": state.get("tenant_id", "generic"),
        }
        for k, v in candidates.items():
            if not param_names or k in param_names:
                kwargs[k] = v

        try:
            result = await fn(**kwargs)
        except Exception:  # pragma: no cover
            logger.exception("group_b sub-agent %s failed — emitting mock fallback", name)
            return await _mock_sub_agent_factory(name)(state)
        update = _normalize_sub_agent_output(name, result)
        return await _run_inline_debate(state, update, agent_name=name)

    _wrapped.__name__ = f"{name}_adapted"
    return _wrapped


# ★ 전역 debate 동시 실행 semaphore — Bedrock ThrottlingException 방지.
# 기본 1 (직렬). 환경변수 QA_DEBATE_GLOBAL_PARALLEL 로 상향 가능.
# sub-agent 는 8개 병렬로 LLM 호출 중이라 debate 까지 겹치면 TPM 즉시 초과.
_debate_global_sem: Any = None  # asyncio.Semaphore — 첫 호출 시 lazy init


def _get_debate_global_sem() -> Any:
    """Lazy-init 전역 debate semaphore. 이벤트 루프 안에서 호출되어야 함."""
    import asyncio as _asyncio
    import os as _os

    global _debate_global_sem
    if _debate_global_sem is None:
        try:
            n = int(_os.environ.get("QA_DEBATE_GLOBAL_PARALLEL", "1"))
        except (TypeError, ValueError):
            n = 1
        n = max(1, n)
        _debate_global_sem = _asyncio.Semaphore(n)
        logger.info(
            "★ debate global semaphore 초기화 · QA_DEBATE_GLOBAL_PARALLEL=%d "
            "(1=직렬, Bedrock TPM 보호)",
            n,
        )
    return _debate_global_sem


async def _run_inline_debate(
    state: dict[str, Any], update: dict[str, Any], *, agent_name: str
) -> dict[str, Any]:
    """★ Option A — sub-agent 완료 직후 inline debate 실행 (async wrapper).

    update.evaluations 에 있는 각 item 에 대해 QA_DEBATE_ENABLED 라면 즉시 토론 실행.
    결과:
      - update.evaluations 의 score 는 debate merged 로 덮어쓰기
      - update.debates = {item_no: DebateRecord.model_dump()} 병합
      - update._debate_events = [{event, data}, ...] 병합 (server_v2 가 SSE 로 중계)

    ★ 직렬화: 전역 asyncio.Semaphore(QA_DEBATE_GLOBAL_PARALLEL=1) 로 한 번에 1개 sub-agent
    의 debate 만 진행. 8개 sub-agent 가 병렬로 돌아도 debate 는 줄을 서서 차례대로.
    Bedrock ThrottlingException 방지 목적 — 병렬 시 "HTTP 429: Too many tokens" 으로
    fallback_median 줄줄이 실패하는 이슈 수정.

    ★ run_debate 는 sync (AG2 initiate_chat + boto3 Bedrock 동기 호출). asyncio.to_thread
    로 별도 스레드에서 실행하여 이벤트 루프 비워둠.
    """
    import asyncio as _asyncio

    from v2.debate.node import (
        apply_debate_to_evaluations,
        is_debate_enabled,
        run_debates_for_evaluations,
    )

    if not is_debate_enabled():
        return update
    evaluations = update.get("evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        return update

    sem = _get_debate_global_sem()
    async with sem:
        logger.info("🎭 [inline-debate agent=%s] semaphore 획득 — 토론 시작", agent_name)
        try:
            debates, events = await _asyncio.to_thread(
                run_debates_for_evaluations,
                state=state,
                evaluations=evaluations,
                agent_name=agent_name,
            )
        except Exception:
            logger.exception("inline debate [%s]: run_debates_for_evaluations 실패", agent_name)
            return update
        finally:
            logger.info("🎭 [inline-debate agent=%s] semaphore 해제", agent_name)

    if not debates:
        return update
    # evaluations score 덮어쓰기 + debates / events 병합
    merged_evals = apply_debate_to_evaluations(evaluations=evaluations, debates=debates)
    out = {**update, "evaluations": merged_evals, "debates": debates}
    if events:
        out["_debate_events"] = events
    return out


def _normalize_sub_agent_output(name: str, result: Any) -> dict[str, Any]:
    """Sub Agent 반환값을 LangGraph state update dict 로 정규화.

    허용 포맷:
      - dict{evaluations, wiki_updates, ...} — 그대로 pass through
      - (SubAgentResponse, wiki_updates) 튜플 — items 를 evaluations 로 펼침
      - SubAgentResponse (pydantic/dict) — items 를 evaluations 로 펼침
      - 기타 — 로그 후 빈 업데이트
    """
    if result is None:
        return {}

    # (SubAgentResponse, wiki_updates) 튜플
    if isinstance(result, tuple) and len(result) == 2:
        response, wiki = result
        update = _unwrap_response(name, response)
        if isinstance(wiki, dict):
            for k, v in wiki.items():
                update.setdefault(k, v)
        return update

    # dict — evaluations 키 보유 시 그대로
    if isinstance(result, dict):
        if "evaluations" in result:
            return result
        # items[] 를 가진 응답 (SubAgentResponse dict 형태) → evaluations 로 전환
        if "items" in result:
            return _unwrap_response(name, result)
        # 그 외는 pass-through
        return result

    # pydantic 모델일 경우 dict 로
    if hasattr(result, "model_dump"):
        return _unwrap_response(name, result.model_dump())

    logger.warning("_normalize_sub_agent_output[%s]: unexpected result type %s", name, type(result))
    return {}


def _unwrap_response(name: str, response: Any) -> dict[str, Any]:
    """SubAgentResponse(items[]) → evaluations 포맷 변환."""
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    if not isinstance(response, dict):
        return {}

    items = response.get("items") or []
    evaluations = []
    for item in items:
        evaluations.append({
            "status": "success",
            "agent_id": f"{name}-agent",
            "evaluation": item,
        })
    return {"evaluations": evaluations}


def _mock_sub_agent_factory(name: str) -> Callable:
    """구현 미완료 Sub Agent 의 placeholder. skeleton smoke 용.

    각 Sub Agent 담당 항목에 대해 score=max, evaluation_mode="skipped" 로
    evaluations 에 append. 실 구현 교체 전까지 graph 가 compile + 동작 가능.
    """
    from v2.schemas.enums import CATEGORY_META

    # Sub Agent name → CategoryKey 매핑
    _name_to_category = {
        "greeting": "greeting_etiquette",
        "listening_comm": "listening_communication",
        "language": "language_expression",
        "needs": "needs_identification",
        "explanation": "explanation_delivery",
        "proactiveness": "proactiveness",
        "work_accuracy": "work_accuracy",
        "privacy": "privacy_protection",
    }

    category_key = _name_to_category.get(name)
    if not category_key or category_key not in CATEGORY_META:
        item_numbers: list[int] = []
    else:
        item_numbers = list(CATEGORY_META[category_key]["items"])  # type: ignore[assignment]

    async def _mock(state: dict[str, Any]) -> dict[str, Any]:
        from v2.contracts.rubric import max_score_of
        logger.debug("mock_sub_agent[%s]: emitting skipped placeholders for items %s", name, item_numbers)
        evaluations = []
        for item_num in item_numbers:
            max_s = max_score_of(item_num)
            evaluations.append({
                "status": "success",
                "agent_id": f"{name}-agent",
                "evaluation": {
                    "item_number": item_num,
                    "item_name": f"mock_{item_num}",
                    "max_score": max_s,
                    "score": max_s,
                    "evaluation_mode": "skipped",  # mock 은 skipped 로 표시
                    "judgment": "[MOCK] skeleton placeholder — 실 Sub Agent 로 교체 예정",
                    "evidence": [],
                    "deductions": [],
                    "confidence": 0.5,
                    "flag": "mock_sub_agent",
                    "mandatory_human_review": False,
                    "force_t3": item_num in (9, 17, 18),
                },
            })
        return {"evaluations": evaluations}

    _mock.__name__ = f"mock_{name}"
    return _mock


# ===========================================================================
# Layer 4 노드 (Dev5) — optional loading
# ===========================================================================


def _load_layer4_node() -> Callable:
    """Layer 4 report_generator_v2 로딩. 실패 시 mock 으로 대체."""
    try:
        from v2.layer4 import report_generator_node  # type: ignore[attr-defined]
        return report_generator_node
    except Exception as e:  # pragma: no cover
        logger.warning("layer4: report_generator_node 로딩 실패 — mock 사용 (%s)", e)

        def _mock_layer4(state: dict[str, Any]) -> dict[str, Any]:
            return {
                "report": {
                    "mock": True,
                    "summary": {"total_score": state.get("orchestrator", {}).get("final_score", {}).get("after_overrides", 0)},
                },
            }
        return _mock_layer4


# ===========================================================================
# Short-circuit / skip 라우터
# ===========================================================================


def _route_after_layer1(state: dict[str, Any]) -> list[Send] | str:
    """Layer 1 직후 라우팅.

    - quality.unevaluable=True → Layer 4 직결 (Layer 2/3 skip)
    - 그 외 → 활성 Sub Agent 들로 Send fan-out (tenant + team 기반 동적 결정)
    """
    preprocessing = state.get("preprocessing") or {}
    if preprocessing.get("quality", {}).get("unevaluable"):
        logger.info("_route_after_layer1: unevaluable=True → short-circuit to layer4")
        return "layer4"

    active = _resolve_active_sub_agents(state)
    logger.info(
        "_route_after_layer1: fan-out to %d sub-agents (tenant=%s team=%s) → %s",
        len(active), state.get("tenant_id"), state.get("team_id"), active,
    )
    return [Send(name, state) for name in active]


def _route_after_layer2_barrier(state: dict[str, Any]) -> list[str]:
    """Layer 2 barrier 후 라우팅 — 두 갈래 병렬 fan-out.

    1. `layer3` — 기존 4-Layer 파이프라인 (debate → layer4 → gt → hitl)
    2. `ksqi_orchestrator` — 신규 KSQI 9개 항목 평가 그룹

    두 분기는 별도 종료점 (각각 END) 으로 향하며, LangGraph 가 양쪽 모두 완료되어야
    전체 graph 가 종료된다. KSQI 결과는 ksqi_report 가 별도 ksqi_report state 필드로 출력.
    """
    return ["layer3", "ksqi_orchestrator"]


def _route_after_layer3(state: dict[str, Any]) -> str:
    """Layer 3 완료 후 라우팅.

    - `state.plan.skip_phase_c_and_reporting=True` → combined_report (Layer 4 chain 스킵)
    - 그 외 → Layer 4
    skip 케이스에서도 combined_report 까지는 거쳐야 KSQI 분기와 합류해 그래프가 종료됨.
    """
    plan = state.get("plan") or {}
    if plan.get("skip_phase_c_and_reporting"):
        logger.info("_route_after_layer3: skip_phase_c_and_reporting=True → combined_report")
        return "combined_report"
    return "layer4"


# ===========================================================================
# Layer 2 barrier 노드
# ===========================================================================


def _layer2_barrier(state: dict[str, Any]) -> dict[str, Any]:
    """Layer 2 Send fan-out 완료를 대기하는 barrier.

    LangGraph Send API 는 모든 Send 대상이 한 노드로 수렴할 때 자동으로 완료
    대기를 처리한다. 본 barrier 는 no-op 이지만 trace/log 용으로 존재.
    """
    evaluations = state.get("evaluations", []) or []
    logger.info("layer2_barrier: collected %d evaluations from sub-agents", len(evaluations))
    return {}


# ===========================================================================
# 그래프 빌더
# ===========================================================================


def build_graph_v2():
    """V2 QA 파이프라인 그래프 빌드 + compile.

    구조:
        START → layer1 → [fan-out 8 sub-agents] → layer2_barrier → layer3 → layer4 → END
                   ↓ unevaluable                                                 ↓ skip_phase_c
                 layer4                                                          END
    """
    builder = StateGraph(QAStateV2)

    # Layer 1 (단일 노드, tracked)
    builder.add_node("layer1", _make_tracked_node("layer1", layer1_node))

    # Layer 2 Sub Agents (optional loading)
    sub_agents = _load_sub_agents()
    for name, fn in sub_agents.items():
        builder.add_node(name, _make_tracked_node(name, fn))

    # Layer 2 barrier (Send 수렴점)
    builder.add_node("layer2_barrier", _make_tracked_node("layer2_barrier", _layer2_barrier))

    # Layer 3 (tracked)
    builder.add_node("layer3", _make_tracked_node("layer3", layer3_node))

    # Debate (Phase 2) — Layer 3 산출 persona_step_spread 기반 선택 실행.
    # QA_DEBATE_ENABLED=false 면 즉시 {} 반환, graph 구조는 그대로 유지.
    from v2.debate.node import debate_node
    builder.add_node("debate", _make_tracked_node("debate", debate_node))

    # Layer 4 (tracked — optional)
    layer4_node = _load_layer4_node()
    builder.add_node("layer4", _make_tracked_node("layer4", layer4_node))

    # GT Comparison (tracked — Layer 4 후속, AI vs GT 점수 비교)
    from v2.layer4.gt_comparison import gt_comparison_node
    builder.add_node("gt_comparison", _make_tracked_node("gt_comparison", gt_comparison_node))

    # GT Evidence Comparison (tracked — gt_comparison 후속, LLM 으로 사람 vs AI 근거 비교)
    from v2.layer4.gt_evidence_comparison import gt_evidence_comparison_node
    builder.add_node(
        "gt_evidence_comparison",
        _make_tracked_node("gt_evidence_comparison", gt_evidence_comparison_node),
    )

    # HITL Queue Populator (tracked — 검수 필요 항목을 human_reviews 에 자동 적재)
    from v2.hitl.queue_populator import hitl_queue_populator_node
    builder.add_node(
        "hitl_queue_populator",
        _make_tracked_node("hitl_queue_populator", hitl_queue_populator_node),
    )

    # ── KSQI 그룹 (Layer 2 barrier 직후 layer3 와 병렬 fan-out) ──
    from v2.nodes.ksqi import (
        KSQI_NODE_FUNCS,
        KSQI_NODES,
        ksqi_barrier_node,
        ksqi_orchestrator_node,
        ksqi_report_node,
        route_ksqi_fanout,
    )

    builder.add_node(
        "ksqi_orchestrator",
        _make_tracked_node("ksqi_orchestrator", ksqi_orchestrator_node),
    )
    for ksqi_name, ksqi_fn in KSQI_NODE_FUNCS.items():
        builder.add_node(ksqi_name, _make_tracked_node(ksqi_name, ksqi_fn))
    builder.add_node(
        "ksqi_barrier",
        _make_tracked_node("ksqi_barrier", ksqi_barrier_node),
    )
    builder.add_node(
        "ksqi_report",
        _make_tracked_node("ksqi_report", ksqi_report_node),
    )

    # ── Combined Report — 두 분기 (Layer 4 chain / KSQI chain) 모두 완료 후 통합 보고서 ──
    from v2.nodes.combined_report import combined_report_node
    builder.add_node(
        "combined_report",
        _make_tracked_node("combined_report", combined_report_node),
    )

    # === 엣지 ===
    builder.add_edge(START, "layer1")

    # Layer 1 → (Layer 2 fan-out | layer4)
    # base 8 + 부서특화 노드 모두 fan-out 후보로 등록 — 실제 활성은 router 에서 결정
    _all_sub_agents: tuple[str, ...] = SUB_AGENT_NAMES + DEPT_SUB_AGENT_NAMES
    builder.add_conditional_edges(
        "layer1",
        _route_after_layer1,
        {**{name: name for name in _all_sub_agents}, "layer4": "layer4"},
    )

    # 각 Sub Agent → Layer 2 barrier (base + dept)
    for name in _all_sub_agents:
        builder.add_edge(name, "layer2_barrier")

    # Layer 2 barrier → [Layer 3 + KSQI Orchestrator] 병렬 분기
    builder.add_conditional_edges(
        "layer2_barrier",
        _route_after_layer2_barrier,
        {"layer3": "layer3", "ksqi_orchestrator": "ksqi_orchestrator"},
    )

    # KSQI 분기: orchestrator → fan-out 9 nodes → barrier → report → END
    builder.add_conditional_edges(
        "ksqi_orchestrator",
        route_ksqi_fanout,
        {name: name for name in KSQI_NODES},
    )
    for ksqi_name in KSQI_NODES:
        builder.add_edge(ksqi_name, "ksqi_barrier")
    builder.add_edge("ksqi_barrier", "ksqi_report")
    builder.add_edge("ksqi_report", "combined_report")

    # combined_report → END (양쪽 분기 모두 도착해야 진행됨)
    builder.add_edge("combined_report", END)

    # Layer 3 → debate (토론 노드는 무조건 1회 경유. QA_DEBATE_ENABLED=false 또는 spread 미달 시
    # 내부에서 즉시 반환 — graph 경로는 동일 유지).
    builder.add_edge("layer3", "debate")

    # debate → (Layer 4 | combined_report) — skip_phase_c_and_reporting 플래그는 layer3 출력의 plan 을 보기 때문에
    # debate 이후에도 동일한 라우팅 규칙 적용. skip 케이스에서도 combined_report 까지 도달해야
    # KSQI 분기와 합류해 graph 가 정상 종료됨.
    builder.add_conditional_edges(
        "debate",
        _route_after_layer3,
        {"layer4": "layer4", "combined_report": "combined_report"},
    )

    # Layer 4 → GT Comparison → GT Evidence Comparison → HITL Queue Populator → combined_report
    builder.add_edge("layer4", "gt_comparison")
    builder.add_edge("gt_comparison", "gt_evidence_comparison")
    builder.add_edge("gt_evidence_comparison", "hitl_queue_populator")
    builder.add_edge("hitl_queue_populator", "combined_report")

    graph = builder.compile()
    logger.info(
        "V2 graph compiled: START → layer1 → [fan-out %d base + %d dept sub-agents] → layer2_barrier "
        "→ {layer3 → debate → layer4 → gt_comparison → gt_evidence_comparison → hitl_queue_populator | "
        "ksqi_orchestrator → [fan-out 9 KSQI nodes] → ksqi_barrier → ksqi_report} "
        "→ END (parallel branches). short-circuit: unevaluable, skip_phase_c_and_reporting. "
        "debate node: spread-gated via QA_DEBATE_ENABLED / QA_DEBATE_SPREAD_THRESHOLD. "
        "dept routing: tenant_id=shinhan + team_id 매칭 시 *_accuracy 등 부서특화 노드 fan-out.",
        len(SUB_AGENT_NAMES), len(DEPT_SUB_AGENT_NAMES),
    )
    return graph


# ===========================================================================
# 편의 — 모듈 레벨 싱글톤
# ===========================================================================


_graph_v2_singleton = None


def get_graph_v2():
    """싱글톤 graph 반환 (테스트 / 배치 재사용)."""
    global _graph_v2_singleton
    if _graph_v2_singleton is None:
        _graph_v2_singleton = build_graph_v2()
    return _graph_v2_singleton
