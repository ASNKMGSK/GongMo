# QA Pipeline V3 — EC2 배포 키트

사용자 AWS 계정에 독립 배포. **모든 단계는 사용자가 직접 명시적으로 실행해야만 동작**.

> ⚠️ **에이전트 / 자동화 / CI 가 이 스크립트를 실행하지 않음**. Claude Code 세션에서도 사용자가 "배포해" / "provision 실행해" 같이 명시적으로 지시해야 실행됨. 파일 생성·수정만으로는 아무 AWS 리소스도 만들어지지 않음.

## 전제
- `~/.aws/credentials` 에 `default` 프로파일 있고 us-east-1 기본
- AWS 계정에 Bedrock Claude Sonnet 4 model access 활성화 완료
- 로컬에 Python 3.13 + Node 20 + pnpm 10 설치
- `boto3` 설치 (`pip install boto3`)

## 실행 순서 (최초 1회)

### 1) EC2 프로비저닝 — `provision.py`
```bash
cd packages/agentcore-agents/qa-pipeline/deploy
python provision.py
```
생성 리소스 (모두 `qa-pipeline-v3` 네임 prefix, idempotent):
- IAM Role + InstanceProfile (Bedrock / S3 / SSM 권한)
- S3 버킷 `qa-deploy-{AccountId}-us-east-1`
- SecurityGroup (22 / 80 / 443 / 3000 / 8081 ingress 0.0.0.0/0)
- EC2 `t3.medium` · Ubuntu 22.04 · 30GB gp3

결과는 `provision.out.json` 에 저장됨. **이 파일은 deploy.py 가 읽으므로 삭제 금지.**

### 2) EC2 초기 셋업 — `deploy.py --target bootstrap`
```bash
python deploy.py --target bootstrap
```
SSM 으로 `bootstrap.sh` 를 EC2 에서 실행:
- Python 3.13 · Node 20 · pnpm 10 · pm2 설치
- `/opt/qa-pipeline` (백엔드) · `/opt/qa-webapp` (프론트) 디렉토리
- `qa-pipeline.service` (systemd) · `qa-webapp` (pm2 ecosystem) 정의
- nginx 리버스 프록시 (`/` → :3000, `/api/` → :8081)

### 3) 환경변수 파일 EC2 에 업로드 (1회 수동)
- `.env.backend.example` → `/opt/qa-pipeline/.env` 로 복사 후 값 점검
- `.env.frontend.example` → `/opt/qa-webapp/.env.production` 으로 복사 후 값 점검

SSM Session Manager 로 접속:
```bash
aws ssm start-session --target $(jq -r .instance_id provision.out.json)
```

### 4) 첫 배포
```bash
python deploy.py --target both
```

## 이후 업데이트 — 사용자가 명시적으로 실행할 때만 동작
```bash
python deploy.py --target backend    # 백엔드만 재배포
python deploy.py --target frontend   # 프론트만 재배포
python deploy.py --target both       # 둘 다
```

배포 스크립트는 **자동 실행되지 않음**. CI/CD / cron / 에이전트 자동 트리거 없음.
배포 시점은 전적으로 사용자 판단.

## 확인 URL
```
http://<public-ip>/            ← 프론트 (Next.js UI)
http://<public-ip>/api/health  ← 백엔드 health
```
공용 IP 는 `provision.out.json` 의 `public_ip` 필드.

## 롤백
- 백엔드: `/opt/qa-pipeline.old` 로 이전 버전 보존됨. `mv` 로 교환 + `systemctl restart qa-pipeline`
- 프론트: `/opt/qa-webapp.old` 동일 방식 + `pm2 reload qa-webapp`

## 정리 (배포 중단 / 리소스 삭제)
현재는 생성 스크립트만 있고 destroy 스크립트 없음. 수동 삭제:
- EC2 terminate
- SG / IAM Role / S3 버킷 (내용물 비우고)

필요하면 `destroy.py` 추가 요청.

## 주의
- Bedrock TPM quota 가 제한적이면 `QA_DEBATE_MAX_PARALLEL=1` 유지 권장
- `next.config.ts` 에 `output: "standalone"` 설정 필요 — 없으면 프론트 빌드 실패
- CDK 스택은 별개 — 이 kit 으로 생성한 리소스는 CDK 와 drift 있으므로 섞어 쓰지 말 것
