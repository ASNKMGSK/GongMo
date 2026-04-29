# 배포 가이드 — QA Pipeline Multi-Tenant

> **정책**: EC2 인플레이스 배포 유지. CDK 재배포 금지 (IP 고정).
> CDK 스택은 **신규 인프라(테이블/IAM/로그 그룹)** 만 적용한다.
> `cdk deploy` 는 PL 승인 후에만 실행한다.

---

## 1. 운영 환경 개요

| 항목 | 값 |
|---|---|
| EC2 인스턴스 | `i-0cfa13fc99fcd4dfa` |
| 공인 IP | `100.29.183.137` (고정 유지) |
| 실행 방식 | systemd 로 FastAPI (`server.py`) 상시 구동 |
| 소스 업데이트 | S3 + SSM RunCommand (인플레이스) |
| Python | `~/.conda/envs/py313/python.exe` |
| 배포 디렉터리 | SSM `/qa/deploy/dir` → env `<EC2_DEPLOY_DIR>` (하드코딩 금지) |
| Cognito User Pool | env `COGNITO_USER_POOL_ID` / SSM `/qa/cognito/user-pool-id` |
| 레거시 DynamoDB (평가) | env `LEGACY_EVAL_TABLE` / SSM `/qa/legacy/eval-table` |
| 레거시 DynamoDB (세션) | env `LEGACY_SESSION_TABLE` / SSM `/qa/legacy/session-table` |
| 레거시 S3 버킷 | env `QA_BUCKET_NAME` (공용) |

---

## 2. 앱 인플레이스 배포 (코드/설정 업데이트)

1. **로컬에서 패키징**
   ```bash
   cd packages/qa-pipeline-multitenant
   zip -r /tmp/qa-pipeline-mt.zip qa-pipeline/ -x "*/__pycache__/*" "*/.pytest_cache/*"
   ```

2. **S3 업로드** (97KB 한계 우회: SSM 인라인 대신 S3 경유)
   ```bash
   aws s3 cp /tmp/qa-pipeline-mt.zip s3://<deploy-bucket>/qa-pipeline-mt/$(date +%Y%m%d-%H%M%S).zip
   ```

3. **SSM RunCommand 로 EC2 에서 교체**
   - 리전: `us-east-1` (S3/SSM/EC2 리전 반드시 일치)
   - SigV4 서명 필수 (botocore 기본값)
   - 서비스 리스타트: `sudo systemctl restart qa-pipeline`

4. **헬스체크**
   ```bash
   curl -fsS http://100.29.183.137/health
   ```

> ⚠️ **CDK 재배포로 EC2 를 재생성하면 IP 가 바뀐다**. 반드시 위 인플레이스 경로를 사용한다.

---

## 3. CDK 신규 인프라 적용 (테이블/IAM/로그 그룹)

본 패키지 `cdk/` 아래 스택은 **DynamoDB 테이블 5종, IAM Role, CloudWatch Log Group** 만 생성한다.
EC2/VPC/ALB 는 건드리지 않는다.

### 3.1 합성만 (CI/로컬 검증)

```bash
cd packages/qa-pipeline-multitenant/cdk
~/.conda/envs/py313/python.exe -m pip install -r requirements.txt
~/.conda/envs/py313/python.exe -m aws_cdk --version   # CDK CLI 별도 설치 필요
cdk synth
```

- `cdk synth` 는 CloudFormation 템플릿을 `cdk.out/` 에 출력만 한다.
- **자동 deploy 금지** — PR/리뷰 단계에서는 합성만 검증한다.

### 3.2 수동 적용 (PL 승인 후)

```bash
# 스택별 개별 적용 — 전체 --all 금지
cdk deploy QaMultiTenantTables --require-approval never
cdk deploy QaMultiTenantObservability --require-approval never
cdk deploy QaMultiTenantIam --require-approval never
```

- 각 스택의 `RemovalPolicy=RETAIN` 이므로 실수 삭제 시에도 리소스는 유지된다.
- 테이블 생성 후 Dev2 의 `data/dynamo.py` 의 `_TABLE_SK` 매핑과 일치하는지 확인.

### 3.3 스택 의존 관계

