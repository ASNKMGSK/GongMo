# V2 QA Pipeline — Public API 명세

> V2 4-Layer QA 평가 파이프라인의 공개 API 문서. HTTP 엔드포인트, 요청/응답 스키마, Python 모듈 사용법, 환경 변수, tenant 확장 방법을 다룬다.

본 문서의 대상 컴포넌트: `v2/graph_v2.py` (LangGraph 4-Layer), `v2/serving/server_v2.py` (FastAPI), `v2/serving/main_v2.py` (uvicorn entry), `v2/scripts/run_direct_batch_v2.py` (인프로세스 배치 러너).

---

## 1. HTTP 엔드포인트

### 1.1 엔드포인트 요약

| 메서드 | 경로 | 설명 | 응답 |
|--------|------|------|------|
| GET | `/ping` | AgentCore Runtime liveness probe | `{"status": "ok"}` |
| GET | `/health` | 일반 liveness probe | `{"status": "healthy", "service": "qa-pipeline-v2", "version": "2.0.0"}` |
| GET | `/readyz` | Readiness probe (graph 빌드 성공 여부) | 성공 `{"ready": true, "pipeline": "v2"}` / 실패 503 `{"ready": false, "graph_build_error": "..."}` |
| POST | `/evaluate` | V2 파이프라인 실행 — JSON 입력 → JSON 응답 | `{preprocessing, evaluations, orchestrator, report, routing, error, completed_nodes, node_timings, _meta}` |
| POST | `/invocations` | AgentCore Runtime invoke entrypoint | `/evaluate` 로 위임 |

기본 포트: **8081** (`PORT` 환경 변수로 override 가능. V1 8080 과 충돌 방지).

### 1.2 `/evaluate` 요청 스키마

```jsonc
{
  "transcript": "상담사: 안녕하세요...\n고객: 네...",  // 필수
  "consultation_id": "667890",                        // 선택 — 없으면 session_id 생성
  "session_id": "...",                                 // 선택
  "customer_id": "...",                                // 선택
  "consultation_type": "general",                     // 선택 — V1 호환 필드
  "tenant_id": "generic",                              // 선택 — 기본 "generic"
  "llm_backend": "bedrock",                            // 선택 — "bedrock" | "sagemaker"
  "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0", // 선택
  "stt_metadata": {                                    // 선택 — 미지정 시 기본값 주입
    "transcription_confidence": 0.95,
    "speaker_diarization_success": true,
    "duration_sec": 180.0,
    "has_timestamps": false,
    "masking_format": {"version": "v1_symbolic"}
  },
  "plan": {                                            // 선택
    "skip_phase_c_and_reporting": false               // true 시 Layer 3 consistency/grade + Layer 4 전체 스킵
  }
}
```

필수 필드는 `transcript` 하나. 나머지는 기본값이 주입된다.

### 1.3 `/evaluate` 응답 스키마

```jsonc
{
  "preprocessing": {
    "quality": {"passed": true, "unevaluable": false, "reasons": []},
    "detected_sections": {
      "opening": {"start": 0, "end": 3},
      "body": {"start": 3, "end": 18},
      "closing": {"start": 18, "end": 24}
    },
    "intent_type": "claim",                // 또는 dict 확장 형태
    "intent_type_primary": "claim",
    "deduction_triggers": {
      "불친절": false,
      "개인정보_유출": false,
      "오안내_미정정": false
    },
    "pii_tokens": [
      {"raw": "***", "utterance_idx": 4, "inferred_category": "PHONE", "inference_confidence": 0.82}
    ],
    "rule_pre_verdicts": {"1": {...}, "2": {...}, "17": {...}, ...}
  },
  "evaluations": [
    { "agent_id": "greeting-agent", "category": "greeting_etiquette", "items": [...], ... },
    { "agent_id": "privacy-protection-agent", "category": "privacy_protection", "items": [...], ... }
    // 8개 Sub Agent
  ],
  "orchestrator": {
    "final_score": {"raw_total": 92, "after_overrides": 92, "grade": "A"},
    "overrides": {"applied": false, "reasons": []},
    "consistency_flags": []
  },
  "report": {
    "summary": {"total_score": 92, "max_score": 100, "grade": "A", "one_liner": "...", "strengths": [...], "improvements": [...]},
    "coaching_points": [...]
  },
  "routing": {
    "decision": "T2",
    "hitl_driver": "policy_driven",
    "priority_flags": [
      {"code": "privacy_protection_force_t3", "description": "...", "severity": "critical", "item_numbers": [17, 18]}
    ],
    "estimated_review_time_min": 5,
    "tier_reasons": ["force_t3 항목 (#17, #18) 평가 활성"]
  },
  "error": null,
  "completed_nodes": ["layer1", "layer2_greeting", "...", "layer4"],
  "node_timings": [{"node": "layer1", "elapsed_sec": 1.23}, ...],
  "_meta": {
    "pipeline": "v2",
    "elapsed_sec": 12.45,
    "session_id": "v2-667890-1745234567"
  }
}
```

