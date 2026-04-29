# Phase 1 Migration Plan (문서/코드만, 실 배포 금지)

> 작성자: `pl-architect` / 작성일: 2026-04-17 (v2 — 사용자 결정 반영)
> Status: **§3 6개 결정 확정 · §5 운영 정보 placeholder 유지 · 배포 금지 유지**
> 본 문서는 실행을 위한 것이 아니라 **계획/결정 기록용**. 실행 명령은 참고용 표기이며, 어떤 AWS 리소스도 건드리지 말 것.

---

## 0. 현재 상태

- Phase 0 완료: `packages/qa-pipeline-multitenant/` 에 백엔드/UI/CDK/테스트 전체 스켈레톤 확보 (pytest 42/42).
- 운영: 코오롱 단일 테넌트로 EC2 `i-0cfa13fc99fcd4dfa / 100.29.183.137` 에 `packages/agentcore-agents/qa-pipeline/` 가 이미 운영 중.
- 운영 데이터는 단일 테넌트 전용 DynamoDB/S3 에 존재 (`tenant_id` 필드 없음).

---

## 1. Phase 1 목표

1. 코오롱 운영을 **중단/변경 없이** 멀티테넌트 아키텍처로 전환.
2. 기존 운영 데이터에 `tenant_id="kolon_default"` 를 주입하여 Pool 모델 호환.
3. Cognito 사용자 풀에 `custom:tenant_id` / `custom:role` 속성 추가 (기존 사용자는 일괄 패치).
4. EC2 인플레이스 배포로 새 코드 반영 (IP 유지, 서비스 무중단).

---

## 2. 마이그레이션 단계 (Draft — 실행 전 사용자 승인 필요)

| # | 단계 | 위험도 | 되돌림 | 설명 |
|---|---|---|---|---|
| 2.1 | DynamoDB 신규 테이블 생성 (qa_tenants 외 4종) | **높음** | 삭제 | 빈 테이블 5개 — 운영 영향 없음. CDK deploy 또는 수동 생성. |
| 2.2 | `kolon_default` TenantConfig 초기 seed 삽입 | 낮음 | 행 삭제 | 1행 put. Dev4 온보딩 가이드 사용. |
| 2.3 | 기존 데이터 백필 — `tenant_id=kolon_default` 일괄 주입 (DynamoDB + S3) | **높음** | 롤백 어려움 | 운영 데이터 변형. 반드시 스냅샷 선행. |
| 2.4 | Cognito 커스텀 속성 추가 (`custom:tenant_id`, `custom:role`) | 중간 | 속성 제거 가능 (데이터 보존) | User Pool schema 변경. 기존 토큰은 재로그인 필요. |
| 2.5 | 기존 사용자 일괄 패치 (`custom:tenant_id=kolon_default`) | 중간 | 사용자별 수정 | 관리 API/스크립트로 일괄 업데이트. |
| 2.6 | EC2 인플레이스 배포 — 새 코드 반영 | **높음** | 이전 코드로 롤백 | IP 유지. boto3+S3+SSM 방식. |
| 2.7 | 로그인/평가/xlsx/비교 smoke 테스트 (실서비스) | 낮음 | — | 최소 1회 전체 플로우. |
| 2.8 | 운영 모니터링 (24~72h) — 감사 로그/메트릭/에러율 | 낮음 | — | CloudWatch + qa_audit_log. |

---

## 3. 결정 확정 (2026-04-17, 사용자 승인)

전 항목 **PL 권장안 그대로 채택**. 배포 금지는 유지 — 결정 반영은 **문서/스크립트로만**.

| # | 항목 | 확정안 | 비고 |
|---|---|---|---|
| 3.1 | 2.1 DynamoDB 생성 | **CDK** (`QaMultiTenantTables` 스택) | Dev6 `docs/CDK_DEPLOY_PLAN.md` 참조. 실행은 사용자 별도 승인 후 |
| 3.2 | 2.3 데이터 백필 | **비파괴 복사** | 기존 테이블 원본 보존, 신규 테이블에만 write. Dev2 `docs/BACKFILL_PLAN.md` |
| 3.3 | 2.4 Cognito 속성 추가 시점 | **신규 테넌트 추가 직전** | 현재 `kolon_default` 단일 — 2번째 테넌트 온보딩 전 스키마 확장. Pool ID 는 §5 placeholder |
| 3.4 | 2.5 기존 사용자 일괄 패치 | **N ≤ 20: Cognito 콘솔 bulk / N > 20: `AdminUpdateUserAttributes` 스크립트** | 실 사용자 수 확정 시 분기 결정 |
| 3.5 | 2.6 EC2 배포 | **인플레이스 + AMI 스냅샷** | boto3+S3+SSM 방식, IP `100.29.183.137` 유지 |
| 3.6 | 2.7 Smoke 테스트 승인자 | **코오롱 운영담당** | 실 담당자 이름은 §5 placeholder |

---

## 4. 롤백 플랜 (단계별)

