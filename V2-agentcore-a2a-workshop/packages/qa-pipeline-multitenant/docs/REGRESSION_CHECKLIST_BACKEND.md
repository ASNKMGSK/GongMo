# REGRESSION_CHECKLIST_BACKEND.md

> **오너**: Dev1 (backend-core)
> **상태**: Phase 1 사전 준비 — **실행 금지 (deploy / AWS 호출 / Cognito / DynamoDB 일체 금지)**
> **연관**: [PHASE1_MIGRATION_PLAN.md §2.7 Smoke 테스트](./PHASE1_MIGRATION_PLAN.md#phase-1-migration-steps)
> **우산 Task**: #11 Phase 1 사전준비

---

## 사용 방법

각 항목은 아래 3-열 구조로 작성된다:

- **체크**: 검증 대상 동작 한 줄 요약.
- **예상 증거**: pytest 파일명 / `app.user_middleware` 출력 / 응답 JSON 샘플 / 디렉토리 구조 등 — 실제 실행 시 이 값과 비교.
- **비고**: 관련 코드 경로, ARCHITECTURE.md 절, 주의사항.

실행 커맨드는 모두 다음 주석으로 시작:

```bash
# DO NOT RUN — Phase 1 freeze
```

§2.7 스모크 테스트 직전(Phase 1 해제 후)에만 이 체크리스트를 pytest + curl 로 실제 수행한다.

---

## 0. 사전 점검 — 환경 / 버전 고정

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 0.1 | Python 3.13 (`~/.conda/envs/py313/python.exe`) | `python --version` → `Python 3.13.x` | CLAUDE.md 규약 |
| 0.2 | requirements 의존성 설치 (pyjwt[crypto], fastapi, pydantic, sse-starlette, python-multipart, pymupdf) | `pip list | grep -E "pyjwt|fastapi"` | `qa-pipeline/requirements.txt` |
| 0.3 | 모든 Py 파일 py_compile 통과 | `py_compile` exit 0 | CI 게이트 |
| 0.4 | `sys.path` 에 `qa-pipeline/` prepend 확인 | `server.py` 상단 34~35줄 | lazy import 전제 |

```bash
# DO NOT RUN — Phase 1 freeze
cd packages/qa-pipeline-multitenant/qa-pipeline
~/.conda/envs/py313/python.exe -c "import py_compile; import pathlib
for f in pathlib.Path('.').rglob('*.py'):
    py_compile.compile(str(f), doraise=True)
print('all OK')"
```

---

## 1. 미들웨어 체인 (ARCHITECTURE.md §10.1) 리그레션

### 1.1 등록 순서 & 실행 흐름

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 1.1.1 | `app.user_middleware` 리스트 순서 = `[CORS, Tenant, RateLimit, Audit]` | 아래 출력 `['CORSMiddleware', 'TenantMiddleware', 'RateLimitMiddleware', 'AuditLogMiddleware']` | `server.py` 115~128줄 |
| 1.1.2 | 요청 흐름: CORS → Tenant → RateLimit → Audit → Routers | 더미 미들웨어 로그 in-out 역순 매칭 | 회귀 방지 테스트 회로 |

```python
# DO NOT RUN — Phase 1 freeze
# expected output: ['CORSMiddleware', 'TenantMiddleware', 'RateLimitMiddleware', 'AuditLogMiddleware']
from server import app
print([m.cls.__name__ for m in app.user_middleware])
```

### 1.2 TenantMiddleware exempt 경로

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 1.2.1 | `/ping /health /readyz /docs /redoc /openapi.json /ui` JWT 없이 200 | `GET /health` → `{"status":"healthy"}` | `middleware/tenant.py::_EXEMPT_PATHS` |
| 1.2.2 | exempt 경로에서 `request.state.tenant_id = ""` 주입 | `request.state` 속성 4종 모두 빈값 | 미들웨어 dispatch line 112 |

### 1.3 JWT 인증 거부 케이스 (401 UNAUTHORIZED)

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 1.3.1 | Authorization 헤더 없음 + LOCAL_TENANT_ID 미설정 → 401 | `{"error":{"code":"UNAUTHORIZED","message":"tenant_id not found...","tenant_id":null,"request_id":"<uuid>"}}` | `middleware/tenant.py` |
| 1.3.2 | Bearer 토큰 포맷 오류 (예: `Basic xxx`) → 401 | 같은 스키마, `message: "invalid authorization token"` | `_extract_bearer` |
| 1.3.3 | JWT 디코드 실패 (손상된 토큰) → 401 | `message: "invalid authorization token"` | `_decode_jwt` except |
| 1.3.4 | JWT 있지만 `custom:tenant_id` claim 누락 → 401 | `message: "tenant_id not found..."` | claims.get fallback |
| 1.3.5 | `custom:tenant_id` 가 빈 문자열 → 401 | 같은 스키마 | `_valid_tenant_id` |

예상 응답 샘플 (§10.2):
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "tenant_id not found — Authorization: Bearer <JWT> required",
    "tenant_id": null,
    "request_id": "a1b2c3d4e5f6..."
  }
}
```

### 1.4 LOCAL_TENANT_ID 폴백

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 1.4.1 | `LOCAL_TENANT_ID=kolon_default` 환경에서 JWT 없이 `/api/me` 200 | `{"tenant_id":"kolon_default","role":"member","email":"",...}` | 개발 편의용, 프로덕션 unset 필수 |
| 1.4.2 | 환경변수에 유효하지 않은 값 (`KOLON` 대문자) → 401 | `_valid_tenant_id` 거부 | 대소문자 정책 |

### 1.5 admin + X-Tenant-Override

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 1.5.1 | non-admin + X-Tenant-Override 헤더 → 403 TENANT_MISMATCH | `{"error":{"code":"TENANT_MISMATCH","message":"X-Tenant-Override requires role=admin",...}}` | tenant_id 는 원 요청자 값 |
| 1.5.2 | admin + 유효한 override → 정상 200, `request.state.tenant_id = override_tid`, `tenant_override = True` | `/api/me` 응답 `"override":true` | 전환 후 원 요청자 claim 은 `tenant_claims` 로 보존 |
| 1.5.3 | admin + 유효하지 않은 override (공백/대문자) → 400 INVALID_REQUEST | `{"error":{"code":"INVALID_REQUEST","message":"invalid X-Tenant-Override: 'BAD'",...}}` | `_valid_tenant_id` |

### 1.6 tenant_id 정규식 (`^[a-z0-9_]{2,64}$`)

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 1.6.1 | 소문자+숫자+언더스코어 2~64자 허용 (`kolon_default`, `acme_co`, `t2`) | 200 | Dev4 와 통일 |
| 1.6.2 | 대문자 포함 거부 (`Kolon`, `ACME`) | 401 | `middleware/tenant.py::_TENANT_ID_RE` |
| 1.6.3 | 1자 거부 (`a`) | 401 | min_length=2 |
| 1.6.4 | 특수문자 포함 거부 (`kolon-default`, `kolon.default`) | 401 | 하이픈 금지 |
| 1.6.5 | 65자 이상 거부 | 401 | max_length=64 |

```python
# DO NOT RUN — Phase 1 freeze
import re
_RE = re.compile(r"^[a-z0-9_]{2,64}$")
for tid in ["kolon_default", "a", "Kolon", "kolon-x", "x"*65]:
    print(tid, bool(_RE.match(tid)))
