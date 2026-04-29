# QA Pipeline — Multi-Tenant Edition

> **Status**: Phase 0 완료 (2026-04-17) — pytest 42/42 통과, 인터페이스 호환성 검증 완료
> **Base**: `packages/agentcore-agents/qa-pipeline/` (단일 테넌트 운영본)
> **Target**: Pool 모델 멀티테넌트 — JWT claim 기반 테넌트 식별
> **배포 정책**: EC2 인플레이스 배포 유지 (CDK 재배포 없음)

## 1. 개요

기존 단일 테넌트 QA 평가 파이프라인 (`packages/agentcore-agents/qa-pipeline/`) 을
**Pool 모델 멀티테넌트**로 전환한 새 구현체입니다.

- **격리 모델**: Pool (공유 DB + `tenant_id` 컬럼/프리픽스)
- **테넌트 식별**: Cognito JWT claim `custom:tenant_id`
- **기본 테넌트**: `kolon_default` (기존 데이터 백필 대상)
- **슈퍼어드민**: `custom:role=admin` + 헤더 `X-Tenant-Override` 로 타 테넌트 접근

## 2. 폴더 구조 (Phase 0 산출물 반영)

```
packages/qa-pipeline-multitenant/
├── README.md                          # 이 문서
├── ARCHITECTURE.md                    # ★ 인터페이스 계약 (Single Source of Truth)
│
├── docs/                              # 설계 문서
│   ├── TEST_REPORT.md                 # Phase 0.5 테스트 리포트 (42/42 pass)
│   ├── PHASE0_INTEGRATION_REPORT.md   # Phase 0 통합 리포트
│   ├── DATA_ISOLATION.md              # Dev2 — dynamo/s3/secrets/opensearch 가이드
│   ├── TENANT_CONFIG.md               # Dev4 — TenantConfig 3단계 온보딩
│   ├── STATE_MIGRATION.md             # Dev3 — 단일→멀티 state 마이그레이션
│   └── DEPLOY.md                      # Dev6 — EC2 인플레이스 배포 + CDK 적용
│
├── qa-pipeline/                       # 백엔드 (FastAPI + LangGraph)
│   ├── server.py                      # FastAPI 진입점 (미들웨어 체인 등록)
│   ├── config.py                      # 공용 SageMaker/App 설정
│   ├── requirements.txt
│   ├── graph.py                       # LangGraph 빌드
│   ├── state.py                       # QAState + TenantContext (Dev3)
│   │
│   ├── middleware/                    # Dev1 + Dev6
│   │   ├── tenant.py                  # JWT → request.state.tenant_id
│   │   ├── rate_limit.py              # 테넌트당 분당 N회
│   │   ├── audit_log.py               # /evaluate* 호출 감사 로그
│   │   └── errors.py                  # error_response 공용 빌더 (§10.2)
│   │
│   ├── routers/                       # Dev1
│   │   ├── _tenant_deps.py            # require_tenant_id / require_admin 헬퍼
│   │   ├── schemas.py                 # Pydantic 요청/응답 모델
│   │   ├── evaluate.py                # /evaluate, /evaluate/stream, /evaluate/pentagon
│   │   ├── xlsx_save.py               # /save-xlsx (테넌트 디렉토리)
│   │   ├── compare.py                 # /analyze-compare, /analyze-manual-compare
│   │   ├── wiki.py                    # /wiki/* (테넌트 디렉토리)
│   │   └── me.py                      # /api/me, /api/tenants, /admin/tenants
│   │
│   ├── tenant/                        # Dev4
│   │   ├── config.py                  # TenantConfig dataclass
│   │   ├── store.py                   # DynamoDB qa_tenants CRUD + LRU 5분
│   │   └── presets/                   # 업종 프리셋 (industrial/insurance/ecommerce/generic)
│   │
│   ├── data/                          # Dev2 — 테넌트 격리 헬퍼
│   │   ├── dynamo.py                  # tenant_query/get/put/delete/update
│   │   ├── s3.py                      # tenants/{tid}/... prefix 강제
│   │   ├── secrets.py                 # /qa/{tid}/{name} 경로
│   │   └── opensearch.py              # 자동 tenant_id term 필터
│   │
│   ├── nodes/                         # Dev3 — LangGraph 노드 (20개)
│   │   ├── orchestrator.py            # entry guard: tenant 누락 시 ValueError
│   │   ├── dialogue_parser.py
│   │   ├── greeting.py / courtesy.py / scope.py / understanding.py / proactiveness.py
│   │   ├── mandatory.py / work_accuracy.py / incorrect_check.py / ...
│   │   ├── consistency_check.py / report_generator.py / score_validation.py
│   │   ├── retrieval.py / wiki_compiler.py / ...
│   │   └── skills/                    # 공용 스킬 (node_context 에 tenant 필드)
│   │
│   ├── prompts/                       # Dev4 — 테넌트 오버라이드 로더
│   │   ├── __init__.py                # load_prompt(name, *, tenant_id, ...)
│   │   ├── _common_preamble.sonnet.md
│   │   └── tenants/{tenant_id}/       # 테넌트별 프롬프트 오버라이드
│   │
│   └── observability/                 # Dev6
│       └── metrics.py                 # CloudWatch Dimension=TenantId
│
├── chatbot-ui/                        # 프론트엔드 (Dev5)
│   └── qa_pipeline_reactflow.html     # React 18 UMD + tenantFetch + 스위처 + 브랜딩
│
├── cdk/                               # 인프라 (Dev6)
│   ├── app.py
│   ├── cdk.json
│   ├── requirements.txt
│   └── stacks/
│       ├── qa_tenant_table_stack.py   # DynamoDB 5종 (PITR, TTL)
│       ├── qa_tenant_iam_stack.py     # aws:PrincipalTag/tenant_id 기반 Role
│       └── qa_observability_stack.py  # LogGroup + MetricFilter 4종
│
└── tests/                             # 42 테스트 (정적+단위+통합)
    ├── conftest.py
    ├── pytest.ini
    ├── test_tenant_middleware.py      # Dev1 — JWT/401/403/override
    ├── test_tenant_config.py          # Dev4 — validate/to_dict/프리셋
    ├── test_data_isolation.py         # Dev2 — tenant_id 가드
    ├── test_state_propagation.py      # Dev3 — require_tenant/build_initial_state
    ├── test_prompt_loader.py          # Dev4 — 오버라이드 우선순위
    └── test_integration_isolation.py  # PL — 2-테넌트 격리 시뮬
```

