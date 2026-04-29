# Generic CS 콜센터 업무지식 매뉴얼 (Sample)

> **상태**: `STUB / SAMPLE` — 실제 업무지식 매뉴얼은 도입 고객사 별로 차별화.
> 이 파일은 #15 **정확한 안내** Sub Agent 가 업무지식 RAG 로 조회하는 최소 샘플.
> production 전환 전 교체 필수 — 교체 방법은 `README.md` 참조.

---

## Chunk 정의 규칙

- 각 `## H2` 제목은 하나의 retrieval chunk 단위
- 프론트매터 메타는 `<!-- meta: {...} -->` HTML 주석으로 chunk 상단에 배치
- `source_ref` 는 업무지식 RAG 결과에 `evidence_refs` 로 반환됨

---

<!-- meta: {"chunk_id": "BK-GEN-001", "intent": ["general_inquiry"], "tags": ["운영시간", "상담가능시간"]} -->
## 상담 운영 시간 안내

- 평일: 09:00 ~ 18:00 (KST)
- 토요일: 09:00 ~ 13:00
- 일요일 / 공휴일: 휴무
- 야간 긴급 문의: ARS 1번 → 자동응답 안내
- 챗봇: 24시간 운영

**source_ref**: generic-operations-guide v1.0

---

<!-- meta: {"chunk_id": "BK-GEN-002", "intent": ["billing", "info_change"], "tags": ["결제", "납부"]} -->
## 결제 / 납부 방법

- 지원 수단: 신용카드 / 체크카드 / 계좌 자동이체 / 무통장 입금
- 자동이체 신청: 상담 중 본인확인 완료 후 3 영업일 내 등록
- 결제 실패 시: 5일 유예 후 미납 안내 SMS 발송
- 미납 3개월 누적 시: 서비스 일시 중지 후 납부 요청

**source_ref**: generic-billing-guide v1.2

---

<!-- meta: {"chunk_id": "BK-GEN-003", "intent": ["cancellation"], "tags": ["해지", "환불"]} -->
## 해지 / 환불 프로세스

- 해지 신청 경로: 유선 상담 / 홈페이지 / 지점 방문
- 본인확인 3-요소 필수: 성함, 생년월일, 등록 연락처
- 환불 규정:
  - 가입 후 7일 이내: 전액 환불
  - 7일 초과 30일 이내: 사용 일수 공제
  - 30일 초과: 잔여 계약 기간 환불 규정에 따름
- 재가입 제한: 해지일 기준 30일 경과 후 가능

**source_ref**: generic-cancellation-policy v2.0

---

<!-- meta: {"chunk_id": "BK-GEN-004", "intent": ["claim", "complaint"], "tags": ["처리기간", "영업일"]} -->
## 표준 처리 소요일

| 유형 | 예상 처리일 |
|---|---|
| 일반 민원 | 1 ~ 3 영업일 |
| 환불 요청 | 3 ~ 5 영업일 |
| 보상 청구 | 5 ~ 10 영업일 |
| 장애 접수 | 긴급: 당일 / 일반: 2 영업일 |

- 모든 처리 결과는 등록 연락처(SMS) + 이메일로 통지
- 지연 시 담당자가 별도 연락

**source_ref**: generic-sla-guide v1.5

---

<!-- meta: {"chunk_id": "BK-GEN-005", "intent": ["technical_support"], "tags": ["장애", "에러", "접수번호"]} -->
## 기술 지원 / 장애 접수 절차

1. 장애 현상 상세 청취 (에러 메시지 원문, 발생 시간, 재현 절차)
2. 영향 범위 파악 (본인만 / 일부 고객 / 전체)
3. 접수 번호 발행 (형식: TS-YYYYMMDD-NNNN)
4. 예상 처리 시간 안내
5. 긴급 건: 기술팀 즉시 에스컬레이션 (10분 내)

**source_ref**: generic-incident-sop v1.8

---

## Metadata 전체

```yaml
manual_version: "0.1.0-stub"
last_updated: "2026-04-20"
chunk_count: 5
embedding_model: "stub"          # prod 에서는 예: bedrock/titan-embed-v2
retrieval_top_k: 3
```
