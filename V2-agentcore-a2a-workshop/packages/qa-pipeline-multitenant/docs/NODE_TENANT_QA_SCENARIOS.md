# 노드별 tenant_id 주입 QA 시나리오 매트릭스

> **Scope**: Dev3 (pipeline-state) — Task #11 Phase 1 사전 준비 산출물
> **실행 금지**: 본 문서는 통합 테스트 시나리오 설계만 담고 있으며, AWS 리소스 호출이나 실 배포를 수행하지 않는다.
> **참조**: `state.py`, `graph.py`, `nodes/`, `docs/STATE_MIGRATION.md`, `ARCHITECTURE.md` §5

---

## 0. 공통 전제

- 파이프라인 진입은 `orchestrator_node` 가 유일 — entry 가드로 `state["tenant"]["tenant_id"]` falsy 시 즉시 `ValueError` 발생.
- 노드 12종은 크게 세 부류:
  - **Passthrough**: 파이프라인 뼈대 역할, LLM/프롬프트 비소비 → `dialogue_parser`, `score_validation`
  - **LLM + load_prompt 소비**: `greeting, understanding, courtesy, mandatory, scope, proactiveness, work_accuracy, incorrect_check, consistency_check, report_generator`
  - **로컬 규칙 기반**: `greeting` / `incorrect_check` 에 일부 잔존 (LLM fallback 경로) — tenant_id 사용은 LLM 경로만
- 본 문서는 **노드별 매트릭스** 형식으로 입력 / 기대 동작 / 검증 포인트 / 회귀 리스크 를 정리.

---

## 1. 공통 시나리오 (전 노드 공통 — orchestrator 레벨)

| 시나리오 | 입력 state shape | 기대 동작 | 검증 포인트 | 회귀 리스크 |
|---|---|---|---|---|
| C1 happy path | `{"tenant": {"tenant_id": "kolon_default", "config": {...}, "request_id": "r-1"}, "transcript": "...", "current_phase": "init"}` | orchestrator → dialogue_parser 디스패치 | `next_node == "dialogue_parser"` | 없음 |
| C2 tenant 필드 누락 | `{"transcript": "...", "current_phase": "init"}` | `ValueError("QAState missing 'tenant' context...")` | 예외 타입/메시지, 스택이 graph 내부에 국한 | Dev1 라우터 미들웨어 바이패스 시 즉시 차단 |
| C3 tenant_id 빈 문자 | `{"tenant": {"tenant_id": "", "config": {}, "request_id": "r"}}` | `ValueError` (falsy 가드) | 예외 발생, 부분 실행 금지 | 미들웨어가 빈값 허용하지 않도록 Dev1 검증 대칭 |
| C4 config 빈 dict | `{"tenant": {"tenant_id": "t1", "config": {}, "request_id": "r"}}` | 정상 실행, 노드는 기본값 동작 | 노드 실행 OK, load_prompt 는 default prompts/ 폴백 | 후속 Phase 3 에서 `config["qa_items_enabled"]` 소비 시 재점검 |
| C5 request_id 누락 | `{"tenant": {"tenant_id": "t1", "config": {}}}` (request_id 없음) | 정상 실행, 메타에 빈 문자 전파 | node_traces[*].request_id == "" | Dev1 Middleware 가 uuid 폴백 보장 |
| C6 ctx.tenant_id 전파 | 모든 Phase 병렬 팬아웃 | Send() 각 노드에 tenant 필드 전달 | `_BASE_FIELDS` 에 "tenant" 포함 확인 | `_select_state_for_node` 수정 시 tenant 누락 우려 |

---

## 2. 노드별 상세 매트릭스

### 2.1 dialogue_parser (Passthrough)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | transcript 존재 | `parsed_dialogue` / `agent_turn_assignments` 생성 | state["parsed_dialogue"]["turns"] 길이 > 0 | 턴 파싱 실패 시 후속 Phase A 전체가 텍스트 전량 fallback |
| 빈 transcript | `transcript=""` | 빈 assignments 반환, 에러 없이 다음 phase 진입 | Phase A 진입 시 각 노드가 `build_llm_failure_result` 반환 | — |
| 트레이스 tenant 메타 | 정상 + tenant 주입 | `node_traces[0].tenant_id == state["tenant"]["tenant_id"]` | graph._record_trace 가 input_snapshot 에 tenant_id 포함 | graph.py 의 _capture_node_input 수정 시 메타 누락 |

