# State / 노드 회귀 체크리스트 (Dev3 파트)

> **Scope**: Task #11 Phase 1 사전 준비. **PL 지시 전 실행 금지**.
> **대상**: Task #4 산출물(state.py, graph.py, nodes/, skills/node_context.py, STATE_MIGRATION.md)
> **연관 문서**: `NODE_TENANT_QA_SCENARIOS.md`, `STATE_MIGRATION.md`, `ARCHITECTURE.md`

---

## 0. 사전 준비 (실행 금지 — 본 문서는 체크리스트 작성만)

- [ ] PL이 Phase 2 이상으로 승격(배포 차단 해제)했는지 확인
- [ ] 로컬 py313 (`~/.conda/envs/py313/python.exe`) 환경 활성 상태
- [ ] `packages/agentcore-agents/qa-pipeline/` (원본) 수정 없음 — `git diff` 무변화 확인
- [ ] boto3 호출이 포함된 경로는 **mock 또는 localstack** 전제로 실행 (실 AWS 호출 금지)

---

## 1. 정적 검증 (AWS 호출 없음)

### 1.1 shape diff
- [ ] `python scripts/validate_state_shape.py` 실행 → exit=0 반환
- [ ] 12-key state shape 확인: `tenant, transcript, consultation_type, customer_id, session_id, llm_backend, bedrock_model_id, current_phase` (Dev3 헬퍼) + `evaluations, completed_nodes, node_timings, next_node` (Dev1 라우터 보강)
- [ ] `session_id`, `customer_id`, `llm_backend`, `bedrock_model_id` 모두 키는 존재하되 기본값(빈문자/None) 허용

### 1.2 load_prompt 호출 스캔
- [ ] `grep -rn "load_prompt(" packages/qa-pipeline-multitenant/qa-pipeline/nodes/` 결과 20건 모두 `tenant_id=` 키워드 포함
- [ ] `consistency_check.py` / `report_generator.py` 에서 `include_preamble=False` 유지
- [ ] 헬퍼 함수 (`_get_*_prompt` / `_get_*_system_prompt`) 시그니처에 `tenant_id: str = ""` 존재
- [ ] `courtesy.py` 의 polite_expression 경로에서 `_tenant_id = (state.get("tenant") or {}).get("tenant_id", "")` 상위 바인딩 존재
- [ ] `consistency_check.py::_llm_verify` 본체 상단에 `_tenant_id = tenant_id` 바인딩 존재

### 1.3 NodeContext 확장
- [ ] `nodes/skills/node_context.py` 의 `NodeContext` 에 `tenant_id: str = ""` / `tenant_config: dict = {}` / `request_id: str = ""` 필드 존재
- [ ] `from_state` 가 `state["tenant"]` dict 에서 3필드를 정확히 추출
- [ ] `tenant_id_from_state(state)` 헬퍼 제공 확인

### 1.4 orchestrator 가드
- [ ] `nodes/orchestrator.py::orchestrator_node` 상단에 tenant 누락/빈문자 → ValueError 가드 존재
- [ ] 로그 라인이 `tenant=%s phase=%s` 형식으로 tenant_id 포함
- [ ] ValueError 메시지에 STATE_MIGRATION.md 경로 참조 포함

### 1.5 graph Send 팬아웃
- [ ] `graph.py::_BASE_FIELDS` 에 `"tenant"` 포함
- [ ] `_select_state_for_node` 가 tenant 필드를 항상 전달 (12개 노드 대상)
- [ ] `_record_trace` 가 `input_snapshot["tenant_id"]` 를 `node_timings` / `node_traces` 에 전파

---

## 2. 단위 테스트 시나리오 (pytest — PL 승인 후 실행)

### 2.1 state.py
- [ ] `build_initial_state(...)` 반환 state 의 key 집합 검증
- [ ] `build_initial_state(tenant_config=None)` 가 `tenant.store.get_config` 호출 시도 (mock)
- [ ] `build_initial_state(tenant_config={...})` 가 store 호출 skip
- [ ] `require_tenant(state)` 누락/빈문자/None 시 ValueError 발생
- [ ] `TenantContext` TypedDict 구조 (3 키)

