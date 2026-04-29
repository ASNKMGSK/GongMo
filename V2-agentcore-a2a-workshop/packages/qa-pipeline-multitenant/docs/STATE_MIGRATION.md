# State Migration Guide: 단일 테넌트 → 멀티 테넌트

> **Scope**: `packages/agentcore-agents/qa-pipeline/state.py` (단일) → `packages/qa-pipeline-multitenant/qa-pipeline/state.py` (멀티)
> **Owner**: Dev3 (pipeline-state)
> **합의 문서**: `../ARCHITECTURE.md` 5절, 11절 4번

---

## 1. 핵심 변경 요약

| 항목 | 단일 테넌트 | 멀티 테넌트 |
|---|---|---|
| state 진입점 | `QAState(transcript=..., ...)` | `QAState(tenant=TenantContext, transcript=..., ...)` |
| 신규 필수 필드 | — | `tenant: {tenant_id, config, request_id}` |
| 노드 접근 방식 | `ctx.consultation_type`, `state.get(...)` | 추가로 `ctx.tenant_id`, `ctx.tenant_config`, `ctx.request_id` |
| 프롬프트 로드 | `load_prompt(name, backend=...)` | `load_prompt(name, tenant_id=..., backend=...)` |
| LLM 결과 메타 | `{status, agent_id, evaluation, ...}` | 위 + `tenant_id` (audit/trace) |
| 그래프 entry 검증 | 없음 | `orchestrator_node` 진입 시 `state["tenant"]` 누락이면 즉시 `ValueError` |

---

## 2. TenantContext 계약

```python
# state.py
class TenantContext(TypedDict):
    tenant_id: str      # 영문/숫자/언더스코어. JWT custom:tenant_id 에서 유래
    config: dict        # TenantConfig.to_dict() — Dev4 가 produce
    request_id: str     # 요청 추적 ID (로그/메트릭/감사 로그 상관용)

class QAState(TypedDict, total=False):
    tenant: TenantContext   # 필수 — 누락 시 orchestrator 가 즉시 차단
    # ... 기존 필드 (transcript, evaluations, verification, report 등)
```

`state["tenant"]` 는 **read-only** 이다. 전 노드는 이 필드를 읽기만 하고 절대
수정하지 않는다. (TypedDict 이므로 정적 보장이 없고 런타임 컨벤션이다.)

---

## 3. Dev1 라우터 — state 주입 책임

기존 단일 테넌트 라우터는 다음과 같다:

```python
# 단일 테넌트 (packages/agentcore-agents/qa-pipeline/routers/evaluate.py)
initial_state = {
    "transcript": body.transcript,
    "consultation_type": body.consultation_type or "general",
    ...
}
result = await graph.ainvoke(initial_state)
```

멀티 테넌트는 `tenant` 필드를 반드시 포함한다:

```python
# 멀티 테넌트 (권장 — tenant.store.get_config 자동 조회)
from state import build_initial_state

initial_state = build_initial_state(
    tenant_id=request.state.tenant_id,                      # 미들웨어가 설정
    request_id=request.state.request_id,                    # 추적용
    transcript=body.transcript,
    consultation_type=body.consultation_type or "general",
    customer_id=body.customer_id or "",
    session_id=body.session_id or "",
    llm_backend=body.llm_backend,
    bedrock_model_id=body.bedrock_model_id,
)
# tenant_config 미지정 → 내부에서 tenant.store.get_config(tenant_id).to_dict()
# 테넌트 미존재 시 KeyError — 라우터에서 404/403 매핑
result = await graph.ainvoke(initial_state)
```

**테스트/로컬 개발**에서 DynamoDB 조회를 피하려면 `tenant_config` 를 직접 주입:

```python
initial_state = build_initial_state(
    tenant_id="kolon_default",
    tenant_config={"display_name": "Kolon", "industry": "industrial"},  # 직접 주입
    request_id="test-req",
    transcript=sample,
)
```

`build_initial_state(...)` 는 `state.py` 가 제공하는 헬퍼 — 필드명 오타/키 누락 방지 + 테넌트 조회 일관성 보장. `config` 는 `to_dict()` snapshot 이라 `score_overrides` 의 키가 string 이다. int 로 복원이 필요하면 `TenantConfig.from_dict(state["tenant"]["config"])`.

