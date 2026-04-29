# `kolon_default` 백필 입력 검증 노트

> **경계**: 본 문서는 **Dev2 백필 스크립트의 입력 검증 항목**만 기술한다.
> 스크립트 실행/DynamoDB put/S3 upload 는 본 문서 범위가 **아니다** — Phase 2 승인 이후 Dev2 가 주도.
> Phase 1 기간 (2026-04-17 시점) 에는 본 문서와 `docs/seed/kolon_default.json` 이 정적 입력으로만 존재.

## 배경

기존 단일 테넌트 파이프라인(`packages/agentcore-agents/qa-pipeline/`) 은 tenant_id 개념 없이 코오롱 전용으로 운영되었다. 멀티테넌트 전환 시 기존 데이터/구성은 `tenant_id="kolon_default"` 로 소급 귀속된다. 본 노트는 Seed JSON 이 기존 운영 기대치와 정합하는지 검증하는 체크포인트를 제공한다.

## Seed 입력 파일

- `docs/seed/kolon_default.json` (정적 입력)
- 형식: `TenantConfig.to_dict()` 호환 (README 참조)

## 입력 검증 체크 (스크립트 로직 없이도 육안/로컬 확인 가능)

### 1. 스키마/타입 정합

- [ ] `TenantConfig.from_dict(json.load(f)).validate()` 로컬 통과.
- [ ] `tenant_id` == `"kolon_default"`, 파일명과 일치.
- [ ] `industry` == `"industrial"` (원본 프리셋과 일치).
- [ ] `is_active` == `true`.
- [ ] `created_at` / `updated_at` 이 ISO8601 UTC.

### 2. 평가 항목/점수 정합 (기존 운영과 동일한지)

기존 단일 테넌트 파이프라인이 1~21 전체 항목을 사용했는지 `packages/agentcore-agents/qa-pipeline/nodes/` 및 `prompts/` 파일 존재로 확인 (수정 금지 — 참조만).

- [ ] `qa_items_enabled` 가 `[1..21]` 전체. 누락 항목 있으면 운영 리포트 형식 변경 필요 → Dev3 와 합의 전까지 백필 보류.
- [ ] `score_overrides` 기본 빈 `{}` — 기존 만점 체계 유지. 필요 시 개별 고지 후 추가.

### 3. 모델 ID 호환성

- [ ] `default_models.primary` 가 Bedrock 계정/리전(`us-east-1`) 에서 호출 가능한지 Dev6 와 합의.
- [ ] `default_models.fast` 도 동일.
- [ ] 모델 ID 변경은 운영 평가 결과 품질에 직결 — 회귀 테스트 계획 있는지 확인.

### 4. 프롬프트 오버라이드 경로 정합

- [ ] `prompt_overrides_dir == "prompts/tenants/kolon_default/"` — 로더 규약과 일치.
- [ ] `qa-pipeline/prompts/tenants/kolon_default/` 실제 존재 (현재는 `README.md` 만).
- [ ] 로더 시그니처(`load_prompt(name, *, tenant_id=...)`) 에 맞춰 21개 활성 항목 전부 기본 `prompts/{name}.sonnet.md` 폴백 로드 가능해야 한다 — 파일 존재 여부 로컬 확인 (AWS 없이).

### 5. 레이트 리밋 / 쿼터 정합

- [ ] `rate_limit_per_minute` (기본 120) 값이 현재 운영 트래픽의 P95 를 수용하는지 Dev6 와 합의.
- [ ] `storage_quota_gb` (기본 50) 값이 기존 S3 사용량 대비 여유 있는지 확인.

### 6. UI 브랜딩 정합

- [ ] `branding.primary_color` / `secondary_color` 가 Dev5 기본 테마와 대비/접근성 OK.
- [ ] `logo_url` 은 Phase 1 에서는 빈 문자열 허용 — Phase 2 이전에 공개 CDN URL 로 교체.

### 7. 민감 필드 미포함

- [ ] JSON 내 API 키/시크릿/이메일/사번 등 민감 필드 없음.
- [ ] Git 저장 허용 수준인지 PR 리뷰에서 재확인.

### 8. 키 집합 동등성 (to_dict ↔ from_dict 라운드트립)

- [ ] 로컬 스모크: 아래 동등성 보장.
  ```python
  import json
  from tenant import TenantConfig
  with open("docs/seed/kolon_default.json", encoding="utf-8") as f:
      d = json.load(f)
  cfg = TenantConfig.from_dict(d)
  cfg.validate()
  d2 = cfg.to_dict()
  # created_at/updated_at 은 from_dict 이 보존, 동일해야 함
  assert d["tenant_id"] == d2["tenant_id"]
  assert d["industry"] == d2["industry"]
  assert set(d["qa_items_enabled"]) == set(d2["qa_items_enabled"])
  ```
- [ ] 위가 실패하면 Dev4 에 알림 (`tenant/config.py` 버그 가능성).

## Dev2 백필 스크립트 측 가이드 (참고)

Phase 2 에서 Dev2 가 구현할 백필 스크립트 예상 인터페이스 — 본 문서는 **동작 요구 스펙**이지 본 문서 작성자가 구현하지 않는다.

```python
# scripts/backfill_tenants.py  (Dev2 소유 — 아직 구현 전)
import json, pathlib
from tenant import TenantConfig, put_config

for p in pathlib.Path("packages/qa-pipeline-multitenant/docs/seed").glob("*.json"):
    with p.open(encoding="utf-8") as f:
        cfg = TenantConfig.from_dict(json.load(f))
    cfg.validate()
    put_config(cfg)          # upsert + updated_at 갱신
    print("backfilled:", cfg.tenant_id)
```

- `put_config` 가 재검증 + 캐시 반영을 수행하므로 스크립트는 단순 루프.
- 실행은 Phase 2 승인 후 Dev2 가 담당 — Dev4 는 본 문서와 seed JSON 까지만 제공.

## Phase 1 종료 시 유지 기대 상태

- `docs/seed/kolon_default.json` 존재, 스키마 검증 통과.
- 본 노트의 체크 1~8 항목 중 AWS 무관 항목 전부 체크 완료.
- AWS 호출 필요 항목(모델 호환/쿼터 실측 등) 은 Phase 2 승인까지 [보류] 로 남긴다.
- DynamoDB/S3 에는 **아무 것도 쓰지 않은 상태**를 Phase 1 종료 상태로 본다.
