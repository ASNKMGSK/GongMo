# Item #16 — 필수 안내 이행 (max 5점)

**STT 기반 통합 상담평가표 v2.0** 의 "업무 정확도" 대분류 (15점) 내 항목 #16.

**평가모드**: full
**처리방식**: Intent 분류 + 스크립트 매칭
**비고**: 문의 유형 classifier 필요
**ALLOWED_STEPS**: [5, 3, 0]

## 평가 기준

**문의 유형별 필수 안내 사항(스크립트)을 누락 없이 전달하였는가?**

- **5점**: 필수 안내 사항 **모두 누락 없이** 진행
- **3점**: 필수 안내 사항 중 **일부 누락**
- **0점**: 필수 안내 사항 **미진행** 또는 **다수 누락**

## 판정 기준

- 문의 유형 (`intent_type`) 에 따라 필수 안내 스크립트가 정해진다.
- 스크립트 예:
  - **교환/반품**: 회수 절차, 예상 소요일, 반송장 보관, 1회 무상 여부
  - **주문 확인**: 배송 상태, 예상 도착일
  - **결제 오류**: 환불 일정, 확인 경로
- 필수 안내 `required_items[]` 와 상담사 발화를 대조해 **포함/누락** 판정.
- `required_items` 가 없는 intent 는 자동 **5점** + `evaluation_mode="skipped"` 가능.

## Evidence 강제

- Evidence 최소 1개 필수 (full 모드).
- 스키마: `{speaker, timestamp, quote, turn_id}`.

## 출력 (JSON)

```json
{
  "item_number": 16,
  "score": 5,
  "evaluation_mode": "full",
  "intent_type": "상품교환",
  "required_items": ["회수 일정", "반송장 보관", "1회 무상"],
  "missing_items": [],
  "deductions": [],
  "evidence": [
    {"speaker": "상담사", "timestamp": null, "quote": "반송장은 버리지 마시고 교환 완료될 때까지 꼭 보관 부탁드리겠습니다", "turn_id": 65}
  ],
  "self_confidence": 5,
  "summary": "..."
}
```

## 규칙

- `score` 는 정확히 5 / 3 / 0 중 하나.
- `score + Σ(deductions[].points) === 5`.
- full 모드 Evidence 최소 1개 필수.
- 한국어 작성. 한자 금지.

## 자기 검증

1. score 가 5 / 3 / 0 중 하나인가?
2. `intent_type` / `required_items` 가 명시됐는가?
3. 누락 항목 (`missing_items`) 이 감점 사유와 일치하는가?
4. `score + Σ(deductions.points) == 5` 산술 검증 통과인가?
