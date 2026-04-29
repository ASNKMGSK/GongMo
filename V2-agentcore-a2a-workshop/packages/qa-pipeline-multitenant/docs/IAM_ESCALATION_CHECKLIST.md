# IAM 승격 체크리스트 — Phase 5

> ## [!] 실행 금지 — 문서/체크리스트 전용
> `aws iam`, `aws sts`, `boto3` 호출 등 실 AWS 변경/조회 명령은 **실행하지 않는다**.
> 본 체크리스트는 Phase 5 승격 계획 참조용이며, 실제 실행은 PL + 사용자 명시 승인 후에만 진행한다.

---

## 1. 현재 상태 (Phase 1 이후)

- CDK 스택 `QaMultiTenantIam` 이 **새 Role** `qa-multitenant-app-role` 을 생성한다.
- 운영 EC2 (`i-0cfa13fc99fcd4dfa`) 의 InstanceProfile 은 **변경하지 않는다** — 기존 Role 과 신규 Role 은 공존 상태.
- 신규 Role 의 Assume Principal: `ec2.amazonaws.com` (Service). 실 EC2 attach 는 Phase 5.
- 정책 Sid 목록 (cdk.out 검증):
  - `TenantScopedDynamoDb` (LeadingKeys=`${aws:PrincipalTag/tenant_id}`)
  - `TenantScopedS3Object` / `TenantScopedS3List`
  - `TenantScopedSecretsRead` / `TenantScopedSecretsWrite` (`/qa/${aws:PrincipalTag/tenant_id}/*`)
  - `OpenSearchServerlessAccess` (aoss:APIAccessAll) / `OpenSearchDomainHttp` (es:ESHttp*)
  - `AllowTagSession` (sts:TagSession for tenant_id)
  - CloudWatch Logs + PutMetricData (Namespace=QaMultiTenant)

## 2. PrincipalTag 시나리오 예시

Phase 5 에서 실제 요청 시 세션 태그를 부여해 격리를 강제한다.
다음은 **코드 레퍼런스만** — 실행 금지.

```python
# [!] 실행 금지 — Phase 5 Assume 패턴 레퍼런스
import boto3
sts = boto3.client("sts")
resp = sts.assume_role(
    RoleArn="arn:aws:iam::<ACCOUNT>:role/qa-multitenant-app-role",
    RoleSessionName=f"qa-{tenant_id}",
    Tags=[{"Key": "tenant_id", "Value": tenant_id}],
    TransitiveTagKeys=["tenant_id"],
)
# 이 세션으로 DynamoDB/S3/Secrets 호출 시, 정책의
# ${aws:PrincipalTag/tenant_id} 가 tenant_id 로 바인딩됨.
```

확인 명령 (실행 금지, 참조만):

```bash
# [!] 실행 금지 — caller identity / session tag 확인
aws sts get-caller-identity
aws sts get-session-token
```

## 3. Phase 5 승격 단계

아래 각 단계는 PL 승인 + 사용자 명시 후에만 수행.

- [ ] **A. 최소 권한 검증 (드라이런)**
  - 각 라우터/노드에서 호출하는 `boto3` API 를 수집 → 정책 Sid 와 매칭 확인
  - 누락 권한 있으면 CDK 스택 업데이트 (문서에만 반영, deploy 금지)
- [ ] **B. 스테이지 Role 테스트**
  - 스테이지 AWS 계정에 `qa-multitenant-app-role` 배포 (스테이지 전용)
  - 테스트 EC2 또는 로컬 AssumeRole 로 DynamoDB/S3/Secrets 호출 정상 동작 확인
  - 테넌트 A 키로 테넌트 B 리소스 접근 시 AccessDeniedException 확인
- [ ] **C. 운영 EC2 InstanceProfile 연결 준비**
  - 현재 운영 Role 의 정책 백업 (콘솔 JSON 다운로드)
  - 신규 Role 을 운영 계정에 배포 (`cdk deploy QaMultiTenantIam`)
  - InstanceProfile 생성 및 Role attach (CDK 또는 수동)
- [ ] **D. 원자적 전환**
  - EC2 InstanceProfile 교체 시 롤링 방식 — 공인 IP `100.29.183.137` 재생성 금지 (EIP 유지)
  - 전환 창 동안 `/health` / `/evaluate` 호출 성공률 모니터 (CloudWatch)
- [ ] **E. 기존 Role 비활성화**
  - 운영 안정 1주 후 기존 Role 의 정책 삭제 (Role 자체는 RETAIN)

## 4. 최소 권한 검증표

| 영역 | 호출자 (예상) | 필요 권한 | 정책 Sid |
|---|---|---|---|
| DynamoDB: qa_tenants 읽기 | tenant.store.get_config | Query/GetItem | TenantScopedDynamoDb |
| DynamoDB: qa_evaluations_v2 쓰기 | routers/evaluate | PutItem/UpdateItem | TenantScopedDynamoDb |
| DynamoDB: qa_audit_log 쓰기 | middleware/audit_log | PutItem | TenantScopedDynamoDb |
| DynamoDB: qa_quota_usage ADD | middleware/rate_limit (tenant_atomic_counter) | UpdateItem | TenantScopedDynamoDb |
| S3: 리포트 업로드 | routers/save_xlsx | PutObject | TenantScopedS3Object |
| S3: raw 업로드 | routers/evaluate | PutObject | TenantScopedS3Object |
| S3: 목록 조회 | 관리 UI | ListBucket (prefix) | TenantScopedS3List |
| Secrets: LLM 키 | routers/evaluate | GetSecretValue | TenantScopedSecretsRead |
| Secrets: 관리 | admin/tenants | CreateSecret/PutSecret | TenantScopedSecretsWrite |
| OpenSearch: 검색 | data/opensearch | aoss:APIAccessAll / es:ESHttp* | OpenSearch* |
| CloudWatch: 메트릭 | observability/metrics | PutMetricData | (inline) |
| Logs: 앱 로그 | systemd / app | CreateLogStream/PutLogEvents | (inline) |

## 5. 리스크 및 완화

| 리스크 | 완화 |
|---|---|
| LeadingKeys Condition 에 `ForAllValues:StringEquals` 사용 — 빈 리스트는 모두 만족 | 애플리케이션 레이어(`data/dynamo.py`) 에서 `_require_tenant_id` 가드 병행 (현재 구현됨) |
| PrincipalTag 누락 (세션 태그 미부여) | 레이어에서 AssumeRole 실패 시 즉시 500 반환 + 모니터링 |
| EC2 InstanceProfile 교체 시 장애 | 스테이지 충분 검증 + 블루/그린 패턴 |
| Phase 5 이전 App 이 직접 DynamoDB 에 접근 | 기존 Role 정책이 광범위한 상태 유지 — 보완은 애플리케이션 레이어 격리 |

## 6. 연결 문서

- `PHASE1_MIGRATION_PLAN.md` (PL) — 전체 마이그레이션 로드맵
- `CDK_DEPLOY_PLAN.md` — 스택별 배포 체크리스트
- `CDK_SYNTH_SNAPSHOT.md` — 합성 결과 스냅샷
- `DATA_ISOLATION.md` (Dev2) — 데이터 레이어 격리 규약
