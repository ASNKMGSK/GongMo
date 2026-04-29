# BACKFILL_PLAN — 단일 테넌트 → `kolon_default` 비파괴 백필

> **Owner**: Dev2 (`data-isolation`)
> **연결**: `PHASE1_MIGRATION_PLAN.md` §2.3 (데이터 백필 단계) + §5 미결 이슈 2/3
> **원칙**: 파일만, 실행 금지, dry-run 금지. 본 문서는 코드(`scripts/backfill/`)의 실행 절차를 기술만 할 뿐 *실행하지 않는다*.

---

## 0. 요약 (PHASE1_MIGRATION_PLAN §3.2 **비파괴 복사** 확정안 반영)

| 대상 | 원본 (env/SSM 외부화) | 대상 | 방식 |
|---|---|---|---|
| 평가 레코드 | env `LEGACY_EVAL_TABLE` (또는 SSM `/qa/legacy/eval_table`) | `qa_evaluations_v2` | Scan → tenant_id 주입 → PutItem (비파괴) |
| 세션 레코드 | env `LEGACY_SESSION_TABLE` (또는 SSM `/qa/legacy/session_table`) | `qa_sessions` | 동일 |
| S3 raw/reports | `s3://$QA_BUCKET_NAME/<원prefix>/...` (공용 버킷) | `s3://$QA_BUCKET_NAME/tenants/kolon_default/...` | CopyObject (Delete 없음) |
| 시크릿 | 기존 `/a2a_gateway/...` 등 | `/qa/kolon_default/<name>` | 수동 mapping — 별도 `SECRETS_MIGRATION_CHECKLIST.md` 참조 |

**§3.2 확정: 비파괴 복사** — 원본 테이블/버킷/시크릿은 모두 보존. 롤백은 대상(신규) 삭제로 충분. 운영 테이블/버킷명은 **환경변수 또는 SSM** 로만 주입하며 스크립트 내 하드코딩 금지.

---

## 1. 사전 조건 (PHASE1_MIGRATION_PLAN §5 미결 해결 필수)

| # | 필요 정보 | 주입 경로 | 담당 | 상태 |
|---|---|---|---|---|
| 1 | 평가 테이블명 | env `LEGACY_EVAL_TABLE` 또는 SSM `/qa/legacy/eval_table` | 운영팀/PL | 미결 |
| 2 | 세션 테이블명 | env `LEGACY_SESSION_TABLE` 또는 SSM `/qa/legacy/session_table` | 운영팀/PL | 미결 |
| 3 | S3 버킷명 (공용) | env `QA_BUCKET_NAME` (§3.2 공용 버킷 정책) | 운영팀/PL | 미결 |
| 4 | 기존 DynamoDB 스키마 | 운영팀 제공 DescribeTable JSON → `docs/SCHEMA_DIFF.md` 수기 작성 | Dev2 | 미결 |
| 5 | 신규 5종 테이블 배포 (CDK `QaMultiTenantTables` — §3.1) | CDK deploy (PL 별도 승인) | Dev6 | CDK synth OK / deploy 미실시 |
| 6 | EC2 배포 디렉토리 | SSM `/qa/deploy/dir` | 운영팀/PL | 미결 |

**위 6개 정보가 모두 확정되기 전에는 백필 스크립트를 실행하지 않는다. placeholder 를 실제 값으로 하드코딩 금지.**

---

## 2. 단계 (실행 금지, 설계 기술만)

### 2.1 스키마 diff 분석 (사전 작업, 실 AWS 호출 없음)

- 입력: 운영팀이 제공한 기존 테이블 DescribeTable JSON (텍스트 파일로 전달)
- 출력: `docs/SCHEMA_DIFF.md` — 속성명/타입 차이, 추가 필드(tenant_id) 목록
- Dev2 가 JSON 을 받아 수기로 diff 작성. 스크립트는 없다.

### 2.2 비파괴 복사 전략 (§3.2 확정안)

