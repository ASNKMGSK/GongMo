## 공통 출력 규칙
- Output ONLY valid JSON. No markdown, no explanation.
- All text in Korean (summary, descriptions).
- evidence_ref는 반드시 실제 턴 번호 사용 (예: "turn_3", "turn_7"). "turn_N" 사용 금지.

## 공통 evidence 규칙 (만점 포함 전 케이스)
- `evidence` 배열은 **점수와 무관하게 반드시 1개 이상 포함** (빈 배열 금지).
- **만점(`score === max_score`)** 이어도 상담사(`speaker: "agent"`) 발화 중 해당 항목의 **긍정 근거**가 되는 turn 1~3개를 exact quote + turn 번호 + relevance 설명과 함께 반드시 포함.
  - 예: 인사 평가 만점 → 첫/끝 인사 turn 인용. 공감 평가 만점 → 공감 표현이 나타난 turn 인용. 명확성 만점 → 핵심 설명 turn 인용.
- **감점 케이스**: 감점 사유가 되는 문제 발화 turn을 exact quote 로 포함. 만점 아닌 경우에도 점수 산정 근거가 되는 대표 turn 을 포함해야 함.
- `evidence[].text` 는 STT 원문 exact quote (생략/요약 금지).
- `evidence[].turn` 은 실제 `[Turn N]` 번호. 추정/가상 번호 금지.
- `evidence[].speaker` 는 `"agent"` 또는 `"customer"` (기본적으로 상담사 발화 우선).
