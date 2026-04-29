# Data Isolation Policy — `data/` 레이어

> **Owner**: Dev2 (`data-isolation`)
> **Base**: `ARCHITECTURE.md` §3 (DynamoDB), §4 (S3)
> **Scope**: `packages/qa-pipeline-multitenant/qa-pipeline/data/`

---

## 1. 원칙

1. **Pool 모델**: 모든 AWS 리소스는 공유하되, 엔트리(row / 객체 / 시크릿 / 문서)마다 `tenant_id` 로 격리한다.
2. **tenant_id 필수**: 헬퍼 함수의 인자로 `tenant_id` 가 누락되면 `ValueError("tenant_id is required for query isolation")` 를 즉시 던진다.
3. **Direct boto3 금지**: 라우터 / 노드 / 서비스 코드는 `boto3.client(...)` 를 직접 호출하지 말고 본 레이어만 사용한다.
4. **키/경로 강제**: S3 는 `tenants/{tid}/...`, Secrets 는 `/qa/{tid}/{name}`, OpenSearch 는 자동 `term` 필터.
5. **가드 대신 예외**: 실수는 런타임에 바로 터뜨려 개발 중 적발한다 (Fail-fast).

---

## 2. DynamoDB (`data.dynamo`)

### 테이블 (ARCHITECTURE.md §3)

| 테이블 | PK | SK | 용도 |
|---|---|---|---|
| `qa_tenants` | `tenant_id` | — | 테넌트 Config |
| `qa_evaluations_v2` | `tenant_id` | `evaluation_id` | 평가 결과 |
| `qa_sessions` | `tenant_id` | `session_id` | 세션 |
| `qa_audit_log` | `tenant_id` | `timestamp` | 감사 로그 (TTL 30일) |
| `qa_quota_usage` | `tenant_id` | `yyyy-mm` | Rate Limit |

### Sample item 규약 (Dev6 CDK 합의)

- **`qa_audit_log`**: `timestamp` 는 `%Y-%m-%dT%H:%M:%S.%fZ` (ms precision ISO8601), `ttl` 는 `int(now + 30d)` epoch seconds. 테이블 TTL attribute 이름은 `ttl`.
- **`qa_quota_usage`**: Rate Limit 카운터는 `minute_counters` (Map, key=분 버킷 `"%Y-%m-%dT%H:%M"`, value=N) + `updated_at` (S). 상위 카운터(`request_count` 등)는 선택 사용.

### API

```python
from data import (
    tenant_query, tenant_get_item, tenant_put_item,
    tenant_delete_item, tenant_update_item,
)

# Query — 반드시 tenant_id
rows = tenant_query("qa_evaluations_v2", tenant_id)
rows = tenant_query("qa_evaluations_v2", tenant_id, sk_prefix="2026-04-")

# Get
item = tenant_get_item("qa_tenants", tenant_id)            # SK 없음
item = tenant_get_item("qa_sessions", tenant_id, "sess-abc")

# Put — item dict 에 tenant_id 없으면 ValueError
tenant_put_item("qa_evaluations_v2", {
    "tenant_id": tenant_id,
    "evaluation_id": "eval-123",
    "score": 92,
})

# Delete / Update
tenant_delete_item("qa_sessions", tenant_id, "sess-abc")
tenant_update_item(
    "qa_sessions", tenant_id, "sess-abc",
    update_expression="SET #s = :v",
    expression_values={":v": "done"},
    expression_names={"#s": "status"},
)
```

### 가드
- `tenant_id` 가 `None` / 빈 문자열 → `ValueError`.
- `put_item` 은 `item["tenant_id"]` 가 없으면 거부 (사람이 실수하는 가장 흔한 지점).
- SK 가 있는 테이블에 SK 없이 `get/delete/update` 호출 시 `ValueError`.

---

## 3. S3 (`data.s3`)

### 키 구조

```
{QA_BUCKET_NAME}/
└── tenants/
    └── {tenant_id}/
        ├── raw/{yyyy-mm-dd}/{filename}.txt
        ├── reports/{yyyy-mm-dd}/{eval_id}.xlsx
        ├── prompts-override/...
        └── exports/...
```

