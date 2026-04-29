# 신규 테넌트 온보딩 절차

> **담당**: Dev4 (tenant-config)
> **관련 문서**: `ARCHITECTURE.md` 2절(TenantConfig), 3절(DynamoDB), 7절(프롬프트 로더)

신규 테넌트를 등록하려면 아래 세 단계를 순서대로 수행한다. 기본 테넌트(`kolon_default`)
는 이미 등록되어 있으므로 이 가이드는 **신규** 테넌트 추가에만 적용된다.

---

## 1단계. `TenantConfig` 인스턴스 생성

업종 프리셋을 기반으로 시작하는 것을 권장한다.

```python
from tenant import TenantConfig
from tenant.presets import apply_preset

cfg = apply_preset(
    industry="insurance",                  # industrial | insurance | ecommerce | generic
    tenant_id="samsung_life",              # ^[a-z0-9_]{2,64}$
    display_name="삼성생명",
    branding={
        "logo_url": "https://cdn.example.com/samsung_life.png",
        "primary_color": "#1428a0",
        "secondary_color": "#f5f5f5",
    },
)
cfg.validate()   # ValueError 면 필드 점검
```

프리셋 없이 처음부터 구성하려면 다음과 같이 `TenantConfig(...)` 를 직접 구성한다.

```python
cfg = TenantConfig(
    tenant_id="acme_retail",
    display_name="Acme Retail",
    industry="ecommerce",
    qa_items_enabled=[1, 2, 3, 4, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17],
    score_overrides={11: 12, 12: 15, 14: 12, 15: 12},
    default_models={
        "primary": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "fast":    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    },
    prompt_overrides_dir="prompts/tenants/acme_retail/",
    branding={"primary_color": "#ff6f00"},
    rate_limit_per_minute=120,
    storage_quota_gb=30,
)
cfg.validate()
```

검증 실패 항목 예시
- `tenant_id` 정규식 `^[a-z0-9_]{2,64}$` 위반
- `industry` 가 7개 허용값 외
- `qa_items_enabled` 에 1~21 범위 밖 값 / 중복 포함
- `score_overrides` 의 키 1~21 벗어남, 값 1~100 벗어남

---

## 2단계. DynamoDB 에 `put_config`

`qa_tenants` 테이블에 레코드를 기록한다. `updated_at` 은 자동 갱신된다.

```python
from tenant import put_config

saved = put_config(cfg)        # cfg.to_dict() 로 직렬화 후 tenant_put_item 호출
print(saved.updated_at)        # 방금 갱신된 UTC ISO8601
```

내부 동작
- `TenantConfig.validate()` 재호출
- `updated_at` 을 `datetime.now(UTC)` 으로 대체 (immutable 이므로 `dataclasses.replace` 사용)
- `data/dynamo.tenant_put_item("qa_tenants", cfg.to_dict())` 로 저장
- 메모리 LRU 캐시(TTL 5분)에 즉시 반영

캐시 강제 무효화가 필요하면 `invalidate_cache(tenant_id)`.

---

## 3단계. (선택) 프롬프트 오버라이드 디렉토리 작성

기본 프롬프트로 충분하면 이 단계는 생략 가능하다. 테넌트별 커스텀 프롬프트가 필요한 경우만 수행.

1. 디렉토리 생성
   ```bash
   mkdir -p qa-pipeline/prompts/tenants/acme_retail
   cp qa-pipeline/prompts/tenants/kolon_default/README.md \
      qa-pipeline/prompts/tenants/acme_retail/README.md
   ```
2. 오버라이드할 항목 파일만 복사 후 수정 — 모든 항목을 복사할 필요 없음.
   ```bash
   cp qa-pipeline/prompts/item_16_mandatory_script.sonnet.md \
      qa-pipeline/prompts/tenants/acme_retail/item_16_mandatory_script.sonnet.md
   ```
3. `TenantConfig.prompt_overrides_dir` 를 `"prompts/tenants/acme_retail/"` 로 설정 후 `put_config` 재호출.
   (현재는 참고용 메타 필드 — 로더는 규약된 폴더 구조(`prompts/tenants/{tid}/`)만 사용한다.)
4. 로더 동작 확인
   ```python
   from prompts import load_prompt, clear_cache
   clear_cache()
   text = load_prompt("item_16_mandatory_script", tenant_id="acme_retail")
   # 자체 출력 규칙이 있는 프롬프트는 preamble 비활성화
   text2 = load_prompt("report_generator", tenant_id="acme_retail", include_preamble=False)
   ```

로더 시그니처
```python
load_prompt(name: str, *, tenant_id: str, include_preamble: bool = True, backend: str | None = None) -> str
```
- `tenant_id` 는 keyword-only 필수.
- `backend` 는 예약 — 현재 ``.sonnet.md`` 우선 + ``.md`` 폴백이 기본 동작.

로더 우선순위 (ARCHITECTURE.md 7절)
1. `prompts/tenants/{tenant_id}/{name}.sonnet.md`
2. `prompts/tenants/{tenant_id}/{name}.md`
3. `prompts/{name}.sonnet.md`
4. `prompts/{name}.md`
5. 모두 없으면 `FileNotFoundError`

공통 preamble(`_common_preamble.sonnet.md`) 은 로더가 자동 prepend 하므로 오버라이드에서 중복 포함하지 말 것. preamble 자체도 테넌트별 오버라이드 가능 — 동명 파일을 테넌트 폴더에 두면 기본 preamble 을 대체.

---

## 검증 체크리스트

온보딩 완료 전 다음을 확인한다.

- [ ] `get_config(tid)` 가 방금 저장한 레코드를 반환한다.
- [ ] `cfg.qa_items_enabled` 의 모든 항목 번호에 대해 `load_prompt(item_key, tid)` 가 성공한다.
- [ ] `cfg.default_models["primary"]` 모델 ID 가 계정/리전에서 호출 가능한지 확인.
- [ ] 브랜딩 필드(`logo_url`, `primary_color`)가 UI 에서 렌더되는지 (Dev5 검증).
- [ ] `qa_audit_log`, `qa_evaluations_v2` 에 `tenant_id` 가 정상 기록되는지 smoke 테스트.

---

## 업종 프리셋 요약

| 업종 | 활성 항목 수 | 주요 score_overrides |
|---|---|---|
| `industrial` | 21 (전체) | — |
| `insurance`  | 17 | item 8 / 16 / 18 가중치 상향 |
| `ecommerce`  | 15 | item 11 / 12 / 14 / 15 가중치 상향 |
| `generic`    | 15 (핵심) | — |

프리셋 정의는 `qa-pipeline/tenant/presets/{industry}.py`. 새 업종 추가 시 이 폴더에 파일 추가 + `presets/__init__.py` 의 `_BUILDERS` 에 등록.
