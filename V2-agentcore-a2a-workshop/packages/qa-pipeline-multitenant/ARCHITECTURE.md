# Multi-Tenant Architecture — Interface Contract

> **이 문서는 전 팀원이 참조하는 단일 진실 원천(Single Source of Truth)이다.**
> 인터페이스 변경은 PL 의 승인 후 이 문서를 먼저 업데이트하고 구현한다.

---

## 1. 테넌트 식별 흐름

```
브라우저 → Cognito Login → JWT (custom:tenant_id=kolon)
                              ↓
                          Authorization: Bearer <JWT>
                              ↓
                   FastAPI middleware/tenant.py
                              ↓
                   request.state.tenant_id = "kolon"
                              ↓
              모든 라우터 / LangGraph state / DB 쿼리
```

- **권한 없는 요청**: tenant_id 없으면 401 Unauthorized
- **슈퍼 어드민**: `custom:role=admin` + 헤더 `X-Tenant-Override: <tid>` 허용
- **로컬 개발**: 환경변수 `LOCAL_TENANT_ID=kolon_default` 폴백

---

## 2. TenantConfig 스키마 (Dev4 owner)

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class TenantConfig:
    tenant_id: str                              # PK, 영문/숫자/언더스코어
    display_name: str                           # "코오롱산업"
    industry: Literal[
        "industrial", "insurance", "ecommerce",
        "banking", "healthcare", "telco", "generic"
    ]
    qa_items_enabled: list[int]                 # 활성화된 평가 항목 번호 (1~21)
    score_overrides: dict[int, int]             # {item_no: max_score 변경}
    default_models: dict[str, str]              # {"primary": "sonnet-4", "fast": "haiku-4-5"}
    prompt_overrides_dir: str | None            # "prompts/tenants/kolon/" or None
    branding: dict                              # {"logo_url", "primary_color", "secondary_color"}
    rate_limit_per_minute: int = 60
    storage_quota_gb: int = 10
    created_at: str                             # ISO8601
    updated_at: str
    is_active: bool = True
```

**저장 위치**: DynamoDB `qa_tenants` (PK=`tenant_id`)
**캐시**: 메모리 LRU 5분 TTL (`tenant.store.get_config(tid)`)

**메서드 계약 (Dev4 확정)**:
- `TenantConfig.to_dict()` — DynamoDB/JSON 직렬화. `score_overrides` 키는 문자열로 정규화 (DynamoDB 요구).
- `TenantConfig.from_dict(d)` — `score_overrides` 키를 `int` 로 복원. 누락 필수 키는 `KeyError`.
- `TenantConfig.validate()` — `ValueError` (tenant_id `^[a-z0-9_]{2,64}$`, industry Literal, qa_items 1~21 중복금지, score 1~100, rate/storage 1~100000).

**Store 계약 (Dev4 확정)**:
- `get_config(tid) → TenantConfig` / 없으면 `KeyError` / 빈 tid `ValueError`
- `put_config(config) → TenantConfig` — `validate()` 후 `updated_at` 자동 갱신, 캐시 반영
- `list_configs() → list[TenantConfig]` — 슈퍼어드민 전용 (일반 요청 경로 금지)
- `invalidate_cache(tenant_id=None)` — None=전체 flush

---

## 3. DynamoDB 테이블 (Dev2 owner)

| 테이블 | PK | SK | 용도 |
|---|---|---|---|
| `qa_tenants` | `tenant_id` | — | 테넌트 메타/Config |
| `qa_evaluations_v2` | `tenant_id` | `evaluation_id` | 평가 결과 |
| `qa_sessions` | `tenant_id` | `session_id` | 세션 상태 |
| `qa_audit_log` | `tenant_id` | `timestamp` | 감사 로그 (TTL 30일) |
| `qa_quota_usage` | `tenant_id` | `yyyy-mm` | 월별 사용량 (Rate Limit) |

**모든 GSI 도 `tenant_id` 가 첫 키**.
**모든 쿼리는 `tenant_id` 필수** — 헬퍼 `dynamo.tenant_query(table, tid, ...)` 강제 사용.

---

## 4. S3 prefix 구조 (Dev2 owner)

```
qa-pipeline-bucket/
└── tenants/
    └── {tenant_id}/
        ├── raw/{date}/{filename}.txt
        ├── reports/{date}/{eval_id}.xlsx
        ├── prompts-override/        # 테넌트별 프롬프트 백업
        └── exports/
