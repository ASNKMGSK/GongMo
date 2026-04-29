# QA 평가 파이프라인 V2 (LangGraph 4-Layer)

> **V2 전환 완료 (2026-04-20)**. 운영 코드는 `v2/` 하위 4-Layer + 8 Sub Agent 아키텍처로 이전됨 (`v2/graph_v2.py`, `v2/serving/main_v2.py`, `v2/serving/server_v2.py`, `v2/scripts/run_direct_batch_v2.py`). V1 단일 3-Phase 파이프라인 노드 (greeting / understanding / courtesy / mandatory / scope / proactiveness / work_accuracy / incorrect_check / orchestrator / consistency_check / score_validation / report_generator) 는 제거됨. 루트에 남은 V1 잔존물: `nodes/skills/`, `nodes/dialogue_parser.py`, `nodes/llm.py`, `nodes/qa_rules.py`, `nodes/json_parser.py`, `state.py`, `config.py`, `graph.py` (축소 stub — `_make_tracked_node` 래퍼만 유지). 모두 V2 가 import 로 의존하는 모듈. 본문은 V2 기준.

LangGraph `StateGraph` 기반 4-Layer QA 평가 파이프라인입니다. 전처리 → Sub Agent 병렬 평가 → Orchestrator 집계 → Post-processing (Confidence / Tier / Report) 4단계로 고정된 아키텍처이며, 2026-04-20 Phase A2 최종 확정본이 반영되어 있습니다.

## 4-Layer 아키텍처

```
Input STT → Layer 1 전처리 (단일 노드)
         │   quality_gate / segment_splitter / pii_normalizer
         │   rule_pre_verdictor / deduction_trigger_detector
         │
         ├──→ [short-circuit A] preprocessing.quality.unevaluable → Layer 4 (T3) 직결
         │
         ▼
          Layer 2  Sub Agent 8개 (Send fan-out 병렬)
         │   Group A: greeting / listening_comm / language / needs
         │   Group B: explanation / proactiveness / work_accuracy / privacy
         │
         ▼
          Layer 2 barrier (8 Sub Agent 완료 대기)
         │
         ▼
          Layer 3  Orchestrator V2 (단일 노드)
         │   aggregator / overrides / consistency_checker / grader
         │
         ├──→ [short-circuit B] plan.skip_phase_c_and_reporting → END
         │
         ▼
          Layer 4  Post-processing (단일 노드)
         │   confidence.calculator → routing.tier_router
         │   → layer4.evidence_refiner → layer4.report_generator_v2
         │
         ▼
          QAOutputV2 JSON
```

Short-circuit 2종:
- **A**: `preprocessing.quality.unevaluable=True` 이면 Layer 1 직후 Layer 2/3 스킵, Layer 4 가 T3 라우팅으로 종결
- **B**: `state.plan.skip_phase_c_and_reporting=True` 이면 Layer 3 의 consistency/grade 및 Layer 4 전체 스킵 후 END (프롬프트 튜닝 배치용, V1 플래그 의미 유지)

## Layer 별 역할

| Layer | 구성 모듈 | 입력 | 산출물 |
|-------|-----------|------|--------|
| **Layer 1** | `v2/layer1/node.py` (quality_gate / segment_splitter / pii_normalizer / rule_pre_verdictor / deduction_trigger_detector) | `transcript`, `stt_metadata` | `preprocessing.quality`, `preprocessing.detected_sections`, `preprocessing.pii_tokens`, `preprocessing.intent_type` (str \| dict), `rule_pre_verdicts`, `deduction_triggers` |
| **Layer 2** | `v2/agents/group_a/*.py` + `v2/agents/group_b/*.py` (8 Sub Agent) | `preprocessing` + `llm_backend`/`bedrock_model_id` | `SubAgentResponse` 8건 (`evaluations`) |
| **Layer 3** | `v2/layer3/{aggregator,override_rules,consistency_checker,grader,orchestrator_v2}.py` | `evaluations` + `deduction_triggers` | `orchestrator.final_score`, `orchestrator.overrides`, `orchestrator.consistency_flags` |
| **Layer 4** | `v2/confidence/calculator.py`, `v2/routing/tier_router.py`, `v2/layer4/{evidence_refiner,report_generator_v2,overrides_adapter}.py` | 전체 state | `routing`, `summary`, `coaching_points`, 최종 `QAOutputV2` |

