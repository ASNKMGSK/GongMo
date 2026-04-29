# Reasoning Index JSON Schema (v2)

PDF §7.2 Reasoning RAG 별도 인덱스. 과거 평가자가 남긴 **판정 근거 문장**을
embedding 하여 Confidence `rag_stdev` 계산과 일관성 감사에 활용.

Golden-set 과 **별도 인덱스**로 분리한다:

- Golden-set : Few-shot 예시 (segment_text 중심)
- Reasoning index : 판정 근거 문장 (rationale 중심, 점수 불확실성 지표)

## 파일 구조

`<NN>_<slug>.json` — 1~18 평가항목 각 1개 파일. 파일명은 golden_set 과 동일한
slug 규칙을 따라 파일 교차 참조 용이.

## JSON 스키마

```json
{
  "item_number": 1,
  "item_name": "첫인사",
  "version": "0.1.0-stub",
  "reasoning_records": [
    {
      "record_id": "r_001",
      "score": 5,
      "rationale": "인사말/소속/상담사명 3요소 모두 포함하여 만점 부여",
      "quote_example": "안녕하십니까 ○○ 고객센터 상담사 김○○ 입니다",
      "evaluator_id": "senior_a",
      "tags": ["full_compliance", "opening"],
      "stub_seed": true
    }
  ]
}
```

## 필드 규약

- `record_id` : 파일 내 유일 ID (`r_NNN`).
- `score` : 평가자 부여 점수. item 별 `allowed_steps` 준수.
- `rationale` : 판정 근거 문장. **embedding 대상** (Jaccard prototype 단계).
- `quote_example` : 해당 rationale 과 연결된 발화 예시 (trace 용).
- `evaluator_id` : 가명화된 평가자 식별자 (senior_a/senior_b/rater_c 등).
- `tags` : rationale 분류 태그 (full_compliance / missing_X / borderline / ...).
- `stub_seed` : true 이면 합성 stub 데이터. production 시 false 또는 미존재.

## 사용 규약 (PDF 원칙 7.5 / 7.2)

- **허용** : `rag_stdev = stdev(retrieved records' scores)` — Confidence 신호.
- **금지** : rationale score 의 가중평균/중앙값을 "최종 점수"로 사용 금지.
- **금지** : transcript 전체 embedding 만으로 retrieval 하지 말 것. 반드시
  `item_number` 로 pool 을 좁힌 후 rationale 유사도 계산.

## 항목당 레코드 수

Stub seed 단계에서는 각 파일에 3~5개 record 를 `allowed_steps` 분포에 맞춰 배치.
production 단계에서는 실제 평가자 판정 근거 문장을 축적해 record 수 확장.