> 최종 full `QAOutputV2` (13개 최상위 필드) 를 원한다면 Python 모듈 사용 방식을 참고 (§3). HTTP `/evaluate` 응답은 `preprocessing` / `evaluations` / `orchestrator` / `report` / `routing` 등 5개 상위 블록을 반환한다.

### 1.4 오류 응답

| 상태 | 조건 | 응답 |
|------|------|------|
| 400 | `transcript` 누락 | `{"error": "bad_request", "detail": "transcript 필수"}` |
| 500 | graph 실행 중 예외 | `{"error": "graph_invoke_failed", "detail": "<message>"}` |
| 503 | graph 빌드 실패 | `{"error": "graph_build_failed", "detail": "<message>"}` |

---

## 2. Sub Agent 공통 응답 포맷

각 Sub Agent 는 동일한 `SubAgentResponse` 포맷으로 응답한다. Layer 3 Orchestrator 는 이 포맷만 알면 집계 가능.

### 2.1 `SubAgentResponse` (`v2/schemas/sub_agent_io.py`)

```python
class SubAgentResponse(TypedDict, total=False):
    agent_id: str                    # "greeting-agent" / "privacy-protection-agent" / ...
    category: CategoryKey             # "greeting_etiquette" | "listening_communication" | ... (8종)
    status: SubAgentStatus            # "success" | "partial" | "error"
    items: list[ItemVerdict]          # 카테고리 내 평가항목 전부
    category_score: int               # 집계된 category 점수 (Orchestrator 가 재검증)
    category_max: int                 # unevaluable 차감 후 category 만점
    category_confidence: int          # 1~5, 카테고리 전반 신뢰도 (Sub Agent self-report)
    llm_backend: str                  # "bedrock" | "sagemaker"
    llm_model_id: str | None
    elapsed_ms: int | None
    error_message: str | None
```

### 2.2 `ItemVerdict` (평가항목 1건)

```python
class ItemVerdict(TypedDict, total=False):
    item_number: int                  # 1~18
    item_name: str                    # "첫인사" / "끝인사" / ...
    item_name_en: str | None
    max_score: int                    # ALLOWED_STEPS[item_number][0]
    score: int | None                 # snap_score_v2 경유. unevaluable 은 None
    evaluation_mode: EvaluationMode   # "full" | "structural_only" | "compliance_based" |
                                      # "partial_with_review" | "skipped" | "unevaluable"
    judgment: str                     # 한 줄 요약
    deductions: list[DeductionEntry]  # [{reason, points, rule_id, evidence_refs}]
    evidence: list[EvidenceQuote]     # full 모드는 최소 1개 필수
    llm_self_confidence: LLMSelfConfidence  # {score(1~5), rationale}
    rule_llm_delta: RuleLLMDelta | None     # Rule 1차 판정 있는 항목만
    mode_reason: str | None
    details: dict[str, Any] | None    # 항목별 메타 (예: #2 farewell_elements, #7 cushion_word_count)
    force_t3: bool                    # Sub Agent 판정으로 T3 강제 권고
    mandatory_human_review: bool
    infra_tags: list[str]             # "[SKIPPED_INFRA]" / "[RAG_UNAVAILABLE]"
```

### 2.3 `EvidenceQuote`

