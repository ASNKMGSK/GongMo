# 신규 테넌트 온보딩 체크리스트

> **Phase 1 단계**: 배포 금지 기간이므로 AWS 호출이 필요한 단계는 **[보류]** 로 표시.
> 실행 승인(Phase 2 이상) 후 해당 항목 체크 가능.

관련 문서
- `docs/TENANT_CONFIG.md` — 온보딩 절차 상세 설명
- `docs/seed/README.md` — seed JSON 스키마/규약
- `ARCHITECTURE.md` §2 (TenantConfig), §3 (DynamoDB), §7 (프롬프트 로더)

---

## A. 설계 단계 (Phase 1 실행 가능)

- [ ] 신규 `tenant_id` 결정 — `^[a-z0-9_]{2,64}$`, 운영 중 고유.
- [ ] 업종(`industry`) 확정 — `industrial / insurance / ecommerce / banking / healthcare / telco / generic` 중 택1.
- [ ] 활성 평가 항목(`qa_items_enabled`) 범위 확정 — 업종 프리셋 참고.
- [ ] 점수 가중치 조정(`score_overrides`) 필요 여부 결정.
- [ ] 기본 모델(`default_models.primary`, `default_models.fast`) 확정 — 계정/리전에서 호출 가능한 ID 인지 확인.
- [ ] 브랜딩 자산 수집 — `logo_url`(공개 CDN), `primary_color`, `secondary_color`.
- [ ] 레이트 리밋/스토리지 쿼터 기본값(60/10GB) 외 별도 요구 확인.
- [ ] 프롬프트 오버라이드 필요 여부 결정 (필요 시 아래 D 섹션에서 준비).

## B. Seed JSON 작성 (Phase 1 실행 가능, 파일 only)

- [ ] `docs/seed/{tenant_id}.json` 파일 생성 — TenantConfig.to_dict() 형식 준수.
- [ ] 파일명과 내부 `tenant_id` 일치 확인.
- [ ] 민감정보 미포함 확인 (API 키, 시크릿, PII 금지).
- [ ] 로컬에서 `TenantConfig.from_dict(json.load(f)).validate()` 통과 확인 (AWS 호출 없음).
- [ ] `created_at` / `updated_at` 을 UTC ISO8601 `YYYY-MM-DDTHH:MM:SSZ` 형식으로 기재.
- [ ] PR 리뷰 — 최소 1인 승인 (PL 권장).

## C. 프롬프트 오버라이드 (선택)

기본 프롬프트로 충분하면 C 섹션은 건너뛴다.

- [ ] `qa-pipeline/prompts/tenants/{tenant_id}/` 디렉토리 생성.
- [ ] 오버라이드할 항목만 복사 (`{name}.sonnet.md`).
- [ ] 공통 preamble 중복 포함 금지 (로더 자동 prepend).
- [ ] 로컬에서 `from prompts import load_prompt, clear_cache; clear_cache(); load_prompt("{name}", tenant_id="{tid}")` 로드 성공 확인.
- [ ] 기존 기본 프롬프트와 diff 리뷰.

## D. 인프라 반영 (Phase 2 이상 — **Phase 1 기간 보류**)

- [ ] **[보류]** DynamoDB `qa_tenants` 에 put_item — Dev2 백필 스크립트 사용 (CDK `QaMultiTenantTables` 생성 이후).
- [ ] **[보류]** S3 prefix `tenants/{tenant_id}/` 생성 (S3 는 자동 생성이지만 IAM 확인).
- [ ] **[보류]** Cognito 사용자에 `custom:tenant_id={tenant_id}` claim 부여 테스트 계정 준비.
- [ ] **[보류]** (대형 테넌트만) 전용 런타임/IAM 프로파일 준비.

### D.1 Cognito `custom:tenant_id` 속성 추가 시점 (확정: §3.3)

사용자 결정(2026-04-17) — Cognito User Pool 에 `custom:tenant_id` 속성을 **추가하는 시점**은
아래 조건을 **충족할 때만** 수행한다. 그 이전까지는 Cognito schema 에 손대지 않는다.

- [ ] **[조건]** `qa_tenants` 테이블에 `kolon_default` 외 **2번째 테넌트 레코드**가 put 직전 상태.
  - 즉 본 체크리스트의 A~C 단계를 완료한 신규 테넌트가 실제 운영 투입 직전.
- [ ] **[보류]** Cognito User Pool Schema 에 `custom:tenant_id` (String, Mutable=true) 추가.
  - 현 Phase 1 (`kolon_default` 단일 운영) 시점에서는 속성 미생성 상태가 정상.
  - 단일 테넌트 운영 중에는 middleware 측 `LOCAL_TENANT_ID=kolon_default` 폴백으로 충분 (ARCHITECTURE.md §1).
- [ ] **[보류]** 기존 사용자 일괄 패치 방식 선택 (§3.4):
  - `N ≤ 20` → Cognito 콘솔 bulk update
  - `N > 20` → 스크립트 `AdminUpdateUserAttributes` 루프 (Dev6 영역)
  - 실제 사용자 수 `<USER_COUNT>` 는 운영담당 확정 후 기입.
- [ ] **[보류]** 속성 추가 후 기존 사용자 전원이 `custom:tenant_id=kolon_default` 를 갖는지 smoke 확인.
- [ ] **[보류]** 2번째 테넌트의 JWT 발급 → `/api/me` 200 + 올바른 config 반환 확인.

> **주의**: 이 조건을 지키지 않고 Cognito 속성을 먼저 추가하면, 기존 사용자의 JWT 에 claim 이 없어 미들웨어가 401 을 반환 → 단일 테넌트 사용자 전원 로그인 실패 리스크. 따라서 "2번째 테넌트 추가 직전" 조건은 엄격히 적용.

## E. 검증 (Phase 2 이상 — **Phase 1 기간 보류**)

- [ ] **[보류]** `get_config("{tenant_id}")` 가 put 한 레코드 그대로 반환.
- [ ] **[보류]** `/api/me` 호출 시 `config` 에 to_dict 결과 포함.
- [ ] **[보류]** 활성 qa_items 전체에 대해 `load_prompt(item_key, tenant_id)` 성공.
- [ ] **[보류]** 평가 1건 실행 → `qa_evaluations_v2` 에 `tenant_id={tid}` 로 기록.
- [ ] **[보류]** `qa_audit_log` 에 요청 1행 기록.
- [ ] **[보류]** UI 에서 브랜딩(로고/색상) 정상 렌더 (Dev5 검증 지원).

## F. 운영 전환 (Phase 2 이상 — **Phase 1 기간 보류**)

- [ ] **[보류]** 쿼터 모니터링 대시보드(Dev6) 에 테넌트 추가.
- [ ] **[보류]** 레이트 리밋 임계값 실측 후 조정.
- [ ] **[보류]** 장애 시 롤백 절차 공유 (seed JSON PR revert + invalidate_cache).

---

## Phase 1 Dev4 완료 기준 (본 체크리스트 기준)

Phase 1 기간에 Dev4 가 책임지는 "`kolon_default` 를 실제 백필할 준비"는 다음 세 가지 체크로 충분하다.

- [x] Seed JSON (`docs/seed/kolon_default.json`) 작성 및 스키마 검증.
- [x] README (`docs/seed/README.md`) — 디렉토리 규약 확립.
- [x] 백필 입력 검증 항목 문서(`docs/kolon_default_integration_notes.md`) — Dev2 에게 정적 입력 제공.

실제 백필/운영 전환은 Phase 2 승인 후 Dev2/Dev6 주도로 진행.