- 원본 테이블/버킷은 **읽기만** 수행 (Scan / ListObjectsV2 / GetObject). 삭제·수정 금지.
- 대상에 쓸 때 반드시 `data/dynamo::tenant_put_item` 또는 `data/s3::tenant_put_object` 경유. `tenant_id` 는 "kolon_default" 고정.
- 실패한 항목은 `failed.jsonl` 에 로그 — 재시도 시 이 파일만 재처리.
- 체크포인트: LastEvaluatedKey / NextContinuationToken 을 `progress.json` 에 주기 저장 (재시작 지원).
- 원본명은 **env 또는 SSM 에서만** 읽는다 (`LEGACY_EVAL_TABLE`, `LEGACY_SESSION_TABLE`, `QA_BUCKET_NAME`). 스크립트 내 하드코딩 금지.

### 2.3 실행 순서 (승인 후)

```
(1) PL 승인 + §1 사전 조건 전원 확인
(2) AMI 스냅샷 + DynamoDB PITR 스냅샷
(3) scripts/backfill/backfill_evaluations.py  ← 현재는 스텁
(4) scripts/backfill/backfill_sessions.py     ← 현재는 스텁
(5) scripts/backfill/backfill_s3_prefix.py    ← 현재는 스텁
(6) scripts/backfill/verify_backfill.py 실행 → 리포트 검토
(7) 샘플 쿼리 (AWS 콘솔) 로 교차 확인
(8) 24h 모니터링
```

### 2.4 롤백 플랜

| 실패 지점 | 조치 |
|---|---|
| DynamoDB 백필 오류 | 신규 테이블 비우기 (`BatchWriteItem DELETE` 또는 테이블 삭제·재생성) |
| S3 복사 오류 | `tenants/kolon_default/` prefix 일괄 삭제 (원본 보존됨) |
| Cognito 패치 오류 | 별도 플랜 — 본 문서 범위 밖 (`PHASE1_MIGRATION_PLAN` §2.5 롤백 참조) |

원본은 전 단계 보존이므로 어떤 실패에서도 "운영 복귀" 는 "신규 측 폐기" 한 번이면 된다.

---

## 3. 검증 기준 (verify_backfill.py 기대 출력)

- DynamoDB: 원본 item count == `tenant_query("qa_evaluations_v2", "kolon_default")` count (±0)
- 샘플 10건 랜덤 비교: 속성 동치 (tenant_id 제외)
- S3: 원본 객체 수 == `tenant_list_objects("kolon_default")` 수 (±0)
- 체크섬 샘플: 무작위 20객체 ETag/MD5 일치

---

## 4. 금지 사항 (2026-04-17 배포 freeze 준수)

- 어떤 스크립트도 실행 금지. dry-run 플래그 도입 금지.
- 자격증명 (AWS access key / session token / Cognito 토큰) 사용 금지.
- 운영 리소스 변형 금지 (원본 테이블/버킷/시크릿/사용자 일체).
- CDK deploy / SSM parameter 수정 / EC2 인플레이스 배포 금지.
- placeholder / env var (`LEGACY_EVAL_TABLE`, `LEGACY_SESSION_TABLE`, `QA_BUCKET_NAME`, `<ACCOUNT_ID>`) 를 하드코딩된 실제 값으로 대체 금지 — 런타임 env/SSM 로만 주입.

---

## 5. 연결 문서

- `ARCHITECTURE.md` §3 (DynamoDB 테이블 정의), §4 (S3 prefix)
- `docs/DATA_ISOLATION.md` — 헬퍼 계약
- `docs/PHASE1_MIGRATION_PLAN.md` §2.3 (본 백필 단계), §3.1 (CDK 채택), §3.2 (비파괴 복사 확정안), §5 (env placeholder 목록), §6 (체크리스트)
- `docs/SECRETS_MIGRATION_CHECKLIST.md` — 시크릿 mapping 표 (본 문서 별도)