```python
class EvidenceQuote(TypedDict, total=False):
    speaker: str                      # "상담사" | "고객" (tenant 별 alias 허용)
    timestamp: str | None             # "00:00:02" 또는 None
    quote: str                        # STT 원문 그대로
    turn_id: int | None               # V1 dialogue_parser turn id
```

원칙 3: 판정당 최소 1개 evidence 필수 (`evaluation_mode="full"` 에서 validator 가 강제).

---

## 3. Python 모듈 사용

### 3.1 graph 빌드 & 직접 호출

```python
from v2.graph_v2 import build_graph_v2
from v2.schemas.qa_output_v2 import QAOutputV2

graph = build_graph_v2()
initial_state = {
    "transcript": "...",
    "tenant_id": "generic",
    "consultation_id": "667890",
    "session_id": "v2-667890-abc",
    "customer_id": "667890",
    "llm_backend": "bedrock",
    "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "stt_metadata": {
        "transcription_confidence": 0.95,
        "speaker_diarization_success": True,
        "duration_sec": 180.0,
        "has_timestamps": False,
        "masking_format": {"version": "v1_symbolic"},
    },
    "plan": {"skip_phase_c_and_reporting": False},
}

final_state = await graph.ainvoke(initial_state)

# Layer 4 가 생성한 QAOutputV2 (pydantic) — 완전 직렬화 형태
qa_output = final_state.get("qa_output")
if isinstance(qa_output, QAOutputV2):
    print(qa_output.model_dump(by_alias=True))
```

### 3.2 주요 모듈 import 경로

| 목적 | Import |
|------|--------|
| LangGraph 빌드 | `from v2.graph_v2 import build_graph_v2` |
| 최종 출력 모델 | `from v2.schemas.qa_output_v2 import QAOutputV2` |
| Sub Agent IO | `from v2.schemas.sub_agent_io import SubAgentResponse, ItemVerdict, EvidenceQuote` |
| Enum / Literal | `from v2.schemas.enums import EvaluationMode, RoutingTier, HITLDriver, CategoryKey, CATEGORY_META, FORCE_T3_ITEMS, GRADE_BOUNDARIES, GRADE_BOUNDARY_MARGIN` |
| V2 rubric / snap | `from v2.contracts.rubric import ALLOWED_STEPS, snap_score_v2, is_valid_step, allowed_steps_of, max_score_of, V2_MAX_TOTAL_SCORE` |
| Layer 1 전처리 | `from v2.layer1.node import layer1_node` |
| Layer 3 orchestrator | `from v2.layer3.node import layer3_node` |
| RAG | `from v2.rag.golden_set import retrieve_fewshot` / `from v2.rag.reasoning import retrieve_reasoning` / `from v2.rag.business_knowledge import retrieve_knowledge, lookup_business_knowledge` |
| Sub Agent (Group A) | `from v2.agents.group_a import greeting_sub_agent, listening_comm_sub_agent, language_sub_agent, needs_sub_agent` |
| Sub Agent (Group B) | `from v2.agents.group_b import explanation_sub_agent, proactiveness_sub_agent, work_accuracy_sub_agent, privacy_sub_agent` |

### 3.3 `snap_score_v2` 사용 규칙

Sub Agent / Layer 1 rule_pre_verdictor 에서 점수를 산출할 때는 반드시 `snap_score_v2` 를 경유.

```python
from v2.contracts.rubric import snap_score_v2

raw_llm_score = 3
item_number = 17
final = snap_score_v2(item_number, raw_llm_score)
# → 3 (V2 는 [5,3,0] 단계 유지. V1 snap_score 는 0 으로 강제 변환)
```

---

## 4. RAG 3종 API

### 4.1 Golden-set (`retrieve_fewshot`)

```python
from v2.rag.golden_set import retrieve_fewshot

result = retrieve_fewshot(
    item_number=2,            # 1~18
    intent="claim",           # Layer 1 분류 intent 또는 "*"
    segment_text="...",       # Segment 추출기가 고른 발화 묶음 (transcript 전체 금지)
    tenant_id="generic",
    top_k=5,
)
# result: FewshotResult(examples=[...], match_reasons=[...], ...)
```

### 4.2 Reasoning (`retrieve_reasoning`) — Confidence stdev 전용