### 2.2 orchestrator 가드
- [ ] `orchestrator_node({"current_phase": "init"})` → ValueError
- [ ] `orchestrator_node({"tenant": {"tenant_id": ""}, ...})` → ValueError
- [ ] `orchestrator_node({"tenant": {"tenant_id": "t", "config": {}, "request_id": "r"}, "current_phase": "init"})` → `next_node="dialogue_parser"`

### 2.3 NodeContext
- [ ] `NodeContext.from_state({"tenant": {...}, ...})` 3필드 추출
- [ ] tenant 누락 시 `ctx.tenant_id == ""`, `ctx.tenant_config == {}`

---

## 3. 통합 시나리오 (로컬 mock — PL 승인 후 실행)

### 3.1 Happy path
- [ ] 단일 tenant_id 로 transcript 처리 시 evaluations 집계 정상
- [ ] evaluations[*].tenant_id 가 모두 일치 (메타 유지)
- [ ] verification["tenant_id"] / report["tenant_id"] 일치
- [ ] node_traces[*].tenant_id 가 전 노드에서 일치

### 3.2 격리 (동시 실행)
- [ ] tid_a / tid_b 로 두 요청 동시 실행 — 결과 간 tenant_id 누수 없음
- [ ] 프롬프트 LRU 캐시가 (tenant_id, name) 쌍으로 구분 — Dev4 검증 항목과 병렬 확인

### 3.3 오류 경로
- [ ] LLM timeout → 개별 노드 fallback 경로, 파이프라인 중단 없음
- [ ] evaluations=[] 로 consistency_check 진입 → status=error 반환, 파이프라인 이어짐
- [ ] report_generator evaluations=[] → 조기 에러 반환

### 3.4 오버라이드 폴백
- [ ] prompts/tenants/{tid}/item_01_greeting.sonnet.md 존재 시 해당 파일 로드
- [ ] 오버라이드 미존재 시 prompts/item_01_greeting.sonnet.md 로 폴백
- [ ] 오버라이드/디폴트 모두 없는 name 으로 호출 → FileNotFoundError

---

## 4. 실행 순서 불변성 검증

- [ ] dialogue_parser → Phase A (5병렬: greeting, understanding, courtesy, incorrect_check, mandatory)
- [ ] Phase B1 (2병렬: scope, work_accuracy)
- [ ] Phase B2 (1: proactiveness)
- [ ] Phase C (2병렬: consistency_check, score_validation)
- [ ] report_generator
- [ ] 각 Phase 완료 전 다음 Phase 진입 차단 확인 (orchestrator 재디스패치)

---

## 5. 회귀 리스크 — 발견 시 라우팅

| 증상 | 1차 오너 | 근거 |
|---|---|---|
| state["tenant"] 누락 500 | Dev1 (middleware) | graph 전 차단 책임 |
| load_prompt FileNotFoundError (default 포함) | Dev4 | 프롬프트 로더 범위 |
| prompts/tenants/{tid}/ override 적용 안 됨 | Dev4 | 오버라이드 로더 우선순위 |
| LangGraph node ValueError | Dev3 | 가드/시그니처 문제 |
| evaluations/report에 tenant_id 메타 누락 | Dev3 | 노드 반환 구조 |
| SSE/응답 payload 의 tenant_id 누락 | Dev1 | 라우터 직렬화 |
| audit_log / metrics 의 tenant 누락 | Dev6 | 관찰성 범위 |
| DynamoDB / S3 교차 접근 | Dev2 | 격리 레이어 |
| UI 전환 시 tenant 표시 오류 | Dev5 | 프론트 범위 |

---

## 6. 완료 보고

- [ ] 본 체크리스트 전체 항목에 체크 / PL 에 1줄 SendMessage 완료 회신
- [ ] 미이슈 항목은 아래에 파일 + 라인 + 현상 기록
