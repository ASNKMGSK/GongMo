# PII 토큰 스펙 (v2_categorical)

## 개요

V2 QA 파이프라인은 STT 전사본의 개인정보(PII)를 마스킹된 상태로 소비한다.
Forward-compatibility 대비 — 현재 v1_symbolic (`***` 단일 symbol) 에서
v2_categorical (카테고리 보존 10종 토큰) 로 전환 시 STT 팀 합의 기반이 되는 스펙 문서.

출처: `STT_기반_통합_상담평가표_v2.xlsx` "마스킹 정책" 탭,
`AI_QA_Agent_Design_Document_v2.pdf` §9.3.

## 10종 카테고리 테이블 (설계서 §9.3 canonical)

설계서 기준 카테고리명 · 토큰. 운영 합의 시 STT 팀과 협의해 `pii_normalizer.py::V2_CATEGORY_NAMES`
및 `schemas/enums.py::PIICategory` 와 Dev1 `contracts/preprocessing.py::PIICategory` 를
동일값으로 정렬해야 한다 (현재 부분 불일치 — 하단 "코드 상수 정렬 필요" 참조).

| # | 카테고리 | 토큰 | 예시 대상 | 위반 심각도 |
|---|---|---|---|---|
| 1 | 이름 | `[NAME]` | 고객명, 제3자 성명 | 중 |
| 2 | 전화번호 | `[PHONE]` | 휴대폰, 집·회사 전화 | 중 |
| 3 | 주민번호 | `[RRN]` | 주민등록번호, 외국인등록번호 | **최고** |
| 4 | 계좌 | `[ACCOUNT]` | 은행 계좌번호 | 높음 |
| 5 | 카드 | `[CARD]` | 카드번호, CVC | 높음 |
| 6 | 주소 | `[ADDRESS]` | 집·회사 주소 | 중 |
| 7 | 이메일 | `[EMAIL]` | 이메일 주소 | 낮음 |
| 8 | 금액 | `[AMOUNT]` | 결제액, 잔액 | 낮음 (PII 아님) |
| 9 | 날짜 | `[DATE]` | 생년월일, 가입일 | 낮음~중 |
| 10 | 기타 | `[PII_OTHER]` | 기타 식별정보 | 중 |

## Forward-compatibility 4장치 (설계서 §9.3)

### (1) PII 토큰 정규화 레이어

- 위치: `v2/layer1/pii_normalizer.py`
- Layer 1 초입. 외부에서 들어온 `***` 또는 카테고리 토큰을 내부 canonical form
  (`[PII_<CATEGORY>_<N>]`) 으로 변환.
- 미래 전환 시 **유일한 수정 지점**.

### (2) 카테고리 추정 필드

- `***` 등장 시 문맥 기반(앞뒤 25자) 으로 카테고리 추정.
- JSON 필드: `inferred_category`, `inference_confidence`.
- v2 전환 시 후향 검증 데이터로 활용.
- Heuristic 규칙은 `pii_normalizer.py::_CATEGORY_HINTS` 참조.

### (3) 마스킹 포맷 버전 메타데이터

- `masking_format.version` ∈ {`v1_symbolic`, `v2_categorical`}.
- 최종 JSON 최상위 (`schemas/qa_output_v2.py::MaskingFormatBlock`).
- Drift 분석의 필수 키.

### (4) 카테고리 토큰 스펙 사전 문서화

- 본 문서 (`v2/docs/pii_token_spec.md`).
- STT 팀 협의 기반.

## 심각도 → Override 매핑

설계서 §5.2 "개인정보 유출 (제3자 정보 안내 등)" Override 와 연결:

| 심각도 | 토큰 | Override 동작 |
|---|---|---|
| 최고 | `[RRN]` | 해당 항목 0점 + 별도 보고서 + T3 필수 검수 |
| 높음 | `[ACCOUNT]`, `[CARD]` | 해당 항목 0점 + T3 |
| 중 | `[NAME]`, `[PHONE]`, `[ADDRESS]`, `[PII_OTHER]` | T3 권고 (`privacy_protection` 카테고리 감점) |
| 낮음~중 | `[DATE]` | 문맥 확인 |
| 낮음 | `[EMAIL]` | T2 플래그 |
| 낮음 (PII 아님) | `[AMOUNT]` | 감점 대상 아님 |

## 코드 참조

- 토큰 상수 (Dev1 구현): `v2/layer1/pii_normalizer.py::V2_CATEGORY_NAMES`
- Enum / Literal: `v2/schemas/enums.py::PIICategory`
  (Dev1 `contracts/preprocessing.py::PIICategory` 와 정합)
- 메타데이터 블록: `v2/schemas/qa_output_v2.py::MaskingFormatBlock` / `PIITokenRecord`
- 마스킹 버전 자동 감지: `pii_normalizer.py::_detect_masking_version`
- 문맥 기반 카테고리 추정: `pii_normalizer.py::_infer_category_from_context`

## 코드 상수 정렬 필요 (2026-04-20 현재)

Dev1 Layer 1 구현 (`pii_normalizer.py` / `contracts/preprocessing.py` / `schemas/enums.py`) 의
실제 10종 카테고리 키는 아래와 같이 설계서 §9.3 표 · 본 문서와 부분 불일치.

| 설계서 / STT 팀 합의 (본 문서) | Dev1 구현 현재 값 | 상태 |
|---|---|---|
| `[NAME]` | `NAME` | 일치 |
| `[PHONE]` | `PHONE` | 일치 |
| `[RRN]` | `RRN` | 일치 |
| `[ACCOUNT]` | `ACCT` | **불일치** (축약형) |
| `[CARD]` | `CARD` | 일치 |
| `[ADDRESS]` | `ADDR` | **불일치** (축약형) |
| `[EMAIL]` | `EMAIL` | 일치 |
| `[AMOUNT]` | — | **미정의** (구현은 `ORDER` 보유) |
| `[DATE]` | `DOB` | **불일치** (DOB 는 DATE 의 부분집합) |
| `[PII_OTHER]` | `OTHER` | **불일치** (prefix) |

**운영 전환 전 필수 조치** (PL 결재 후):
1. `pii_normalizer.py::V2_CATEGORY_NAMES` 10종을 위 canonical 값으로 재정의
2. `schemas/enums.py::PIICategory` / `contracts/preprocessing.py::PIICategory` 동기화
3. `_CATEGORY_HINTS` 의 target 카테고리명 교체 (ACCT → ACCOUNT 등)
4. 기존 샘플 전사본/골든셋에 `ORDER` / `DOB` 로 저장된 `inferred_category` 는 마이그레이션 스크립트로 일괄 변환 (`DOB → DATE`, `ORDER → PII_OTHER` 또는 별도 운영 결정)

## v1 → v2 전환 시 체크리스트

1. STT 팀과 토큰 포맷 확정 (본 문서의 10종 테이블 기반)
2. 상단 "코드 상수 정렬 필요" 항목 4건 해소
3. `pii_normalizer.py::masking_format_version` 자동 감지 로직 검증
4. v1_symbolic 건 전환 전 후향 `inferred_category` 정확도 평가 (>= 85% 목표)
5. v2_categorical 전환 후 `#17` / `#18` 프롬프트의 "내용 판정 금지" 조항 재검토 (카테고리 보존 시 일부 재활성 가능)
6. 심각도별 Override 매핑을 `override_rules.py` 반영