```python
from v2.rag.reasoning import retrieve_reasoning

result = retrieve_reasoning(
    item_number=10,
    transcript_slice="...",
    tenant_id="generic",
    top_k=10,
)
# result.stdev / result.sample_size → Layer 4 confidence calculator 가 사용
# 금지: result.examples[].score 가중평균으로 최종 점수 산출 (원칙 7.5)
```

### 4.3 Business Knowledge (`retrieve_knowledge` / `lookup_business_knowledge`) — #15 전용

동기 API:

```python
from v2.rag.business_knowledge import retrieve_knowledge

result = retrieve_knowledge(
    intent="product_inquiry",
    query="...",
    tenant_id="generic",
    top_k=3,
)
# result.unevaluable=True 면 Sub Agent 는 unevaluable 상태로 반환 (강제)
```

Dev3 `work_accuracy` Sub Agent 호환 async 어댑터:

```python
from v2.rag.business_knowledge import lookup_business_knowledge

result = await lookup_business_knowledge(
    consultation_type="general",
    intent="product_inquiry",
    product="정기보험A",
    transcript_slice="...",
    top_k=5,
    tenant_id="generic",
)
# result dict keys: available, hits, coverage, confidence, match_reason
```

---

## 5. 배치 실행 — `run_direct_batch_v2.py`

HTTP/SSE 우회. `graph.ainvoke()` 를 인프로세스 직접 호출해 2~5MB `node_traces` 누적 + SSE idle disconnect 로 인한 hang 문제를 해결.

### 5.1 사용

```bash
cd packages/agentcore-agents/qa-pipeline
~/.conda/envs/py313/python.exe v2/scripts/run_direct_batch_v2.py
```