```

**IAM 정책**: 테넌트 격리는 IAM Condition `${aws:PrincipalTag/tenant_id}` 로 강제 (Phase 5).

---

## 5. LangGraph state 확장 (Dev3 owner)

```python
# state.py
from typing import TypedDict, Optional

class TenantContext(TypedDict):
    tenant_id: str
    config: dict          # TenantConfig.to_dict()
    request_id: str       # 추적용

class QAState(TypedDict, total=False):
    tenant: TenantContext              # ⭐ 신규 — 모든 노드 read-only 접근
    transcript: str
    qa_plan: dict
    item_evaluations: dict
    final_report: dict
    # ... 기존 필드
```

**전 노드는 `state["tenant"]` 만 읽고 절대 수정하지 않는다.**
노드 함수 시그니처:
```python
def evaluate_xxx(state: QAState) -> QAState:
    tid = state["tenant"]["tenant_id"]
    config = state["tenant"]["config"]
    # ... 평가 로직
```

---

## 6. 라우터 인터페이스 (Dev1 owner)

| 엔드포인트 | 요청 | 응답 | 테넌트 |
|---|---|---|---|
| `POST /evaluate` | `{transcript, llm_backend, ...}` | `{evaluation_id, ...}` | JWT |
| `POST /evaluate/stream` | 동상 | SSE | JWT |
| `POST /save-xlsx` | multipart (file, filename) | `{path}` | JWT |
| `GET /api/me` | — | `{tenant_id, role, config}` | JWT |
| `GET /api/tenants` | — | 슈퍼어드민 전용 | JWT |
| `POST /admin/tenants` | `TenantConfig` | 생성 | role=admin |

**xlsx 저장 경로**: `~/Desktop/QA평가표 테스트/{tenant_id}/{yyyy-mm-dd}/`

**`/api/me` config 폴백 정책 (Dev1 + Dev5 합의)**: JWT tenant_id 는 있으나 `qa_tenants` 에 레코드가 없는 경우 `200 OK` + `config: null` 반환 (운영 drift 허용). `TENANT_NOT_FOUND` (§10.2) 는 명시적 조회 엔드포인트 (예: 향후 추가될 `/admin/tenants/{tid}`) 에서만 사용.

---

## 7. 프롬프트 오버라이드 로더 (Dev4 owner)

```python
# prompts/__init__.py (Dev4 확정)
def load_prompt(
    name: str,
    *,
    tenant_id: str,
    include_preamble: bool = True,
    backend: str | None = None,
) -> str:
    """우선순위 4단계:
    1. prompts/tenants/{tenant_id}/{name}.sonnet.md
    2. prompts/tenants/{tenant_id}/{name}.md
    3. prompts/{name}.sonnet.md
    4. prompts/{name}.md
    없으면 FileNotFoundError.

    include_preamble : consistency_check/report_generator 공용 preamble 토글 (default True)
    backend          : 예약 (현재 accept-and-ignore)
    """
```

**모든 호출처는 `tenant_id` keyword-only 필수**, 기본값 금지.

**업종 프리셋 (Dev4 확정)**: Phase 0 는 4종 구현 — `industrial` / `insurance` / `ecommerce` / `generic`.
`banking` / `healthcare` / `telco` 는 `Industry` Literal 에 예약만 되어 있으며 Phase 3 온보딩 시점 추가.
`get_preset(industry)` 미등록 → `KeyError`.

---

## 8. UI 인터페이스 (Dev5 owner)

- 로그인 후 `/api/me` 호출 → `tenantContext` 전역 상태
- 모든 API 호출에 `Authorization: Bearer <JWT>` 자동 첨부
- 헤더 영역: 테넌트 표시명 + 로고 + 색상 테마 적용
- 슈퍼어드민: 우측 상단 테넌트 스위처 드롭다운 (`X-Tenant-Override` 헤더)
- 평가 이력 테이블에 `tenant_id` 컬럼 (어드민만 노출)

---

## 9. 감사 로그 / Rate Limit (Dev6 owner)

- 모든 `/evaluate*` 호출은 `qa_audit_log` 에 1행 기록
- 미들웨어 `rate_limit.py` — 테넌트당 분당 N회 (Config 기반)
- CloudWatch 메트릭: `Dimension=TenantId`
  - `EvaluationCount`, `TokenUsage`, `LatencyP95`, `FailureRate`

---

## 10. Phase 의존도 (작업 순서)

```
Phase 0 (전원, 1주):  ARCHITECTURE.md 합의 + 스키마 결정
   ↓
