# Tenant Seed JSON

> **배포 금지 원칙 (2026-04-17 기준)**: 본 디렉토리 파일은 **정적 입력**이다.
> 로드/업로드/DynamoDB 쓰기 등 실행 로직은 본 문서 범위가 아니며, 실제 적용은
> Dev2 백필 스크립트(Phase 1 승인 이후) 를 통해서만 수행한다.

## 역할

- `qa_tenants` DynamoDB 테이블 백필 시 **정적 입력 파일**로 사용.
- 팀 전체가 테넌트 스펙을 PR 단위로 리뷰할 수 있도록 사람이 읽기 쉬운 JSON 형식 유지.
- 현재는 `kolon_default` 1건(Phase 1 기본 테넌트). 신규 고객 온보딩 시 이 폴더에 파일을 추가.

## 파일명 규약

- `docs/seed/{tenant_id}.json`
- 파일명은 JSON 내부의 `tenant_id` 와 정확히 일치해야 한다.
- `tenant_id` 는 `^[a-z0-9_]{2,64}$` 를 따른다 (TenantConfig validate 동일 규칙).

## JSON 스키마 (TenantConfig.to_dict() 형식)

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `tenant_id` | string | ✓ | `^[a-z0-9_]{2,64}$`. 파일명과 일치. |
| `display_name` | string | ✓ | UI 노출 이름, 1~128자. |
| `industry` | string | ✓ | `industrial` / `insurance` / `ecommerce` / `banking` / `healthcare` / `telco` / `generic` 중 하나. |
| `qa_items_enabled` | int[] | ✓ | 활성화할 평가 항목 번호. 1~21, 중복 금지. |
| `score_overrides` | object(str→int) | ✓ | 키는 "1"~"21" **문자열** (DynamoDB Map 제약). 값은 1~100 (max_score 변경). |
| `default_models` | object(str→str) | ✓ | 최소 `primary`, `fast` 두 키 권장. 모델 ID 는 계정/리전에서 호출 가능해야 한다. |
| `prompt_overrides_dir` | string \| null | ✓ | `"prompts/tenants/{tenant_id}/"` 또는 null. **메타 필드** — 로더는 규약된 폴더 구조를 사용하므로 참고용. |
| `branding` | object | ✓ | `logo_url`, `primary_color`, `secondary_color`, `accent_color` 등. UI 가 소비. |
| `rate_limit_per_minute` | int | ✓ | 1~100000. |
| `storage_quota_gb` | int | ✓ | 1~100000. |
| `created_at` | string (ISO8601 UTC) | ✓ | `YYYY-MM-DDTHH:MM:SSZ`. |
| `updated_at` | string (ISO8601 UTC) | ✓ | 동일 형식. 백필 시 로더가 재생성할 수도 있음. |
| `is_active` | bool | ✓ | 비활성 테넌트는 API 에서 차단 대상. |

### 민감정보 금지

- OAuth client secret, API 키, IAM 자격증명, 고객 PII 등은 **절대 포함 금지**.
- 모델 ID(Bedrock/SageMaker endpoint name)와 `logo_url` 공개 CDN URL 정도까지 허용.

## 로컬 검증 절차 (실행은 Phase 2 이후)

이 단계는 팀원이 **로컬에서만** 실행해 스키마 호환을 확인할 때 사용한다. 본 문서는 실행을 강제하지 않는다.

```python
import json, pathlib
from tenant import TenantConfig

seed_dir = pathlib.Path("packages/qa-pipeline-multitenant/docs/seed")
for p in seed_dir.glob("*.json"):
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    cfg = TenantConfig.from_dict(data)
    cfg.validate()
    assert cfg.tenant_id == p.stem, f"{p.name}: tenant_id mismatch"
    print("ok:", cfg.tenant_id)
```

위 코드는 AWS 호출을 하지 않는다(파일+메모리 only). 실패 시 seed JSON 을 수정.

## 백필 흐름 개요 (Dev2 영역 — 본 문서 범위 외)

1. 운영 배포 승인 후 Dev2 백필 스크립트가 본 디렉토리를 읽는다.
2. 각 파일을 `TenantConfig.from_dict(...).validate()` 로 검증한다.
3. `data.tenant_put_item("qa_tenants", cfg.to_dict())` 로 upsert.
4. `kolon_default_integration_notes.md` 의 검증 항목을 통과하면 완료.

## 변경 관리

- seed JSON 수정은 PR 리뷰 필수.
- 기존 테넌트의 `qa_items_enabled` / `score_overrides` 변경은 운영 리포트 형식에 영향 — Dev3/Dev5 에게 사전 공지.
- `tenant_id`, `industry` 는 불변 — 변경 필요 시 신규 tenant_id 로 재발행.