### 5.2 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `BATCH_OUTPUT_SUFFIX` | `v2_direct` | 결과 폴더 접미사 (`batch_<timestamp>_<suffix>`) |
| `BATCH_MAX_CONCURRENT` | `2` | 병렬 샘플 수 (Bedrock throttle 완화) |
| `PER_SAMPLE_TIMEOUT` | `600` | 샘플당 타임아웃 (초) |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-20250514-v1:0` | Bedrock 모델 ID |
| `SKIP_PHASE_C_REPORTING` | `1` | True 시 Layer 3 consistency/grade + Layer 4 전체 스킵 |
| `LOG_LEVEL` | `INFO` | `logging` 레벨 |

### 5.3 입출력 경로

- Input: `C:\Users\META M\Desktop\Re-qa샘플데이터\학습용\<sample_id>_*.txt` (하드코딩, 스크립트 상수 `SAMPLES_DIR`)
- Output: `C:\Users\META M\Desktop\프롬프트 튜닝\batch_<timestamp>_<suffix>/<sample_id>_result.json`

기존 결과 파일이 있으면 skip (idempotent).

### 5.4 Self-Consistency 병합

N회 median 병합은 `scripts/merge_self_consistency.py` (V1 계속 사용) 로 처리.

```bash
python scripts/merge_self_consistency.py --folders v2_sc1 v2_sc2 v2_sc3 --output v2_final
```

---

## 6. 공통 참조 표

### 6.1 `evaluation_mode` 6종

| 모드 | 의미 | score | 합산 규칙 |
|------|------|-------|-----------|
| `full` | 완전 평가 | 0~max | `items[i].score` 합산 |
| `structural_only` | 내용 검증 불가, 구조만 평가 (#9) | 0~max | `items[i].score` 합산 |
| `compliance_based` | 규정 준수 여부 평가 (#17, #18) | 0~max | `items[i].score` 합산 |
| `partial_with_review` | AI + 인간 검수 (#15 RAG 부분 커버리지) | 0~max | `items[i].score` 합산 |
| `skipped` | 해당 상황 부재 (#3 말겹침 없음) | 만점 고정 | `items[i].max_score` 합산 |
| `unevaluable` | STT 품질 등 평가 불가 | None | 합산에서 **제외** + `category_max` 에서도 차감 |

### 6.2 `ALLOWED_STEPS` (V2 — `v2/contracts/rubric.py`)

| 항목 | 만점 | 허용 단계 |
|------|------|-----------|
| #1 첫인사 | 5 | `[5, 3, 0]` |
| #2 끝인사 | 5 | `[5, 3, 0]` |
| #3 경청 | 5 | `[5]` (skipped 고정 만점) |
| #4 공감 | 5 | `[5, 3, 0]` |
| #5 대기 멘트 | 5 | `[5, 3, 0]` |
| #6 정중한 표현 | 5 | `[5, 3, 0]` |
| #7 쿠션어 | 5 | `[5, 3, 0]` |
| #8 문의 파악 | 5 | `[5, 3, 0]` |
| #9 고객정보 확인 | 5 | `[5, 3, 0]` (structural_only + force_t3) |
| #10 설명 명확성 | 10 | `[10, 7, 5, 0]` |
| #11 두괄식 답변 | 5 | `[5, 3, 0]` |
| #12 문제 해결 | 5 | `[5, 3, 0]` |
| #13 부연 설명 | 5 | `[5, 3, 0]` |
| #14 사후 안내 | 5 | `[5, 3, 0]` |
| #15 정확한 안내 | 10 | `[10, 5, 0]` (partial_with_review) |
| #16 필수 안내 이행 | 5 | `[5, 3, 0]` |
| #17 정보 확인 절차 | 5 | `[5, 3, 0]` (compliance_based + force_t3) |
| #18 정보 보호 준수 | 5 | `[5, 3, 0]` (compliance_based + force_t3) |

합계 만점 = 100 (`v2/contracts/rubric.py::V2_MAX_TOTAL_SCORE`).

### 6.3 Tier 4종 (`v2/routing/tier_router.py`)

| Tier | 의미 | 비중 (generic) | 트리거 |
|------|------|----------------|--------|
| `T0` | 자동 통과 | ≤ `initial_t0_cap` (0.30) | 모든 신호 high, 강제 검수 없음 |
| `T1` | 스팟체크 | `t1_sample_rate` (0.05) | T0 중 무작위 |
| `T2` | 플래그 검수 | ~15~20% | Confidence ≤ 2 / 신호 불일치 / 등급 경계 ±3 |
| `T3` | 필수 검수 | ≤5% | 감점 트리거 / STT 품질 저하 / `force_t3` 항목 / `mandatory_human_review` |

`FORCE_T3_ITEMS = {9, 17, 18}`. 이 중 하나라도 evaluable 이면 상담 전체 T3.

### 6.4 Confidence 4 신호

1. **LLM Self-Confidence** — Sub Agent 가 반환 (1~5 정수).
2. **Rule vs LLM 일치** — `RuleLLMDelta.agreement`. Rule pre-verdict 있는 항목만.
3. **RAG stdev** — Reasoning RAG `retrieve_reasoning().stdev`. `sample_size < rag_min_sample_size (3)` 이면 penalty (기본 0.5 가중치).
4. **Evidence 품질** — high / medium / low. Sub Agent self-report.

### 6.5 Override 3종 (`v2/layer3/override_rules.py`)

| 트리거 (`OverrideTrigger`) | 액션 (`OverrideAction`) | 영향 |
|----------------------------|-------------------------|------|
| `profanity` / `contempt` / `arbitrary_disconnect` (rudeness) | `all_zero` | 총점 0점 |
| `privacy_leak` | `item_zero` | 해당 항목 0점 + 별도 보고 |
| `uncorrected_misinfo` | `category_zero` | 업무정확도 대분류 0점 |
| `preemptive_disclosure` | `item_zero` | #17 패턴 A |

### 6.6 PII 카테고리 (`v2/schemas/enums.py::PIICategory`)

`NAME` / `PHONE` / `ADDR` / `CARD` / `DOB` / `EMAIL` / `RRN` / `ACCT` / `ORDER` / `OTHER` / `UNKNOWN`.

`masking_format.version`:
- `v1_symbolic` — 모든 PII 를 `***` 로 치환 (현재 운영)
- `v2_categorical` — `[NAME]` / `[PHONE]` / ... 카테고리 보존 (미래)

### 6.7 CategoryKey 및 CATEGORY_META

`v2/schemas/enums.py::CategoryKey` (8종):

| key | label_ko | items | max_score |
|-----|----------|-------|-----------|
| `greeting_etiquette` | 인사 예절 | [1, 2] | 10 |
| `listening_communication` | 경청 및 소통 | [3, 4, 5] | 15 |
| `language_expression` | 언어 표현 | [6, 7] | 10 |
| `needs_identification` | 니즈 파악 | [8, 9] | 10 |
| `explanation_delivery` | 설명력 및 전달력 | [10, 11] | 15 |
| `proactiveness` | 적극성 | [12, 13, 14] | 15 |
| `work_accuracy` | 업무 정확도 | [15, 16] | 15 |
| `privacy_protection` | 개인정보 보호 | [17, 18] | 10 |

### 6.8 등급 경계 (`v2/schemas/enums.py::GRADE_BOUNDARIES`)

| Grade | 최소 총점 |
|-------|-----------|
| S | 95 |
| A | 85 |
| B | 70 |
| C | 50 |
| D | 0 |

경계 ±3점 이내 (`GRADE_BOUNDARY_MARGIN = 3`) 이면 T2 강제.

---

## 7. 환경 변수 전체 목록

### 7.1 서버 실행

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `PORT` | `8081` | `server_v2` 리스너 포트 (V1 8080 과 충돌 방지) |
| `LOG_LEVEL` | `INFO` | uvicorn / python logging 레벨 |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | — | AWS 리전 (Bedrock / SSM) |
| `GATEWAY_URL` | — | Orchestrator Agent Gateway URL (QA 서비스는 직접 사용 안 함) |

### 7.2 LLM

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-20250514-v1:0` | 기본 모델 |
| `V2_GROUP_B_SKIP_LLM` | `0` | `1` 시 Group B Sub Agent LLM 호출을 mock 으로 대체 (개발용) |
| `SAGEMAKER_MAX_CONCURRENT` | `10` (Bedrock) / `3~4` (SageMaker) | LLM 동시 요청 세마포어 |

