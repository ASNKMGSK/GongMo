<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Sub Agent IO 계약 명세 (Phase A1)

**버전**: v1.0-draft (2026-04-20, Dev5 기안)
**관련 파일**:
- `v2/schemas/sub_agent_io.py` — TypedDict 엄격 정의
- `v2/schemas/enums.py` — EvaluationMode / CategoryKey / CATEGORY_META
- `v2/schemas/qa_output_v2.py` — 최종 JSON 직렬화 (pydantic)

**합의 대상**: Dev2 (Group A: #1-9) · Dev3 (Group B: #10-18) · PL 검토

---

## 1. 설계 원칙 (설계서 §3 재확인)

- **원칙 2**: Rubric 이 점수를, RAG 는 부가 정보를. Sub Agent 최종 점수는 rubric 기준이며 RAG 과거 점수의 가중평균 금지.
- **원칙 3**: Evidence 인용이 점수의 전제 조건. `evaluation_mode=full` 시 `evidence[]` 최소 1개 필수.
- **원칙 5**: `evaluation_mode` 필드로 한계 투명 노출 (6종).

---

## 2. Sub Agent 8개 매핑 (설계서 §4 Layer 2)

| CategoryKey                | 한글명         | 담당 Dev | 포함 항목                          | 카테고리 배점 |
|---------------------------|--------------|---------|---------------------------------|-------------|
| `greeting_etiquette`      | 인사 예절      | Dev2    | #1 첫인사 / #2 끝인사              | 10          |
| `listening_communication` | 경청 및 소통   | Dev2    | #3 경청(skipped) / #4 호응 / #5 대기 | 15          |
| `language_expression`     | 언어 표현     | Dev2    | #6 정중표현 / #7 쿠션어             | 10          |
| `needs_identification`    | 니즈 파악     | Dev2    | #8 문의파악 / #9 고객정보(structural_only) | 10      |
| `explanation_delivery`    | 설명력·전달력 | Dev3    | #10 명확성 / #11 두괄식             | 15          |
| `proactiveness`           | 적극성        | Dev3    | #12 문제해결 / #13 부연 / #14 사후   | 15          |
| `work_accuracy`           | 업무 정확도   | Dev3    | #15 정확안내(partial) / #16 필수안내 | 15          |
| `privacy_protection`      | 개인정보 보호 | Dev3    | #17 절차 / #18 준수 (둘 다 compliance_based, FORCE_T3) | 10 |

**총 100점** (프로토타입은 #3 경청 skipped → 만점 5 고정).

---

## 3. Sub Agent 입력 (Orchestrator → Sub Agent)

Layer 3 Orchestrator 가 각 Sub Agent 에게 전달하는 페이로드. Sub Agent 는 1회 LLM 호출로 카테고리 내 모든 항목을 동시 평가한다.

```json
{
  "agent_id": "greeting-agent",
  "category": "greeting_etiquette",
  "items_to_evaluate": [1, 2],
  "tenant_id": "generic",
  "consultation_id": "CALL_20260420_001",

  "transcript_slice": {
    "turns": [
      {"turn_id": 0, "speaker": "상담사", "timestamp": "00:00:02",
       "text": "안녕하십니까, ○○ 고객센터 상담사 김철수입니다"},
      {"turn_id": 1, "speaker": "고객", "timestamp": "00:00:06", "text": "네, 안녕하세요"}
    ],
    "segment_refs": {
      "opening": {"start": 0, "end": 3},
      "body": {"start": 4, "end": 40},
      "closing": {"start": 41, "end": 45}
    }
  },

  "rubric_md": "경로: tenants/generic/rubric.md 의 해당 카테고리 섹션 발췌",

  "rule_pre_verdicts": {
    "1": {
      "score": 5, "confidence": 0.95, "confidence_mode": "soft",
      "rationale": "인사말/소속/상담사명 3요소 rule 매칭 성공",
      "evidence_turn_ids": [0], "evidence_snippets": ["안녕하십니까..."],
      "elements": {"greeting": true, "affiliation": true, "agent_name": true},
      "recommended_for_llm_verify": true
    }
  },

  "few_shot_examples": [
    {"turn_excerpt": "...", "score": 5, "rationale": "..."}
  ],

  "intent": {
    "primary_intent": "상품문의", "sub_intents": ["가입조건"],
    "product": "자동이체", "complexity": "simple"
  },

  "masking_format": {"version": "v1_symbolic", "spec": "PII 전부 '***'"},
  "llm_backend": "bedrock",
  "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0"
}
```

**필드 정의**
- `items_to_evaluate` — 해당 카테고리의 item_number 배열. CATEGORY_META 기준.
- `transcript_slice.turns` — Layer 1 dialogue_parser 가 생성한 턴 목록. 카테고리별 agent_turn_assignments 로 pre-filter 가능.
- `rule_pre_verdicts` — Layer 1 (e) 산출. `confidence_mode=hard` 면 LLM 호출 생략 가능 (#1 첫인사 완벽 매칭 등). `soft` 면 LLM 재검증 필수.
- `few_shot_examples` — Dev4 Golden-set RAG 가 k=3~5 retrieve. 비어있을 수 있음 (Phase 0 미완료 시).
- `intent` — #16 필수 안내 이행 평가에 필수. work_accuracy / proactiveness Sub Agent 도 참조.

---

## 4. Sub Agent 출력 (Sub Agent → Orchestrator)

### 4.1 `SubAgentResponse` 전체 구조

```json
{
  "agent_id": "greeting-agent",
  "category": "greeting_etiquette",
  "status": "success",

  "items": [
    { /* ItemVerdict (§4.2) */ },
    { /* ItemVerdict */ }
  ],

  "category_score": 10,
  "category_max": 10,
  "category_confidence": 5,

  "llm_backend": "bedrock",
  "llm_model_id": "claude-sonnet-4-6",
  "elapsed_ms": 2340,
  "error_message": null
}
```

**검증 규칙**
- `category_score == Σ items[].score` (Orchestrator 재계산 대조).
- `category_max == CATEGORY_META[category]["max_score"]`.
- `items` 는 `items_to_evaluate` 와 길이·item_number 셋 완전 일치.
- `category_confidence` 는 1~5 정수.
- `status` 가 `error` 이면 Orchestrator 가 Override 로 대체 처리.

### 4.2 `ItemVerdict` — 평가항목 1건

```json
{
  "item_number": 1,
  "item_name": "첫인사",
  "item_name_en": "Opening greeting",
  "max_score": 5,
  "score": 5,
  "evaluation_mode": "full",
  "judgment": "인사말/소속/상담사명 3요소 모두 포함",

  "deductions": [],

  "evidence": [
    {
      "speaker": "상담사",
      "timestamp": "00:00:02",
      "quote": "안녕하십니까, ○○ 고객센터 상담사 김철수입니다",
      "turn_id": 0
    }
  ],

  "llm_self_confidence": {
    "score": 5,
    "rationale": "3요소 모두 명확히 확인됨"
  },

  "rule_llm_delta": {
    "has_rule_pre_verdict": true,
    "rule_score": 5,
    "llm_score": 5,
    "agreement": true,
    "override_reason": null,
    "verify_mode_used": false
  },

  "mode_reason": null
}
```

**감점이 있는 예시 (ItemVerdict)**

```json
{
  "item_number": 1,
  "item_name": "첫인사",
  "max_score": 5,
  "score": 3,
  "evaluation_mode": "full",
  "judgment": "소속 안내 누락",
  "deductions": [
    {
      "reason": "3요소 중 소속 누락",
      "points": 2,
      "rule_id": "greeting_element_missing_1",
      "evidence_refs": [0]
    }
  ],
  "evidence": [
    {"speaker": "상담사", "timestamp": "00:00:02",
     "quote": "안녕하세요, 상담사 김철수입니다", "turn_id": 0}
  ],
  "llm_self_confidence": {"score": 4, "rationale": "소속 누락 명확"},
  "rule_llm_delta": {
    "has_rule_pre_verdict": true,
    "rule_score": 3, "llm_score": 3, "agreement": true,
    "override_reason": null, "verify_mode_used": false
  },
  "mode_reason": null
}
```

### 4.3 필드 상세 규약

| 필드                    | 타입                  | 제약                                                               |
|------------------------|----------------------|-------------------------------------------------------------------|
| `item_number`          | int (1~18)           | qa_rules 의 item_number 와 1:1                                     |
| `max_score`            | int                  | qa_rules.max_score 와 동일                                         |
| `score`                | int                  | **반드시 snap_score 경유** — ALLOWED_STEPS 준수                     |
| `evaluation_mode`      | Literal 6종          | full / structural_only / compliance_based / partial_with_review / skipped / unevaluable |
| `judgment`             | str                  | 한 줄 요약. 빈 문자열 금지.                                         |
| `deductions`           | list[DeductionEntry] | 만점이면 `[]`. Σ points == max_score - score 가 성립해야 함.       |
| `evidence`             | list[EvidenceQuote]  | full 모드 최소 1개 필수. skipped/unevaluable 은 `[]` 허용.         |
| `llm_self_confidence`  | obj                  | `score` 는 1~5 정수. 프롬프트에 앵커 명시 (§8.1).                  |
| `rule_llm_delta`       | obj 또는 null        | Layer 1 rule_pre_verdict 있는 항목만 객체, 없으면 null.            |
| `mode_reason`          | str 또는 null        | mode ∈ {skipped, unevaluable, partial_with_review} 일 때 필수.    |

### 4.4 `EvidenceQuote` 스펙 (원칙 3)

```json
{
  "speaker": "상담사",
  "timestamp": "00:00:02",
  "quote": "안녕하십니까, ○○ 고객센터 상담사 김철수입니다",
  "turn_id": 0
}
```

- `speaker` — "상담사" | "고객" (tenant_config 에서 override 가능).
- `timestamp` — ISO 시:분:초. STT 가 타임스탬프 미제공 시 `null`.
- `quote` — 원문 그대로. 수정/요약 금지. Hallucination 방지.
- `turn_id` — Layer 1 dialogue_parser 의 turn_id. 없으면 `null`.

---

## 5. evaluation_mode 6종 Enum (설계서 §5.3)

| mode                   | 언제 사용                                                          | 예시                              | evidence 필수 |
|------------------------|-------------------------------------------------------------------|----------------------------------|-------------|
| `full`                 | 완전 평가 가능, 모든 정보 활용                                       | #1 첫인사 / #7 쿠션어 / #10 명확성 | **필수 1개 이상** |
| `structural_only`      | 마스킹으로 내용 검증 불가, 절차/구조만 평가                          | #9 고객정보 확인                  | 가능 시 첨부 |
| `compliance_based`     | 규정 준수 여부 기준 평가 (내용 무관)                                 | #17 정보확인 / #18 정보보호        | 패턴 매칭 근거 |
| `partial_with_review`  | AI 판정 + 인간 검수 필수 (RAG 부재 등)                               | #15 정확한안내 (업무지식 RAG 미연결) | 가능 시 첨부 |
| `skipped`              | 해당 상황 부재 (만점 고정)                                          | #3 경청 / #7 쿠션어 (거절 상황 없을 때) | 빈 배열 허용 |
| `unevaluable`          | STT 품질 등으로 평가 자체 불가                                      | 품질 저하 상담                    | 빈 배열 허용 |

**Sub Agent 프롬프트 가이드** — 모드별 분기 로직을 프롬프트에 명시:

```
- 쿠션어 활용 (#7): 상담 중 "거절/불가/양해" 상황이 없으면 evaluation_mode=skipped, score=max_score, mode_reason 에 상황 부재 명시.
- 정확한 안내 (#15): 업무지식 RAG 결과가 입력에 없으면 evaluation_mode=partial_with_review, score 는 LLM 초안, mode_reason="업무지식 RAG 미연결".
- 고객정보 확인 (#9): 마스킹 환경이므로 항상 evaluation_mode=structural_only, 절차 순서(본인확인 → 질의 → 응답 → 확인완료) 준수 여부만 평가.
```

---

## 6. 처리 계약 (Sub Agent 측 구현 규약)

1. **LLM 호출 1회 원칙** — 카테고리 내 모든 항목을 1회 호출로 반환. 호출 분할 시 비용↑·일관성↓.
2. **snap_score 강제** — LLM 이 반환한 raw score 는 반드시 `nodes.skills.reconciler.snap_score(item_number, score)` 경유.
3. **타임아웃 예외 전파** — `except LLMTimeoutError: raise` 를 `except Exception` 앞에 배치. 파이프라인 중단 시그널.
4. **rule_llm_delta 필수 대상** — #1, #2, #16, #17 (Layer 1 rule_pre_verdict 가 나오는 항목). 나머지는 `null`.
5. **FORCE_T3_ITEMS={9,17,18} 준수** — Sub Agent 는 판정만, Tier 는 Layer 4 가 강제. Sub Agent 가 routing 필드 건드리지 말 것.
6. **Rule `confidence_mode=hard`** — 비용 절감 위해 LLM 생략 허용. 이 경우 `rule_llm_delta.verify_mode_used=true`, `llm_self_confidence.score` 는 rule confidence 로부터 유도.

---

## 7. 에러 / 부분 성공 처리

### status=partial
일부 항목은 평가 성공, 일부는 타임아웃/무근거 발생 시.
- 실패 항목은 `ItemVerdict.evaluation_mode="unevaluable"`, `score=0`, `mode_reason` 에 사유.
- `items` 배열은 여전히 `items_to_evaluate` 길이 유지.
- Orchestrator 는 unevaluable 항목을 Override 대상으로 판단 (T3 강제).

### status=error
카테고리 전체 실패 (LLM 호출 자체 불가).
- `items` 는 빈 배열 또는 불완전 상태.
- `error_message` 에 사유 (예: `"Bedrock ThrottlingException"`).
- Orchestrator 의 reconciler 가 rule fallback 으로 대체 + `[SKIPPED_INFRA]` 태그.
- Layer 4 tier_router 가 `unevaluable_items` 로 인식 → T3 policy_driven.

---

## 8. 파이썬 import 예시 (Dev2/Dev3 구현 가이드)

```python
from typing import cast
from v2.schemas import (
    SubAgentResponse, ItemVerdict, EvidenceQuote,
    CategoryKey, CATEGORY_META, EvaluationMode,
)
# V1 snap_score 재사용
from nodes.skills.reconciler import snap_score

def build_greeting_response(items: list[dict], ...) -> SubAgentResponse:
    verdicts: list[ItemVerdict] = []
    for raw in items:
        verdict: ItemVerdict = {
            "item_number": raw["item_number"],
            "item_name": raw["item_name"],
            "max_score": raw["max_score"],
            "score": snap_score(raw["item_number"], raw["llm_score"]),
            "evaluation_mode": cast(EvaluationMode, raw["mode"]),
            "judgment": raw["judgment"],
            "deductions": raw.get("deductions", []),
            "evidence": raw["evidence"],
            "llm_self_confidence": {"score": raw["llm_self"], "rationale": raw.get("rationale")},
            "rule_llm_delta": raw.get("rule_llm_delta"),
            "mode_reason": raw.get("mode_reason"),
        }
        verdicts.append(verdict)

    return {
        "agent_id": "greeting-agent",
        "category": cast(CategoryKey, "greeting_etiquette"),
        "status": "success",
        "items": verdicts,
        "category_score": sum(v["score"] for v in verdicts),
        "category_max": CATEGORY_META["greeting_etiquette"]["max_score"],
        "category_confidence": raw.get("category_confidence", 4),
        "llm_backend": "bedrock",
        "llm_model_id": "claude-sonnet-4-6",
        "elapsed_ms": 2340,
        "error_message": None,
    }
```

---

## 9. 계약 변경 시 규칙

- **Breaking change** (필드 제거/타입 변경): PL 승인 + Dev2/Dev3/Dev5 3자 합의 필수.
- **Additive change** (필드 추가 — total=False 의 새 Optional): 사전 공지 후 추가 가능.
- 변경 시 `v2/schemas/sub_agent_io.py` 와 본 문서를 **동시 수정**. 버전 번호 bump.
- V1 `EvaluationResult` 호환성은 레거시 노드 재사용 시에만 적용. V2 신규 개발은 반드시 본 스펙 준수.

---

**회신 요청**: Dev2/Dev3 각자 Group 의 9-9 항목별 mode 할당 (어떤 항목이 어떤 조건에서 skipped/structural_only/compliance_based 인지) 초안 확인 후 이견 SendMessage 부탁드립니다.