---

## 4. 노드 작성 규약 (Dev3 범위)

### 4.1 ctx 사용 패턴 (권장)

```python
async def my_evaluate_node(state: QAState, ctx: NodeContext) -> dict:
    # 멀티테넌트 필드
    tid = ctx.tenant_id                          # str
    cfg = ctx.tenant_config                      # dict (TenantConfig.to_dict())
    req_id = ctx.request_id                      # str

    # 프롬프트 로드 — tenant_id 는 키워드 전용
    system_prompt = load_prompt(
        "item_XX_my_prompt",
        tenant_id=tid,
        include_preamble=True,
        backend=ctx.llm_backend,
    )

    # ... LLM 호출 ...

    return {
        "evaluations": [{
            "status": "success",
            "agent_id": "my-agent",
            "tenant_id": tid,                    # audit/trace 메타
            "evaluation": {...},
        }]
    }
```

### 4.2 state 직접 접근 (ctx 없는 레거시 노드)

`ctx` 를 받지 않는 노드(예: `consistency_check_node(state)`)는 다음 패턴:

```python
tenant_id = (state.get("tenant") or {}).get("tenant_id", "")
```

`state["tenant"]` 가 반드시 존재한다는 가정은 `orchestrator_node` 의 entry 가드가
보장하지만, `.get(...) or {}` 를 붙여 방어적으로 처리한다.

### 4.3 금지 사항

- `state["tenant"]` 에 write → **금지** (read-only 약속 위반)
- `tenant_id` 를 **위치 인자**로 전달 → **금지** (`load_prompt` 키워드 전용)
- `tenant_id` 를 하드코딩 → **금지** (반드시 state/ctx 에서 유래)
- 프롬프트 로드 시 `tenant_id=""` 빈 문자열로 호출 → **지양** (Dev4 의 기본 테넌트 폴백에 의존)

---

## 5. 실행 순서 — 변경 없음

단일 테넌트 파이프라인의 3-Phase 순서는 그대로다:

```
dialogue_parser
   ↓
Phase A (greeting || understanding || courtesy || incorrect_check || mandatory)
   ↓
Phase B1 (scope || work_accuracy)
   ↓
Phase B2 (proactiveness)
   ↓
Phase C (consistency_check || score_validation)
   ↓
report_generator
   ↓
END
```

> **절대 금지**: 파이프라인 순서 변경, 순차 노드 병렬화 제안 (`../ARCHITECTURE.md` 11절 / 프로젝트 원칙).

---

## 6. 테스트 및 검증

1. **tenant 누락 가드 테스트**: `state = {"transcript": "..."}` 로 graph 호출 → `ValueError` 발생해야 한다.
2. **tenant_id 전파 테스트**: evaluation 결과 각 항목의 `tenant_id` 필드가 설정되어야 한다.
3. **프롬프트 오버라이드 테스트**: `state["tenant"]["tenant_id"] = "kolon_default"` 로 호출 시 Dev4 로더가 테넌트 전용 프롬프트를 먼저 조회해야 한다.
4. **격리 회귀 테스트**: 서로 다른 tenant_id 의 요청을 동시 실행 시 교차 데이터가 없어야 한다 (Dev2 의 DB 격리 테스트와 통합).

---

## 7. 관련 파일

| 파일 | 역할 |
|---|---|
| `qa-pipeline/state.py` | `TenantContext`, `QAState`, `build_initial_state()`, `require_tenant()` |
| `qa-pipeline/graph.py` | `build_graph()` — 구조 불변, tracing 에 tenant_id 첨부 |
| `qa-pipeline/nodes/orchestrator.py` | 진입 가드 — tenant 누락 시 즉시 실패 |
| `qa-pipeline/nodes/skills/node_context.py` | `NodeContext.from_state()` 가 `tenant_id`/`tenant_config`/`request_id` 노출 |
| `qa-pipeline/nodes/__init__.py` | `NODE_REGISTRY` — graph 빌드 시 참조 |
| 각 평가 노드 | `load_prompt(..., tenant_id=...)`, 결과에 `tenant_id` 메타 부착 |