## 8 Sub Agent 매핑

| Group | Sub Agent | 담당 항목 | evaluation_mode 지원 | 비고 |
|-------|-----------|-----------|----------------------|------|
| A | `greeting` | #1 첫인사, #2 끝인사 | full | 3-요소 규칙 + rule_pre_verdict verify mode |
| A | `listening_comm` | #3 경청 (skipped), #4 공감, #5 대기 | full / skipped | #3 은 STT 제약으로 skipped 기본 만점 |
| A | `language` | #6 정중한 표현, #7 쿠션어 | full | #7 refusal-gated (거절 윈도우 있을 때만) |
| A | `needs` | #8 문의 파악, #9 고객정보 확인 | full / structural_only | #9 은 structural_only + force_t3 |
| B | `explanation` | #10 설명 명확성 (10점), #11 두괄식 | full | #10 은 4단계 `[10,7,5,0]` |
| B | `proactiveness` | #12 문제 해결, #13 부연 설명, #14 사후 안내 | full | |
| B | `work_accuracy` | #15 정확한 안내 (10점), #16 필수 안내 이행 | full / partial_with_review | #15 는 업무지식 RAG 부재 시 partial/unevaluable 분기 |
| B | `privacy` | #17 정보 확인 절차, #18 정보 보호 준수 | compliance_based | 두 항목 모두 force_t3 |

### evaluation_mode 6종 (설계서 §5.3)

