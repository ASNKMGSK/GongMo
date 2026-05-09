# V3 QA Pipeline 코드 최적화 Audit — 진행 로그

> 시작: 2026-04-29
> 팀: `qa-opt-audit` (TeamCreate)
> 목적: 4영역 병렬 코드 audit → P0/P1/P2 우선순위 + file:line + 개선안 수집

---

## 팀원 / 담당 영역

| 팀원 | 영역 | Task ID | 상태 |
|------|------|---------|------|
| `be-pipeline-core` | Layer1 + Layer2 평가 노드 + 그래프/오케스트레이터 | #1 | 진행중 |
| `be-debate-hitl` | AG2 토론 + HITL RAG + judge LLM | #2 | 진행중 |
| `be-layer4-serving` | Layer4 + server_v2 SSE + 배치 스크립트 | #3 | 진행중 |
| `fe-react` | chatbot-ui-next 전체 | #4 | 진행중 |

모두 Opus 모델, effort=high.

---

## 점검 범위 (각 팀원에게 전달된 scope)

### be-pipeline-core
- v2/layer1/* (quality_gate, segment_splitter, pii_normalizer, deduction_trigger, rule_pre_verdictor, run_layer1.py)
- v2/nodes/* (8개 평가 노드 + skills/constants + skills/reconciler)
- v2/llm.py / nodes/llm.py (LLM 호출, semaphore, 재시도, 캐시)
- v2/orchestrator.py / v2/graph_v2.py

### be-debate-hitl
- v2/debate/* (team.py, run_debate.py, node.py, schemas.py)
- v2/hitl/* (rag_retriever.py — AOSS, KNN/BM25, 임베딩, prompt 포맷)
- v2/judge_agent.py
- DEBATE_EXCLUDED_ITEMS / threshold 처리

### be-layer4-serving
- v2/layer4/* (gt_comparison, gt_evidence_comparison, score_validation, consistency_check, report_generator)
- v2/serving/server_v2.py (FastAPI, SSE, _apply_debate_overrides, _build_initial_state)
- scripts/run_direct_batch.py / scripts/merge_self_consistency.py

### fe-react
- components/* (PipelineFlow, EvaluateRunner, DebatePanel, results/*Panel)
- lib/* (pipeline.ts, types, hooks, sse client)
- app/* (Next.js 16 app router)
- next.config.ts, tailwind 설정

---

## 리포트 수집 상태

- [x] be-pipeline-core 리포트 (2026-04-29 17:45)
- [x] be-debate-hitl 리포트 (2026-04-29 17:43)
- [x] be-layer4-serving 리포트 (2026-04-29 17:44)
- [x] fe-react 리포트 (2026-04-29 17:47)

---

## 리포트 본문

### be-pipeline-core (수신 완료 2026-04-29 17:45)

대상: `v2/layer1/*`, `v2/agents/group_a/*`, `v2/agents/group_b/*`, `v2/graph_v2.py`, `v2/layer3/orchestrator_v2.py`, `nodes/llm.py`.

#### P0 (정확성/안정성/누수)

1. **이중 세마포어 — 모든 Group B LLM 호출이 두 번 acquire** — `v2/agents/group_b/_llm.py:165-176`
   - 현재: `call_bedrock_json` 진입에 group_b 자체 sem acquire 후 내부 `invoke_and_parse` (`nodes/llm.py:599-606`) 가 nodes/llm 의 sem 또 acquire.
   - 문제: `_GROUP_B_MAX_CONCURRENT=1` 기본 → 17 항목 × 3 persona = 51 호출이 직렬 실행. 8 sub-agent fan-out 무력화.
   - 개선안: group_b 외부 sem 제거하고 nodes/llm 단일 sem 에 위임. `_loop_semaphores` 자료구조도 삭제.
   - 임팩트: latency (분 단위 회귀)

2. **`SAGEMAKER_MAX_CONCURRENT` env 충돌 — group_a 와 group_b 가 의미 다름** — `v2/agents/group_b/_llm.py:66`, `nodes/llm.py:89`
   - group_b 기본 1, nodes/llm 기본 10. env 미설정 시 모순.
   - 개선안: 별도 env 키 분리 또는 P0-1 적용으로 자동 해소.
   - 임팩트: correctness (운영 시 예측 불가)

3. **응답 캐시 키가 backend / model / max_tokens 무시** — `nodes/llm.py:568`
   - 현재: `_response_cache_key(messages, None, None, 0)` — messages 만 키.
   - 문제: 모델 전환 (Sonnet → Haiku, debate/judge 다른 모델) 시 첫 응답 캐시 hit → 잘못된 점수.
   - 개선안: backend / model_id / max_tokens 메타 attach 후 cache key 에 포함.
   - 임팩트: correctness (모델 실험 무효화)

4. **`asyncio.get_event_loop()` 사용 — Python 3.13 deprecation + loop mismatch 위험** — `v2/agents/group_b/_llm.py:84-87`
   - sync 컨텍스트 호출 시 새 loop 생성 + sem 바인딩 → 호출자 loop 와 mismatch RuntimeError 가능.
   - 개선안: `get_running_loop()` 만 사용 + RuntimeError 명시. nodes/llm.py:97-103 패턴으로 통일.
   - 임팩트: correctness (3.13 경고 + loop mismatch)

5. **node_traces 누적의 근본 원인 — 8 sub-agent fan-out 결과를 깊이 5 walk + sanitize 사본 누적** — `v2/graph_v2.py:526` + `graph.py:91-127`
   - CLAUDE.md 의 "2~5MB node_traces → SSE idle disconnect" 직접 원인.
   - 개선안: (a) `_record_trace.output` 을 키 목록 + 메타 (count/size) 만 저장. 풀 trace 는 `QA_FULL_TRACE=1` env 게이팅. (b) `_sanitize_trace_output` 노드당 8KB cap.
   - 임팩트: mem (state 누적), latency (SSE flush), prod 안정성

#### P1 (성능/유지보수)

1. **8 평가 노드 helper 코드 거의 완전 중복 (DRY 위반 ~1500줄)** — group_a/{greeting,language,listening_comm,needs}.py + group_b/{explanation,proactiveness,work_accuracy,privacy}.py
   - `_inject_hybrid_fields`, `_rule_fallback_result`, `_safe_fewshot`, `_safe_reasoning`, `_build_segment_text`, `_build_user_message`, `_evaluate_item` (3-persona ensemble + reconcile + neutral 채택) 가 각 100+ 줄씩 거의 동일 복제. 미묘한 차이 이미 발생 (`ITEM_MAX_SCORE.get(...)` vs 하드코딩 `10 if item==10 else 5`).
   - 개선안: `v2/agents/group_b/_persona_runner.py::evaluate_item_with_personas` 단일 추출. `group_b/_helpers.py` + `group_a/_helpers.py` 헬퍼 모음.
   - 임팩트: 유지보수 (8x → 1x), 코드 ~1500 → ~600줄

2. **L1 정규식이 매 호출 컴파일** — `v2/layer1/deduction_trigger_detector.py:181-294` + `rule_pre_verdictor.py:81-82, 500-503, 575-576`
   - 50 turn × 5 카테고리 × 평균 6 패턴 = 1500회 매칭. Python re LRU(512) 가 일부 흡수.
   - 개선안: 모듈 로드 시 `_PROFANITY_REGEXES = [re.compile(p) for p in ...]` precompile.
   - 임팩트: latency (Layer 1 ~수십 ms/sample, 배치 N건 누적)

3. **`_has_privacy_violation` 매 호출 import** — `v2/layer1/rule_pre_verdictor.py:564-577`
   - 함수 본문 `from nodes.skills.constants import PRIVACY_VIOLATION_PATTERNS` inline. 다른 곳에서 이미 module-level import.
   - 개선안: 상단으로 이동 + P1-2 와 함께 precompile.

4. **`_extract_persona_details` / `force_single_persona` group_b 4개 파일 매 호출 inline import** — `v2/agents/group_b/{explanation,proactiveness,work_accuracy}.py`
   - private name 외부 직접 참조 (이름 컨벤션 위반).
   - 개선안: 모듈 상단 + public name rename. P1-1 통합으로 해결.

5. **`_route_after_layer3` plan dict/pydantic 양쪽 미호환** — `v2/graph_v2.py:541-552` + `v2/layer3/orchestrator_v2.py:75`
   - `state.plan.skip_phase_c_and_reporting` (CLAUDE.md 표기) 가 pydantic 객체일 때 `.get()` 깨짐.
   - 개선안: `(plan_obj.get(...) if isinstance(plan_obj, dict) else getattr(plan_obj, ..., False))` 양쪽 호환.
   - 임팩트: correctness (배치 우회 미동작 가능성)

6. **`build_item_verdict` (group_a) vs `make_item_verdict` (group_b) 두 개 verdict 빌더 미세 차이** — `_shared.py:248-369` vs `base.py:223-279`
   - 시그니처/필드명 차이 (`raw_score` vs `score`, `llm_self` vs `llm_self_confidence`).
   - 개선안: `v2/contracts/verdict_builder.py` 추출 후 단일 진실.
   - 임팩트: correctness (스키마 drift)

7. **응답 캐시 maxsize=256 + TTL 1시간 — 효용 낮음 + 매 호출 sha256 비용** — `nodes/llm.py:369-401`
   - prompt 거의 매번 다름 (persona prefix / fewshot 변동) → 미스 + 256 슬롯 끝없이 회전.
   - 개선안: env 게이팅 비활성 기본값. retry 안전망은 별도 함수 인자로.
   - 임팩트: latency (sha256), mem

#### P2 (nice-to-have)

1. `SAGEMAKER_MAX_OUTPUT_TOKENS` 가 Bedrock 백엔드에서 dead — SageMaker deprecate 시 제거
2. `_fallback_result` (group_b explanation.py:110-115) 가 timeout 1개일 때 다른 결과 손실 — 정책 통일 필요
3. `run_persona_ensemble` (group_a) 와 group_b inline `if _fsp(): [None, neutral_only, None]` 분리 구현 — P1-1 로 통합
4. `_normalize_evidence` group_a 4개 파일 동일 — P1-1 자동 해소
5. quality_gate stt_metadata=None 시 silent pass — `stt_metadata_missing` 모드 추가 권장

#### 추가 정합 확인

- ✅ `ALLOWED_STEPS[17]=[5,3,0]` (rubric.py:8) + snap_score_v2 사용 — V1 충돌 회복
- ✅ `nodes/skills/reconciler.normalize_fallback_deductions` 가 base.py:360 에서 호출 — 인프라 폴백 정화 정합
- ✅ LLMTimeoutError 처리: group_a/group_b 양쪽 `except LLMTimeoutError: raise` 가 generic 앞 위치 정합
- ⚠ timeout 정책: explanation 은 "둘 다 timeout 일 때만 raise", proactiveness/work_accuracy 는 미세 차이 — 통일 필요

---

### be-debate-hitl (수신 완료 2026-04-29 17:43)

대상: `v2/debate/{team,run_debate,node,personas,schemas}.py`, `v2/hitl/rag_retriever.py`, `v2/judge_agent.py`.
이전 픽스 정착 확인: round_tracker truth-source (team.py:107-147 → run_debate.py:_structure_rounds), HITL `cos X.XX` 노출 (rag_retriever.py:233), `_apply_debate_overrides` judge 교체 (server_v2.py:1361-1414). **세 픽스 모두 정착 확인 완료.**

#### P0 (정확성/안정성/누수)

1. **HITL AOSS 클라이언트가 매 호출마다 새로 생성됨 — 연결 누수 + N+1** — `v2/hitl/rag_retriever.py:43-56`
   - 현재: `def _client_or_none(): ... return _aoss._make_client(endpoint)` — `aoss_store._CLIENT_CACHE` 우회.
   - 문제: `retrieve_human_cases()` 가 토론마다 (16 항목 × 병렬 worker) 호출되는데, 매번 `_make_client` → `boto3.Session().get_credentials()` + `AWSV4SignerAuth` 생성 + 새 `OpenSearch` HTTP 풀 (pool_maxsize=50). 16 토론 × 2 (judge + judge_agent.deliberate) × Bedrock retry = 30+ 신규 OS client/run. boto3 sigV4 캐시 미공유, IMDS 메타 round-trip 폭증, sock fd 누수 위험.
   - 개선안: `aoss_store.get_store(INDEX_NAME).client` 사용 — 이미 `_CLIENT_CACHE` 가 endpoint 단위 싱글톤 운영 중 (aoss_store.py:128-155). 또는 모듈 전역 `_HITL_CLIENT` 캐시 (lock+lazy init).
   - 임팩트: latency (call당 200~500ms IMDS+TLS) / fd 누수 / cost (boto3 STS quota)

2. **`asyncio.run()` 을 ThreadPoolExecutor 워커에서 호출 — event loop 누수 가능** — `v2/debate/run_debate.py:950`
   - 현재: `result = asyncio.run(deliberate_post_debate(...))` — debate_node `_worker` 스레드 안에서.
   - 문제: `asyncio.run` 은 새 loop 를 만들고 끝나면 닫지만, `deliberate_post_debate` 안의 `call_bedrock_json` 이 `_get_semaphore()` 로 가져오는 세마포어가 **모듈 전역**이면 다른 loop 에 바인딩된 `asyncio.Semaphore` 라 `RuntimeError: ... is bound to a different event loop` 가 발생할 수 있음.
   - 개선안: 옵션 A — `_llm.py::_get_semaphore` 를 `contextvars` 또는 per-loop dict 로 변경. 옵션 B — debate_node 가 한 개의 asyncio loop 를 루트에서 만들고 워커는 `asyncio.run_coroutine_threadsafe` 로 submit.
   - 임팩트: correctness (간헐 RuntimeError) / mem (loop 객체 누수)

3. **AG2 `is_termination_msg` 가 round_tracker 에 의존하지만 hook 보다 늦게 호출 → 1턴 over-shoot** — `v2/debate/team.py:319-341`
   - 현재: `_is_termination` 가 `round_tracker.get("votes_by_round")` 를 읽음.
   - 문제: 한 라운드의 *마지막* persona 가 발화한 뒤 `_is_termination` 이 호출되는데, persona_count 명 모두 발화 완료한 다음 message 파싱 시점 = 모더레이터가 없으니 *다음 라운드 첫 persona* 가 한 turn 더 발화한 후. **즉 1턴 over-shoot 보장.**
   - 개선안: hook 안에서 `is_scoring_turn` 일 때 `seen.add` 후 `len(seen)==persona_count` 면 즉시 `round_tracker["should_terminate"]=True` 마킹. `_is_termination` 은 그 플래그만 본다.
   - 임팩트: cost / latency (라운드 1 만장일치 케이스에서도 1턴 추가, 항목 16개 × 1턴 = 추가 Bedrock 호출 ~16회/run)

4. **judge `_post_debate_fallback` 호출 시 LLMTimeoutError 분기 누락** — `v2/debate/run_debate.py:961-977`
   - 현재: `except Exception as exc: if "LLMTimeoutError" in type(exc).__name__: raise`.
   - 문제: 문자열 매칭으로 타입 검사 — 안티패턴. `LLMTimeoutErrorCustom` 같은 sub-class가 추가되면 이름 매칭 실패.
   - 개선안: `from v2.agents.group_b._llm import LLMTimeoutError` 함수 상단 import + `except LLMTimeoutError: raise`.
   - 임팩트: correctness (타임아웃 시 median fallback 으로 삼키는 회귀 위험)

5. **`_invoke_post_debate_judge` rounds=[] 케이스에서 판사 호출 시 transcript 슬라이스 잘림** — `v2/debate/run_debate.py:953-960`
   - 문제: `_build_judge_only_record` 경로 (AG2 build_team 실패) 에서 rounds=[] 로 들어가면, judge 가 토론 transcript 없이 **상담 원문 2.5KB + initial_positions 만** 보고 결정. judge prompt Step 3은 "토론 흐름 분석" 인데 토론이 없어 SOP 가 깨짐. judge hallucinate 가능.
   - 개선안: `_build_judge_only_record` 경로일 때는 `judge_agent.deliberate` (debate-less) 를 호출해야 함. 또는 `deliberate_post_debate` 에 `debate_rounds=[]` 일 때 명시 분기.
   - 임팩트: correctness (AG2 실패 케이스에서 judge 환각)

6. **`votes_by_round` round 키가 cap 된 round 와 overshoot round 양쪽에 분산 저장** — `v2/debate/team.py:121-132`
   - 현재: `cur_round_for_raw = min(cur_round_for_raw, max_rounds_cap)` 한 *후* `vbr.setdefault(cur_round, ...)` 에 저장.
   - 문제: 라운드 3 시작 시 strict 가 발화하면 round_tracker.round=3, cap=2 → vbr[2]["strict"] = new_score (이전 라운드 strict 점수 덮어씀).
   - 개선안: `round_tracker["round"] = min(round_tracker["round"], cap)`. 또는 cap 도달 시 hook 이 즉시 `should_terminate=True` 마킹.
   - 임팩트: correctness (라운드 over-shoot + 합의 오판)

#### P1 (성능/유지보수)

7. **judge transcript 가 매번 max 2500자 그대로 전송 — 압축/요약 미적용** — `v2/judge_agent.py:678,803`
   - 16 항목 × 2 호출 × Bedrock retry = 동일 transcript ~64회 Bedrock context 에 실림. Bedrock 입력 토큰 비용 / TPM throttle 의 주범.
   - 개선안: (a) 항목별 segment 만 잘라 전송. (b) prompt caching (`cache_control`) 추가.
   - 임팩트: cost (입력 토큰 30~50% 절감 가능) / latency

8. **persona system prompt 가 build_debate_team 호출마다 재빌드 — 16 항목 × 3 페르소나 = 48회** — `v2/debate/team.py:265-267`
   - 개선안: `personas.py` 에 `@functools.lru_cache(maxsize=64) def build_persona_system_prompt_cached(persona, steps: tuple[int,...])` 추가.
   - 임팩트: latency (~50ms/run)

9. **GroupChat / GroupChatManager 가 매 토론마다 신규 빌드 — stateful 객체 재사용 불가** — `v2/debate/team.py:236-351`
   - 임팩트: latency 소폭 (50~150ms/run)

10. **`_decide_final` 에서 last 라운드의 `consensus` 검증이 동일 점수면 무조건 승격** — `v2/debate/run_debate.py:1119-1133`
    - max_rounds=2 인데 라운드 1 만장일치도 종료 → debate 의 의미 약화.
    - 개선안: `len(rounds) >= req.max_rounds` 일 때만 consensus 인정.
    - 임팩트: correctness (정책 일관성)

11. **`_pick_candidates` 에서 spread 가 0인데 정렬에 사용** — `v2/debate/node.py:172`
    - 정책이 "spread 무관 모두 토론" 으로 변경됐는데 정렬은 그대로 spread 우선. 의미 무.
    - 개선안: sort 자체를 제거. max_items 제한 운영 시에만 sort.
    - 임팩트: 가독성

12. **`_emit_fallback_discussion_events` 에서 vote 동일 점수일 때 `consensus_reached=True` 발송** — `v2/debate/run_debate.py:382-397`
    - AG2 실패한 fallback 인데 합의 라벨로 표시 → UI 모순.
    - 개선안: `consensus_reached: False` 로 고정.
    - 임팩트: UX / 신뢰도

13. **JSON 파싱 실패 turn 의 evidence_refs 누락** — `v2/debate/run_debate.py:687-690`
    - 페르소나가 evidence_refs 를 string 으로 반환하면 빈 list. judge prompt 에 못 흘러감.
    - 개선안: string 인 경우 `[s.strip() for s in evidence_refs_raw.split(",") if s.strip()]` 변환.
    - 임팩트: correctness (judge evidence 검증 약화)

14. **fallback 5중 안전망 중 dead-ish layer** — `v2/debate/run_debate.py`
    - ① AG2 import 실패 (L466) 는 prod 에서 dead. ②/③ 도 대부분 ④ 이전에 처리됨.
    - 권고: ① 을 dev 전용 marker 로 명시화. 또는 prod hard-import.
    - 임팩트: 유지보수

15. **HITL retrieve_human_cases 의 query_text=transcript_slice 그대로 임베딩 — 길이 / 캐시 미스율** — `v2/judge_agent.py:316,789`
    - transcript 전문 임베딩은 의미 희석. 한 run 내 16 항목 × 2 호출 = 32회 임베딩.
    - 개선안: query_text 를 segment_text (해당 항목 발화 구간) 로 좁히기.
    - 임팩트: cost / 검색 품질 ↑

16. **`_summarize_human_cases` 가 매번 새 dict 생성** — `v2/judge_agent.py:954-980`
    - 미미 (P2 강등 후보)

17. **AG2 cache_seed=None — Bedrock 응답 캐시 비활성** — `v2/debate/team.py:53`
    - 동일 transcript 재평가 (배치/디버깅) 매번 LLM 호출.
    - 개선안: dev 에서는 `cache_seed=42` 토글 env.
    - 임팩트: cost / dev 속도

#### P2 (nice-to-have)

18. **`PERSONA_PROMPTS` import 시 1회 disk read** — `v2/debate/personas.py:64`
    - dev 시 재시작 권장 주석 추가.

19. **`PERSONA_META` 하드코딩된 한글 라벨 / 이모지** — `v2/debate/run_debate.py:58-77`
    - i18n 시 별도 모듈 분리.

20. **`_ITEM_TO_NODE` 가 18개 하드 매핑 — `qa_rules.py` / `nodes/skills/constants.py` 와 중복** — `v2/debate/run_debate.py:81-100`
    - DRY: canonical 상수 import.

21. **`_decide_final` 의 last.verdict.consensus 보정 코드 dead path 가능성** — `v2/debate/run_debate.py:1082-1112`
    - moderator 가 GroupChat 에 안 들어감 → 분기 영원히 False. 코드 삭제 또는 legacy 주석화.

22. **logger 형식 비일관** — `v2/debate/node.py` (이모지 풍부) vs `v2/judge_agent.py` (text-only).

#### 권장 우선순위

P0-1 (HITL 클라이언트 캐시) → P0-3 (1턴 over-shoot) → P0-2 (asyncio.run 누수) → P1-7 (transcript 압축) → P0-5 (rounds=[] judge SOP) → P1-15 (segment-only 임베딩).

---

### be-layer4-serving (수신 완료 2026-04-29 17:44)

검토 대상: `v2/layer4/{gt_comparison,gt_evidence_comparison,overrides_adapter,evidence_refiner,report_generator_v2}.py`, `v2/serving/server_v2.py` (3,931 lines), `v2/scripts/run_direct_batch_v2.py`. 프롬프트의 `score_validation.py` / `consistency_check.py` 등은 V2 에 부재 (V1 잔재 — layer3 orchestrator_v2 가 흡수).

#### P0 (정확성/안정성/누수)

1. **GT 시트 매칭 알고리즘 server `/v2/gt-scores` 와 `gt_comparison.py` 에 중복 — drift 위험** — `v2/serving/server_v2.py:740-768` + `v2/layer4/gt_comparison.py:96-114`
   - 두 곳에서 정확히 동일한 lenient 3단 매칭 로직 인라인. 668451-A 류 변형 한쪽만 패치 시 batch 와 UI 결과 달라짐.
   - 개선안: `v2/layer4/_gt_loader.py` 에 `match_sheet(sheet_names, sample_id, explicit_sheet)` 단일 함수 추출. xlsx path resolution (Windows/Linux 후보) 도 통합.
   - 임팩트: correctness, maintainability

2. **SSE `/evaluate/stream` 종료 시 `accum.evaluations` 가 그대로 result 이벤트에 직렬화 — 2~5MB 단일 이벤트** — `v2/serving/server_v2.py:2367-2378`
   - `result` SSE 이벤트가 final_state 전체를 한 번의 `data:` 라인으로 푸시. node_traces hang 의 근본 원인.
   - `_sanitize_trace_output` 은 `node_trace` 이벤트(라인 2317) 에만 적용, `result` 페이로드에는 미적용.
   - 개선안: `result_summary` (final_score / 카테고리 점수) 즉시 emit + `result_detail_<chunk_idx>` 페이지네이션. 또는 `evaluations[].evidence` max 5건으로 컷 + `_sanitize_trace_output(result)` 통과.
   - 임팩트: latency (SSE disconnect), mem

3. **`/v2/rag/build` SSE 에 keepalive 없음 — bootstrap 30s+ idle 구간에서 nginx/cloudflare disconnect** — `v2/serving/server_v2.py:589-669`
   - `_asyncio.to_thread(line_q.get)` 무한 대기.
   - 개선안: `asyncio.wait_for(..., timeout=15)` + TimeoutError 시 `: keepalive\n\n` yield (다른 SSE 와 동일 패턴).
   - 임팩트: correctness (bootstrap UI 끊김)

4. **`/save-xlsx`, `/v2/gt-scores`, `/v2/result/full`, `/v2/review/edits`, `/hitl_rag_*` 가 async route 안에서 동기 file I/O — event loop 블록** — `v2/serving/server_v2.py:730, 886, 3129, 3191, 3563-3578, 3732-3742`
   - openpyxl.load_workbook, write_bytes, read_text, glob+sort+parse 루프. hitl_rag_cases 1000+ 파일이면 수 초 블록.
   - 개선안: 각각 `await asyncio.to_thread(...)` 로 감싸기.
   - 임팩트: latency (다른 동시 요청 hang)

5. **`_apply_debate_overrides` 에서 `ITEM_MAX_SCORE` import 가 매 evaluation 마다 실행 + inner/new_inner 양쪽에 동일 코드 18줄 중복** — `v2/serving/server_v2.py:1361-1368, 1394-1434`
   - 개선안: 모듈 레벨 import + lazy cache. `_assign_overrides(target_dict, final_score, judge_*, ...)` 헬퍼 추출.
   - 임팩트: maintainability

6. **gt_evidence_comparison 결과 캐시 부재 — 동일 (sample_id, item) 재호출 시 LLM 재실행** — `v2/layer4/gt_evidence_comparison.py:196-256`
   - "1시간 TTL 캐시" 는 미구현 상태로 보임 (이전 컨텍스트가 다른 브랜치일 가능성).
   - 개선안: `_GT_EVIDENCE_CACHE: dict[(sample_id, item, ai_evidence_hash), dict]` + TTL 3600s. ai_evidence content hash 자연 무효화.
   - 임팩트: cost ($0.34/샘플 절감), latency

7. **`run_direct_batch_v2.py extract_result` 에 gt_comparison / debates 누락 — batch 결과가 UI 결과와 다름** — `v2/scripts/run_direct_batch_v2.py:108-134`
   - `final_state["gt_comparison"]`, `final_state["gt_evidence_comparison"]`, `final_state["debates"]` 미저장. `_apply_debate_overrides` 도 batch 에서 미적용.
   - 개선안: extract_result 에 3개 필드 추가 + debate override import.
   - 임팩트: correctness

8. **`_save_consultation_edits_snapshot` 의 datetime 직렬화가 `default=str`** — `v2/serving/server_v2.py:2598-2643`
   - sqlite3 row 의 datetime 객체가 들어오면 Python repr 로 직렬화 (ISO 보장 안 됨).
   - 개선안: `default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)`.
   - 임팩트: correctness (HITL 스냅샷 ISO 파싱 클라이언트 깨짐)

#### P1

9. **`_DISCUSSION_GATES` FIFO cleanup 이 단순 list slice — 활성 gate 도 제거 가능** — `v2/serving/server_v2.py:127-134`
   - 개선안: `created_at` 보관 + 30분+ signaled=True 인 것만 cleanup. 종료 시 명시 pop.
   - 임팩트: mem, correctness (long-running hang)

10. **`SSELogHandler` 가 매 요청마다 5개 logger 에 attach — 동시 요청 cross-talk** — `v2/serving/server_v2.py:1953-1961, 2350-2356`
    - 두 개의 동시 `/evaluate/stream` 이 서로의 로그를 받음.
    - 개선안: contextvar session_id 주입 후 일치하는 큐만 emit. 또는 logger 패턴 폐기 → graph state 명시 채널.
    - 임팩트: correctness, privacy (다른 세션 로그 노출)

11. **`_LAYER2_SUB_AGENTS` / `_SUB_AGENT_ITEM_MAP` drift — 신한 dept 미지원** — `v2/serving/server_v2.py:1531-1540, 1586-1595`
    - dept agent 의 `assigned_turns` / `rule_pre_verdicts` UI Trace 탭에서 누락.
    - 개선안: dept registry items 동적 보강.
    - 임팩트: correctness (UI Trace 신한 dept 비어 보임)

12. **`_node_phase` 에 GT 비교 노드 누락 — "other" 분류로 phase event 미발송** — `v2/serving/server_v2.py:1565-1574`
    - 개선안: `_BACKEND_TO_FRONTEND_NODES` 에 gt_comparison / gt_evidence_comparison 추가.
    - 임팩트: UX (GT 비교 무음 진행)

13. **`SAGEMAKER_MAX_CONCURRENT × BATCH_MAX_CONCURRENT` 곱셈효과 — burst 30+ Bedrock 호출** — `run_direct_batch_v2.py:49`, `gt_evidence_comparison.py:49`, `nodes/llm.py SAGEMAKER_MAX_CONCURRENT=10`
    - 2 × 8 sub-agent × 1.5 = 24 + gt_evidence 5 = 30+ → throttle 즉발. CLAUDE.md 표 값이 곱셈 미인지.
    - 개선안: 글로벌 LLM 세마포어를 graph state 에 주입 (sub-agent + gt_evidence + judge + reconciler 모두 동일 세마포어 await). 또는 `BATCH × SAGEMAKER ≤ 모델 RPM/60` 가드.
    - 임팩트: cost / latency (throttle retry burn)

14. **`apply_overrides_to_scores` 의 affected_items=[] 케이스 silent skip** — `v2/layer4/overrides_adapter.py:211-227`
    - "override 적용됨" 표시되는데 점수 그대로.
    - 개선안: warning + applied=False 강제. 또는 trigger fallback.
    - 임팩트: correctness (silent override 미적용)

15. **`_extract_response`: orchestrator 가 debate 미적용 점수로 raw_total 계산 → 응답 단계 evaluations 만 덮어쓰기 → 총점 ≠ 항목합** — `v2/serving/server_v2.py:1442`
    - 개선안: orchestrator (layer3) 에서 debates 보고 합산. 또는 `_apply_debate_overrides` 후 raw_total 재계산.
    - 임팩트: correctness (UI 총점 vs 카드 합 불일치)

16. **`run_direct_batch_v2.py output_file.exists()` skip 이 incomplete write 도 skip** — `v2/scripts/run_direct_batch_v2.py:147-149`
    - 0-byte / truncated JSON 도 skip → silent corruption.
    - 개선안: `.tmp` write → rename atomic. 또는 size > 100 + json.loads validate.
    - 임팩트: correctness

17. **`set_runtime_force_single` process-global state — 동시 요청 race** — `v2/serving/server_v2.py:1483-1492, 1836`
    - 다중 사용자 환경에서 persona_mode 토글이 다른 요청에 누설.
    - 개선안: graph state 에 `force_single_persona` 필드 + sub-agent 가 그 값 read. process-global 폐기.
    - 임팩트: correctness

18. **`gt_comparison.py` xlsx fallback 이 Windows-only — EC2 Linux 에서 GT 비교 무용** — `v2/layer4/gt_comparison.py:60-68`
    - 환경변수 없으면 `r"C:\Users\META M\Desktop"` 탐색. server `/v2/gt-scores` 는 Linux fallback 있음.
    - 개선안: P0-1 의 공통 `_xlsx_utils.resolve_path()` 에 양쪽 fallback 통합.
    - 임팩트: correctness (EC2 layer4 GT 비교 무용)

#### P2

19. `_count_source_docs` / `_list_tenant_scopes` 동기 disk walk → `to_thread` 감싸기 (server_v2.py:287-373)
20. evidence flatten 로직이 `gt_comparison`, `_node_item_scores`, `_compact_items_for_judge`, `report_generator_v2` 4군데에 중복 → 단일 `flatten_evaluation()` 헬퍼
21. `_group_items_by_category` 가 priority_flags 투영 후 재호출 (report_generator_v2.py:493, 554) → 한 번 빌드 후 in-place mutate
22. `/analyze-compare` Sonnet 4 prompt caching 미사용 → 50%+ 비용 절감 가능 (server_v2.py:3253-3259)
23. `_LOG_STREAM_LOGGERS` rate limit 부재 → 1000+ 라인 폭주 가능 (server_v2.py:1905)
24. `refine_evidence` quote[:40] prefix 매칭 false positive (evidence_refiner.py:83) → 정규화 비교
25. `_parse_verdict` 키워드 폴백 mismatch 우선순위 모호 (gt_evidence_comparison.py:127-135)
26. evaluation flatten 로직 중복 (P2-20 과 동일 패턴)
27. `run_direct_batch_v2.py asyncio.wait_for` 단일 단계 — 노드 단위 grace 없음 → timeout 시 partial state dump
28. `_adapt_report_for_frontend` categories items 와 `report["item_scores"]` 이중 적용 가능성 (server_v2.py:1208-1281)
29. `_resolve_site_id` 캐시 없음 (report_generator_v2.py:74-81) — micro
30. `/v2/aws-resources` 6개 boto3 sync 호출 — 1~3초 event loop 블록 (server_v2.py:894-1131)

#### 종합 우선순위

가장 시급: **P0-1 (GT 매칭 중복)** + **P0-2 (SSE result 5MB 단일 푸시 — disconnect 근본 원인)** + **P0-13 (LLM 곱셈 throttle)**.
공통 패턴: flatten_evaluation 헬퍼 부재로 4-5중 중복. 한 번 정리 시 P0-1 / P2-20 / P2-26 동시 해결.

---

### fe-react (수신 완료 2026-04-29 17:47)

전반적으로 PipelineFlow / EvaluationNode / LayerNode / GroupNode 는 `memo + custom comparator + 모듈 스코프 hoist` 가 잘 적용되어 있음. 다만 **상위 (EvaluateRunner / AppStateContext / DiscussionModal mount / 키프얼라이브 패널)** 에서 보호막을 무효화하는 패턴 다수.

#### P0 (정확성/UX 망가짐 / 메모리 누수 / 잘못된 메모이제이션)

1. **AppStateContext value 객체가 매 dispatch 마다 재생성 → 모든 useAppState 소비자 재렌더** — `lib/AppStateContext.tsx:571-603`
   - `state` 가 deps 에 있어 SSE 이벤트당 reducer → state 새 ref → value 새 ref → 25+ 소비자 fan-out 재렌더. SSE 100/초 시 100회 풀-앱 재렌더.
   - 개선안: state-context / dispatch-context 분리. dispatch-context value 는 mount 시 1회만 생성. 또는 `useSyncExternalStore` / zustand 로 selector 패턴.
   - 임팩트: TTI / FPS (SSE 폭주 시)

2. **DiscussionModal mount 가 매 SSE 이벤트당 새 props ref → memo 무력화 → 1100줄 트리 재렌더** — `components/EvaluateRunner.tsx:2539-2624`
   - `Object.entries(debateByNode).map(...)` + 5개 inline arrow handler 매 렌더 새 ref. shallow comparator 항상 false.
   - 개선안: `useMemo([debateByNode])` 로 activeDebates 메모, handler `useCallback`. 모달 닫혔을 때 본체 자체 미마운트 (`{discussionNodeId !== null && <DiscussionModal/>}`).
   - 임팩트: FPS (EvaluateRunner 가장 큰 hot path)

3. **transcript snapshot effect 매 키 입력마다 100KB+ JSON.stringify** — `components/EvaluateRunner.tsx:1155-1174`
   - `JSON.stringify([transcript, appState.siteId, ...])` — STT 텍스트 수십~수백 KB 를 매 입력당 전체 직렬화.
   - 개선안: `useEffect(() => { reset(); }, [transcript, appState.siteId, ...])` — React 가 deps reference equality 로 비교. stringify 자체 불필요.
   - 임팩트: typing 지연 / 메모리

4. **EvaluationNode `e.currentTarget.style.background` 직접 mutate + React commit race** — `components/nodes/EvaluationNode.tsx:414-425`
   - inline DOM mutate 가 다음 commit 의 background prop 과 race. debateStatus 전환 직후 잔존.
   - 개선안: CSS `:hover` pseudo class. button 에 `transition: background` 이미 있음.
   - 임팩트: 시각적 미세 떨림 + 패턴 자체가 다른 곳에 퍼지면 누수 위험

5. **AppShell + usePipelineRun + MatrixPanel + RagAdminPanel 이 deprecated `BASE_URL` 사용 — runtime serverUrl override 무효화** — `AppShell.tsx:9,75,111,267`, `usePipelineRun.ts:14`, `MatrixPanel.tsx:9,93`, `RagAdminPanel.tsx:10,95`
   - AppShell health check 가 정적 BASE 로 고정 → 사용자가 Server URL 바꿔도 health 인디케이터 잘못된 endpoint.
   - 개선안: `getBaseUrl()` 호출로 마이그.
   - 임팩트: 정확성/UX (batch 평가가 wrong server 로 갈 수 있음)

#### P1 (성능)

6. **MainTabs keep-alive 9개 패널이 모두 `useAppState()` 구독 → 모두 재렌더** — `components/MainTabs.tsx:77, 209-219, 277-293`
   - `display:none` 으로 mount 유지 + 전체 state 구독 → SSE 이벤트당 9패널 재렌더, DOM 5,000+ 노드 commit.
   - 개선안: P0-1 (selector pattern) 적용 시 자동 완화. MainTabs 도 필요 5개 필드만 슬라이스 구독.

7. **ResultsTab 매 렌더 IIFE 로 verification_issues / 토론 entries 정렬** — `components/results/ResultsTab.tsx:597-871, 968-1015`
   - 두 개의 큰 `(() => {...})()` IIFE 가 매 렌더 build/sort/filter.
   - 개선안: `useMemo([report])` 또는 별도 컴포넌트 추출.

8. *(원 리포트 P1-8: 위험 없음 — useMemo deps primitive 라 안정. 통과)*

9. *(원 리포트 P1-9: P2 수준)*

10. **NodeDrawer 항상 mount — 4 useMemo 가 SSE 이벤트당 재실행** — `GlobalNodeDrawer.tsx:30-39`, `results/NodeDrawer.tsx:163`
    - `<NodeDrawer nodeId={state.openNodeId}/>` 항상 마운트. nodeId=null 일 때 line 163 에서 return 하지만 그 위 useMemo 4개 (lines 120, 130, 137, 142, 154) 는 실행. SSE 이벤트당 streamingItems/traces/rawLogs filter+slice.
    - 개선안: GlobalNodeDrawer 에서 `if (!state.openNodeId) return null;` 가드.

11. **EvaluateRunner inline arrow handler 다수 — useCallback 누락** — `EvaluateRunner.tsx:2200-2400, 2408-2520`
    - 자식 input/select 가 memo 없어 무영향이나 일관성 측면.

12. **번들 — ManualEvalAttach 가 PipelinePanel chunk 에 xlsx 정적 import (~250KB gz)** — `lib/manualEvalParser.ts:17`
    - 개선안: `xlsxExport.ts:61` 의 `XLSX = await import("xlsx")` 동적 import 패턴 적용.
    - 임팩트: 평가 페이지 TTI (모바일/저속망)

13. **BusEdge / FlowEdge EdgeTraveler — animated 토글 시 `<animateMotion>` 60+ 동시** — `components/edges/EdgeTraveler.tsx:53-69`
    - chainCount=3 SMIL × 활성 엣지 20+ = 60+ animation. SMIL 자체 경량이나 누적 비용.
    - 우선도 P2 수준. 회귀 위험 큼.

#### P2 (nice-to-have)

14. **DebatePanel.tsx dead code 의심** — 250줄. type 만 import 됨.
15. **NodeDrawer mount 가드 (P1-10 과 동일 패턴)**
16. **`useCountUp` strict mode double mount 잠재 이슈** — `lib/useAnimations.ts:32-41`
17. **`itemToNodeId` LEGACY_TO_V2_NODE 우려는 false alarm — 모듈 상수 안정**
18. ItemCard inline arrow handlers 정리
19. PostRunReviewModal extractTurnsFromPreprocessing 위험 없음 (가드 있음)
20. **`"use client"` 45개 — server component 활용 0** — 평가 페이지는 SSE 라 자연스러우나 정적 데이터 prefetch 여지
21. raw `<img>` 미사용 ✓
22. **무거운 dep import**: lodash/moment/date-fns 정적 import 없음 ✓. xlsx 만 P1-12.

#### 핵심 P0 3개 요약

1. **AppStateContext value 재생성 → 풀-앱 fan-out 재렌더** (`lib/AppStateContext.tsx:571-603`)
2. **DiscussionModal mount 가 SSE 이벤트당 1100줄 트리 재렌더** (`EvaluateRunner.tsx:2539-2624`)
3. **transcript snapshot JSON.stringify 매 키 입력당 100KB+ 직렬화** (`EvaluateRunner.tsx:1155`)

PipelineFlow 자체는 잘 보호됨 — 상위 컴포넌트에서 보호막 깨는 것이 핵심. 번들 P1: xlsx 동적 import (~250KB gz 절감).

---

## PL 종합

4명 리포트 교차 분석. 영역간 **공통 root cause** 가 여러 P0 의 근본 원인 — 한 번 픽스 시 multiple 해결.

---

### 🔴 Tier S — 즉시 수정 (운영 안정성/정확성 직결)

| # | 이슈 | 영역 | 근거 | 임팩트 |
|---|------|------|------|--------|
| **S1** | **node_traces 누적 + SSE result 5MB 단일 이벤트** | be-pipeline-core P0-5 + be-layer4-serving P0-2 | `graph_v2.py:526` + `graph.py:91-127` 의 깊이 5 walk + `server_v2.py:2367-2378` 의 final_state 통째 직렬화 | SSE idle disconnect (CLAUDE.md 명시 "in-process batch 우회"의 직접 원인) |
| **S2** | **이중 세마포어 + LLM 곱셈효과 throttle** | be-pipeline-core P0-1/P0-2 + be-layer4-serving P1-13 | `_llm.py:165-176` 외부 sem + `nodes/llm.py:599` 내부 sem 두 번 acquire. `_GROUP_B_MAX_CONCURRENT=1` 기본값 + `BATCH × SAGEMAKER × sub_agent` 곱셈 = burst 30+ | latency (51 호출 직렬화 → 분 단위 회귀) + cost (Bedrock throttle retry burn) |
| **S3** | **AppStateContext value 재생성 → 풀-앱 fan-out 재렌더** | fe-react P0-1 | `AppStateContext.tsx:571-603` state deps 로 인한 25+ 소비자 fan-out | TTI / FPS (SSE 100/초 시 100회 풀-앱 재렌더) |
| **S4** | **GT 매칭 알고리즘 server vs layer4 중복 — drift 위험** | be-layer4-serving P0-1 | `server_v2.py:740-768` ≅ `gt_comparison.py:96-114`. 668451-A 류 한쪽만 패치 시 batch≠UI | correctness |
| **S5** | **응답 캐시 키가 backend/model/max_tokens 무시** | be-pipeline-core P0-3 | `nodes/llm.py:568` `_response_cache_key(messages, None, None, 0)` | correctness (Sonnet→Haiku 전환 시 첫 응답 캐시 hit) |
| **S6** | **HITL AOSS 클라이언트 매 호출 신규 생성 — boto3 N+1 + fd 누수** | be-debate-hitl P0-1 | `hitl/rag_retriever.py:43-56` 매번 `_make_client` | latency (call당 200-500ms IMDS+TLS) + fd 누수 |
| **S7** | **AG2 라운드 1턴 over-shoot — 모든 토론에 추가 Bedrock 호출** | be-debate-hitl P0-3/P0-6 | `team.py:319-341` + `team.py:121-132` `_is_termination` 호출 시점 + cap 처리 race | cost (16 항목 × 1턴 추가 / run) |

---

### 🟠 Tier A — 단기 수정 (성능/정확성)

| # | 이슈 | 영역 | 비고 |
|---|------|------|------|
| A1 | **8 평가 노드 helper 거의 동일 코드 중복 (~1500줄)** | be-pipeline-core P1-1 | `evaluate_item_with_personas` 단일 추출 + `_helpers.py` 모음. 미세 차이 이미 발생 (ITEM_MAX_SCORE 사용 vs 하드코딩) |
| A2 | **SSE async route 안 동기 file I/O 5+ 곳** | be-layer4-serving P0-4 | openpyxl/read_text/glob+sort 가 event loop 블록. 각각 `to_thread` 감싸기 |
| A3 | **`/v2/rag/build` SSE keepalive 부재** | be-layer4-serving P0-3 | bootstrap 30s+ idle 시 nginx/cloudflare disconnect |
| A4 | **DiscussionModal 1100줄 트리 매 SSE 이벤트 재렌더** | fe-react P0-2 | `useMemo` + `useCallback` + 가드 마운트 |
| A5 | **judge transcript 압축 미적용 + segment-only 임베딩** | be-debate-hitl P1-7 + P1-15 | 64회/run 동일 transcript Bedrock context. 입력 토큰 30-50% 절감. prompt caching (`cache_control`) 활성화 |
| A6 | **gt_evidence_comparison 캐시 부재** | be-layer4-serving P0-6 | (sample_id, item, ai_evidence_hash) 키 + TTL 3600s. $0.34/sample 절감 |
| A7 | **`run_direct_batch_v2 extract_result` 가 gt_comparison/debates 누락** | be-layer4-serving P0-7 | batch ≠ UI 결과. extract_result 에 3 필드 추가 |
| A8 | **transcript snapshot JSON.stringify 매 키 입력당 직렬화** | fe-react P0-3 | `useEffect` deps reference equality 사용 |
| A9 | **L1 정규식 매 호출 컴파일** | be-pipeline-core P1-2 | 모듈 로드 시 precompile (수십 ms/sample 절감) |
| A10 | **`asyncio.run()` ThreadPoolExecutor 워커에서 호출 — loop mismatch 위험** | be-debate-hitl P0-2 | per-loop sem 또는 root loop + run_coroutine_threadsafe |
| A11 | **`SSELogHandler` 동시 요청 cross-talk** | be-layer4-serving P1-10 | contextvar session_id 또는 logger 패턴 폐기 |
| A12 | **`_apply_debate_overrides` 후 raw_total 미재계산 → 총점 ≠ 항목합** | be-layer4-serving P1-15 | orchestrator 에서 합산 또는 응답 단계 재계산 |

---

### 🟡 Tier B — 중기 수정 (유지보수/UX)

| # | 이슈 | 영역 | 비고 |
|---|------|------|------|
| B1 | flatten_evaluation 헬퍼 부재로 4-5중 중복 (be-layer4 P2-20/26) | layer4 + serving | 단일 헬퍼로 P0-S4 동시 해결 |
| B2 | `_DISCUSSION_GATES` FIFO cleanup 활성 gate 도 제거 가능 | be-layer4 P1-9 | `created_at` + signaled 검사 |
| B3 | dept registry 가 `_LAYER2_SUB_AGENTS` 와 drift | be-layer4 P1-11 | 신한 dept Trace 탭 비어 보임 |
| B4 | `_node_phase` 에 GT 비교 누락 (UX 무음 진행) | be-layer4 P1-12 | `_BACKEND_TO_FRONTEND_NODES` 추가 |
| B5 | `SAGEMAKER_MAX_CONCURRENT` env 충돌 (group_a 10 vs group_b 1) | be-pipeline-core P0-2 | 별도 env 또는 sem 통합 |
| B6 | `apply_overrides_to_scores` affected_items=[] silent skip | be-layer4 P1-14 | warning + applied=False 강제 |
| B7 | persona prompt build_team 마다 재빌드 (48회) | be-debate-hitl P1-8 | `lru_cache`(persona, tuple(steps)) |
| B8 | `set_runtime_force_single` process-global race | be-layer4 P1-17 | graph state 로 전달 |
| B9 | `_decide_final` consensus 가 라운드 1 만장일치도 종료 → debate 의미 약화 | be-debate-hitl P1-10 | `len(rounds) >= max_rounds` 일 때만 인정 |
| B10 | `MainTabs` keep-alive 9패널 모두 fan-out (P0-S3 의 파생) | fe-react P1-6 | S3 적용 시 자동 완화 |
| B11 | `gt_comparison.py` Linux fallback 미동기화 | be-layer4 P1-18 | EC2 에서 layer4 GT 비교 무용 |
| B12 | `run_direct_batch_v2 output_file.exists()` 가 incomplete write 도 skip | be-layer4 P1-16 | `.tmp` write → atomic rename |
| B13 | NodeDrawer 4 useMemo 가 SSE 이벤트당 재실행 (닫힘 상태) | fe-react P1-10 | GlobalNodeDrawer 가드 |
| B14 | xlsx 정적 import (~250KB gz) | fe-react P1-12 | 동적 `await import("xlsx")` |
| B15 | `_route_after_layer3` plan dict/pydantic 미호환 | be-pipeline-core P1-5 | 양쪽 호환 처리 |
| B16 | `build_item_verdict` (group_a) ↔ `make_item_verdict` (group_b) drift | be-pipeline-core P1-6 | `verdict_builder.py` 추출 |

---

### 🟢 Tier C — 정리/Nice-to-have

위 P2 목록 + dead code (DebatePanel, MODERATOR 분기, SAGEMAKER_MAX_OUTPUT_TOKENS 등). 별도 cleanup 스프린트로.

---

### 📐 통계

- 총 audit 항목: **약 90건** (P0 24 / P1 35 / P2 31)
- 영역간 공통 패턴 (root cause 1개로 multiple 해결):
  - **node_traces 누적** → server_v2 SSE result 5MB + graph_v2 trace walk (S1)
  - **세마포어 + 곱셈효과** → group_b 이중 sem + batch × sagemaker × sub_agent (S2)
  - **AppState fan-out** → 25+ 소비자 + 9 keep-alive 패널 (S3 + B10)
  - **GT 매칭 중복** → server vs layer4 + Linux fallback drift (S4 + B11)
  - **flatten_evaluation 부재** → 4-5중 중복 (B1 + 다수 P2)

---

### 🎯 권장 액션 플랜 (4-스프린트)

**Sprint 1 (긴급, ~3일)**: S1 + S2 + S5 + S6
- node_traces cap (env 게이팅) + SSE result 분할
- group_b 이중 sem 제거 (외부 sem 삭제)
- 응답 캐시 키에 model/backend 포함
- HITL 클라이언트 모듈 캐시 (`_HITL_CLIENT` 전역)

**Sprint 2 (안정성, ~5일)**: S3 + S4 + S7 + A2 + A3 + A11
- AppStateContext state/dispatch split
- GT 매칭 단일 함수 추출 (`_gt_loader.py`)
- AG2 round 종료 플래그 hook 즉시 마킹
- async route 동기 I/O 모두 `to_thread`
- SSE keepalive `/v2/rag/build` 추가
- SSELogHandler contextvar session

**Sprint 3 (성능, ~5일)**: A1 + A4 + A5 + A6 + A7 + A8 + A9
- 8 평가 노드 helper 통합 (`evaluate_item_with_personas`)
- DiscussionModal memo + 가드 마운트
- judge transcript 압축 + segment 임베딩 + prompt caching
- gt_evidence_comparison 캐시
- run_direct_batch_v2 extract_result 보강
- transcript snapshot stringify 제거
- L1 정규식 precompile

**Sprint 4 (유지보수, ~5일)**: Tier B 항목 묶음

---

### 🔍 사용자 의사결정 필요 항목

1. **AG2 토론 종료 정책**: B9 — "라운드 1 만장일치도 종료" vs "max_rounds 까지 진행 후 합의 검증"
   - **결정 (2026-04-30)**: A 유지 (현재 동작) — 토론 결과 신뢰. 변경 없음.
2. **`_GROUP_B_MAX_CONCURRENT`** 의 본래 의도: 별도 한도 vs sem 통합
   - **결정 (2026-04-30)**: B 통합 적용 완료. 아래 "적용 내역" 참조.
3. **응답 캐시 활성/비활성**: 1시간 TTL 응답 캐시가 retry 안전망 외 효용 낮음 — env 게이팅 비활성 기본값 검토.

---

## ✅ 적용 내역

### 2026-04-30 — Tier S 7건 모두 적용 완료

| # | 이슈 | 파일 | 검증 |
|---|------|------|------|
| **S1** | node_traces slim + SSE result evidence 컷 | `graph.py::_record_trace` (`QA_FULL_TRACE` env 게이팅) + `server_v2.py::_trim_evidence_lists_inplace` (5건 컷) + result 페이로드에서 node_traces pop | AST OK |
| **S2** | group_b 외부 세마포어 제거 | `v2/agents/group_b/_llm.py` 전면 정리 | AST OK + ruff |
| **S3** | AppStateContext state/actions split | `lib/AppStateContext.tsx` — `AppStateInternalContext` (state) + `AppStateActionsContext` (dispatch+helpers stable ref) + neue `useAppActions()` / `useAppStateSlice()` hook 추가. `useAppDispatch()` 가 actions context 만 구독 → state 변경 시 무반응 | tsc 통과 (exit 0) |
| **S4** | GT 매칭 single source 추출 | `v2/layer4/_gt_loader.py` 신규 + `server_v2.py /v2/gt-scores` + `gt_comparison.py::_load_gt_items` 양쪽이 `match_sheet()` 사용 | AST OK |
| **S5** | LLM 응답 캐시 키에 model/backend/max_tokens 포함 | `nodes/llm.py::invoke_and_parse` cache_key 빌드 변경 | AST OK |
| **S6** | HITL AOSS 클라이언트 endpoint 단위 모듈 캐시 | `v2/hitl/rag_retriever.py::_client_or_none` + `_HITL_CLIENT_CACHE` (lock+lazy init) | AST OK |
| **S7** | AG2 라운드 합의 hook 즉시 마킹 (1턴 over-shoot 제거) | `v2/debate/team.py::_hook` 안 should_terminate_consensus 마킹 + `_is_termination` 가 그 플래그 우선 검사 | AST OK |

**부수 효과 (자동 해결된 audit 항목)**:
- ✅ P0-2 (env 충돌 group_a 10 vs group_b 1) — S2 단일 sem 으로 해소
- ✅ P0-4 (`asyncio.get_event_loop()` deprecation) — S2 모듈 자체 제거됨

### 2026-04-30 — HITL 데이터 판사 → 페르소나 이전 (사용자 정책 변경)

**정책**: 판사 LLM 은 RAG/HITL 미사용. HITL 데이터는 페르소나 토론 단계에 주입 (사용자 결정 — Layer 2 sub-agent 통합은 별도 사이클).

**변경 4 파일**:

1. `v2/judge_agent.py`:
   - `deliberate()` 안 HITL retrieval + format 코드 제거 (line 308-345 영역)
   - `deliberate_post_debate()` 안 HITL retrieval 제거 (line 783-808 영역)
   - `_build_post_debate_user_message()` 의 `human_cases` 파라미터 + HITL 섹션 빌드 제거
   - `JUDGE_POST_DEBATE_SYSTEM_PROMPT` 의 "Step 0 (HITL 우선 검토)" 섹션 (규칙 A/B/C) 제거
   - 반환 dict 의 `human_cases_meta` 빈 배열로 (스키마 호환 유지)
   - `retrieved_human_cases_count: 0` 고정

2. `v2/debate/personas.py::build_speak_user_message`:
   - `hitl_cases: list[dict] | None = None` 파라미터 추가
   - 함수 본문에 `[과거 휴먼 검증 사례]` 섹션 주입 로직 — `format_human_cases_for_prompt(hitl_cases)` 사용

3. `v2/debate/run_debate.py`:
   - `initial_msg` 빌드 직전 `retrieve_human_cases(item_number, query=transcript, top_k=3)` 호출
   - 결과를 `build_speak_user_message(hitl_cases=...)` 로 전달
   - 페르소나 broadcast 메시지에 HITL 섹션 포함됨 (모든 페르소나가 동일 사례 참조)

4. `v2/serving/server_v2.py::_apply_debate_overrides`:
   - `🎭 [판사 판정 · HITL N건 참조] ...` → `🎭 [판사 판정] ...` 로 단순화 (판사가 HITL 미참조이므로)

**스킵된 작업** (사용자 지시):
- ❌ HITL 골든셋 카테고리 (Layer4 신규 노드 + 패널) — "이건 필요없고"
- ⏸ Layer 2 sub-agent 8개 HITL 통합 — 별도 사이클 (큰 작업)

**검증**: 4 파일 AST OK.

### 2026-04-30 — HITL 사례 프론트 노출 + 페르소나 라벨 한국어화 (후속 패치)

페르소나가 참고한 HITL 사례를 프론트 노드 드로어에 표시하도록 데이터 흐름 확장.

**변경 6 파일**:

1. `v2/debate/schemas.py` — `DebateRecord.persona_hitl_cases: list[dict]` 신규 필드 추가 (judge_human_cases 는 deprecated 로 주석 갱신)
2. `v2/debate/run_debate.py` — `debate_hitl_cases` retrieve 결과를 `_summarize_human_cases` 로 요약 → `persona_hitl_cases_summary` 변수 → DebateRecord 채움 + `discussion_finalized` SSE 이벤트에도 포함
3. `v2/debate/node.py` — `debate_rec` → evaluation merged_inner 매핑 시 `persona_hitl_cases` 포함
4. `v2/serving/server_v2.py::_apply_debate_overrides` — inner 와 new_ev 양쪽에 `persona_hitl_cases` 필드 추가
5. `chatbot-ui-next/lib/types.ts` — `persona_hitl_cases` 타입 추가 (CategoryItem + 토론 record 양쪽), `judge_human_cases` 는 DEPRECATED 주석
6. `chatbot-ui-next/components/results/NodeDrawer.tsx` — 라벨 "📚 판사 참조 HITL 사례" → "📚 페르소나 참조 HITL 사례", `persona_hitl_cases` 우선 사용 + 과거 `judge_human_cases` fallback (backward compat)

**페르소나 라벨 영어 → 한국어 통일** (1 파일):

7. `chatbot-ui-next/components/results/PersonaExecutionDetails.tsx` — `PERSONA_META.label` 을 `"Strict"/"Neutral"/"Loose"` → `"품격"/"정확성"/"고객경험"` 변경. 다른 컴포넌트 (JudgePanel/PersonaScores/PersonaBarChart) 와 일관성 유지.

**검증**: 6 백엔드 AST OK + frontend tsc 통과 (exit 0).

### 2026-04-30 — HITL → 골든셋 RAG 통합 (실험 단계 — 출처 분리 노출)

**정책**: HITL 데이터를 "골든셋 RAG" 의 한 출처로 통합 호칭. 향후 외부 큐레이션 등 추가 source 가능. 실험 단계라 RAG 관리 탭에서 HITL 빌드 섹션을 별도로 노출.

**변경**:

1. `v2/debate/personas.py::build_speak_user_message` — prompt 섹션 헤더 변경:
   - "[과거 휴먼 검증 사례 — 동일 평가 항목 #N, 총 K건]" → "[골든셋 사례 — 사람 검수 정답 (평가 항목 #N, 총 K건)]"

2. `v2/debate/run_debate.py` — 골든셋 retrieve 후 self-match 마킹:
   - `current_cid = req.consultation_id` 와 각 사례의 `consultation_id` 비교
   - 일치 시 `case["is_self_match"] = True` 마킹 (현재 평가 중 원문 자체의 사례 식별)

3. `v2/judge_agent.py::_summarize_human_cases` — frontend 표시용 요약에 신규 필드 추가:
   - `is_self_match: bool` — 자기 자신 매칭 여부
   - `source: str` — 골든셋 출처 (현재 "hitl", 향후 다른 source 가능)

4. `chatbot-ui-next/lib/types.ts` — `persona_hitl_cases[]` 항목에 `source?: string` + `is_self_match?: boolean` 필드 추가 (CategoryItem + DebateRecord 양쪽)

5. `chatbot-ui-next/components/results/NodeDrawer.tsx`:
   - 라벨 "📚 페르소나 참조 HITL 사례" → "📚 페르소나 참조 골든셋"
   - "매칭 사례 없음 (qa-hitl-cases 인덱스에 ...)" → "매칭 사례 없음 (골든셋 RAG 에 ...)"
   - 각 사례 카드에 출처 배지 (`source="hitl"` 노란 라벨) + 자기 자신 매칭 시 🔁 동일 상담 빨간 배지 추가

6. `chatbot-ui-next/components/tabs/RagAdminPanel.tsx`:
   - HitlRagSection 위에 "🌟 골든셋 RAG 관리 (실험)" 패널 신규 추가 — 골든셋 개념 + 현재 출처 (HITL) 명시
   - HitlRagSection 의 panel-title "🔁 HITL 검수 → 판사 학습 데이터" → "🔁 골든셋 RAG · HITL 빌드"
   - 부제 "HITL 검수 데이터를 RAG corpus 로 빌드" → "HITL 검수 데이터를 골든셋 RAG corpus 로 빌드 ... 페르소나 토론 단계에서 참조"

**검증**: 3 백엔드 AST OK + frontend tsc 통과 (exit 0).

---

### 2026-04-30 — group_b 외부 세마포어 제거 (Tier S #2 부분, P0-2/P0-4 자동 해결)

**파일**: `packages/agentcore-agents/qa-pipeline/v2/agents/group_b/_llm.py`

**변경**:
- 제거: `_GROUP_B_MAX_CONCURRENT`, `_loop_semaphores`, `_semaphore_lock`, `_get_semaphore`, `import asyncio`, `import threading`
- 제거: `call_bedrock_json` 안의 `sem = _get_semaphore(); async with sem:` wrapper
- 변경 후 동시성: `nodes/llm.py::_get_semaphore` (`SAGEMAKER_MAX_CONCURRENT` env, 기본 10) 단일 sem 만 적용

**부수 효과**:
- ✅ Tier S #2 (이중 세마포어 제거) — 적용
- ✅ P0-2 (env 충돌 group_a 10 vs group_b 1) — 자동 해결 (단일 env)
- ✅ P0-4 (`asyncio.get_event_loop()` deprecation) — 자동 해결 (모듈 자체 제거)

**예상 임팩트**: Group B 의 17 항목 × 3 페르소나 = 51 LLM 호출이 직렬(1) → 병렬(10) — **평가 시간 30-50% 단축 예상**.

**검증**:
- AST syntax check 통과
- ruff 잔존 warning 3건 (I001 import 정렬, @lru_cache → @cache 제안) 모두 pre-existing — 변경과 무관

---

*Generated 2026-04-29 by qa-opt-audit team (Opus, effort=high)*
