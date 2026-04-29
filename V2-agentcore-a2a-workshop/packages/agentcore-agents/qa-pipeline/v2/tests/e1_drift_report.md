# Phase E1 V2 Drift Report (Dev1 최종)

- **생성일**: 2026-04-20
- **V1 batch**: `C:\Users\META M\Desktop\프롬프트 튜닝\batch_20260419_160641_iter03_clean`
  (iter03_clean, 9 샘플, V1 MAE 3.89 / MAPE 4.18%)
- **V2 batch**: `C:\Users\META M\Desktop\프롬프트 튜닝\batch_20260420_103015_e1_initial`
  (V2 graph_v2 + Layer 1-3 + Dev2 Group A + Dev3 Group B + Dev5 Layer 4 통합)
- **도구**: `v2/validation/run_validation_report.py`
- **리포트**: `v2/validation/reports/e1_final/`

---

## 1. 헤드라인 수치

| 지표 | V2 (9 샘플, n=162) | V1 baseline | Δ |
|---|---:|---:|---:|
| **MAE** | **1.846** | 3.89 | **-2.04 (-53%)** |
| RMSE | 3.34 | — | |
| Bias | -1.38 | — | |
| MAPE | 38.27% | 4.18% | +34.1%p |
| Accuracy | 0.605 | — | |

- **V2 가 V1 대비 MAE 약 53% 감소** — iter03_clean 기준선 개선 확인.
- 그러나 MAPE 은 악화 — #15/#17/#18 0점 일관 이슈가 분모 왜곡 유발 (개선 가능 지점).

## 2. 항목별 드릴다운

| item | n | MAE | RMSE | Bias | MAPE | Accuracy | 판정 |
|---|---:|---:|---:|---:|---:|---:|---|
| #1 첫인사 | 9 | 0.222 | 0.667 | +0.22 | 7.4% | 0.889 | 🟢 우수 |
| #2 끝인사 | 9 | 1.00 | 1.528 | +0.56 | 52.6% | 0.556 | 🟡 양호 |
| #3 경청 | 9 | 0.00 | 0.00 | 0 | 0% | 1.000 | 🟢 완벽 |
| #4 호응공감 | 9 | 2.556 | 3.48 | -2.56 | 55.6% | 0.444 | 🟡 과소평가 |
| #5 대기멘트 | 9 | 1.00 | 1.915 | -1.00 | 20% | 0.667 | 🟡 양호 |
| #6 정중표현 | 9 | 1.556 | 1.764 | +1.56 | 51.9% | 0.222 | 🟡 과대평가 |
| #7 쿠션어 | 9 | 0.00 | 0.00 | 0 | 0% | 1.000 | 🟢 완벽 |
| #8 문의파악 | 9 | 3.00 | 3.606 | -3.00 | 64.4% | 0.222 | 🔴 과소평가 |
| #9 고객정보 | 9 | 2.222 | 3.333 | -2.22 | 44.4% | 0.556 | 🟡 과소평가 |
| #10 설명명확 | 9 | 0.00 | 0.00 | 0 | 0% | 1.000 | 🟢 완벽 |
| #11 두괄식 | 9 | 0.444 | 0.943 | +0.44 | 14.8% | 0.778 | 🟢 우수 |
| #12 문제해결 | 9 | 0.778 | 1.795 | +0.78 | 63.0% | 0.778 | 🟡 양호 |
| #13 부연설명 | 9 | 0.444 | 0.943 | +0.44 | 14.8% | 0.778 | 🟢 우수 |
| #14 사후안내 | 9 | 0.00 | 0.00 | 0 | 0% | 1.000 | 🟢 완벽 |
| **#15 정확안내** | 9 | **10.00** | 10.00 | **-10.00** | 100% | **0.000** | **🚨 버그** |
| #16 필수안내 | 9 | 0.00 | 0.00 | 0 | 0% | 1.000 | 🟢 완벽 |
| **#17 정보확인** | 9 | **5.00** | 5.00 | **-5.00** | 100% | **0.000** | **🚨 버그** |
| **#18 정보보호** | 9 | **5.00** | 5.00 | **-5.00** | 100% | **0.000** | **🚨 버그** |

## 3. 심각 항목 (Dev3 대응 요청)

### #15 정확한 안내 (MAE=10.00)
- V1: 샘플별 0~10점 분포 (평균 약 6.7)
- V2: 9 샘플 전부 0점
- 추정 원인: Group B `work_accuracy_agent` 가 업무지식 RAG 미준비 상태에서 "평가 불가" → 0점. 설계서 §5.1 은 `partial_with_review` 를 제시하나 V2 구현이 0점으로 강제.

### #17 정보 확인 절차 / #18 정보 보호 준수 (MAE=5.00)
- V1: 9 샘플 전부 5점 (정상 상담)
- V2: 9 샘플 전부 0점
- 추정 원인: Group B `privacy_agent` 가 compliance_based 모드로 "패턴 A/B/C 미탐지 = 0점" 반환 구조 가능성.
- **Layer 1 Rule 1차 판정은 정상**: `preprocessing.rule_pre_verdicts["item_17"]` 가 대부분 5점 반환 (iv_performed=True + preemptive_found=False 조합).
- → privacy_agent 가 Layer 1 rule_pre_verdicts 를 consume 안 하고 독자 0점 반환하는 구조로 의심.

**대응**: Dev3 에게 diagnostic SendMessage 송신 완료. 수정 PR 후 batch 재실행 예정.