| 모드 | 의미 | score 처리 |
|------|------|------------|
| `full` | 완전 평가 (모든 정보 사용 가능) | 합산 포함 |
| `structural_only` | 마스킹 등으로 내용 검증 불가, 구조/절차만 평가 (#9) | 합산 포함 |
| `compliance_based` | 규정 준수 여부 기준 평가 (#17, #18) | 합산 포함 |
| `partial_with_review` | AI 판정 + 인간 검수 필수 (#15 RAG 부분 커버리지) | 합산 포함 |
| `skipped` | 해당 상황 부재 (만점 고정, 예: #3 말겹침 없음) | `max_score` 로 합산 |
| `unevaluable` | STT 품질 등으로 평가 불가 | 합산 **제외** + `category_max` 에서도 차감 |

### ALLOWED_STEPS (V2 — `v2/contracts/rubric.py`)

- #1, #2, #4–#9, #11–#14, #16–#18: `[5, 3, 0]`
- #3: `[5]` (skipped 기본 만점 고정)
- #10: `[10, 7, 5, 0]`
- #15: `[10, 5, 0]`

V2 는 #17 / #18 에서 `[5, 3, 0]` — V1 `nodes/qa_rules.py` 의 `[5, 0]` 과 의도적으로 다름 (iter05 snap_score 강제 0 변환 회귀 해소). V1 원본은 수정하지 않고 V2 전용 테이블을 `v2/contracts/rubric.py::ALLOWED_STEPS` 에 둔다. V2 내부 스코어는 반드시 `snap_score_v2(item_number, score)` 경유.

### Override 3종 (`v2/layer3/override_rules.py`)

| 트리거 | 액션 | 영향 범위 |
|--------|------|-----------|
| 불친절 (profanity / contempt / arbitrary_disconnect) | `all_zero` | 총점 0점 |
| 개인정보 유출 (privacy_leak) | `item_zero` | 해당 항목 0점 + 별도 보고 |
| 오안내 미정정 (uncorrected_misinfo) | `category_zero` | 업무정확도 대분류 0점 |

## Confidence · Tier 라우팅

### Confidence 4 신호 (`v2/confidence/calculator.py`)

| 신호 | 소스 | 설명 |
|------|------|------|
| LLM Self-Confidence | Sub Agent 응답 `llm_self_confidence.score` (1~5) | 프롬프트 앵커 명시 |
| Rule vs LLM 일치도 | `RuleLLMDelta.agreement` | Layer 1 rule_pre_verdict ↔ LLM 판정 |
| RAG stdev | Reasoning RAG `retrieve_reasoning` | `sample_size < rag_min_sample_size` 이면 penalty (소표본 보수) |
| Evidence 품질 | high/medium/low 라벨 | Sub Agent self-report |

### Tier (`v2/routing/tier_router.py`)

| Tier | 의미 | 비중 (초기) | 조건 |
|------|------|-------------|------|
| T0 | 자동 통과 | `routing.initial_t0_cap` (generic=0.30) | 모든 신호 high, 강제 검수 조건 없음 |
| T1 | 스팟체크 | `routing.t1_sample_rate` (generic=0.05) | T0 중 무작위 샘플 |
| T2 | 플래그 검수 | ~15~20% | Confidence ≤ 2 / 신호 불일치 / 등급 경계 ±3점 |
| T3 | 필수 검수 | ≤5% | 감점 트리거 / STT 품질 저하 / `force_t3` 항목 |

**FORCE_T3_ITEMS** = `{9, 17, 18}` — 이 중 하나라도 evaluable 이면 상담 전체 T3 강제.

## RAG 3종 (`v2/rag/`)

| RAG | 공개 API | 반환 | 소비 Sub Agent |
|-----|----------|------|----------------|
| Golden-set (few-shot) | `retrieve_fewshot(item_number, intent, segment_text, *, tenant_id, top_k)` | `FewshotResult` (k=3~5) | 모든 Sub Agent (프롬프트 문맥 주입) |
| Reasoning | `retrieve_reasoning(item_number, transcript_slice, *, tenant_id, top_k)` | `ReasoningResult.stdev / sample_size` | Confidence calculator |
| Business Knowledge | `retrieve_knowledge(intent, query, *, tenant_id, top_k)` / async `lookup_business_knowledge(...)` | `KnowledgeResult(available, hits, coverage, confidence)` 또는 `unevaluable=True` | #15 `work_accuracy` Sub Agent 전용 |

Reasoning RAG 는 **confidence 분산 지표** 용도만 허용 (원칙 7.5 — 과거 점수 가중평균으로 최종 점수 산출 금지).

## PII Forward-compatibility (`v2/layer1/pii_normalizer.py`)

마스킹 포맷 2버전 호환:
- `v1_symbolic` — 모든 PII 를 `***` 단일 symbol 로 치환 (현재 운영)
- `v2_categorical` — `[NAME]` / `[PHONE]` / `[RRN]` / `[ACCOUNT]` / `[CARD]` / `[ADDRESS]` / `[EMAIL]` / `[AMOUNT]` / `[DATE]` / `[PII_OTHER]` (미래)

v1_symbolic 환경에서는 `inferred_category` + `inference_confidence` 필드로 문맥 기반 추정치를 함께 저장 (`QAOutputV2.preprocessing.pii_tokens`).

## Tenants 디렉토리 구조

`v2/tenants/<tenant_id>/` — Multi-tenant 확장의 1차 키.

```
v2/tenants/generic/
├── tenant_config.yaml           # version / pii_policy / scoring_policy / routing / confidence
├── rubric.md                     # 평가 루브릭 (phase_a2_v1)
├── prohibited_terms.txt          # 금지어 / 부정어
├── golden_set/                   # 18개 평가항목별 Few-shot JSON
│   ├── 01_first_greeting.json
│   └── ... (18_privacy_compliance.json)
├── business_knowledge/
│   └── manual.md                 # #15 RAG chunk (meta 주석 + H2 단위)
├── mandatory_scripts/
│   └── intent_to_script.yaml     # #16 intent → 필수 안내 매핑
└── README.md
```

`tenant_config.yaml` 주요 블록:
- `version.rubric = "phase_a2_v1"` (PL 최종 확정 2026-04-20)
- `scoring_policy.allowed_steps` — `v2/contracts/rubric.py::ALLOWED_STEPS` 와 동기화 필수
- `scoring_policy.force_t3_items = [9, 17, 18]`
- `routing.initial_t0_cap = 0.30` / `t1_sample_rate = 0.05` / `grade_boundary_margin = 3`
- `confidence.rag_min_sample_size = 3` / `rag_small_sample_weight = 0.5`

새 테넌트 추가: `cp -r v2/tenants/generic v2/tenants/<new_tenant_id>` 후 `tenant_id` / `tenant_name` / `domain` / rubric / golden_set / business_knowledge 업데이트.

## 최종 출력 (`QAOutputV2`) 최상위 필드

`v2/schemas/qa_output_v2.py::QAOutputV2` — pydantic v2. 13개 최상위 블록:

| 필드 | 설명 |
|------|------|
| `consultation_id` | 상담 식별자 |
| `tenant` | 테넌트 ID (Multi-tenant 쿼리 1차 키) |
| `evaluated_at` | ISO-8601 timestamp (UTC) |
| `versions` | `{model, rubric, prompt_bundle, golden_set, pipeline}` — drift 분석 전제 |
| `masking_format` | `{version, spec}` — v1_symbolic / v2_categorical |
| `stt_metadata` | `{transcription_confidence, speaker_diarization_success, duration_sec, has_timestamps}` |
| `preprocessing` | Layer 1 산출 (intent_type / detected_sections / deduction_triggers / pii_tokens) |
| `evaluation` | `{categories: [8 × CategoryBlock]}` — 항목별 score / evidence / deductions / confidence / flag |
| `overrides` | `{applied, reasons: [OverrideEntry]}` |
| `final_score` | `{raw_total, after_overrides, grade}` |
| `routing` | `{decision, hitl_driver, priority_flags, estimated_review_time_min, tier_reasons}` |
| `summary` | `{total_score, grade, one_liner, strengths, improvements}` |
| `coaching_points` | `[CoachingPoint(item_number, category, message, priority)]` |
| `diagnostics` | layer 별 elapsed / error |

## 파일 구조

```
qa-pipeline/
├── v2/
│   ├── graph_v2.py                     # LangGraph StateGraph (4-Layer)
│   ├── contracts/
│   │   ├── rubric.py                   # V2 ALLOWED_STEPS / snap_score_v2
│   │   ├── preprocessing.py            # Layer 1 계약
│   │   └── sub_agent_io_spec.md        # Sub Agent IO 명세
│   ├── schemas/
│   │   ├── enums.py                    # EvaluationMode / RoutingTier / HITLDriver / CategoryKey / ...
│   │   ├── sub_agent_io.py             # SubAgentResponse / ItemVerdict / EvidenceQuote
│   │   ├── qa_output_v2.py             # QAOutputV2 최종 JSON
│   │   └── state_v2.py                 # QAStateV2 (LangGraph 상태)
│   ├── layer1/
│   │   ├── node.py                     # Layer 1 진입점
│   │   ├── quality_gate.py
│   │   ├── segment_splitter.py
│   │   ├── pii_normalizer.py
│   │   ├── rule_pre_verdictor.py
│   │   └── deduction_trigger_detector.py
│   ├── agents/
│   │   ├── group_a/ (greeting, listening_comm, language, needs)
│   │   └── group_b/ (explanation, proactiveness, work_accuracy, privacy)
│   ├── layer3/
│   │   ├── orchestrator_v2.py
│   │   ├── aggregator.py
│   │   ├── override_rules.py
│   │   ├── consistency_checker.py
│   │   └── grader.py
│   ├── layer4/
│   │   ├── evidence_refiner.py
│   │   ├── overrides_adapter.py
│   │   └── report_generator_v2.py
│   ├── confidence/                     # Confidence 4 신호 계산
│   ├── routing/                        # tier_router / tenant_policy
│   ├── rag/                            # golden_set / reasoning / business_knowledge
│   ├── tenants/generic/                # 기본 테넌트
│   ├── prompts/                        # Sub Agent 프롬프트 (group_a / group_b)
│   ├── serving/
│   │   ├── server_v2.py                # FastAPI (/ping /health /readyz /evaluate /invocations)
│   │   └── main_v2.py                  # uvicorn 진입점 (PORT 기본 8081)
│   └── scripts/
│       └── run_direct_batch_v2.py      # 인프로세스 배치 러너
├── nodes/                              # V1 잔존 — V2 가 import 만 함
│   ├── skills/                         # reconciler / constants / pattern_matcher / scorer
│   ├── dialogue_parser.py              # segment_splitter 가 재사용
│   ├── llm.py                          # get_chat_model / invoke_and_parse / LLMTimeoutError
│   ├── qa_rules.py                     # V1 ALLOWED_STEPS (V2 는 참조하지 않음)
│   └── json_parser.py
├── graph.py                            # _make_tracked_node 래퍼만 유지 (축소 stub)
├── state.py / config.py                # V1 공유
├── README.md                           # 이 문서
└── API.md                              # V2 HTTP / Python API 명세
```

## HTTP 엔드포인트 (V2 `server_v2.py`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/ping` | AgentCore Runtime liveness probe (항상 200) |
| GET | `/health` | `{"status": "healthy", "service": "qa-pipeline-v2", "version": "2.0.0"}` |
| GET | `/readyz` | graph 빌드 성공 여부 — 실패 시 503 |
| POST | `/evaluate` | V2 파이프라인 실행 (JSON 입력 → JSON 응답) |
| POST | `/invocations` | AgentCore Runtime invoke entrypoint (`/evaluate` 위임) |

기본 포트 8081 (V1 8080 충돌 방지). 상세 스펙은 `API.md` 참조.

## 실행 방법

### 로컬 서버 기동

```bash
cd packages/agentcore-agents/qa-pipeline
pip install -r requirements.txt
~/.conda/envs/py313/python.exe -m v2.serving.main_v2
# http://localhost:8081
```

환경변수 override:

```bash
PORT=9000 LOG_LEVEL=DEBUG ~/.conda/envs/py313/python.exe -m v2.serving.main_v2
```

### 인프로세스 배치 (프롬프트 튜닝)

HTTP/SSE 우회 — 2~5MB `node_traces` 누적 + SSE idle disconnect 로 hang 발생하는 문제 해결.

```bash
cd packages/agentcore-agents/qa-pipeline
BATCH_OUTPUT_SUFFIX=v2_iter01 BATCH_MAX_CONCURRENT=2 \
  ~/.conda/envs/py313/python.exe v2/scripts/run_direct_batch_v2.py
```

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `BATCH_OUTPUT_SUFFIX` | `v2_direct` | 결과 폴더 접미사 |
| `BATCH_MAX_CONCURRENT` | `2` | 병렬 샘플 수 (Bedrock throttle 완화) |
| `PER_SAMPLE_TIMEOUT` | `600` | 샘플당 타임아웃 초 |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-20250514-v1:0` | Bedrock 모델 ID |
| `SKIP_PHASE_C_REPORTING` | `1` | True 시 Layer 3 consistency/grade + Layer 4 전체 스킵 |

### 요청 예 (`/evaluate`)

```bash
curl -X POST http://localhost:8081/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "상담사: 안녕하세요 ...\n고객: 네 ...",
    "tenant_id": "generic",
    "stt_metadata": {
      "transcription_confidence": 0.95,
      "speaker_diarization_success": true,
      "duration_sec": 180.0,
      "has_timestamps": false,
      "masking_format": {"version": "v1_symbolic"}
    },
    "plan": {"skip_phase_c_and_reporting": false}
  }'
```

## 주요 환경 변수

| 변수 | 용도 |
|------|------|
| `PORT` | `server_v2` 포트 (기본 8081) |
| `LOG_LEVEL` | 로그 레벨 (기본 INFO) |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | AWS 리전 |
| `BEDROCK_MODEL_ID` | 기본 Bedrock 모델 |
| `V2_GROUP_B_SKIP_LLM` | Group B LLM 호출 우회 (mock 모드, 개발용) |
| `ROUTING_INITIAL_T0_CAP` / `ROUTING_T1_SAMPLE_RATE` | tier_router 파라미터 override (`tenant_config.yaml` 상위 우선) |
| `BATCH_*` / `PER_SAMPLE_TIMEOUT` / `SKIP_PHASE_C_REPORTING` | `run_direct_batch_v2.py` 용 |

## V1 자산 참조 관계

V2 는 다음 V1 모듈을 import (V1 원본은 수정하지 않음):

| V1 모듈 | V2 소비처 | 재사용 심볼 |
|---------|-----------|-------------|
| `nodes.skills.reconciler` | `v2/agents/group_a/_shared.py`, `v2/agents/group_b/base.py`, `v2/scripts/run_direct_batch_v2.py` | `normalize_fallback_deductions`, `reconcile_evaluation`, `snap_score` (V2 는 추가로 `snap_score_v2` 사용) |
| `nodes.skills.constants` | `v2/layer1/deduction_trigger_detector.py`, `v2/layer1/rule_pre_verdictor.py` | `PROFANITY_PATTERNS`, `PRIVACY_VIOLATION_PATTERNS`, `HOLD_SILENCE_MARKERS` 등 |
| `nodes.skills.pattern_matcher` | `v2/layer1/rule_pre_verdictor.py` | `PatternMatcher` |
| `nodes.dialogue_parser` | `v2/layer1/segment_splitter.py` | `_parse_turns`, `_separate_speakers`, `_detect_segments`, `_build_turn_assignments`, `_create_turn_pairs` |
| `nodes.llm` | `v2/agents/group_a/*.py`, `v2/agents/group_b/_llm.py` | `get_chat_model`, `invoke_and_parse`, `LLMTimeoutError` |
| `graph._make_tracked_node` | `v2/graph_v2.py` | 트레이스 래퍼 |

## 의존성 (requirements.txt)

| 패키지 | 용도 |
|--------|------|
| `langgraph>=0.4.0` | StateGraph 4-Layer 파이프라인 |
| `langchain-core>=0.3.0` | LangChain 코어 |
| `langchain-aws>=0.2.0` | Bedrock 연동 |
| `boto3>=1.35.0` | AWS SDK |
| `fastapi>=0.115.0` | HTTP 서버 |
| `uvicorn[standard]>=0.30.0` | ASGI 서버 |
| `pydantic>=2.0.0` | QAOutputV2 validation |
| `pyyaml` | tenant_config.yaml 로더 |

## 기술 스택

| 항목 | 기술 |
|------|------|
| 파이프라인 | LangGraph StateGraph (4-Layer, Send fan-out) |
| LLM (기본) | Anthropic Claude Sonnet 4 (Bedrock us-east-1) |
| LLM (대체) | SageMaker vLLM (Qwen 3 8B) — `llm_backend="sagemaker"` |
| LLM 클라이언트 | LangChain BaseChatModel (`nodes/llm.py::get_chat_model`) |
| 서버 | FastAPI + uvicorn |
| Python | 3.13 (conda py313) |
| 컨테이너 | ARM64 (CDK `Platform.LINUX_ARM64`) |

## 테스트

`qa-pipeline/tests/` 및 `v2/tests/` 에 E2E / smoke / contract 테스트가 포함되어 있음. 실행:

```bash
pytest qa-pipeline/v2/tests/
```

Validation artifact 는 `v2/validation/reports/<timestamp>/summary.md` 로 출력.

## 관련 문서

- `API.md` — V2 HTTP / Python 공개 API 명세
- `v2/contracts/sub_agent_io_spec.md` — Sub Agent IO 계약 상세
- `v2/agents/group_a/_MAPPING_MATRIX.md` — Group A 매핑
- `v2/prompts/group_b/README.md` — Group B 프롬프트 가이드
- `v2/tenants/generic/README.md` — 기본 테넌트 설명
- `v2/tests/e1_drift_report.md` — Drift 리포트
