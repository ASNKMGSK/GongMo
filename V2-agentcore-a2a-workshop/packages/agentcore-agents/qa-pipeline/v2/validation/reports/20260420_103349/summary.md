# Phase E1 V2 Validation Report
- V1 batch: `C:\Users\META M\Desktop\프롬프트 튜닝\batch_20260419_160641_iter03_clean`
- V2 batch: `C:\Users\META M\Desktop\프롬프트 튜닝\batch_20260420_103015_e1_initial`
- Generated: 2026-04-20T10:33:49

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

## 2. Score Drift (V1 vs V2)
- Common samples: 0
- V1-only: 9
- V2-only: 9
- **Overall** (n=0): MAE=0.0 · RMSE=0.0 · Bias=0.0 · MAPE=0.0% · Accuracy=0.0

### Per-item metrics (MAE / MAPE / Accuracy)
| item | n | MAE | RMSE | Bias | MAPE | Acc |
|---|---|---|---|---|---|---|
| #1 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #2 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #3 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #4 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #5 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #6 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #7 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #8 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #9 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #10 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #11 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #12 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #13 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #14 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #15 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #16 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #17 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |
| #18 | 0 | 0.0 | 0.0 | 0.0 | 0.0% | 0.0 |

## 3. Tier Distribution (V2)
- T0: 9 (100.0%)
- T1: 0 (0.0%)
- T2: 0 (0.0%)
- T3: 0 (0.0%)
- unknown: 0 (0.0%)
- Target: {'T0': '~70%', 'T1': '5~10%', 'T2': '15~20%', 'T3': '≤5%'}

## 4. Confidence Calibration (V2)
- Distribution: {1: 0, 2: 20, 3: 0, 4: 32, 5: 29, -1: 81}
- Low confidence (≤2) items: 20

## 5. Evidence Quality
- Total items: 162
- Empty evidence: 110 (67.9%)
- Avg quote length: 34.6 chars
- Speaker mismatch: 0

## 6. Evaluation Mode Frequency (V2)
- full: 92 (56.8%)
- structural_only: 8 (4.9%)
- compliance_based: 18 (11.1%)
- partial_with_review: 14 (8.6%)
- skipped: 21 (13.0%)
- unevaluable: 9 (5.6%)
- unknown: 0 (0.0%)

---
*지표 제한: MAE/RMSE/Bias/MAPE/Accuracy 만 사용. Pearson/Spearman/κ/R² 금지 (CLAUDE.md).*