호출자는 `tenants/{tid}/` prefix 를 **직접 쓰지 않는다** — 헬퍼가 강제한다.

### API

```python
from data import (
    tenant_put_object, tenant_get_object,
    tenant_list_objects, tenant_delete_object, tenant_presigned_url,
)

tenant_put_object(tenant_id, "raw/2026-04-17/call.txt", body=b"...", content_type="text/plain")
resp = tenant_get_object(tenant_id, "raw/2026-04-17/call.txt")
data = resp["Body"].read()

objs = tenant_list_objects(tenant_id, prefix="reports/2026-04-")
tenant_delete_object(tenant_id, "reports/2026-04-17/eval-123.xlsx")

url = tenant_presigned_url(tenant_id, "reports/2026-04-17/eval-123.xlsx", expires_in=900)
```

### 버킷 설정
- 기본 버킷은 `QA_BUCKET_NAME` 환경변수. 호출마다 `bucket=` 로 덮을 수 있음.
- Phase 5 에서 IAM `aws:PrincipalTag/tenant_id` 로 경로 격리를 IAM 단에서도 강제한다.

---

## 4. Secrets Manager (`data.secrets`)

### 경로

```
/qa/{tenant_id}/{secret_name}
```

예) `/qa/kolon/llm/anthropic-api-key`

### API

```python
from data import get_tenant_secret, put_tenant_secret, delete_tenant_secret

key = get_tenant_secret(tenant_id, "llm/anthropic-api-key")
# JSON 시크릿이면 dict 로, 아니면 str/bytes 로 반환

put_tenant_secret(tenant_id, "llm/anthropic-api-key", {"apiKey": "sk-..."})
delete_tenant_secret(tenant_id, "llm/anthropic-api-key")  # 7일 soft-delete
```

- 메모리 캐시 5분 TTL (시크릿 호출 비용 절감).
- `invalidate_secret_cache(tenant_id=...)` 로 강제 무효화 가능.

---

## 5. OpenSearch (`data.opensearch`)

### 필터 필드
- 모든 문서는 `tenant_id` 필드를 포함한다 (헬퍼가 자동 주입).
- `tenant_search` 는 호출자의 query 에 `{"term": {"tenant_id": tid}}` 를 **자동 필터 주입**한다.

### API

```python
from data import tenant_search, tenant_index_doc

tenant_index_doc(tenant_id, "qa-knowledge", {
    "title": "상품 설명 스크립트",
    "content": "...",
})

resp = tenant_search(tenant_id, "qa-knowledge", {
    "query": {"match": {"content": "환불 절차"}},
    "size": 10,
})
```

### 환경변수
- `OPENSEARCH_HOST`, `OPENSEARCH_PORT` (default 443), `OPENSEARCH_SERVICE` (`aoss` or `es`).
- opensearch-py / requests-aws4auth 는 optional — `data.opensearch` import 시에만 로드.

---

## 6. 허용/금지 패턴

### OK

```python
# 라우터 / 노드
from data import tenant_query
rows = tenant_query("qa_evaluations_v2", request.state.tenant_id)
```

### 금지

```python
# boto3 직접 호출
dynamodb = boto3.resource("dynamodb")
dynamodb.Table("qa_evaluations_v2").query(...)  # tenant_id 누락 위험

# S3 raw key
s3.put_object(Bucket=b, Key="raw/...", Body=...)  # prefix 우회

# tenant_id 없이 put
tenant_put_item("qa_evaluations_v2", {"evaluation_id": "..."})  # → ValueError
```

---

## 7. 테스트 방침

- `moto` 로 DynamoDB / S3 / Secrets 를 모킹하여 단위 테스트. (Phase 1 말 도입)
- `tenant_id` 누락 / 미스매치 케이스는 반드시 부정 테스트 (assertRaises ValueError).
- 통합 테스트는 `LOCAL_TENANT_ID=kolon_default` 미들웨어 폴백을 사용해 실리소스 없이도 가능.

---

## 8. 변경 관리

- 인터페이스 변경은 PL 승인 → 본 문서 먼저 업데이트 → Dev1 / Dev4 에게 `SendMessage` 통보 → 구현.
- 테이블/키 추가는 Dev6 CDK 스택과 반드시 동시에 변경한다.