### 7.3 Routing / Confidence override (tenant_config.yaml 상위 우선)

| 변수 | 기본값 | 대응 tenant_config 경로 |
|------|--------|--------------------------|
| `ROUTING_INITIAL_T0_CAP` | `0.30` | `routing.initial_t0_cap` |
| `ROUTING_T1_SAMPLE_RATE` | `0.05` | `routing.t1_sample_rate` |
| `ROUTING_GRADE_BOUNDARY_MARGIN` | `3` | `routing.grade_boundary_margin` |
| `CONFIDENCE_RAG_MIN_SAMPLE_SIZE` | `3` | `confidence.rag_min_sample_size` |
| `CONFIDENCE_RAG_SMALL_SAMPLE_WEIGHT` | `0.5` | `confidence.rag_small_sample_weight` |

### 7.4 배치 러너

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `BATCH_OUTPUT_SUFFIX` | `v2_direct` | 결과 폴더 접미사 |
| `BATCH_MAX_CONCURRENT` | `2` | 병렬 샘플 수 |
| `PER_SAMPLE_TIMEOUT` | `600` | 샘플당 타임아웃 초 |
| `SKIP_PHASE_C_REPORTING` | `1` | True 시 Layer 3 consistency/grade + Layer 4 스킵 |

---

## 8. Tenant 확장 방법

`v2/tenants/<tenant_id>/` 디렉토리를 추가하면 Layer 3 Orchestrator 가 자동 로드한다.

### 8.1 기본 구조

```
v2/tenants/<tenant_id>/
├── tenant_config.yaml           # 필수
├── rubric.md                    # 평가 루브릭 (프롬프트 참조)
├── prohibited_terms.txt         # 금지어
├── golden_set/                  # 18개 JSON
│   ├── 01_first_greeting.json
│   └── ... (18_privacy_compliance.json)
├── business_knowledge/
│   └── manual.md                # #15 RAG chunk (meta 주석 + H2)
├── mandatory_scripts/
│   └── intent_to_script.yaml    # #16 intent → 필수 안내 매핑
└── README.md
```

### 8.2 `tenant_config.yaml` 필수 키