| 단계 | 롤백 방법 | 소요 시간 |
|---|---|---|
| 2.1 신규 테이블 생성 | 테이블 삭제 | ~분 |
| 2.2 seed 삽입 | `qa_tenants/kolon_default` 1행 삭제 | 즉시 |
| 2.3 백필 | 신규 테이블 폐기, 기존 테이블 그대로 사용 | ~분 (비파괴 전제) |
| 2.4 Cognito 속성 | 속성 유지 (제거 불가) — 운영은 기존 Pool 로 계속 동작 | — |
| 2.5 사용자 패치 | 사용자별 `custom:tenant_id=null` 복원 | 중 |
| 2.6 EC2 배포 | 이전 코드 artifact 재배포 (AMI 스냅샷 전제) | ~10~30분 |

**핵심 원칙**: 2.3 (백필) 은 반드시 비파괴 방식 (신규 테이블로 복사). 운영 테이블 직접 변형 금지.

---

## 5. 운영 정보 placeholder (사용자 제공 대기)

스크립트/문서는 아래 placeholder 로 작성하고, 사용자 정보 제공 시 환경변수/SSM 으로 **외부화만** 수행. 코드 내 하드코딩 **절대 금지**.

| 항목 | placeholder | 외부화 방법 |
|---|---|---|
| 1. 운영 Cognito User Pool ID | `<COGNITO_POOL_ID>` | env `COGNITO_USER_POOL_ID` |
| 2. 운영 DynamoDB 기존 테이블명 | `<LEGACY_EVAL_TABLE>` / `<LEGACY_SESSION_TABLE>` | env `LEGACY_*_TABLE` or SSM `/qa/legacy/*` |
| 3. 운영 S3 버킷명 | `<LEGACY_S3_BUCKET>` / `<QA_BUCKET_NAME>` | env `QA_BUCKET_NAME` (Dev2/Dev6 공용) |
| 4. EC2 배포 경로 | `<EC2_DEPLOY_DIR>` (`/home/ec2-user/qa-pipeline` 또는 `/home/ubuntu/qa-pipeline`) | SSM `/qa/deploy/dir` |
| 5. 운영 사용자 수 | `<USER_COUNT>` | 운영담당 문의 — 20 임계값으로 §3.4 분기 |
| 6. 배포 가능 시간대 | `<DEPLOY_WINDOW>` | 운영담당 조율 |

---

## 6. 산출물 체크리스트 (Phase 1 진입 전 준비)

- [ ] `scripts/backfill/dynamo_backfill.py` — 기존 → 신규 테이블 복사 + `tenant_id=kolon_default` 주입 (**실행 금지, 코드만**)
- [ ] `scripts/backfill/s3_backfill.py` — 기존 prefix → `tenants/kolon_default/` 복사 (**실행 금지, 코드만**)
- [ ] `scripts/cognito/add_custom_attrs.py` — User Pool schema 확장 (**실행 금지, 코드만**)
- [ ] `scripts/cognito/bulk_update_users.py` — 기존 사용자에 `custom:tenant_id=kolon_default` (**실행 금지, 코드만**)
- [ ] `docs/PHASE1_CHECKLIST.md` — 배포 당일 체크리스트 (PL 작성)
- [ ] `docs/DEPLOY.md` — 이미 Dev6 작성 완료. Phase 1 전용 섹션 추가 권장.
- [ ] Dev1 리그레션 체크리스트 / Dev3 노드 QA 시나리오 / Dev4 seed JSON / Dev5 UI JWT 시나리오 — Dev 영역 산출물

---

## 7. 의사결정 트리 (사용자 승인 순서)

```
1. 운영 환경 정보 제공 (§5 미결 이슈 1~6) ──────┐
                                                ↓
2. 2.1 DynamoDB 생성 방법 선택 (a/b/c) ────────┐ │
                                                ↓ ↓
3. 2.3 백필 방식 확정 (비파괴 권장) ────────────┐│ │
                                                ↓↓ ↓
4. Dev 팀이 백필/Cognito 스크립트 코드 완성 ────┐│││
                                                 ↓↓↓↓
5. 사용자가 단계별 실행 승인 (PL 은 명령만 공유, 직접 실행 금지)
```

---

## 8. PL 추천

- Phase 1 을 **3개 마일스톤**으로 분할:
  - **M1 (설계 완성)**: 본 문서 + 스크립트 코드 + 체크리스트. 실행 0건.
  - **M2 (스테이징 리허설)**: 사용자가 스테이징 환경을 제공하면 Dev/PL 이 문서대로 리허설. 운영 영향 0.
  - **M3 (운영 반영)**: 사용자 명시 승인 하에 단계별 실행. 각 단계 종료 시 사용자 확인 후 다음 단계.
- M2 는 스테이징이 없으면 스킵 가능하나 리스크 증가.

---

## 9. 결론

Phase 1 은 **실 운영 리스크가 높은 작업** (데이터 백필/Cognito 스키마/EC2 재배포) 으로 구성. 본 단계에서는 **일체 실행 금지**, 문서/코드만 준비. 사용자의 단계별 승인 없이 어떤 AWS 리소스도 변경하지 않음.

§3 6개 결정 **확정 완료** — 스크립트/문서가 확정안에 맞춰 작성 가능. §5 운영 정보 6개는 placeholder 로 외부화하여 사용자 제공 시 즉시 주입 가능한 구조 유지.
