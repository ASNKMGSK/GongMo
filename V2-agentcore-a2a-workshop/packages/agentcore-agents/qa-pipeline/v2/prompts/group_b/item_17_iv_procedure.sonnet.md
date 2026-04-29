# Item #17 — 정보 확인 절차 (max 5점)

**STT 기반 통합 상담평가표 v2.0** 의 "개인정보 보호" 대분류 (10점) 내 항목 #17.

**평가모드**: compliance_based (절차 준수 여부 기준)
**처리방식**: Rule 중심 + LLM verify
**비고**: 패턴 탐지, **T3(필수 검수) 라우팅**
**ALLOWED_STEPS**: [5, 3, 0]

## 평가 기준

**본인 확인 플로우를 규정된 순서대로 이행하였는가?**

- **5점**: 본인 확인 멘트 → 정보 질의(양해 표현) → 고객 응답 → 확인 완료 안내 **순서 준수**
  - **예외 A (선제 제공 허용)**: 고객이 자발적으로 PII(연락처/성함)를 선제 제공 → 이후 상담사가 **복창 확인** (예: "*** 고객님 본인 맞으시지요") + 확인 완료 안내 를 수행한 경우 **5점 유지**. 1단계(본인 확인 멘트)·2단계(양해 표현) 누락을 감점하지 **않음**. ※ 이 예외는 규정 운영상 인정된 플로우.
- **3점**: **경미한 순서 이탈** — 아래 중 하나:
  - 1단계 또는 2단계 중 **하나만 누락** (예: 본인 확인 멘트 없이 바로 "성함 부탁드립니다" 로 정보 질의 시작)
  - 양해 표현 부재 (단, 고객 선제 제공 예외 A 는 3점 아님 — 위 5점 규정 적용)
  - 중간 단계 건너뛰기지만 최종 확인 완료 안내(4단계) 는 수행
- **0점**: **순서 위반 다건** 또는 본인 확인 절차 **자체 생략**
  - 2단계 이상 누락 + 확인 완료 안내(4단계) 도 없음
  - 고객 응답(3단계) 도 없이 임의 상담 진행

**※ PII 토큰(`***`) 등장 위치와 본인 확인 트리거 문구 순서로 판정.**

## 판정 기준 (compliance_based 원칙)

- **내용 무관**, 오직 **순서 패턴** 준수 여부.
- 4단계 순서:
  1. 본인 확인 멘트 (예: "본인 확인을 위해 정보 여쭤보겠습니다")
  2. 정보 질의 + 양해 표현 (예: "번거로우시겠지만 연락처 말씀 부탁드리겠습니다")
  3. 고객 응답 (`***` 토큰 등장)
  4. 확인 완료 안내 (예: "*** 고객님 본인 맞으십니까", "소중한 정보 확인 감사드립니다")
- 4단계 모두 순서 준수 → 5점
- **고객 선제 제공 예외 (A)**: 3단계(고객 응답) 가 1/2단계 없이 먼저 등장 + 이후 4단계(복창 확인) 수행 → **5점** (역순 위반 아님)
- 1~2단계 중 하나만 누락, 3/4단계 수행 → 3점
- 2단계 이상 누락 또는 4단계(확인 완료) 자체 부재 → 0점
- `force_t3=true` 고정 — 인간 검수 필수.

## Evidence 강제

- Evidence 최소 1개 필수 (compliance_based 모드).
- 스키마: `{speaker, timestamp, quote, turn_id}`.

## 출력 (JSON)

```json
{
  "item_number": 17,
  "score": 5,
  "evaluation_mode": "compliance_based",
  "force_t3": true,
  "mandatory_human_review": true,
  "procedure_steps": {
    "self_identification": true,
    "info_request_with_apology": true,
    "customer_response": true,
    "completion_notice": true
  },
  "deductions": [],
  "evidence": [
    {"speaker": "상담사", "timestamp": null, "quote": "고객님 연락처와 성함 말씀 부탁드리겠습니다", "turn_id": 7},
    {"speaker": "상담사", "timestamp": null, "quote": "*** 고객님 본인 맞으십니까", "turn_id": 14}
  ],
  "self_confidence": 4,
  "summary": "..."
}
```

## 규칙

- `score` 는 정확히 **5 / 3 / 0** 중 하나 (ALLOWED_STEPS = [5, 3, 0]).
- `score + Σ(deductions[].points) === 5` (5점이면 빈 배열, 3점이면 points 합계 2, 0점이면 points 합계 5).
- `evaluation_mode="compliance_based"` + `force_t3=true` + `mandatory_human_review=true` 고정.
- Evidence 최소 1개 필수.
- 한국어 작성. 한자 금지.

## 자기 검증

1. score 가 **5 / 3 / 0** 중 하나인가? (중간 값 금지)
2. 고객 선제 제공 케이스 (예외 A) 인지 먼저 확인했는가? — 해당 시 1/2단계 누락을 감점하지 않음 → 5점 유지.
3. 4단계 순서 (`procedure_steps`) 가 명시됐는가?
4. `force_t3=true` + `mandatory_human_review=true` 가 있는가?
5. `score + Σ(deductions.points) == 5` 산술 검증 통과인가?
