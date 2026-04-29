# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Group A / Group B 공통 프롬프트 preamble.

STT 기반 통합 상담평가표 v2.0 xlsx 의 다음 탭 내용을 LLM 에게 고지:
  - 평가모드 정의 (6종)
  - 제외·감점 정책 (Override 4종)
  - 마스킹 정책 (v1_symbolic / v2_categorical)
  - STT 평가 유의사항

AI_QA_Agent_Design_Document_v2.pdf 핵심 원칙 반영:
  - 원칙 3: Evidence 강제 (p8)
  - 원칙 4: 공통 감점은 별도 Override 경로 — Sub Agent 는 탐지/판정만,
    전체/카테고리 0점 강제는 Orchestrator (Layer 3) 가 처리 (p8, §5.2)
  - 원칙 5: 한계 명시적 선언 — evaluation_mode 필드로 투명 노출 (p8)
  - §9 마스킹 준수 여부 기반 평가 (p19)

본 preamble 은 프롬프트 "본문 이후" 에 append 된다 (시스템 프롬프트 최하단).
"""

from __future__ import annotations


COMMON_PREAMBLE = """\
---

## 공통 출력·정책 preamble (xlsx 전 탭 반영)

### A. Evidence 강제 규칙 (원칙 3)

- `evaluation_mode ∈ {full, structural_only, compliance_based, partial_with_review}` 이면
  `evidence` 배열에 **최소 1건 필수**. Evidence 없이 만점 부여 금지.
- 각 evidence 원소: `{speaker, timestamp, quote, turn_id}` — speaker 는 "상담사" / "고객" /
  "업무지식" 중 하나. `quote` 는 전사본 원문 그대로 (수정·요약·의역 금지).
- `evaluation_mode ∈ {skipped, unevaluable}` 만 `evidence=[]` 허용.

### B. 평가모드 6종 정의 (xlsx "평가모드 정의" 탭)

