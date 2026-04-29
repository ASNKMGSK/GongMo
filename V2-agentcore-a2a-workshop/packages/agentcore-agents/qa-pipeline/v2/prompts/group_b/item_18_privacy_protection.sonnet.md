# Item #18 — 정보 보호 준수 (max 5점)

**STT 기반 통합 상담평가표 v2.0** 의 "개인정보 보호" 대분류 (10점) 내 항목 #18.

**평가모드**: compliance_based (위반 패턴 탐지)
**처리방식**: Rule 패턴 + T3 필수
**비고**: **T3 라우팅 무조건**
**ALLOWED_STEPS**: [5, 0]

## 평가 기준

**상담 중 개인정보 취급 규정 위반이 발생하지 않았는가?**

- **5점**: 위반 패턴 **없음**
- **0점**: 아래 위반 패턴 중 **하나라도 탐지**

### 위반 패턴

- **패턴 A**: 본인 확인 전 상담사 **선언급** (상담사 발화 내 PII 토큰이 본인 확인 트리거보다 **먼저**)
- **패턴 B**: **제3자 지칭**("남편분", "지인", "가족분") 후 PII 관련 안내
- **패턴 C**: 고객이 **본인 확인 거부** 후 상담 계속 진행

**※ 마스킹 환경에서는 탐지만 수행, 최종 판정은 T3 인간 검수.**

## 판정 기준 (compliance_based 원칙)

- 내용 무관, 오직 **패턴 탐지** 준수 여부.
- 패턴 A / B / C 중 하나라도 탐지되면 `violations[]` 에 등록 후 score=0.
- 탐지 전무 → 5점.
- `force_t3=true` 고정 — 인간 검수 필수 (마스킹 환경에서 AI 판단은 잠정).

## Evidence 강제

- Evidence 최소 1개 필수 (compliance_based 모드).
- 위반 탐지 시 해당 위반 턴을 Evidence 로 출력.
- 미탐지 시에도 본인 확인 절차 수행 턴을 Evidence 로 출력.
- 스키마: `{speaker, timestamp, quote, turn_id}`.

## 출력 (JSON)

```json
{
  "item_number": 18,
  "score": 5,
  "evaluation_mode": "compliance_based",
  "force_t3": true,
  "mandatory_human_review": true,
  "violations": [],
  "patterns_checked": ["pattern_A", "pattern_B", "pattern_C"],
  "deductions": [],
  "evidence": [
    {"speaker": "상담사", "timestamp": null, "quote": "*** 고객님 본인 맞으십니까", "turn_id": 14}
  ],
  "self_confidence": 4,
  "summary": "위반 패턴 탐지 없음. 최종 판정은 T3 필수."
}
```

## 규칙

- `score` 는 정확히 5 / 0 중 하나 (binary).
- `score + Σ(deductions[].points) === 5`.
- `evaluation_mode="compliance_based"` + `force_t3=true` + `mandatory_human_review=true` 고정.
- `patterns_checked` 에 A/B/C 3개 모두 기재.
- `violations[]` 에 탐지 패턴 (예: `{"pattern": "A", "turn_id": 3, "description": "..."}`) 기재.
- Evidence 최소 1개 필수.
- 한국어 작성. 한자 금지.

## 자기 검증

1. score 가 5 또는 0 인가? (3점 금지)
2. `patterns_checked` 에 A/B/C 3개가 모두 있는가?
3. `force_t3=true` + `mandatory_human_review=true` 가 있는가?
4. `score + Σ(deductions.points) == 5` 산술 검증 통과인가?
