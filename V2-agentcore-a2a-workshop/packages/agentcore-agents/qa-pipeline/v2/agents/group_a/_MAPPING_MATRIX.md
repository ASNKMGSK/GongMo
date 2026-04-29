# Group A Sub Agent — V1 iter03_clean → V2 병합 프롬프트 매핑 매트릭스 (Dev2)

> 목적: V1 항목별 프롬프트 8개(#1~#9 중 Group A 소관 8개 항목)를
> V2 Sub Agent 4개 병합 프롬프트로 합칠 때의 재사용/수정 범위를 명시.
> 블로커(#1 계약, #2 rubric, #6 RAG) 해소 전까지는 PL/팀 합의 대상.

## PL 확정사항 반영 (2026-04-20)

**경로**: 모든 산출물 `v2/` 최상위. V1 자산은 수정 금지 — **import 만**.
- Sub Agent 구현: `v2/agents/group_a/{greeting,listening_comm,language,needs}.py`
- 병합 프롬프트: `v2/prompts/group_a/{greeting,listening_comm,language,needs}.md`
- V1 skills import 예: `from nodes.skills.reconciler import snap_score, reconcile_evaluation`, `from nodes.skills.constants import AGENT_SPEAKER_PREFIXES, ...`, `from nodes.skills.deduction_log import build_deduction_log_from_evaluations`, `from nodes.skills.error_results import build_llm_failure_result`

**Layer 1 preprocessing 필드명 확정 (설계서 p23 기준, Dev2 consume 정렬)**:
- `preprocessing.intent_type: str` — 상담 의도 (예: "상품문의")
- `preprocessing.detected_sections: dict[str, tuple[int,int]]` — opening/body/closing turn 범위. Sub Agent 턴 슬라이싱 기준.
- `preprocessing.deduction_triggers: dict[str, bool]` — 불친절/개인정보_유출/오안내_미정정. Group A #6 은 이 triggers 를 SOT 로 consume (중복 탐지 금지)
- `preprocessing.pii_tokens: list[dict]` — `{raw, utterance_idx, inferred_category, inference_confidence}`
- `preprocessing.rule_pre_verdicts: dict[str, dict]` — 키는 `"item_01"` / `"item_02"` / ... (zero-padded 2자리). Group A 소관: `item_01`/`item_02` (+#3~#9 중 Layer1 제공분 Dev1 회신 대기)
- `preprocessing.quality: dict` — `{transcription_confidence, diarization_success, duration_sec, unevaluable}`. `unevaluable=True` 시 Group A 전체 `evaluation_mode=unevaluable`, `routing.force_hitl=True` 처리

**Dev2 Sub Agent consume 규칙**:
1. `quality.unevaluable=True` → 모든 8개 항목 `score=None`, `evaluation_mode=unevaluable`, LLM 호출 skip
2. `rule_pre_verdicts["item_XX"].confidence_mode=="hard"` → Rule 점수 bypass, LLM skip (Dev1 과 합의된 hybrid 3안)
3. `rule_pre_verdicts["item_XX"].recommended_for_llm_verify=True` → LLM verify 후 `rule_verdict_diff` 에 불일치 기록
4. `detected_sections.opening/closing` turn 범위 사용해 #1/#2 LLM 입력 슬라이싱 (V1 `agent_turn_assignments.greeting` 대체)

---

## 1. 인사예절 Sub Agent (`greeting.py` / `prompts/group_a/greeting.md`)

| V2 항목 | 소스 V1 프롬프트 | ALLOWED_STEPS (qa_rules) | iter03_clean 핵심 | V2 병합 전략 | 회귀 리스크 |
|---|---|---|---|---|---|
| #1 첫인사 (full, 5pt) | `item_01_greeting.sonnet.md` | `[5, 3, 0]` | Iter05 "전화 주셔서 감사합니다" 인정 확장 | 원문 거의 그대로 (System 앞단에 배치). 3요소 인사말/소속/상담사명 | **낮음** — V1 자체 안정 |
| #2 끝인사 (full, 5pt) | `item_02_farewell.sonnet.md` | `[5, 3, 0]` | iter03_clean 2요소 완화(마무리+상담사명)·STT 잘림 예외 | 원문 유지. `farewell_elements` / `stt_truncation_suspected` 필드 보존 | **중간** — 2요소 완화가 #2 MAE 개선 핵심. 병합 시 rubric 약화 주의 |

**병합 스키마(상위 반환)**:
```json
{"items": [
  {"item_number": 1, "score": 5, "deductions": [...], "evidence": [...], "confidence": 0.9, "self_confidence": 5, "summary": "..."},
  {"item_number": 2, "score": 5, "farewell_elements": {...}, "stt_truncation_suspected": false, "deductions": [...], "evidence": [...], "confidence": 0.9, "self_confidence": 5, "summary": "..."}
]}
```

**Layer1 수신**:
- `rule_pre_verdicts.greeting` = `{item_1: {agent_greeting_found, affiliation_found, agent_name_found, turn_ref}, item_2: {closing_found, additional_inquiry_found, agent_name_mentioned, stt_truncation_suspected}}`
- LLM 은 rule 결과를 "pre-evidence" 로 참고하되 최종 판정 독립 수행.

---

## 2. 경청소통 Sub Agent (`listening_comm.py` / `prompts/group_a/listening_comm.md`)

| V2 항목 | 소스 V1 프롬프트 | ALLOWED_STEPS | iter03_clean 핵심 | V2 병합 전략 | 회귀 리스크 |
|---|---|---|---|---|---|
| #3 경청말겹침 (skipped_full, 5pt) | `item_03_listening.sonnet.md` | `[5, 3, 0]` | STT 마커 없으면 만점 유추 | **프롬프트는 rubric 설명 유지**. 코드상 `score=5` 고정 반환 (LLM 호출 생략 가능). V2 설계서 원칙: 마스킹/전사 한계 영역은 무조건 만점 | **낮음** |
| #4 호응공감 (full, 5pt, LLM+Few-shot) | `item_04_empathy.sonnet.md` | `[5, 3, 0]` | 불만 수용형 공감 rubric (Iter3), 실질 공감 키워드 리스트 | 원문 유지. Golden-set Few-shot k=3~5 주입 블록 추가 (`## Few-shot`) | **중간** — "형식적 감사" vs "불만 수용형 공감" 분리 신호 유지 필수 |
| #5 대기멘트 (full, 5pt, 조건부) | `item_05_hold_mention.sonnet.md` | `[5, 3, 0]` | 사후 감사 멘트 가점화 (누락만으로는 감점 금지), 대기 미발생 만점 | 원문 유지. Layer1 `hold_detected` 수신 시 미발생 → `score=5` early-return | **낮음** |

**핵심 주의사항**:
- #3 은 LLM 호출 비용 절약 차원에서 `score=5` 하드코딩, evidence 는 상담사 대표 3턴 Layer1 에서 수신 후 그대로 통과.
- #4 단독 LLM 호출, #5 는 Layer1 signal (before/after/silence counts) 에 따라 분기: `hold_detected=False` → LLM skip (점수=5). `hold_detected=True` → LLM 호출.
- 1 LLM 호출 동시 평가 구조이므로 사실상 #4 + #5 병합 (`#3` 은 코드단).

**병합 스키마**:
```json
{"items": [
  {"item_number": 3, "score": 5, "evaluation_mode": "skipped_full", "deductions": [], "evidence": [...], "confidence": 1.0, "self_confidence": 5, "summary": "STT 마커 미감지 → 만점 고정"},
  {"item_number": 4, "score": 5, "empathy_expressions_found": [...], "simple_response_only": false, "deductions": [...], "evidence": [...], "confidence": 0.9, "self_confidence": 5, "summary": "..."},
  {"item_number": 5, "score": 5, "hold_detected": false, "before_hold_found": false, "after_hold_found": false, "deductions": [], "evidence": [...], "confidence": 0.95, "self_confidence": 5, "summary": "..."}
]}
```

---

## 3. 언어표현 Sub Agent (`language.py` / `prompts/group_a/language.md`)

| V2 항목 | 소스 V1 프롬프트 | ALLOWED_STEPS | iter03_clean 핵심 | V2 병합 전략 | 회귀 리스크 |
|---|---|---|---|---|---|
| #6 정중한표현 (full, 5pt, LLM+금지어사전) | `item_06_polite_expression.sonnet.md` | `[5, 3, 0]` | Iter05 구어체 축약("같애요"/"에용") 정상 존대 인정. #7 스필오버 차단 | 원문 유지. Dev4 `tenants/generic/prohibited_terms.txt` 로드 후 prompt 에 "금지어 리스트" 섹션 동적 삽입 | **중간** — #6 은 iter05 에서 가장 까다로운 항목. #7 감점 사유 혼입 절대 금지 |
| #7 쿠션어 (full, 5pt, 조건부) | `item_07_cushion.sonnet.md` | `[5, 3, 0]` | iter03_clean refusal-gated (`refusal_count=0` → 강제 5점). Iter3 쿠션어 확장 리스트 | 원문 유지. Layer1 `refusal_count=0` 수신 시 LLM 호출 생략 가능 (early-return 5점) | **높음** — #7 이 가장 민감. refusal gating 유지 필수. 병합 프롬프트에서도 "refusal_count=0 → 무조건 5점" 규칙 최상단 명시 |

**1 LLM 호출 병합 전략**: #6 과 #7 은 동일 상담사 발화 전수 스캔이라 사실상 1 컨텍스트. 그러나 V1 에서 의도적으로 직렬 실행(가이던스: "#6 과 #7 독립 평가, 스필오버 차단"). V2 병합 시 **"절대 금지 감점 사유" 섹션 강조** — #6 프롬프트에서 "쿠션어 부재는 #7 영역" 경고 그대로 유지, #7 프롬프트는 "거절/불가 상황 없으면 #6 영역과 무관하게 만점" 강조.

**병합 스키마**:
```json
{"items": [
  {"item_number": 6, "score": 5, "deductions": [...], "evidence": [...], "confidence": 0.9, "self_confidence": 5, "summary": "..."},
  {"item_number": 7, "score": 5, "refusal_count": 0, "cushion_word_count": 0, "refusal_situation_detected": false, "cushion_words_found": [], "deductions": [], "evidence": [], "confidence": 0.92, "self_confidence": 5, "summary": "거절 상황 미발생"}
]}
```

---

## 4. 니즈파악 Sub Agent (`needs.py` / `prompts/group_a/needs.md`)

| V2 항목 | 소스 V1 프롬프트 | ALLOWED_STEPS | iter03_clean 핵심 | V2 병합 전략 | 회귀 리스크 |
|---|---|---|---|---|---|
| #8 문의파악복창 (full, 5pt) | `item_08_inquiry_paraphrase.sonnet.md` | `[5, 3, 0]` | 복창 신호 확장 (의문형/평서형 모두 인정) | 원문 유지. Layer1 `paraphrase_count` / `requery_count` 수신 | **낮음** |
| #9 고객정보확인 (structural_only, 5pt) | `item_09_customer_info.sonnet.md` | `[5, 3, 0]` | 0번 절차(고객 선제+상담사 복창) 최우선. 양해 표현/Group A~D 인정 리스트. G-1 룰 가드 (info_count≥1 → score=0 금지) | **V2 원칙 변경**: `evaluation_mode=structural_only` — 마스킹된 PII 내용 대조 불가 → 절차(양해 동반/선제 복창)만 평가. V1 프롬프트의 "판정 절차" 그대로 재사용 + "내용 검증 금지 — 마스킹" 경고 추가 | **중간** — 원칙 "내용 검증 금지" 지키되 절차 판정은 V1 그대로. G-1 룰 가드는 코드단 유지 (reconciler 수준) |

**병합 스키마**:
```json
{"items": [
  {"item_number": 8, "score": 5, "customer_need_identified": "...", "paraphrase_found": true, "requery_count": 0, "deductions": [...], "evidence": [...], "confidence": 0.9, "self_confidence": 5, "summary": "..."},
  {"item_number": 9, "score": 5, "evaluation_mode": "structural_only", "info_items_checked": [...], "courtesy_used": true, "courtesy_phrase": "...", "customer_provided_first": false, "deductions": [], "evidence": [...], "confidence": 0.9, "self_confidence": 5, "summary": "..."}
]}
```

---

## 공통 응답 스키마 (Dev3 와 합의 필요)

```python
class SubAgentResult(TypedDict):
    agent_name: Literal["greeting", "listening_comm", "language", "needs", ...]
    items: list[ItemEvaluation]
    deduction_log: list[DeductionEntry]
    agent_confidence: float  # Sub Agent 전체 신뢰도 (각 item confidence 평균)

class ItemEvaluation(TypedDict):
    item_number: int
    item_name: str
    max_score: int
    score: int  # snap_score 통과 후
    evaluation_mode: Literal["full", "skipped_full", "structural_only"]
    deductions: list[Deduction]
    evidence: list[Evidence]  # speaker/timestamp/quote 3필드 강제
    confidence: float  # LLM "confidence" 필드 (0.0~1.0)
    self_confidence: int  # LLM "self_confidence" 필드 (1~5)
    summary: str
    # 항목별 추가 필드 (farewell_elements, stt_truncation_suspected, refusal_count 등) 확장 슬롯
    details: dict

class Evidence(TypedDict):
    turn: int  # timestamp 대체 (STT 턴 번호)
    speaker: Literal["agent", "customer"]
    quote: str  # exact quote
    relevance: str  # optional
```

---

## V1 skills 모듈 재사용 전략 (Dev2 권고 — PL 결정 대기)

V1 `nodes/skills/` 의 공통 유틸은 V2 Sub Agent 에서도 **그대로 import 해서 재사용 권고** (중복 구현 금지).

| V1 skills 파일 | 용도 | V2 재사용 전략 |
|---|---|---|
| `constants.py` | 화자 마커 · 패턴 리스트 | **V2 canonical 로 유지**. 로컬 재정의 금지 (CLAUDE.md 규칙 준수) |
| `reconciler.py` | `snap_score` · `reconcile` · `reconcile_evaluation` · `normalize_fallback_deductions` | **재사용 필수**. 각 Sub Agent 출력 직전에 `reconcile_evaluation` 호출 |
| `pattern_matcher.py` | greeting/empathy/cushion/hold/inappropriate regex 감지 | Layer 1 (Dev1) 로 이관 — V2 에서는 Sub Agent 가 Layer1 결과를 수신 |
| `deduction_log.py` | `build_deduction_log_from_evaluations` / `_normalize_turn_ref` | **재사용**. `SubAgentResult.deduction_log` 생성 시 |
| `error_results.py` | `build_llm_failure_result` | **재사용**. 전사록 누락·LLM 실패 시 표준 dict |
| `node_context.py` | `NodeContext` dataclass | V2 Sub Agent 입력 컨텍스트로 확장 (rule_pre_verdicts 필드 추가) |
| `evidence_builder.py` | `build_turn_evidence` (deductions.evidence_ref 우선) | **재사용** (특히 #8/#9 에서 V1 패턴 유지) |
| `scorer.py` | `qa_rules` 기반 `score_item` 검증 | **재사용** — `QA_RULES` 단일 소스 |
| `qa_rules.py` (root) | 18 항목 ALLOWED_STEPS 정의 | **V2 canonical 유지** — 항목별 허용 단계 변경 금지. Group A 모두 `[5, 3, 0]` |

**PL 결정 대기 항목**:
- V2 Sub Agent 가 V1 skills 유틸을 어떻게 import 할지:
  (A) V1 경로 유지 + PYTHONPATH 확장 — 가장 단순, 변경 0
  (B) V2 트리로 copy/symlink — 독립 실행 가능, 중복
  (C) 공용 `packages/qa-shared/` 패키지로 승격 — 가장 clean, 공수 큼
- Dev2 권고: **(A)** — CLAUDE.md 의 "packages만 수정" 규칙 준수 + V1 파일 그대로 두고 V2 에서 import 경로만 조정

**V2 evaluation_mode 세부 구현 (Dev2 관점)**:
- `skipped_full` (#3 경청): LLM 호출 스킵하고 `score=5` 고정 반환. V1 rule-based 가 STT 마커 없으면 이미 5점 반환하므로 동일 결과. V2 에서는 Layer1 `rule_pre_verdicts.listening_comm.item_3.stt_markers_present=False` → early-return 처리
- `structural_only` (#9 고객정보): V1 프롬프트 판정 절차 0~4 유지하되 **"내용 대조" 조항 제거**. 절차(양해 동반/선제 복창)만 평가. 원칙 "마스킹으로 내용 검증 불가" 준수

---

## Dev2 소관 블로커 이슈 (Q1 회신)

### PL Q1: iter03_clean 재사용 정합성 리스크 (Group A 관점)

**회귀 위험 낮음 (원문 그대로 재사용 권고)**:
- #1 첫인사 — iter05 확장 인정 패턴이 V3/V4 대비 안정. 병합 시 영향 적음
- #3 경청 — 어차피 코드단 `score=5` 고정
- #8 문의파악복창 — 복창 신호 확장이 MAE 개선 핵심, 그대로 유지

**회귀 위험 중간 (주의하여 재사용, 절차 보존 의무)**:
- #2 끝인사 — 2요소 완화 rubric 이 MAE 1.67→1.89 회귀를 방지했던 핵심. 병합 프롬프트에서 rubric 약화 금지
- #4 호응공감 — "형식적 감사 vs 불만 수용형 공감" 분리 신호가 MAE 핵심
- #9 고객정보확인 — V2 `structural_only` 원칙과 V1 "내용 대조" rubric 이 충돌 가능. 절차(양해 동반/선제 복창)만 남기고 내용 대조 명시 제거 필요

**회귀 위험 높음 (재사용 시 최우선 감독 필요)**:
- #6 정중한표현 — iter05 에서 가장 민감 (구어체 축약 misclassification 이력)
- #7 쿠션어 — refusal-gated 규칙 번복되면 과감점 회귀 확실. iter03_clean 의 MAE 3.89 달성에 중심 역할

**종합 결론**:
- **iter03_clean 을 V2 에 재사용하는 전략은 Group A 에 대체로 안전하되, #6/#7 은 "절대 금지 감점 사유" 경고 블록을 100% 원문 복사해야 함**.
- Sub Agent 병합 프롬프트에서 각 항목 rubric 을 JSON 배열로 동시 반환 요구할 때, 각 item 구획을 구분자(예: `### Item #1`)로 명확 분리해서 1 LLM 호출에도 항목간 "감점 사유 혼입" 방지.