```yaml
tenant_id: <tenant_id>
tenant_name: "..."
domain: <domain>
version:
  schema: "v2.0.0"
  rubric: "phase_a2_v1"
  golden_set: "..."
  last_updated: "YYYY-MM-DD"
pii_policy:
  strictness: standard          # strict | standard | relaxed
  masking_required: true
  prohibited_terms_file: "prohibited_terms.txt"
scoring_policy:
  max_total: 100
  allowed_steps:                 # v2/contracts/rubric.py::ALLOWED_STEPS 와 동기화
    "1": [5, 3, 0]
    # ... (18개 전부)
  force_t3_items: [9, 17, 18]
  critical_violations: [...]
  infra_fallback_tags: ["LLM 실패", "규칙 폴백", "ThrottlingException"]
routing:
  initial_t0_cap: 0.30
  t1_sample_rate: 0.05
  grade_boundary_margin: 3
confidence:
  rag_min_sample_size: 3
  rag_small_sample_weight: 0.5
supported_intents: [general_inquiry, complaint, ...]
segment_strategy: {...}
rag:
  golden_set: {path, top_k, index_type}
  reasoning: {source, top_k, stdev_window}
  business_knowledge: {path, top_k, required_items, fallback_mode}
mandatory_scripts:
  source_file: "mandatory_scripts/intent_to_script.yaml"
language:
  primary: ko
  report_language: ko
```

### 8.3 신규 테넌트 체크리스트

1. `cp -r v2/tenants/generic v2/tenants/<new_tenant_id>`
2. `tenant_config.yaml::tenant_id` / `tenant_name` / `domain` 수정
3. `rubric.md` 고객사 정책 반영
4. `golden_set/*.json` 실제 데이터 시딩 (시니어 라벨링 세션 필요)
5. `business_knowledge/manual.md` 매뉴얼 chunk 교체 (meta 주석 + H2 규약 준수)
6. `mandatory_scripts/intent_to_script.yaml` 고객사 콜 플로우 반영
7. `prohibited_terms.txt` 업종별 민감어 보강
8. `supported_intents` 업데이트 + `segment_strategy` 조정
9. 요청 body `tenant_id` 필드로 전달해 테스트

### 8.4 주의사항

- `tenant_config.yaml::scoring_policy.allowed_steps` 은 **`v2/contracts/rubric.py::ALLOWED_STEPS` 와 반드시 일치**해야 한다. 불일치 시 `snap_score_v2` 가 중간 점수를 강제 변환한다 (iter05 #17 회귀 참조).
- `force_t3_items` 변경 시 `v2/schemas/enums.py::FORCE_T3_ITEMS` 와 동기화 여부 확인.
- `golden_set` 는 평가자 편향 오염 방지를 위해 LLM synthetic 생성 금지 — Phase 0 시니어 합의 세션 후 채운다 (`generic` 의 `versions.golden_set = "empty_v0.0"` 사유).

---

## 9. Drift / Validation Artifacts

- `v2/validation/reports/<timestamp>/summary.md` — 배치 실행 시 자동 생성
- `v2/tests/e1_drift_report.md` — 초기 drift 리포트 예시
- `_run_log.md` — `run_direct_batch_v2.py` 실행 요약 (output_dir 내부)

---

## 10. 참고 문서

- `README.md` — QA 파이프라인 전체 개요
- `v2/contracts/sub_agent_io_spec.md` — Sub Agent IO 계약 상세 (원본 설계서)
- `v2/agents/group_a/_MAPPING_MATRIX.md` — Group A 항목 매핑
- `v2/prompts/group_b/README.md` — Group B 프롬프트 가이드
- `v2/tenants/generic/README.md` — 기본 테넌트 설명
- `v2/schemas/qa_output_v2.py` — `QAOutputV2` 소스 (최종 JSON 스키마)
- `v2/schemas/sub_agent_io.py` — Sub Agent 공통 IO 타입
- `v2/schemas/enums.py` — EvaluationMode / RoutingTier / HITLDriver / CategoryKey / FORCE_T3_ITEMS / GRADE_BOUNDARIES
- `v2/contracts/rubric.py` — V2 ALLOWED_STEPS / snap_score_v2
- `removed_v1_files.md` — V1 legacy 제거 이력 및 V2→V1 의존성 매트릭스