### 2.2 greeting (Phase A, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | tenant + assignments["greeting"] | #1/#2 평가, `load_prompt("item_01_greeting", tenant_id=...)` / `item_02_farewell` 호출 | evaluations 에 agent_id="greeting-agent", item 1/2 존재 | tenant_id 빈 문자 전달 시 Dev4 로더 ValueError — orchestrator 가드 우회 감지 |
| override prompt 있음 | prompts/tenants/{tid}/item_01_greeting.sonnet.md 존재 | 테넌트 override 로드 | 시스템 프롬프트 content 비교 | 오버라이드 디렉토리 오타/권한 |
| override 없음 (폴백) | prompts/tenants/ 비어있음 | prompts/item_01_greeting.sonnet.md 로드 | 동일 내용 | 4단계 우선순위 순서 변경 회귀 |
| LLM 실패 | 네트워크 장애 | 규칙 fallback (_evaluate_first_greeting) | rule 결과 반환, deductions 정상 | fallback 경로에 tenant_id 누락 무관 (로컬 규칙) |
| assignments 누락 | transcript 만 | 전체 텍스트 파싱 후 정상 | 결과는 집계됨 | dialogue_parser 회귀 시 영향 |

### 2.3 understanding (Phase A, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | tenant + assignments["understanding"] | #3/#4/#5, load_prompt("item_03_listening"/"item_04_empathy"/"item_05_hold_mention", tenant_id=...) | evaluations item 3,4,5 존재 | tenant_id 추출 누락 |
| #4 empathy fallback | LLM timeout | rule fallback (empathy_count 기반) | 점수 5/3/0 산출 | fallback 진입 경로 보존 |
| ctx.tenant_id 전파 | greeting 과 동일 | NodeContext.from_state 에서 추출 | ctx.tenant_id == state["tenant"]["tenant_id"] | skills/node_context.py 변경 회귀 |

### 2.4 courtesy (Phase A, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | #6 item_06_polite_expression + #7 item_07_cushion | load_prompt 2회 | evaluations item 6,7 | polite_expression 호출 내 `_tenant_id` 지역 바인딩 누락 회귀 |
| 쿠션어 상황 없음 | refusal_count=0 | #7 auto 5점 (LLM skip) | fallback OK | LLM skip 경로에서 tenant_id 미사용 무관 |

### 2.5 mandatory (Phase A, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | #8 item_08_inquiry_paraphrase + #9 item_09_customer_info | 헬퍼 `_get_*_system_prompt(backend, tenant_id=...)` 호출 | evaluations item 8,9 | 헬퍼 signature 변경 시 호환성 |
| LLM 실패 | timeout | 규칙 fallback | fallback score 산출 | — |

### 2.6 incorrect_check (Phase A, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | #17 item_17_iv_procedure + #18 item_18_privacy_protection | `_llm_evaluate_iv_procedure(tenant_id=...)` / `_llm_evaluate_privacy_protection(tenant_id=...)` | evaluations item 17,18 + flags 설정 | tenant_id 인자 누락 시 Dev4 로더 ValueError |
| LLM 실패 | 예외 | 규칙 fallback | pre_analysis 기반 score | — |
| privacy violation 감지 | 민감정보 노출 | flags["privacy_violation"]=True | report_generator 가 소비 | flags 구조 회귀 |

### 2.7 scope (Phase B1, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | #10 item_10_clarity + #11 item_11_conclusion_first, intent_summary 존재 | LLM 2회 | evaluations item 10,11 | intent_summary 미생성 시 mandatory 회귀 영향 |
| intent 미식별 | intent_summary={} | intent_context="" 로 호출 | 프롬프트 content 영향 | — |
| ctx.tenant_id 전파 | gather 병렬 호출 | `_tenant_id = ctx.tenant_id` 를 두 함수에 전달 | gather 시그니처 검증 | 병렬 호출 시 tenant_id 인자 누락 |