```
QaMultiTenantTables       (독립)
QaMultiTenantObservability (독립)
QaMultiTenantIam          → QaMultiTenantTables (테이블 ARN 참조)
```

### 3.4 CDK 채택 근거

`PHASE1_MIGRATION_PLAN.md` §3.1 (사용자 결정 v2) — DynamoDB 테이블 생성은 **CDK (`QaMultiTenantTables`)** 로 진행한다. 상세 배포 체크리스트는 `CDK_DEPLOY_PLAN.md` 참조.

### 3.5 인플레이스 배포 + AMI 스냅샷 (PHASE1 §3.5)

**확정안** (PHASE1_MIGRATION_PLAN.md §3.5): EC2 재생성 없이 인플레이스 배포 — IP `100.29.183.137` 유지. 배포 직전 AMI 스냅샷으로 원복 창구 확보.

> [!] 아래 명령은 전부 **참조 용도**. 실행은 PL + 운영담당 승인 후에만 수행.

**사전 체크 (배포 T-0, 운영담당 협의)**
- [ ] 배포 창(`<DEPLOY_WINDOW>`) 운영담당 확인 — 트래픽 적은 시간대
- [ ] 대상 사용자 수(`<USER_COUNT>`) 운영담당 제공 확인 (N≤20 vs N>20 은 Dev2 백필 플랜 분기)
- [ ] 현재 systemd 상태 `qa-pipeline.service active` (운영담당 SSM 세션에서 확인)
- [ ] 직전 커밋 해시 기록 (롤백용)

**Step 1 — AMI 스냅샷 (롤백 창구)**

```bash
# [!] 실행 금지 — 운영담당이 콘솔/CLI 에서 직접 수행
aws ec2 create-image \
  --instance-id i-0cfa13fc99fcd4dfa \
  --name "qa-pipeline-pre-deploy-$(date -u +%Y%m%d-%H%M%SZ)" \
  --description "Snapshot before multi-tenant rollout" \
  --no-reboot
# → 반환 ImageId 는 배포 로그/Slack 에 기록
```

`--no-reboot` 로 서비스 중단 없이 스냅샷 생성 (I/O consistent 보장은 파일 캐시 플러시 필요 시 옵션 조정).

**Step 2 — 패키징 + S3 업로드**

```bash
# [!] 실행 금지 — 참조용
cd packages/qa-pipeline-multitenant
zip -r /tmp/qa-pipeline-mt.zip qa-pipeline/ \
  -x "*/__pycache__/*" "*/.pytest_cache/*"

aws s3 cp /tmp/qa-pipeline-mt.zip \
  "s3://${QA_BUCKET_NAME}/deploy/qa-pipeline-mt/$(date -u +%Y%m%d-%H%M%SZ).zip"
```

**Step 3 — SSM RunCommand 로 인플레이스 교체**

```bash
# [!] 실행 금지 — 참조용. 스크립트는 운영담당 승인 커맨드로 제출.
aws ssm send-command \
  --instance-ids i-0cfa13fc99fcd4dfa \
  --document-name "AWS-RunShellScript" \
  --parameters commands=\[
    "set -euo pipefail",
    "EC2_DEPLOY_DIR=$(aws ssm get-parameter --name /qa/deploy/dir --query Parameter.Value --output text)",
    "aws s3 cp s3://${QA_BUCKET_NAME}/deploy/qa-pipeline-mt/<artifact>.zip /tmp/deploy.zip",
    "sudo -u ec2-user unzip -o /tmp/deploy.zip -d ${EC2_DEPLOY_DIR}",
    "sudo systemctl restart qa-pipeline",
    "curl -fsS http://127.0.0.1:8080/health"
  \] \
  --region us-east-1
```

- 리전 `us-east-1` 고정 (S3/SSM/EC2 동일 리전)
- SigV4 서명은 botocore 기본
- `${QA_BUCKET_NAME}` / `/qa/deploy/dir` 은 **환경변수·SSM 에서 읽는다** — 값 하드코딩 금지

**Step 4 — 스모크 체크**

```bash
# [!] 실행 금지 — 운영담당이 수행
curl -fsS http://100.29.183.137/health
# /evaluate 테스트 호출은 운영담당이 JWT 발급 후 수행
```

