# V1 Legacy 제거 목록 (2026-04-20)

V2 아키텍처 (`v2/` 하위 4-Layer + 8 Sub Agent) 가 완전히 구현되어 V1 에서 V2 로 전환 완료. V1 전용 코드 중 V2 가 import 하지 않는 것을 제거.

## 의존성 매트릭스 (V2 → V1)

`v2/` 디렉토리 전체 Python 파일의 `import nodes.*` 패턴 정적 분석 결과:

| V1 모듈 | 사용처 (V2 파일) | 사용 대상 |
|---|---|---|
| `nodes.skills.reconciler` | `v2/agents/group_a/_shared.py`, `v2/agents/group_b/base.py`, `v2/scripts/run_direct_batch_v2.py`, `v2/tests/test_layer1_smoke.py` | `normalize_fallback_deductions`, `reconcile_evaluation`, `snap_score` |
| `nodes.skills.constants` | `v2/layer1/deduction_trigger_detector.py`, `v2/layer1/rule_pre_verdictor.py` | `INAPPROPRIATE_LANGUAGE_PATTERNS`, `PREEMPTIVE_DISCLOSURE_PATTERNS`, `PRIVACY_VIOLATION_PATTERNS`, `PROFANITY_PATTERNS`, `THIRD_PARTY_DISCLOSURE_PATTERNS`, `HOLD_SILENCE_MARKERS`, `IV_PROCEDURE_PATTERNS`, `SPEECH_OVERLAP_PATTERNS` |
| `nodes.skills.pattern_matcher` | `v2/layer1/rule_pre_verdictor.py` | `PatternMatcher` |
| `nodes.dialogue_parser` | `v2/layer1/segment_splitter.py` | `_build_turn_assignments`, `_create_turn_pairs`, `_detect_segments`, `_parse_turns`, `_separate_speakers` |
| `nodes.llm` | `v2/agents/group_a/{greeting,listening_comm,language,needs}.py`, `v2/agents/group_b/_llm.py` | `LLMTimeoutError`, `get_chat_model`, `invoke_and_parse` |
| (간접) `graph` 모듈의 `_make_tracked_node` | `v2/graph_v2.py` | `_make_tracked_node` 래퍼 함수 |

## 2차 의존성 (V1 내부)

직접 참조된 모듈들이 다시 참조하는 V1 루트 모듈:

- `nodes/skills/reconciler.py` → `nodes/qa_rules.py`
- `nodes/skills/scorer.py` → `nodes/qa_rules.py` (skills.__init__ 에서 re-export)
- `nodes/skills/dialogue_parser.py` (없음; dialogue_parser 는 nodes/ 직하)
- `nodes/dialogue_parser.py` → `nodes/skills/constants.py`, `state.py` (QAState)
- `nodes/llm.py` → `config.py` (app_config), `nodes/json_parser.py`

## 보존 모듈 (삭제 금지)

V2 가 직접/간접 import:

1. **`nodes/skills/`** 전체 (7개 파일):
   - `constants.py`, `deduction_log.py`, `error_results.py`, `evidence_builder.py`, `node_context.py`, `pattern_matcher.py`, `reconciler.py`, `scorer.py`, `__init__.py`
   - 이유: V2 가 직접 import 및 `nodes/skills/__init__.py` re-export 체인
2. **`nodes/dialogue_parser.py`** (V2 layer1 이 내부 함수 사용)
3. **`nodes/llm.py`** (V2 Group A/B 가 import)
4. **`nodes/qa_rules.py`** (skills 내부에서 사용)
5. **`nodes/json_parser.py`** (`nodes/llm.py` 가 내부 사용)
6. **`state.py`** (QAState 타입 정의 — `nodes/dialogue_parser.py`, `nodes/llm.py` 가 참조)
7. **`config.py`** (app_config — `nodes/llm.py` 가 참조)
8. **`graph.py`** (축소 stub 으로 변경 — `_make_tracked_node` + 헬퍼만 유지, V1 노드 imports 와 StateGraph 빌더 제거)
9. **`nodes/__init__.py`** (수정 — V1 노드 re-export 제거, skills 서브패키지 로딩만 유지)

## 삭제 대상

### V1 노드 파일 (V2 미사용)

- `nodes/greeting.py`
- `nodes/understanding.py`
- `nodes/courtesy.py`
- `nodes/mandatory.py`
- `nodes/scope.py`
- `nodes/proactiveness.py`
- `nodes/work_accuracy.py`
- `nodes/incorrect_check.py`
- `nodes/orchestrator.py`
- `nodes/consistency_check.py`
- `nodes/score_validation.py`
- `nodes/retrieval.py`
- `nodes/report_generator.py`
- `nodes/wiki_compiler.py`
- `nodes/sample_data.py`

### V1 루트 파일

- `main.py` (V1 BedrockAgentCoreApp; V2 는 `v2/serving/main_v2.py`)
- `server.py` (V1 FastAPI; V2 는 `v2/serving/server_v2.py`)
- `pentagon.py` (V1 전용 시각화)
- `pentagon_direct.py` (V1 전용 시각화)
- `transforms.py` (V1 전용 유틸)
- `langgraph.json` (V1 LangGraph config — v2 는 별도 방식)
- `Dockerfile` (V1 기준 — V2 배포 시 재작성 필요 시 복구)
- `requirements.txt` (V1 루트; v2 스크립트 사용 시 재생성 필요 시 복구)
- `graph_visualization.html` (V1 전용 시각화)
- `test_payload.json`, `test_payload_pl.json` (V1 API 테스트 페이로드)
- `API.md` (V1 HTTP API 문서; V2 는 server_v2.py 기반 별도 문서 필요)

### V1 루트 디렉토리

- `prompts/` (V1 프롬프트 세트; V2 는 `v2/prompts/group_a/`, `v2/prompts/group_b/` 사용)
- `routers/` (V1 FastAPI 라우터)
- `raw/` (V1 원본 가이드 자료 — V2 는 `v2/tenants/generic/` 사용)
- `wiki/` (V1 QA 지식베이스 — V2 는 `v2/rag/` + `v2/tenants/generic/business_knowledge/`)
- `_legacy_sagemaker_pipeline/` (이미 legacy 표시된 디렉토리)
- `_server_logs/` (V1 서버 로그)
- `test_outputs/` (V1 회귀 테스트 결과)
- `scripts/` (V1 batch eval 스크립트; V2 는 `v2/scripts/run_direct_batch_v2.py` 사용)

## 수정 대상 (삭제 대신)

- `graph.py` → V1 노드 import / StateGraph 빌더 제거, `_make_tracked_node` + 헬퍼만 유지 (V2 `graph_v2.py` 가 import)
- `nodes/__init__.py` → V1 노드 re-export 제거 (import side-effect 차단)
- `README.md` → V2 전환 주석 추가 (선택; 본 작업에서는 간단 업데이트)

## 검증 계획

V2 import smoke test:
```python
import v2.graph_v2
import v2.schemas
import v2.agents.group_a
import v2.agents.group_b
import v2.rag
import v2.layer1
import v2.layer3
import v2.layer4
```
