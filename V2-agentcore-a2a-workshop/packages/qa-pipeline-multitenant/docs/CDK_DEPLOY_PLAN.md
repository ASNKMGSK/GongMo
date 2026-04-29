# CDK 배포 Plan — Phase 1

> ## [!] 실행 절대 금지 — 문서 전용
> 본 문서의 `cdk deploy` / `cdk diff` / `aws *` / `boto3` 명령은 **실행하지 않는다**.
> 코드블록은 레퍼런스 용도만이며, 실제 적용은 PL 및 사용자 명시 승인 후에만 진행한다.
> (PL `PHASE1_MIGRATION_PLAN.md` §3.1 — DynamoDB 생성 방법 CDK 권장과 연결)

---

## 1. 대상 스택 및 합성 산출물

| 스택 이름 | 파일 | 리소스 | 비고 |
|---|---|---|---|
| `QaMultiTenantTables` | `cdk/stacks/qa_tenant_table_stack.py` | DynamoDB Table x 5 | qa_tenants / qa_evaluations_v2 / qa_sessions / qa_audit_log(TTL=ttl) / qa_quota_usage |
| `QaMultiTenantObservability` | `cdk/stacks/qa_observability_stack.py` | Logs::LogGroup x 1, Logs::MetricFilter x 4 | Namespace=QaMultiTenant, Dimension=TenantId |
| `QaMultiTenantIam` | `cdk/stacks/qa_tenant_iam_stack.py` | IAM::Role x 1, IAM::Policy x 1 | PrincipalTag 기반 격리 정책 |

- 합성: `cdk synth`. AWS API 호출 없음 (CloudFormation 템플릿을 로컬 `cdk.out/` 에 출력만).
- 리전/계정은 `CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION` 로 주입 (기본 `us-east-1`).

## 2. 의존 그래프

```
QaMultiTenantTables            (독립)
QaMultiTenantObservability     (독립)
QaMultiTenantIam  ─ depends on → QaMultiTenantTables (테이블 ARN 참조)
```

## 3. 배포 순서 (참조 — 실행 금지)

다음 명령은 **참조만** 한다. 실제 실행은 PL 승인 후 사용자가 직접 수행한다.

```bash
# [!] 실행 금지 — PL 승인 후에만
cd packages/qa-pipeline-multitenant/cdk
pip install -r requirements.txt

cdk synth                                           # 로컬 합성 검증 (AWS 호출 없음)
cdk diff QaMultiTenantTables                        # 기존 스택과의 차이 확인
cdk diff QaMultiTenantObservability
cdk diff QaMultiTenantIam

cdk deploy QaMultiTenantTables --require-approval never
cdk deploy QaMultiTenantObservability --require-approval never
cdk deploy QaMultiTenantIam --require-approval never
```

- `cdk deploy --all` 은 금지. 반드시 스택별 개별 적용.
- `--require-approval never` 는 대화식 프롬프트 스킵용 — 승인은 사전에 PL 이 수행.
- 전체 과정 중 단 한 번이라도 거부/오류가 나면 즉시 중단 후 PL 에 보고.

## 4. 드리프트 점검 체크리스트

배포 전에 다음을 확인한다. `cdk diff` 실행은 PL 승인 포함.

- [ ] 로컬 `cdk synth` 가 경고 없이 완료 (합성 스냅샷과 일치 — `CDK_SYNTH_SNAPSHOT.md`)
- [ ] `cdk diff QaMultiTenantTables` 에 **테이블 이름 변경/삭제 diff 없음** (PK/SK/TTL 유지)
- [ ] `cdk diff QaMultiTenantIam` 에 기존 운영 Role 삭제 diff 없음 (Phase 5 전까지 공존)
- [ ] `cdk diff QaMultiTenantObservability` 에 Log Group retention downgrade 없음
- [ ] 운영 EC2 `i-0cfa13fc99fcd4dfa` / Elastic IP `100.29.183.137` 을 건드리는 리소스가 diff 에 없음 (본 스택들은 touch 안 함)

## 5. 적용 후 검증 (수동)

실 배포 후에는 사용자가 콘솔/CLI 로 확인. 본 에이전트는 실행하지 않는다.

- [ ] DynamoDB 5종 테이블 `CREATING` → `ACTIVE` (PITR 활성, TTL=`ttl`)
- [ ] CloudWatch Log Group `/qa-multitenant/app` 생성, RetentionDays=Never
- [ ] MetricFilter 4종 (EvaluationCount / TokenUsage / LatencyP95 / FailureRate) 연결 확인
- [ ] IAM Role `qa-multitenant-app-role` 생성 (InstanceProfile 연결은 Phase 5)

## 6. 롤백

- DynamoDB: `RemovalPolicy=RETAIN` 이므로 스택 삭제해도 테이블 유지. 데이터 복구는 PITR `RestoreTableToPointInTime`.
- IAM/LogGroup: `RemovalPolicy=RETAIN`. 역시 유지.
- 스택 삭제 자체는 CloudFormation `delete-stack` — **그러나 Phase 1 단계에서는 금지**.

## 7. 환경 변수 (합성 시)

| 변수 | 기본 | 설명 |
|---|---|---|
| `CDK_DEFAULT_ACCOUNT` | — | 12자리 AWS 계정 ID |
| `CDK_DEFAULT_REGION` | `us-east-1` | 리전 |
| `QA_BUCKET_NAME` | — | S3 버킷 이름 (IAM 정책 resource 에만 반영) |
| `QA_MT_BUCKET` | `qa-multitenant-artifacts` | `QA_BUCKET_NAME` fallback |

## 8. 연결 문서

- `DEPLOY.md` — EC2 인플레이스 배포 + CDK 적용 가이드
- `CDK_SYNTH_SNAPSHOT.md` — 현재 합성 결과 스냅샷 (diff 기준선)
- `IAM_ESCALATION_CHECKLIST.md` — Phase 5 IAM 승격 체크리스트
- `PHASE1_MIGRATION_PLAN.md` §3.1 — PL 마이그레이션 플랜 (DynamoDB 생성 CDK 권장 근거)