### 2.8 work_accuracy (Phase B1, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | #15 item_15_accuracy + #16 item_16_mandatory_script | accuracy_verdict 생성 | evaluations 15,16 + accuracy_verdict["has_incorrect_guidance"] | proactiveness 가 읽는 verdict 구조 회귀 |
| 오안내 감지 | 정정 발화 존재 | verdict severity 설정 | proactiveness accuracy_context 생성 경로 확인 | — |

### 2.9 proactiveness (Phase B2, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | #12/#13/#14, intent_summary + accuracy_verdict | LLM 3회 gather, 각 `_get_*_prompt(backend, tenant_id)` | evaluations 12,13,14 | B1 결과 누락 시 context="" 로 동작 |
| B1 완료 전 도착 | phase_b1 불완전 | orchestrator 가 재디스패치 | phase_b2 진입 차단 | 순서 위반 감지 |

### 2.10 consistency_check (Phase C, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | evaluations + deduction_log + wiki 공유 메모리 | LLM 교차 검증, `_llm_verify(tenant_id=...)` | verification["is_consistent"], verification["tenant_id"] 메타 | `_tenant_id` 상위 스코프 바인딩 회귀 |
| LLM 실패 | timeout | fallback verdict 사용 | fallback 표시 + critical/soft 규칙 기반 생성 | fallback 경로에 tenant_id 메타 유지 |
| evaluations 비어있음 | [] | verification status=error | 짧은 에러 경로 | — |

### 2.11 score_validation (Phase C, Passthrough — 규칙 기반)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | evaluations | 산술 검증, `score_validation` 필드 생성 | passed=bool | LLM/프롬프트 미사용 → tenant_id 무관 |
| 점수 누락 | 일부 item 없음 | missing 플래그 | — | — |

### 2.12 report_generator (Reporting, LLM 경로)

| 시나리오 | 입력 | 기대 | 검증 | 리스크 |
|---|---|---|---|---|
| 정상 | evaluations + verification + score_validation | `_generate_combined_report(tenant_id=...)` → load_prompt("report_generator", tenant_id, include_preamble=False) | report["report"] 존재, report["tenant_id"] 메타 | include_preamble=False 누락 시 preamble 이중 prepend |
| verification critical 이슈 존재 | critical_issues>0 | 보고서에 별도 섹션 | report 본문 내 "critical" 언급 | gate 미사용 정책 유지 |
| evaluations 비어있음 | [] | report status=error 조기 반환 | 라우터 단 SSE 에러 이벤트 | — |

---

## 3. 격리(Isolation) 회귀 시나리오

| 시나리오 | 설명 | 기대 | 검증 |
|---|---|---|---|
| I1 동시 테넌트 실행 | tid_a / tid_b 두 요청 병렬 graph 호출 | 각 요청 결과가 상대 tenant_id 데이터 미포함 | evaluations[*].tenant_id / verification["tenant_id"] / report["tenant_id"] 전부 일치 |
| I2 캐시 간 누수 | load_prompt LRU 캐시 (Dev4) | tid_a 프롬프트와 tid_b 프롬프트가 서로 구별 | (tenant_id, name) 쌍 기반 캐시키 검증 |
| I3 state.tenant write 시도 | 노드가 실수로 `state["tenant"]["tenant_id"] = "other"` | 파이프라인 영향 없음 (LangGraph 가 반환 dict 만 merge) | 노드 반환 결과에 "tenant" 키 포함 안 함을 grep 으로 확인 |

---

## 4. 검증 방법 요약

각 시나리오는 다음 중 하나로 검증:
- **(A) 구조 검증**: `scripts/validate_state_shape.py` 로 state key 집합 비교 (offline)
- **(B) 단위 테스트**: tests/ 하위에 TypedDict / 헬퍼 단위 검증 (PL 지시 시)
- **(C) 정적 분석**: `grep` / `ruff` / `ast` 로 load_prompt 호출 시 tenant_id kwarg 존재 여부 스캔
- **(D) 런타임 통합**: Phase 2+ 에서 PL 승인 후 로컬 mock 환경에서 실행 (본 Phase 범위 외)

## 5. 진행 추적

통합 테스트(Task #9) 시 각 시나리오를 `docs/STATE_REGRESSION_CHECKLIST.md` 의 체크박스에 매핑하여 진행 상태를 기록한다.
