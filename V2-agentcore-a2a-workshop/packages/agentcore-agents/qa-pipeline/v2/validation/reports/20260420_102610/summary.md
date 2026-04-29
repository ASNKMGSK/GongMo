# Phase E1 V2 Validation Report
- V1 batch: `C:\Users\META M\Desktop\프롬프트 튜닝\batch_20260419_160641_iter03_clean`
- V2 batch: `— (V2 배치 미실행, V1 분석만 수행)`
- Generated: 2026-04-20T10:26:10

## 1. Schema Compatibility (V1 → V2)
- Samples: 9 / Items: 162
- Confidence scale mismatch: 162 items
- Missing V2 required (evaluation_mode): 162 items
- Evidence missing timestamp: 138 items
- V1 dropped fields (details): 121 items

### Migration notes
- [필수] V1 confidence float(0~1) → V2 int(1~5) 스케일 변환 필요 (162/162 항목, 100.0%). 변환 규칙 예: round(v1 * 5) with clamp [1,5].
- [필수] V2 evaluation_mode 필드 V1 전체 누락 (162/162 항목). 기본값 'full' 부여 후 #9/#17/#18 은 'structural_only'/'compliance_based' 로 재지정.
- [권장] V2 evidence.timestamp 필드 V1 에 없음 (138/162 항목). V1 샘플에는 STT timestamp 미포함 — timestamp='' 빈문자열로 폴백.
- [정보] V1 details {'backend', 'llm_based'} V2 drop — QAOutputV2.diagnostics 로 이관 가능.

## 5. Evidence Quality
- Total items: 162
- Empty evidence: 24 (14.81%)
- Avg quote length: 29.3 chars
- Speaker mismatch: 0

---
*지표 제한: MAE/RMSE/Bias/MAPE/Accuracy 만 사용. Pearson/Spearman/κ/R² 금지 (CLAUDE.md).*