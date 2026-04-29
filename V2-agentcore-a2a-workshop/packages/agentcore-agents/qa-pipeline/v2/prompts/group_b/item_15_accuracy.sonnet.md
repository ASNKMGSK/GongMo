# Item #15 — 정확한 안내 (max 15점)

**STT 기반 통합 상담평가표 v3.0** 의 "업무 정확도" 대분류 (20점) 내 항목 #15.

**평가모드**: partial_with_review
**처리방식**: LLM + 업무지식 RAG
**비고**: **업무지식 RAG 필수. 부재 시 인간 검수.**
**ALLOWED_STEPS**: [15, 10, 5, 0]

> **2026-04-21 개정**: 배점 10점 → 15점 (#3 경청 제거분 흡수). 오안내 = 리스크 직결이므로 가장 높은 가중.

## 평가 기준

**업무 지식에 기반하여 정확한 정보를 안내하였는가?**

- **15점**: **오안내 없음 + 업무지식 RAG 근거로 명확히 뒷받침되는 정확한 안내**
- **10점**: **오안내 없이 정확한 정보 안내** (RAG 근거 약한 경우 포함)
- **5점**: **부정확한 안내**가 있으나 내용이 미미하거나 **즉시 정정**
- **0점**: 오안내 발생으로 **정정 안내 필요** (정정 미시도)

## 판정 기준 (partial_with_review 원칙)

- 업무지식 RAG 가 제공한 **근거 문서**와 상담사 발화를 대조.
- RAG 부재 (혹은 hit 없음) → **evaluation_mode="partial_with_review"** 유지 + `mandatory_human_review=true`.
- 즉시 정정 신호: 상담사가 본인 발화 직후 "죄송합니다, 정정하겠습니다" / "다시 확인해 보니..." 등으로 수정.

## 자기 정정 판정 기준 (correction_attempted 플래그)

상담사가 오안내를 한 직후 본인 발화로 정정하면 correction_attempted=true.

### correction_attempted=true 인정 조건 (모두 **상담사의 명시적 정정 발화** 필요)

- ✅ "죄송합니다, 정정하겠습니다"
- ✅ "다시 확인해 보니..."
- ✅ "아, 제가 잘못 안내드렸어요"
- ✅ "방금 말씀드린 거 정정하겠습니다"
- ✅ 동일 turn 내 모순되는 2개 정보 중 **후자가 명확히 정정 의도** ("아니 정확히는 ~입니다" 등)

### correction_attempted=false (정정 실패 케이스, 0점 부여 필수)

다음 케이스는 **정정으로 인정 안 함** — 모두 0점:

- ❌ **고객이 이의제기했는데 상담사가 정정 발화 없이 "확인해보겠습니다" 로 회피**
  - 예) 상담사: "단순 변심으로 기록해도 될까요" → 고객: "단순 변심 아닙니다, 포장 불량입니다" → 상담사: "확인해보겠습니다"
  - → 상담사가 "죄송합니다, 단순 변심이 아니라 포장 불량으로 정정하겠습니다" 같은 **명시적 정정 없이 회피** → correction_attempted=**false**, score=**0**
- ❌ **고객이 정정해주고 상담사가 그냥 받아들임**
  - 예) 상담사 오안내 → 고객 "그건 아니죠" → 상담사 "네 알겠습니다"
  - → 자기정정 발화 없음 → false, 0점
- ❌ **"확인해보겠습니다 / 잠시만요" 같이 답을 미루기**
  - → 정정 의도 없음. false, 0점

correction_attempted=false 이면 업무정확도 대분류 전체 0점 Override 트리거 (PDF §5.2).

### 점수 결정 절차 (반드시 순서대로)

1. 오안내 발화 식별 (RAG 근거 또는 고객 이의제기 시점)
2. 직후 상담사 발화 1~3턴 검토
3. 위 ✅ 인정 조건 중 **하나라도 명확히 매칭**되면 → correction_attempted=true → **5점**
4. ❌ 미정정 케이스 중 **하나라도 매칭**되면 → correction_attempted=false → **0점**
5. 오안내 자체가 없고 RAG 근거가 상담사 발화와 **명확히 일치** → **15점**
6. 오안내 없으나 RAG 근거가 약하거나 부분적 → **10점**
7. 의심스러우면 보수적으로 낮은 점수 부여

## OVERRIDE 표기 규칙 (중요)

`judgment` 또는 `summary` 필드 안에 `[OVERRIDE]`, `[강제]`, `Layer 3 강제` 같은 문구를 **임의로 삽입 금지**.
Override 적용은 Layer 3 가 자동 처리하며, LLM 응답에 해당 문구를 적으면 화면에 표시 모순 발생 (점수와 텍스트 불일치). 점수 결정만 정확히 하고, override 문구는 적지 말 것.

## Evidence 강제

- Evidence 최소 1개 필수 (상담사 안내 발화 + RAG 근거 있는 경우 별도 필드로).
- 스키마: `{speaker, timestamp, quote, turn_id}`.

## 출력 (JSON)

```json
{
  "item_number": 15,
  "score": 15,
  "evaluation_mode": "partial_with_review",
  "mandatory_human_review": true,
  "rag_hits": 0,
  "deductions": [],
  "evidence": [
    {"speaker": "상담사", "timestamp": null, "quote": "...", "turn_id": 55}
  ],
  "self_confidence": 3,
  "summary": "RAG 근거 일치 — 정확 안내"
}
```

## 규칙

- `score` 는 정확히 **15 / 10 / 5 / 0** 중 하나.
- `score + Σ(deductions[].points) === 15`.
- `evaluation_mode="partial_with_review"` + `mandatory_human_review=true` 고정.
- Evidence 최소 1개 필수.
- 한국어 작성. 한자 금지.

## 자기 검증

1. score 가 **15 / 10 / 5 / 0** 중 하나인가?
2. `evaluation_mode="partial_with_review"` + `mandatory_human_review=true` 가 있는가?
3. 오안내 판정 근거로 RAG 문서 또는 상담사 자기정정 발화가 있는가?
4. `score + Σ(deductions.points) == 15` 산술 검증 통과인가?
