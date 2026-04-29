# Group B 프롬프트 (Sub Agent) — V2

Dev3 담당 Group B Sub Agent 4종이 사용하는 항목별 프롬프트.

## 파일 구성 (PL 계약 확정 반영)

- `item_10_clarity.sonnet.md` — 설명의 명확성 (V1 iter03_clean + iter04 보존)
- `item_11_conclusion_first.sonnet.md` — 두괄식 답변 (V1 유지)
- `item_12_problem_solving.sonnet.md` — 문제 해결 의지 (V1 유지)
- `item_13_supplementary.sonnet.md` — 부연 설명 및 추가 안내 (V1 유지)
- `item_14_followup.sonnet.md` — 사후 안내 (V1 유지, 즉시해결 만점 조건 그대로)
- `item_15_accuracy.sonnet.md` — 정확한 안내 (V2: RAG chunks 검증 섹션 신규, partial_with_review)
- `item_16_mandatory_script.sonnet.md` — 필수 안내 이행 (V1 유지 + Intent 스크립트)
- `item_17_iv_procedure.sonnet.md` — **V2 재작성** — compliance_based + 패턴 A/B/C + ALLOWED_STEPS [5,3,0]
- `item_18_privacy_protection.sonnet.md` — **V2 재작성** — compliance_based + 패턴 A/B/C + ALLOWED_STEPS [5,3,0]

## 재사용 전략 (PL 확정)

1. **iter03_clean 보존 항목**: #10, #11, #12, #13, #14, #15, #16 — V1 원본 그대로 복사. Phase D1 통합 시 V1 adapter 경로로 LLM 호출.
2. **재작성 항목**: #17, #18 — V2 compliance_based 로 관점 전환 + 패턴 A/B/C 탐지 서술 + 3점 스냅 허용 명시.
3. **RAG 주입 항목**: #15 — `## 업무지식 RAG hits` 섹션 신규. LLM 프롬프트 빌더 (Phase D1) 가 동적 주입.
4. **Few-shot 동적 주입**: Phase D1 에서 `retrieve_fewshot(item, intent, segment)` 결과를 프롬프트 최하단 `## Golden-set 유사 예시` 섹션으로 append.

## ALLOWED_STEPS (V2 rubric, PL 확정 2026-04-20)

```python
{
  10: [10, 7, 5, 0], 11: [5, 3, 0],
  12: [5, 3, 0], 13: [5, 3, 0], 14: [5, 3, 0],
  15: [10, 5, 0], 16: [5, 3, 0],
  17: [5, 3, 0],  # V1 [5,0] → [5,3,0] 확장
  18: [5, 3, 0],  # V1 [5,0] → [5,3,0] 확장
}
```

V1 `nodes/qa_rules.py` 는 불변. V2 rubric 은 `v2/contracts/rubric.py` (Dev5/Dev1 주관) 로 분리.

## 공통 출력 스키마

모든 항목 JSON 출력은 Dev5 `v2/schemas/sub_agent_io.py::ItemVerdict` 를 따른다:
- `score` — ALLOWED_STEPS 허용값 (snap_score 경유)
- `deductions[]` — `{reason, points, evidence_ref, rule_id}`
- `evidence[]` — `{speaker, timestamp, quote, turn_id}`
- `confidence` (0.0~1.0) + `self_confidence` (1~5 — 프롬프트 앵커 강제)
- #17/#18 전용: `patterns_detected: list[str]` (A/B/C)