Phase 1 (Dev1+Dev3, 2주):  middleware + state context — 다른 모든 작업의 전제
   ↓
Phase 2 (Dev2, 2주):  DynamoDB/S3 격리 — 병렬: Dev4 TenantConfig
   ↓
Phase 3 (Dev4, 2주):  업종 프리셋 + 프롬프트 오버라이드
   ↓
Phase 4 (Dev5, 2주):  UI — 백엔드 API 완성 의존
   ↓
Phase 5 (Dev6, 1주):  관찰성 + Rate Limit
   ↓
Phase 6 (PL+전원, 1주):  통합 테스트 + 마이그레이션
```

---

## 10.1 미들웨어 체인 순서 (Dev1 + Dev6 합의)

FastAPI 미들웨어는 아래 순서로 장착 (위 → 아래 = 요청 도착 → 응답 발송):

```
CORSMiddleware                              # Dev1 (기존)
  ↓
TenantMiddleware                            # Dev1  — JWT → request.state.tenant_id / role
  ↓
RateLimitMiddleware                         # Dev6  — tenant_id 기반 분당 N회
  ↓
AuditLogMiddleware                          # Dev6  — /evaluate* 1행 기록
  ↓
Routers
```

- `TenantMiddleware` 가 401 을 반환하면 이후 체인은 실행되지 않는다.
- Rate limit 초과 시 `429 Too Many Requests` + `Retry-After` 헤더.
- AuditLog 는 응답 phase 에 기록 (status_code 포함).

## 10.2 에러 응답 규격 (Dev1 owner)

모든 4xx/5xx 응답은 JSON 형식:

```json
{
  "error": {
    "code": "TENANT_NOT_FOUND" | "UNAUTHORIZED" | "RATE_LIMITED" | "INTERNAL",
    "message": "human readable",
    "tenant_id": "kolon | null",
    "request_id": "uuid"
  }
}
```

| HTTP | code | 조건 |
|---|---|---|
| 401 | `UNAUTHORIZED` | JWT 누락/검증 실패 |
| 403 | `TENANT_MISMATCH` | 리소스의 tenant_id 와 요청 tenant_id 불일치 |
| 404 | `TENANT_NOT_FOUND` | `qa_tenants` 조회 실패 |
| 429 | `RATE_LIMITED` | 분당 한도 초과 |
| 500 | `INTERNAL` | 비정상 예외 |

## 11. 합의 규칙

1. **이 문서를 깨는 변경**은 PL 승인 후 PR 형태로 본 문서 먼저 갱신.
2. **스키마 변경**은 영향받는 Dev 전원에게 SendMessage 로 통보.
3. **새 의존성 추가**는 PL 승인 필요 (`requirements.txt`).
4. **기존 단일 테넌트 코드 (`packages/agentcore-agents/qa-pipeline/`) 수정 금지**.
5. **테스트 환경**: Python 3.13 (`~/.conda/envs/py313/python.exe`).

---

## 12. 참조 파일 목록 (단일 테넌트 원본)

| 영역 | 파일 |
|---|---|
| FastAPI 진입점 | `packages/agentcore-agents/qa-pipeline/server.py` |
| 라우터 | `packages/agentcore-agents/qa-pipeline/routers/{evaluate,wiki,compare,xlsx_save}.py` |
| LangGraph | `packages/agentcore-agents/qa-pipeline/graph.py` |
| 노드 | `packages/agentcore-agents/qa-pipeline/nodes/*.py` |
| 프롬프트 | `packages/agentcore-agents/qa-pipeline/prompts/` |
| UI | `packages/chatbot-ui/qa_pipeline_reactflow.html` |
| CDK | `packages/cdk-infra-python/src/stacks/` |