# 기대: T, F, F, F, F
```

### 1.7 전파된 request.state 속성

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 1.7.1 | `request.state.tenant_id: str` 주입 | `/api/me` 200 에 정상 반영 | 모든 후속 라우터가 사용 |
| 1.7.2 | `request.state.tenant_role: str` ("admin"/"") 주입 | `/api/me.role` 에서 "admin"/"member" 매핑 | me.py 정규화 |
| 1.7.3 | `request.state.tenant_claims: dict` (전체 JWT) | audit_log 에서 사용자 식별에 사용 | `_user_id_from_claims` |
| 1.7.4 | `request.state.tenant_override: bool` 주입 | `/api/me.override` 반영 | admin 전용 헤더 |

---

## 2. 에러 응답 규격 (ARCHITECTURE.md §10.2) 리그레션

### 2.1 envelope 구조

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 2.1.1 | 모든 4xx/5xx 응답이 `{"error":{"code","message","tenant_id","request_id"}}` 형식 | JSON 파싱 후 필드 4종 전부 존재 | `middleware/errors.py::error_response` |
| 2.1.2 | 성공 200 응답은 envelope 없음 (payload 평문) | `/api/me` 200 은 top-level 에 `tenant_id/role/email/...` | 에러만 envelope |

예상 에러 응답 샘플:
```json
{
  "error": {
    "code": "TENANT_MISMATCH",
    "message": "admin role required",
    "tenant_id": "acme_co",
    "request_id": "r-7f3e2a1c"
  }
}
```

### 2.2 HTTPException → code 매핑 (server.py exception_handler)

| HTTP | code | 예시 상황 | 예상 증거 |
|---|---|---|---|
| 400 | INVALID_REQUEST | `raise HTTPException(400, "...")` | `/admin/tenants` 잘못된 payload |
| 401 | UNAUTHORIZED | 미들웨어 외부에서 수동 401 | (드묾) |
| 403 | TENANT_MISMATCH | `require_admin` 실패 | `/api/tenants` non-admin |
| 404 | TENANT_NOT_FOUND | 향후 `/admin/tenants/{tid}` | 현재 미사용 |
| 429 | RATE_LIMITED | Dev6 RateLimitMiddleware | `Retry-After` 헤더 포함 |
| 500 | INTERNAL | 미처리 HTTPException 500 | `put_config` 실패 |

### 2.3 비 HTTPException 예외 처리

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 2.3.1 | `RequestValidationError` → 400 INVALID_REQUEST | Pydantic 스키마 실패 시 | `server.py::_validation_exception_handler` |
| 2.3.2 | 미처리 `Exception` → 500 INTERNAL | ZeroDivision 같은 버그 | `_unhandled_exception_handler` |
| 2.3.3 | `TimeoutError` (pipeline) → 504 (라우터 로컬) | `/evaluate` timeout payload | exception handler 거치지 않음 (라우터가 먼저 JSONResponse 반환) |

### 2.4 tenant_id / request_id 필드 동작

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 2.4.1 | 인증 전 401 응답: `tenant_id: null` | `/api/me` without JWT | `_tenant_id_of` |
| 2.4.2 | 인증 후 403 응답: `tenant_id: "<원 요청자>"` | non-admin 403 | `request.state.tenant_id` 에서 추출 |
| 2.4.3 | 매 요청마다 `request_id` 다름 (X-Request-ID 헤더 미제공 시) | uuid4 hex 32 chars | `_request_id` fallback |
| 2.4.4 | `X-Request-ID` 헤더 제공 시 그 값 echo | 헤더 `abc-123` → 응답 `"request_id":"abc-123"` | `_resolve_request_id` |
| 2.4.5 | 동일 요청 내에서 `request_id` 안정 (여러 번 호출해도 같은 값) | `tenant_context` 2회 호출 시 a==b | request.state 캐시 |

---

## 3. 라우터 엔드포인트 리그레션 (15개)

### 3.1 `/evaluate` 계열

| # | 엔드포인트 | 체크 | 예상 증거 |
|---|---|---|---|
| 3.1.1 | `POST /evaluate` | tenant_ctx 주입 → graph 실행 → 응답에 `tenant_id` 포함 | `{"status":"completed","tenant_id":"kolon_default","elapsed_seconds":...}` |
| 3.1.2 | `POST /evaluate/stream` | SSE 이벤트 스트림, 첫 `status` event 에 `tenant_id` | `event: status\ndata: {"node":"__start__","tenant_id":"kolon_default",...}` |
| 3.1.3 | `GET /evaluate/stream` | 쿼리 파라미터 version, 동일 SSE | 위와 동일 |
| 3.1.4 | `POST /evaluate/csv-compatible` | DB I/O 포맷 {ID, CALL_SEQ, CDATE, UID, CONTENT} 수용, 응답에 tenant_id | `to_csv_compatible` 변환 후 + tenant_id |
| 3.1.5 | `POST /evaluate/pentagon` | Pentagon 5축, 응답에 tenant_id | `{CONTENT:"..."}` → 5점수 + tenant_id |
| 3.1.6 | 전 엔드포인트 초기 state keys 12개 (original 11 + tenant) | `sorted(state.keys())` 일치 | `_build_initial_state` |

### 3.2 `/analyze-compare` 계열

| # | 엔드포인트 | 체크 | 예상 증거 |
|---|---|---|---|
| 3.2.1 | `POST /analyze-compare` | `left_result`/`right_result` 필수, 응답 tenant_id | `{"status":"success","analysis":"...","tenant_id":"..."}` |
| 3.2.2 | `POST /analyze-manual-compare` (manual_rows) | 결정적 경로 (LLM skip), 응답 tenant_id | `{"status":"success","summary":{...},"rows":[...],"tenant_id":"..."}` |
| 3.2.3 | `POST /analyze-manual-compare` (manual_evaluation 텍스트) | LLM 경로, 응답 tenant_id | 위와 유사, `raw` 포함 |

### 3.3 `/save-xlsx` 계열 — 테넌트 격리 ★

| # | 체크 | 예상 증거 |
|---|---|---|
| 3.3.1 | 저장 경로 `~/Desktop/QA평가표 테스트/{tenant_id}/{yyyy-mm-dd}/<filename>.xlsx` | `"relative_path":"kolon_default/2026-04-17/report.xlsx"` |
| 3.3.2 | 응답에 `relative_path`, `filename`, `subfolder`, `tenant_id`, `root`, `size`, `ok:true` 포함 | 각 키 존재 검증 |
| 3.3.3 | 동명 파일 재업로드 → `_(2)`, `_(3)` 접미사 | `"filename":"report_(2).xlsx"`, `"relative_path":".../report_(2).xlsx"` |
| 3.3.4 | path traversal 시도 (`../../evil.xlsx`) → 테넌트 디렉토리 내부에만 저장 (basename 정규화) | 저장 경로 실측 → `<root>/<tenant_id>/<date>/evil.xlsx` |
| 3.3.5 | subfolder `../etc` → `etc` 로 정규화 | 같은 테넌트 디렉토리 내 |
| 3.3.6 | 2-tenant (kolon_default vs acme_co) 저장 시 디렉토리 분리 | 두 테넌트 파일 경로 공통 prefix 없음 외엔 |
| 3.3.7 | `GET /save-xlsx/info` → 테넌트 디렉토리 존재/쓰기 가능 여부 | `{"tenant_id":"...","tenant_dir":"...","writable":true}` |

예상 응답:
```json
{
  "ok": true,
  "path": "/home/u/Desktop/QA평가표 테스트/kolon_default/2026-04-17/report.xlsx",
  "relative_path": "kolon_default/2026-04-17/report.xlsx",
  "filename": "report.xlsx",
  "subfolder": "2026-04-17",
  "tenant_id": "kolon_default",
  "root": "/home/u/Desktop/QA평가표 테스트",
  "size": 12345
}
```

### 3.4 `/wiki/*` (9개) — 테넌트 디렉토리 격리 ★

루트 디렉토리:
- 위키 페이지: `<PIPELINE_DIR>/wiki/tenants/{tenant_id}/`
- 원본 업로드: `<PIPELINE_DIR>/raw/tenants/{tenant_id}/`

| # | 엔드포인트 | 체크 | 예상 증거 |
|---|---|---|---|
| 3.4.1 | `GET /wiki/raw` | `raw/tenants/{tid}/` 파일 리스트 + ingest 상태 | `{"files":[...],"total":N,"pending":M,"tenant_id":"..."}` |
| 3.4.2 | `POST /wiki/upload` (multipart 또는 JSON) | `raw/tenants/{tid}/<name>.md` 저장, 프론트메터에 `tenant_id: {tid}` | `{"status":"uploaded","path":"raw/tenants/{tid}/...","tenant_id":"..."}` |
| 3.4.3 | `POST /wiki/ingest` | `wiki/tenants/{tid}/` 에 페이지 생성, `.ingested.json` 업데이트 | SSE `wiki_done` event, `tenant_id` 포함 |
| 3.4.4 | `DELETE /wiki/raw/{filename}` | `raw/tenants/{tid}/<filename>` 삭제 (path traversal 방어) | `{"status":"deleted","filename":"...","tenant_id":"..."}` |
| 3.4.5 | `GET /wiki/status` | `wiki/tenants/{tid}/` 집계 | `{"status":"built"|"empty","total_pages":N,"tenant_id":"..."}` |
| 3.4.6 | `GET /wiki/search?q=...` | 테넌트별 위키만 검색 | `{"results":[{...,"path":"category/page.md"}],"tenant_id":"..."}` |
| 3.4.7 | `POST /wiki/query` | 테넌트별 인덱스에서 질의 답변 | SSE `wiki_answer` event |
| 3.4.8 | `POST /wiki/save-answer` | `wiki/tenants/{tid}/queries/<title>.md` 저장, frontmatter 에 tenant_id | `{"status":"saved","path":"queries/...","tenant_id":"..."}` |
| 3.4.9 | 2-tenant 격리 — 한 테넌트 업로드 후 다른 테넌트 `/wiki/search` 로 조회 안 됨 | 결과 `results:[]` | 디렉토리 분리 |

### 3.5 `/api/me`, `/api/tenants`, `/admin/tenants`

| # | 엔드포인트 | 체크 | 예상 증거 |
|---|---|---|---|
| 3.5.1 | `GET /api/me` (JWT member) | `role:"member"`, email 추출 | `{"tenant_id":"...","role":"member","email":"a@x.co","override":false,"config":null|{...}}` |
| 3.5.2 | `GET /api/me` (JWT admin) | `role:"admin"` | 위와 유사, role 만 변경 |
| 3.5.3 | `GET /api/me` (미등록 테넌트) | 200 + `config:null` (선택지 A) | config 필드 null, UI 가 기본 브랜딩 |
| 3.5.4 | `GET /api/me` (override 중) | `"override":true`, tenant_id 는 override target | admin 전용 |
| 3.5.5 | `GET /api/tenants` (non-admin) | 403 TENANT_MISMATCH | envelope |
| 3.5.6 | `GET /api/tenants` (admin) | 200 `{"tenants":[...],"total":N}` | 전체 scan, 운영상 희귀 |
| 3.5.7 | `POST /admin/tenants` (non-admin) | 403 | — |
| 3.5.8 | `POST /admin/tenants` (admin, 정상 payload) | 201 `TenantConfig.to_dict()` | `put_config` 반환값 |
| 3.5.9 | `POST /admin/tenants` (admin, 대문자 tenant_id) | 422 (Pydantic pattern) | `^[a-z0-9_]+$` |
| 3.5.10 | `POST /admin/tenants` (admin, unknown industry) | 422 (Literal) | Dev4 스키마 |
| 3.5.11 | `POST /admin/tenants` (admin, `validate()` 실패) | 400 INVALID_REQUEST | `cfg.validate()` ValueError |

`/api/me` 응답 샘플:
```json
{
  "tenant_id": "kolon_default",
  "role": "admin",
  "email": "austinm17821@gmail.com",
  "override": false,
  "config": {
    "tenant_id": "kolon_default",
    "display_name": "코오롱산업",
    "industry": "industrial",
    "qa_items_enabled": [1,2,3,...],
    "branding": {...},
    "...": "..."
  }
}
```

### 3.6 기타 (헬스/AgentCore/UI)

| # | 엔드포인트 | 체크 | 예상 증거 |
|---|---|---|---|
| 3.6.1 | `GET /health` | 미들웨어 exempt, 200 | `{"status":"healthy","service":"qa-pipeline-multitenant","version":"2.0.0"}` |
| 3.6.2 | `GET /readyz` | graph 빌드 + 파일시스템 쓰기 가능 확인 | 200 `{"status":"ready",...}` 또는 503 |
| 3.6.3 | `GET /ping` | AgentCore Runtime 프로브 200 | `{"status":"ok"}` |
| 3.6.4 | `POST /invocations` | AgentCore entrypoint → `/evaluate` 위임 (tenant_ctx 필수) | JWT 또는 LOCAL_TENANT_ID 필요 |
| 3.6.5 | `GET /ui/{filename}` | 정적 HTML 서빙, 미들웨어 exempt | `qa_pipeline_reactflow.html` 200 |

---

## 4. LangGraph state 초기화 리그레션

### 4.1 12-key shape (Dev3 헬퍼 + 라우터 보강)

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 4.1.1 | `sorted(_build_initial_state(...).keys())` == original 11 keys + `{"tenant"}` | `[bedrock_model_id, completed_nodes, consultation_type, current_phase, customer_id, evaluations, llm_backend, next_node, node_timings, session_id, tenant, transcript]` | `routers/evaluate.py::_build_initial_state` |
| 4.1.2 | `state["tenant"]` == `{tenant_id, config, request_id}` 3-key dict | Dev3 TenantContext TypedDict | `routers/_tenant_deps.py::tenant_context` |
| 4.1.3 | `state["current_phase"]` == "init" (Dev3 헬퍼) | 문자열 비교 | `state.py::build_initial_state` |
| 4.1.4 | `state["evaluations"]` == `[]`, `completed_nodes` == `[]`, `node_timings` == `[]`, `next_node` == "" (라우터 setdefault) | 빈 컬렉션 / 빈 문자열 | 집계용 필드 |
| 4.1.5 | `state["llm_backend"]`, `state["bedrock_model_id"]` 키 존재 (None 허용) | Dev3 옵션 A 이후 항상 포함 | 원본 single-tenant 호환 |

```python
# DO NOT RUN — Phase 1 freeze
from routers.evaluate import _build_initial_state
s = _build_initial_state(
    body={'transcript': 't', 'llm_backend': 'bedrock'},
    tenant_ctx={'tenant_id': 'kolon_default', 'config': {}, 'request_id': 'r1'},
)
assert sorted(s.keys()) == [
    'bedrock_model_id','completed_nodes','consultation_type','current_phase',
    'customer_id','evaluations','llm_backend','next_node','node_timings',
    'session_id','tenant','transcript'
], s.keys()
```

### 4.2 `tenant_context()` 동작

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 4.2.1 | `require_tenant_id(request)` 빈값 → 401 HTTPException | 미들웨어 미등록 개발 실수 조기 감지 | `_tenant_deps.py` |
| 4.2.2 | Dev4 `tenant.get_config(tid)` 정상 → `config = cfg.to_dict()` | dict 반환 | 캐시 hit |
| 4.2.3 | `tenant.get_config(tid)` KeyError → `config = {}` | dict 반환 (비어있음) | PL 선택지 A 정책 |
| 4.2.4 | `tenant.get_config(tid)` 예외 (DynamoDB 불가) → `config = {}` + warning 로그 | 서비스 중단 없음 | fail-open |
| 4.2.5 | `request_id` 는 X-Request-ID 헤더 우선, 없으면 신규 uuid | 일관성 확인 | §10.2 정합 |

### 4.3 `tenant_config={}` 폴백 시 노드 동작 (Dev3 확인)

| # | 체크 | 예상 증거 | 비고 |
|---|---|---|---|
| 4.3.1 | 노드는 `state["tenant"]["config"]` 를 `.get()` 으로 접근 — 없으면 기본값 | 파이프라인 정상 완료 | Dev3 STATE_MIGRATION.md |
| 4.3.2 | `orchestrator_node` 가드: `tenant` 필드 누락 / `tenant_id` falsy → ValueError | 라우터 진입 전 401 로 차단되어 실제로 발생 안 함 | 이중 방어 |

---

## 5. 테넌트 격리 시나리오 (실행 X, 시나리오만)

### 5.1 2-Tenant /save-xlsx 격리

전제: `LOCAL_TENANT_ID` 를 Tenant A/B 로 전환하거나 JWT 2개 발급.

| # | 단계 | 예상 결과 |
|---|---|---|
| 5.1.1 | Tenant A (`kolon_default`) 로 `report.xlsx` 저장 | `<root>/kolon_default/2026-04-17/report.xlsx` 생성 |
| 5.1.2 | Tenant B (`acme_co`) 로 `report.xlsx` 저장 | `<root>/acme_co/2026-04-17/report.xlsx` 생성 — 서로 다른 파일 |
| 5.1.3 | A 가 B 의 파일에 접근 시도 — path traversal (`../acme_co/...`) | `basename` 정규화로 `<root>/kolon_default/2026-04-17/acme_co` 형태 (안전) |
| 5.1.4 | `ls <root>/kolon_default/` 와 `ls <root>/acme_co/` 각각 다른 내용 | 파일 리스트 disjoint |

### 5.2 /api/me override 시나리오

| # | 단계 | 예상 결과 |
|---|---|---|
| 5.2.1 | admin JWT (`custom:tenant_id=admin_t`, `custom:role=admin`) 으로 `/api/me` → `tenant_id="admin_t"`, `override=false` | 일반 응답 |
| 5.2.2 | 동일 admin JWT + `X-Tenant-Override: kolon_default` → `tenant_id="kolon_default"`, `override=true` | 전환 확인 |
| 5.2.3 | non-admin + `X-Tenant-Override: kolon_default` → 403 TENANT_MISMATCH | envelope |
| 5.2.4 | admin + `X-Tenant-Override: INVALID` (대문자) → 400 INVALID_REQUEST | `_valid_tenant_id` |

### 5.3 /wiki cross-tenant read 방어

| # | 단계 | 예상 결과 |
|---|---|---|
| 5.3.1 | Tenant A `/wiki/upload` 로 `secret.md` 업로드 | `raw/tenants/kolon_default/secret.md` 생성 |
| 5.3.2 | Tenant A `/wiki/ingest` → `wiki/tenants/kolon_default/.../secret.md` 생성 | 페이지 빌드 |
| 5.3.3 | Tenant B `/wiki/search?q=secret` | `results: []` (디렉토리 다름) |
| 5.3.4 | Tenant B `/wiki/raw` | A 의 파일 목록 없음 |
| 5.3.5 | Tenant B `/wiki/query` 질의 | `pages_content` 에서 A 내용 참조 안 함 |

### 5.4 end-to-end request_id 추적

| # | 단계 | 예상 결과 |
|---|---|---|
| 5.4.1 | UI 가 `X-Request-ID: trace-001` 헤더 첨부 | 서버 응답 envelope / graph trace / audit_log 모두 동일 UUID |
| 5.4.2 | 에러 응답 (예: 403) 후 로그에서 trace-001 grep | 미들웨어 → 라우터 → state → audit_log 순으로 연결 관찰 |
| 5.4.3 | 헤더 미제공 시 매 요청 신규 uuid | request_id 각각 고유 |

### 5.5 AMI 스냅샷 + EC2 인플레이스 배포 (PL 확정안 §3.5)

> **배포 정책**: CDK 재배포 금지 — boto3 + S3 + SSM 방식 인플레이스 배포 유지, IP `100.29.183.137` 고정.
> **참조**: `PHASE1_MIGRATION_PLAN.md §3.5`, `docs/DEPLOY.md` (Dev6), `feedback_qa_ec2_ip_preservation.md`.

| # | 단계 | 예상 결과 | 비고 |
|---|---|---|---|
| 5.5.1 | 배포 직전 — 현재 EC2 AMI 스냅샷 생성 (Dev6 주도) | AMI ID 기록, 롤백 포인트 확보 | `ec2 create-image --no-reboot` |
| 5.5.2 | `/health` `/readyz` 200 (배포 전 베이스라인) | `{"status":"healthy","version":"1.x"}` | 단일 테넌트 버전 |
| 5.5.3 | boto3+S3+SSM 인플레이스 배포 실행 (Dev6) | EC2 SSM send-command 성공, 서비스 재시작 로그 | `~/.conda/envs/py313/python.exe` |
| 5.5.4 | 배포 후 `/health` 200, `/readyz` 200 (그래프 빌드 + FS 쓰기 가능) | `{"version":"2.0.0","service":"qa-pipeline-multitenant"}` | 새 버전 확인 |
| 5.5.5 | `/ping` 200 (AgentCore Runtime 프로브) | `{"status":"ok"}` | 로드밸런서/프로브 안정성 |
| 5.5.6 | IP 고정 확인 — 퍼블릭 IP `100.29.183.137` 유지 | `curl http://100.29.183.137:<PORT>/health` 정상 | CDK 재배포 금지 원칙 |
| 5.5.7 | `POST /api/me` + `Authorization: Bearer <운영 JWT>` → 200 | 운영 Cognito 연동 확인 | **스모크 시 코오롱 운영담당 JWT 사용** |
| 5.5.8 | 스모크 1회: 로그인 → `/evaluate` → `/save-xlsx` → `/analyze-compare` 전체 플로우 | §2.7 의 체크리스트 그대로 | 코오롱 운영담당 승인 후 진행 |
| 5.5.9 | 실패 시 롤백 — AMI 스냅샷으로 인스턴스 복구, IP 연결 재확인 | 이전 버전 `/health` 200 | 5.5.1 의 AMI ID 사용 |
| 5.5.10 | 배포 후 24h 모니터링 — `qa_audit_log` 에러율, CloudWatch `EvaluationCount/FailureRate` | 에러율 < 베이스라인, FailureRate < 1% | §2.8 |

```bash
# DO NOT RUN — Phase 1 freeze
# 5.5.6 IP 확인 (Phase 1 해제 후에만)
curl -sS -o /dev/null -w "%{http_code}\n" "http://100.29.183.137:${PORT}/health"
# 예상: 200
```

**주의**:
- EC2 배포 스크립트 자체는 **Dev6 DEPLOY.md** 소유. Dev1 은 배포 **전후 HTTP 엔드포인트 동작** 만 검증 책임.
- 운영 Cognito / JWT / 테이블명은 **환경변수/SSM** 으로 외부화 (`COGNITO_USER_POOL_ID`, `LEGACY_EVAL_TABLE`, `/qa/deploy/dir` 등). 체크리스트 내 하드코딩 금지.
- 5.5.7~5.5.8 은 **코오롱 운영담당 승인 후** 1회 수행 (§2.7 Smoke 승인자 = 운영담당 확정).

---

## 6. 회귀 테스트 실행 (Phase 1 해제 후)

스모크 테스트는 `PHASE1_MIGRATION_PLAN.md §2.7` 절차에 따라 1회 수행.

```bash
# DO NOT RUN — Phase 1 freeze
# Phase 1 해제 후 ONLY
cd packages/qa-pipeline-multitenant/qa-pipeline
~/.conda/envs/py313/python.exe -m pytest ../tests/ -v
```

기대 pytest 대상 (Dev3 테스트 포함):
- `tests/test_tenant_middleware.py` (Dev1)
- `tests/test_state_propagation.py` (Dev3)
- `tests/test_integration_isolation.py` (PL)

---

## 7. 정기 재검증 주기

- **Phase 1 해제 직전**: 전체 0~5 섹션 실측 (수동 + pytest)
- **Phase 2 진입 시**: `/save-xlsx`, `/wiki/*` S3 전환 후 3.3, 3.4 재검증
- **Phase 5 진입 시**: IAM `aws:PrincipalTag/tenant_id` 적용 후 5.1, 5.3 cross-tenant IAM deny 추가 검증

---

## 8. 부록 — 커맨드 전체 동결 확인

본 문서의 모든 실행 커맨드는 주석 `# DO NOT RUN — Phase 1 freeze` 가 선행되어야 한다. grep 검사:

```bash
# DO NOT RUN — Phase 1 freeze
grep -n "^~/\.conda\|^cd packages\|^python\|^pytest\|^curl\|^aws " docs/REGRESSION_CHECKLIST_BACKEND.md
# 모든 히트가 코드펜스 내부이고 상단 4줄 이내 "DO NOT RUN" 주석이 있어야 함
```

---

**연관 문서**:
- [ARCHITECTURE.md](../ARCHITECTURE.md) §1, §6, §10.1, §10.2
- [PHASE1_MIGRATION_PLAN.md §2.7](./PHASE1_MIGRATION_PLAN.md)
- [STATE_MIGRATION.md](./STATE_MIGRATION.md) (Dev3)
- [DATA_ISOLATION.md](./DATA_ISOLATION.md) (Dev2)
- [TENANT_CONFIG.md](./TENANT_CONFIG.md) (Dev4)
- [UI_JWT_TEST_PLAN.md](./UI_JWT_TEST_PLAN.md) (Dev5)

**버전**: v1.0 (2026-04-17, Phase 1 사전 준비)
**작성자**: Dev1 (backend-core)
**검토자**: PL (pl-architect)