## 3. 아키텍처 한 장 요약

```
   ┌─────────────┐   JWT (custom:tenant_id)   ┌──────────────────────┐
   │  Browser    │ ─────────────────────────▶ │  FastAPI (server.py) │
   │  (React UI) │                            │                      │
   └─────────────┘                            │  CORS                │
                                              │   ↓                  │
                                              │  TenantMiddleware    │  → request.state.tenant_id
                                              │   ↓                  │
                                              │  RateLimitMiddleware │  → 429 RATE_LIMITED
                                              │   ↓                  │
                                              │  AuditLogMiddleware  │  → qa_audit_log
                                              │   ↓                  │
                                              │  Routers             │
                                              │   ↓                  │
                                              │  LangGraph (graph.py)│  → state["tenant"] 전파
                                              │   ↓                  │
                                              │  20 Nodes (nodes/)   │  → load_prompt(..., tenant_id=...)
                                              └──────────────────────┘
                                                    │
                       ┌────────────────────────────┼─────────────────────────┐
                       ▼                            ▼                         ▼
              ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────────┐
              │  DynamoDB        │     │  S3              │     │  Secrets Manager     │
              │  PK=tenant_id    │     │  tenants/{tid}/  │     │  /qa/{tid}/{name}    │
              │  (5 tables)      │     │  raw/reports/... │     │                      │
              └──────────────────┘     └──────────────────┘     └──────────────────────┘
```

### 미들웨어 체인 순서 (ARCHITECTURE.md §10.1)
요청 도착 → **CORS → Tenant → RateLimit → Audit → Routers** → 응답 발송

### 에러 응답 규격 (ARCHITECTURE.md §10.2)
모든 4xx/5xx 는 `{"error": {"code", "message", "tenant_id", "request_id"}}` 형식.

## 4. 팀 구성 (PL 포함 7명)

| 역할 | 이름 | 담당 | Task |
|---|---|---|---|
| **PL** | `pl-architect` | 아키텍처 결정, 인터페이스 계약, 통합 검증 | #1, #9 |
| **Dev1** | `backend-core` | FastAPI middleware, 라우터 테넌트화, server.py, error_response | #2 |
| **Dev2** | `data-isolation` | DynamoDB/S3/Secrets/OpenSearch 격리 레이어 | #3 |
| **Dev3** | `pipeline-state` | LangGraph state, 20개 노드 context 전파 | #4 |
| **Dev4** | `tenant-config` | TenantConfig, 4개 업종 프리셋, load_prompt | #5 |
| **Dev5** | `frontend` | UI tenantFetch, 스위처, 브랜딩, mock 폴백 | #6 |
| **Dev6** | `devops` | CDK 3스택, Rate Limit, 감사 로그, CloudWatch | #7 |

## 5. 기준 결정 사항 (Phase 0 채택안)

