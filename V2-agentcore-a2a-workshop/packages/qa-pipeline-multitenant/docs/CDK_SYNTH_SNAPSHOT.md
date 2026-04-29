# CDK Synth 스냅샷 — Phase 1 기준선

> ## [!] 본 문서는 로컬 `cdk synth` 결과 스냅샷이다.
> `cdk synth` 는 AWS API 호출을 하지 않는다 — CloudFormation 템플릿을 로컬 `cdk.out/` 에 출력한다.
> 본 스냅샷은 향후 `cdk diff` 기준선 용도이며, 배포/수정 근거로는 `CDK_DEPLOY_PLAN.md` 참조.

---

## 1. 합성 환경

- Python: `~/.conda/envs/py313/python.exe` (Python 3.13.12)
- aws-cdk-lib: `requirements.txt` 기준 `>=2.160.0,<3.0.0`
- account (합성용 더미): `123456789012`
- region: `us-east-1`
- env var: `QA_BUCKET_NAME` 미설정 → `QA_MT_BUCKET` fallback → 기본 `qa-multitenant-artifacts`

재현 명령 (로컬 실행 OK — AWS 호출 없음):

```bash
cd packages/qa-pipeline-multitenant/cdk
pip install -r requirements.txt
cdk synth                      # cdk.out/ 에 템플릿 생성
```

## 2. 스택별 리소스 개수

| 스택 | 리소스 타입 | 개수 |
|---|---|---|
| `QaMultiTenantTables` | `AWS::DynamoDB::Table` | 5 |
| `QaMultiTenantObservability` | `AWS::Logs::LogGroup` | 1 |
| `QaMultiTenantObservability` | `AWS::Logs::MetricFilter` | 4 |
| `QaMultiTenantIam` | `AWS::IAM::Role` | 1 |
| `QaMultiTenantIam` | `AWS::IAM::Policy` | 1 |

## 3. 리소스 이름 스냅샷

### 3.1 QaMultiTenantTables (DynamoDB)

| Logical ID | TableName | PK / SK | TTL |
|---|---|---|---|
| QaTenantsTable | `qa_tenants` | tenant_id / — | — |
| QaEvaluationsV2Table | `qa_evaluations_v2` | tenant_id / evaluation_id | — |
| QaSessionsTable | `qa_sessions` | tenant_id / session_id | — |
| QaAuditLogTable | `qa_audit_log` | tenant_id / timestamp | `ttl` (enabled) |
| QaQuotaUsageTable | `qa_quota_usage` | tenant_id / yyyy-mm | — |

공통: BillingMode=PAY_PER_REQUEST / PITR=enabled / SSE=AWS_MANAGED / RemovalPolicy=RETAIN / GSI 없음.

### 3.2 QaMultiTenantObservability

| Logical ID | 이름 | 비고 |
|---|---|---|
| QaMultiTenantAppLogGroup | `/qa-multitenant/app` | RetentionDays=Never (RemovalPolicy=RETAIN) |
| MfEvaluationCount | MetricFilter | $.metric=EvaluationCount → Count, Namespace=QaMultiTenant |
| MfTokenUsage | MetricFilter | $.metric=TokenUsage → `$.value`, Count |
| MfLatencyP95 | MetricFilter | $.metric=LatencyMs → `$.value`, Milliseconds |
| MfFailureRate | MetricFilter | $.metric=Failure → Count |

모든 MetricFilter 의 Dimension: `{TenantId: "$.tenant_id"}`.

### 3.3 QaMultiTenantIam

| Logical ID | 타입 | 이름/주체 |
|---|---|---|
| QaMultiTenantAppRole | IAM::Role | `qa-multitenant-app-role`, AssumedBy=ec2.amazonaws.com |
| QaMultiTenantAppRoleDefaultPolicy* | IAM::Policy | 아래 Statement Sid 들 |

정책 Statement Sid 목록 (Sid 없는 inline 항목 2개 + 명시 Sid 7개):

1. (no-sid) — `logs:CreateLogStream/PutLogEvents/DescribeLogStreams` on `/qa-multitenant/*`
2. (no-sid) — `cloudwatch:PutMetricData` (namespace=QaMultiTenant)
3. `TenantScopedDynamoDb` — 5개 테이블 + `/index/*` ARN (Fn::ImportValue), LeadingKeys 조건
4. `TenantScopedS3Object` — `arn:aws:s3:::<bucket>/tenants/${aws:PrincipalTag/tenant_id}/*`
5. `TenantScopedS3List` — `arn:aws:s3:::<bucket>`, prefix 조건
6. `TenantScopedSecretsRead` — `secret:/qa/${aws:PrincipalTag/tenant_id}/*`
7. `OpenSearchServerlessAccess` — `aoss:APIAccessAll` (*)
8. `OpenSearchDomainHttp` — `es:ESHttp*` on domain/*
9. `TenantScopedSecretsWrite` — Create/Put/Delete/TagResource on `secret:/qa/${aws:PrincipalTag/tenant_id}/*`
10. `AllowTagSession` — `sts:TagSession` (tenant_id 전용)

## 4. 인용된 Fn::ImportValue (QaMultiTenantIam)

Phase 1 DynamoDB 테이블 ARN 5개 각각의 `Arn` 과 `Arn/index/*` — 총 10개 Import.
Import 원천 스택: `QaMultiTenantTables`.

## 5. diff 전략

- `cdk diff <stack>` 은 배포된 스택이 없는 상태에서는 "리소스 추가만" 출력.
- 배포 후의 diff 는 본 스냅샷과 비교해 **예상 밖 삭제/이름 변경** 이 있는지 사용자가 확인한다.
- 자동 승인 금지 — 항상 사람이 검토.

## 6. 재생성 방법 (문서 갱신 시)

```bash
# 로컬 파이썬으로 리소스 카운트 재산출 — AWS 호출 없음
~/.conda/envs/py313/python.exe -c "
import aws_cdk as cdk
from stacks.qa_tenant_table_stack import QaTenantTableStack
from stacks.qa_tenant_iam_stack import QaTenantIamStack
from stacks.qa_observability_stack import QaObservabilityStack
app = cdk.App()
env = cdk.Environment(account='123456789012', region='us-east-1')
t = QaTenantTableStack(app, 'QaMultiTenantTables', env=env)
o = QaObservabilityStack(app, 'QaMultiTenantObservability', env=env)
i = QaTenantIamStack(app, 'QaMultiTenantIam', tables=t.all_tables,
                    bucket_name='qa-mt-test', env=env)
i.add_dependency(t)
app.synth()
"
```

AWS 호출이 발생하면 즉시 중단 — 본 작업 흐름상 발생하지 않아야 한다.