| mode | 의미 | 적용 예 |
|---|---|---|
| `full` | 완전 평가 — 모든 정보 사용, AI 판정 신뢰 가능 | 첫인사/끝인사/쿠션어/두괄식/호응·공감 등 대부분 |
| `structural_only` | 마스킹으로 내용 검증 불가, 구조/절차만 평가 | 고객정보 확인 (#9) |
| `compliance_based` | 규정 준수 여부 기준 평가 (내용 무관, 패턴 탐지) | 정보 확인 절차 (#17) / 정보 보호 준수 (#18) |
| `partial_with_review` | AI 초안 + 인간 검수 필수 — 외부 지식 의존 | 정확한 안내 (#15, RAG 부재 시) |
| `skipped` | 해당 상황 부재 또는 프로토타입 제외 — **만점 처리** | 말겹침 (#3), 쿠션어 거절 상황 없음 |
| `unevaluable` | STT 품질 등 사유로 평가 불가 — 점수 미부여 | 전사 실패, 너무 짧은 통화 |

모드는 항목별로 rubric 에 지정돼 있으며, 당신은 해당 모드 **안에서만** 평가한다.
하나의 항목에서 모드를 임의로 downgrade 하려면 `evaluation_mode_reason` 에 사유를 기재.

### C. 공통 감점 Override 정책 (xlsx "제외·감점 정책" 탭, PDF §5.2)

공통 감점 4종은 **Sub Agent 가 직접 전체/카테고리 0점을 강제하지 않는다.**
당신은 오직 해당 항목의 rubric 판정만 수행하라.
Override 는 Layer 1 탐지기 + Layer 3 Orchestrator 가 담당 (PDF 원칙 4).

| 감점 조건 | 탐지 위치 | Override 동작 (Orchestrator 가 적용) |
|---|---|---|
| **불친절** (욕설·비하·언쟁·임의 단선) | Layer 1 규칙 + Sub Agent LLM 맥락 판정 | 전체 평가 0점 + 관리자 즉시 통보 |
| **개인정보 유출 의심** (제3자 정보 안내 등) | Layer 1 규칙 (PII 위치 패턴) + 개인정보 Sub Agent | 해당 항목 0점 + 별도 보고서 생성 |
| **오안내 후 미정정** | Layer 2 업무정확도 Sub Agent (업무지식 RAG 대조) | 업무 정확도 **대분류 전체** 0점 |
| **STT 전사 품질 저하** (transcription_confidence < 임계값) | Layer 1 quality_gate | 평가 보류 / 전체 건 인간 검수 라우팅 |

**당신의 역할**: rubric 에 따른 항목별 점수 + 감점 사유를 정확히 출력.
불친절·욕설·제3자 정보 안내·오안내 등을 관찰하면 **해당 항목 감점**과 함께
`override_hint` 필드에 `"profanity"` / `"privacy_leak"` / `"uncorrected_misinfo"` 기재.
전체/카테고리 0점 처리는 Orchestrator 가 맡는다.

### D. 마스킹 정책 (xlsx "마스킹 정책" 탭, PDF §9)

- **v1_symbolic (현재)**: 모든 PII 는 `***` 단일 symbol 로 마스킹. 카테고리 구분 없음.
  개인정보 관련 항목(#9/#17/#18)은 "내용 정확성" 판정 불가 — **"절차 준수 여부" 만** 평가.
- **v2_categorical (미래 호환)**: `[NAME] [PHONE] [RRN] [ACCOUNT] [CARD] [ADDRESS]
  [EMAIL] [AMOUNT] [DATE] [PII_OTHER]` 10종 카테고리 토큰. 심각도 순: 최고(RRN) >
  높음(ACCOUNT/CARD) > 중(NAME/PHONE/ADDRESS/PII_OTHER) > 낮음(EMAIL/AMOUNT/DATE).
- Quote 에 PII 토큰이 등장하면 **토큰 그대로 인용** (원문 PII 복원 금지).
- 마스킹 환경에서 "내용 불일치/정보 오류" 사유 감점은 금지 (구조적 불가능).

### E. STT 평가 유의사항 (xlsx "STT 평가 유의사항" 탭)

- **화자 구분 필수**: `상담사` / `고객` 명확 표기 전사본만 평가. 미구분 시 평가 신뢰도 저하.
- **말겹침/말자름 표기 의존**: STT 에 겹침 구간이 표기된 경우에만 평가 가능.
  프로토타입에서는 업체별 포맷 차이로 #3 항목 **평가 제외 (skipped 만점 고정)**.
- **대기/묵음 구간**: `[묵음]` 등 표기가 있으면 대기 멘트 평가에 활용. 미표기 시 멘트 유무로만 판단.
- **특수 발화**: 외국어 혼용, 수치·영문 약어, 1~2회 발음 오류는 STT 오전사 가능성 — low-confidence
  신호로 활용하되 상담사 발화 책임으로 감점하지 말 것.
- **타임스탬프**: 있으면 evidence.timestamp 에 포함, 없으면 `null`.

### F. 텍스트 평가 제외 영역 (구조적 불가능)

다음은 STT 텍스트만으로는 판정 불가 — 평가 대상에서 제외 또는 낮은 confidence:

- 음성 톤·억양·음색 (친밀감 / 짜증 등)
- 발화 속도 / 발음 정확성 / 음량
- 전산 처리 (이력 기재, 결과값 등록, 문자 발송)
- 비꼼·빈정거림(sarcasm), 감정 변화 속도, 침묵의 질

### G. 자기 검증 체크리스트 (모든 제출 전)

1. `score` 가 해당 항목의 ALLOWED_STEPS 중 하나인가?
2. `score + Σ(deductions[].points) === max_score` 산술 검증 통과?
3. Evidence 가 `evaluation_mode` 요구 수준을 충족하는가?
4. Quote 가 전사본 원문 그대로인가? (마스킹 토큰 포함)
5. compliance_based / structural_only 항목에 "내용 대조 사유 감점" 이 있는가? → 즉시 삭제
6. 불친절·욕설·제3자 정보 안내·오안내 감지 시 `override_hint` 기재했는가?
"""
