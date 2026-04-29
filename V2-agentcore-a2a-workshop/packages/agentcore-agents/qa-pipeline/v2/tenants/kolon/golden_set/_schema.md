# Golden-set JSON Schema (v2)

각 `<NN>_<slug>.json` 파일은 아래 구조를 따른다. Dev4 RAG-1 (`v2/rag/golden_set.py`)
이 로드하여 Few-shot 예시로 Sub Agent 에 주입.

```json
{
  "item_number": 1,
  "item_name": "첫인사",
  "category": "인사 예절",
  "max_score": 5,
  "allowed_steps": [5, 3, 0],
  "intents": ["*"],
  "version": "0.1.0-stub",
  "examples": [
    {
      "example_id": "GS-01-FULL-01",
      "score": 5,
      "score_bucket": "full",
      "intent": "general_inquiry",
      "segment_text": "상담사: 안녕하세요. ABC 고객센터 홍길동입니다. 무엇을 도와드릴까요?",
      "rationale": "인사말/소속/상담사명 3요소 모두 포함.",
      "rationale_tags": ["3-요소 충족"],
      "rater_meta": { "rater_type": "senior_consensus", "source": "stub" }
    }
  ]
}
```

**필드 규약**:

- `score_bucket`: `full` (만점) / `partial` (중간) / `zero` (0점)
- `intent`: `tenant_config.yaml::supported_intents` 중 하나 또는 `*` (항목 무관)
- `segment_text`: Segment 추출기가 실제로 전달할 짧은 발화 묶음 (Full transcript 금지)
- `rationale`: 시니어 평가자의 판정 근거 문장 (Reasoning RAG 의 embedding 원본)
- `rater_meta.rater_type`: `senior_consensus` / `single_expert` / `model_gold` (실험용)
- **금지 사항** (원칙 7.5): 인간 평가자 점수 가중평균, 타이트/느슨 평가자 평균은 저장 금지.

**Stub 데이터 주의**:

- 현재 모든 파일의 `examples` 는 예시 1~2 개 수준의 `stub`
- production 배포 전 시니어 합의 결과로 각 score bucket 당 3~5 예시 × 3 bucket = 최소 9 예시 × 18 항목
- seeding 진행은 PL (메인) 결정 대기 (질의 Q3)