- [ ] `/health` 200 OK
- [ ] TenantMiddleware 동작: JWT 누락 시 401, `custom:tenant_id` 누락 시 401
- [ ] `/evaluate` 샘플 요청 1건 성공 + `qa_evaluations_v2` 기록 확인
- [ ] `qa_audit_log` 에 요청 1행 쓰기 확인
- [ ] `qa_quota_usage` 에 minute_counters ADD 확인
- [ ] CloudWatch `QaMultiTenant` 네임스페이스 Dimension=TenantId 메트릭 도착

**Step 5 — 스모크 승인**

- [ ] **코오롱 운영담당 승인** (PL 결정 §3.6) — 서면/Slack 기록
- [ ] 이슈 발견 시 즉시 Step 6 롤백

**Step 6 — 롤백 (이슈 발생 시)**

1. S3 직전 아티팩트로 Step 3 재수행 (코드만 되돌림 — 빠름)
2. 테이블 손상 시 PITR 로 테이블 복원 (`RestoreTableToPointInTime`)
3. 치명적 장애 시 Step 1 AMI 로 EC2 rebuild — **단, EIP 재연결 필수**:
   ```bash
   # [!] 실행 금지 — 참조용
   # 기존 EIP(100.29.183.137) 를 새 인스턴스에 재연결하여 IP 유지
   aws ec2 associate-address --instance-id <new-i-id> --allocation-id <eip-alloc-id>
   ```
   → EIP 재연결 안 하면 IP 가 바뀐다. 반드시 allocation-id 사전 확보.

---

## 4. 환경 변수 (EC2 systemd unit)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AWS_REGION` | `us-east-1` | 모든 AWS 클라이언트 리전 |
| `TENANT_JWT_VERIFY_SIGNATURE` | `0` | 프로덕션은 `1` 로 켜고 JWKS 지정 |
| `TENANT_JWT_JWKS_URL` | — | Cognito JWKS 엔드포인트 |
| `LOCAL_TENANT_ID` | — | 로컬/디버그 폴백 테넌트 |
| `RATE_LIMIT_ENABLED` | `1` | `0`/`false` 로 Rate Limit 비활성화 |
| `AUDIT_LOG_ENABLED` | `1` | `0`/`false` 로 감사 로그 비활성화 |
| `METRICS_ENABLED` | `1` | `0`/`false` 로 CloudWatch PutMetric 비활성화 |
| `QA_BUCKET_NAME` | — | 테넌트 격리 S3 버킷 (Dev2 `s3.py` 와 동일 env) |
| `QA_MT_BUCKET` | `qa-multitenant-artifacts` | `QA_BUCKET_NAME` 미설정 시 fallback (IAM 합성용) |
| `OPENSEARCH_HOST` | — | Dev2 `opensearch.py` 호스트 (aoss/es) |
| `OPENSEARCH_PORT` | `443` | OpenSearch 포트 |
| `OPENSEARCH_SERVICE` | `aoss` | `aoss` (Serverless) 또는 `es` (managed) |

---

## 5. 롤백 절차 (§3.5 Step 6 상세)

1. S3 에서 직전 버전 zip 내려받기 → §3.5 Step 3 반복 (코드 교체 + restart).
2. DynamoDB 테이블은 PITR 로 복원 (`RestoreTableToPointInTime`).
3. CloudWatch Log Group 은 `RemovalPolicy=RETAIN` 로 유지됨.
4. 치명적 장애 시 §3.5 Step 1 AMI 에서 rebuild — 반드시 EIP(`100.29.183.137`) 재연결 절차 수반.

---

## 6. 주의사항 (Do / Don't)

- ✅ **Do**: `cdk synth` 로 템플릿 검증
- ✅ **Do**: 앱 코드는 S3 + SSM 로 인플레이스
- ❌ **Don't**: `cdk deploy --all`
- ❌ **Don't**: EC2 재생성을 유발하는 변경 (AMI/SG/UserData)
- ❌ **Don't**: 기존 `packages/cdk-infra-python/` 건드리기 — 별도 운영 레인
