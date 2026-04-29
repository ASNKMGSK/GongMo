# Item #13 — 부연 설명 및 추가 안내 (max 5점)

**STT 기반 통합 상담평가표 v2.0** 의 "적극성" 대분류 (15점) 내 항목 #13.

**평가모드**: full
**처리방식**: LLM + Few-shot
**ALLOWED_STEPS**: [5, 3, 0]

## 평가 기준

**고객 재문의를 방지할 수 있도록 충분한 부연 설명이 진행되었는가?**

- **5점**: **예상 질문까지 선제적으로 안내**하여 원스톱 처리
- **3점**: 기본 답변은 되었으나 **부연 설명 부족**
- **0점**: **단답형 안내**로 고객 재문의 유발

## 판정 기준

- 선제 안내: 고객이 묻기 전에 필요한 정보를 상담사가 먼저 제공하는 경우.
  - 예) "회수기사가 2~3일 내 방문 예정이고, 미방문 시 재 연락 주시면 되세요"
- 부족 신호: 고객이 같은 주제로 **추가 질문**을 해야 추가 정보가 나옴.
- 원스톱 여부: 상담 종료 시 고객이 다음 단계를 명확히 알 수 있는지.

## Evidence 강제

- Evidence 최소 1개 필수 (full 모드).
- 스키마: `{speaker, timestamp, quote, turn_id}`.
- Quote 는 전사본 원문 그대로.

## 출력 (JSON)

```json
{
  "item_number": 13,
  "score": 5,
  "evaluation_mode": "full",
  "deductions": [],
  "evidence": [
    {"speaker": "상담사", "timestamp": null, "quote": "...", "turn_id": 58}
  ],
  "self_confidence": 4,
  "summary": "..."
}
```

## 규칙

- `score` 는 정확히 5 / 3 / 0 중 하나.
- `score + Σ(deductions[].points) === 5`.
- Evidence 최소 1개 필수.
- 한국어 작성. 한자 금지.

## 자기 검증

1. score 가 5 / 3 / 0 중 하나인가?
2. 감점 사유가 xlsx 평가 기준 3단계 중 하나에 직접 대응하는가?
3. Evidence 가 선제 안내 유무를 직접 보여주는가?
4. `score + Σ(deductions.points) == 5` 산술 검증 통과인가?