## 4. iter05 회귀 해소 검증

PL 중점 항목: `snap_score_v2(17, 3)==3` 유지 여부.

| 검증 | 결과 |
|---|---|
| 단위 테스트 `test_snap_score_v2_preserves_3_for_item17` | ✅ pass |
| V1 `snap_score(17, 3)==0` 불변 유지 테스트 | ✅ pass (V1 원본 수정 0건) |
| ALLOWED_STEPS[17]==[5,3,0] 동결 | ✅ pass |
| **9 샘플 배치에서 #17 3점 반환 실측** | ⚠️ **0건** (전 샘플 0점으로 강제) |

**결론**: snap_score_v2 로직 자체는 iter05 회귀 해소 되었으나, Group B privacy_agent 의 0점 강제로 **실제 V2 배치에서는 3점 케이스가 발생하지 않음**. Dev3 수정 후 재배치 시 정상 분포 예상.

## 5. Tier 분포 / Confidence / Evidence / Mode

### Tier 분포 (V2)
- T0: 100% (9/9)
- T1/T2/T3: 0%
- **사유**: `skip_phase_c_and_reporting=True` 로 Layer 3 grader + Layer 4 tier_router 스킵 → 기본값 T0 만 기록.
- **보강 재실행 필요**: `SKIP_PHASE_C_REPORTING=0` 로 Layer 4 포함 실행해야 정식 측정 가능. 단 Sub Agent evidence=[] 가 67.9% 라 Dev5 pydantic validation 실패 리스크 있음.

### Confidence Calibration (V2)
- 분포: {1:0, 2:20, 3:0, 4:32, 5:29, -1:81}
- -1 (unknown) 81건 = 50% — Sub Agent 가 confidence 를 dict 가 아닌 float 또는 미세팅.
- **개선 필요**: Dev2/Dev3 Sub Agent 공통 응답 포맷 `confidence: {final, signals}` 통일 (#13 후속 태스크).

### Evidence 품질 (V2)
- Empty evidence: **67.9% (110/162)** — V1 14.81% 대비 53%p 악화
- 평균 quote 길이: 34.6 chars (양호)
- Speaker mismatch: 0
- **개선 필요**: Sub Agent 가 full 모드로 판정하면서 evidence 비워두는 사례 많음 → `evaluation_mode=full + evidence=[]` 조합 금지 (원칙 3 위반)

### Evaluation Mode 빈도 (V2)
- full: 56.8%
- skipped: 13.0%
- compliance_based: 11.1%
- partial_with_review: 8.6%
- unevaluable: 5.6%
- structural_only: 4.9%
→ 설계서 §5.3 "한계 투명성" 원칙 6종 모두 등장, 설계대로 작동.

## 6. 스키마 호환성 (V1 → V2)

- Confidence scale mismatch: 100% (V1 float → V2 int)
- evaluation_mode 누락: 100% (V2 신규 필수)
- Evidence timestamp 누락: 85% (V1 STT 메타 부재)
- V1 dropped `details`: 74.7%

**마이그레이션 스크립트 필요 시**: `v2/validation/schema_compat.py::V1_TO_V2_ITEM_FIELDS` 매핑 재활용 (121 라인 도구).

## 7. JSON 실물 pydantic Validation (3 샘플)

- 668437 / 668464 / 668526 각 18 item 중 **11/18 pass (61%)**, 7/18 fail (39%)
- 실패 사유 전체: **"evaluation_mode=full 이면 evidence 최소 1개 필수"**
- 빈발 fail 항목: #6, #8, #10 (Dev2 Group A 측)
- **Dev5 `QAOutputV2.ItemResult` 설계는 원칙 3 준수로 타당** — Sub Agent 측 수정 필요 (full 모드 시 evidence 필수 또는 mode 다운그레이드)

## 8. 테스트 상태

- Dev1 누적: **57/57 pass** (layer1:13 + layer3:18 + graph_v2:9 + server_v2:6 + validation:11)
- V1 원본 수정 0건 ✅
- Pearson/Spearman/κ/R² 사용 0건 ✅ (MAE/RMSE/Bias/MAPE/Accuracy 만)

## 9. 권고사항 — 우선순위

1. **🚨 Priority 1**: Dev3 Group B #15/#17/#18 0점 일관 이슈 수정 → 수정 후 V2 batch 재실행 필요
2. **Priority 2**: Dev2/Dev3 Sub Agent evidence 정책 일원화 — full 모드면 evidence 필수, 누락 시 `partial_with_review` 로 자동 다운그레이드
3. **Priority 3**: Confidence 반환 포맷 통일 — `confidence: {final: int, signals: {...}}` (#13 태스크와 정렬)
4. **Priority 4**: Layer 4 포함 재실행 (`SKIP_PHASE_C_REPORTING=0`) — Tier 분포 정식 측정
5. **Priority 5**: V1 → V2 마이그레이션 스크립트 — iter03_clean 자산 활용 (tools 완비)

## 10. 산출물 경로

- 리포트: `v2/validation/reports/e1_final/{summary.md, 01~06 JSON}`
- 배치 결과: `C:\Users\META M\Desktop\프롬프트 튜닝\batch_20260420_103015_e1_initial\` (9 JSON)
- 도구: `v2/validation/{schema_compat, score_drift, run_validation_report}.py`
- 테스트: `v2/tests/test_validation_smoke.py` (11/11 pass)
- 본 리포트: `v2/tests/e1_drift_report.md`