| 항목 | 채택 |
|---|---|
| 격리 모델 | **Pool** (공유 DB + `tenant_id` 컬럼) |
| 테넌트 식별 | **JWT claim** (`custom:tenant_id`) |
| Cognito | **단일 풀 + custom attribute** |
| 컴퓨트 | **공유 EC2** (Phase 1~2), 대형 테넌트만 전용 런타임 |
| 기본 테넌트 | `kolon_default` (기존 데이터 백필) |

## 6. Quickstart

### 백엔드 로컬 실행
```bash
cd packages/qa-pipeline-multitenant/qa-pipeline
pip install -r requirements.txt
export LOCAL_TENANT_ID=kolon_default
~/.conda/envs/py313/python.exe server.py
```

### 테스트
```bash
cd packages/qa-pipeline-multitenant
~/.conda/envs/py313/python.exe -m pytest tests/ -v
# 42/42 passed
```

### UI
```
브라우저에서 packages/qa-pipeline-multitenant/chatbot-ui/qa_pipeline_reactflow.html 열기
(JWT 없을 시 LOCAL_TENANT_MOCK 폴백으로 단독 미리보기 가능)
```

### CDK (synth 전용, deploy 금지)
```bash
cd packages/qa-pipeline-multitenant/cdk
pip install -r requirements.txt
cdk synth
```

## 7. 주요 엔드포인트

| Method | Path | 인증 | 용도 |
|---|---|---|---|
| GET | `/health` | — | 헬스체크 (미들웨어 allowlist) |
| GET | `/api/me` | JWT | `{tenant_id, role, config}` |
| GET | `/api/tenants` | admin | 테넌트 리스트 (슈퍼어드민) |
| POST | `/admin/tenants` | admin | TenantConfig upsert |
| POST | `/evaluate` | JWT | 동기 평가 |
| POST | `/evaluate/stream` | JWT | SSE 스트리밍 |
| POST | `/evaluate/pentagon` | JWT | CSV 호환 |
| POST | `/analyze-compare` / `/analyze-manual-compare` | JWT | 판정 비교 |
| POST | `/save-xlsx` | JWT | 테넌트 디렉토리 저장 |
| GET/POST/DELETE | `/wiki/*` | JWT | 테넌트별 위키 |

## 8. DynamoDB 테이블 (Dev2 ↔ Dev6 합의)

| 테이블 | PK | SK | 용도 |
|---|---|---|---|
| `qa_tenants` | `tenant_id` | — | 테넌트 메타/Config |
| `qa_evaluations_v2` | `tenant_id` | `evaluation_id` | 평가 결과 |
| `qa_sessions` | `tenant_id` | `session_id` | 세션 상태 |
| `qa_audit_log` | `tenant_id` | `timestamp` | 감사 로그 (TTL 30일) |
| `qa_quota_usage` | `tenant_id` | `yyyy-mm` | 월별 사용량 (Rate Limit) |

## 9. 작업 규칙

1. **모든 팀원은 `ARCHITECTURE.md` 의 인터페이스 계약을 따른다.**
2. 변경 충돌이 예상되면 SendMessage 로 사전 합의.
3. 작업 완료 시 TaskUpdate 로 상태 갱신, PL 에 보고.
4. 기존 `packages/agentcore-agents/qa-pipeline/` 는 **수정 금지** — 참조/복사만.
5. Python: `~/.conda/envs/py313/python.exe` (시스템 Python 금지).
6. CDK deploy 자동 실행 금지 (EC2 인플레이스 배포 유지).

## 10. Phase 1+ 미해결 이슈

1. **기존 데이터 백필** — `tenant_id=kolon_default` 일괄 주입 스크립트 작성 (DynamoDB + S3).
2. **Cognito 마이그레이션** — `custom:tenant_id` / `custom:role` 기존 사용자 주입 절차.
3. **EC2 인플레이스 배포** — boto3+S3+SSM 방식으로 `100.29.183.137` 에 반영 (IP 유지).
4. **IAM Role 승격 (Phase 5)** — `qa-multitenant-app-role` 을 EC2 인스턴스 프로필로 교체.
5. **업종 프리셋 3종** — banking/healthcare/telco 온보딩 시 추가.
6. **OpenSearch 통합 테스트** — 실 쿼리 경로 추가 (현재는 import + 가드만 검증).

## 11. 참조

- `ARCHITECTURE.md` — 인터페이스 계약 (권위 문서)
- `docs/PHASE0_INTEGRATION_REPORT.md` — Phase 0 산출물 요약
- `docs/TEST_REPORT.md` — 42개 테스트 결과 + 결함 라우팅 이력
- `docs/DATA_ISOLATION.md` / `docs/TENANT_CONFIG.md` / `docs/STATE_MIGRATION.md` / `docs/DEPLOY.md`
- 기존 단일 테넌트: `packages/agentcore-agents/qa-pipeline/`
