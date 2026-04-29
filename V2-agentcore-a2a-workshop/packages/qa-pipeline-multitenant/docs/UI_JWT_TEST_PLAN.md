# UI JWT 연동 시험 시나리오 + Mock 폴백 제거 절차

> **Owner**: Dev5 (frontend)
> **산출 HTML**: `packages/qa-pipeline-multitenant/chatbot-ui/qa_pipeline_reactflow.html`
> **Phase 1 제약**: 이 문서는 계획/체크리스트이며, 본 문서의 실행 명령은 **실행 금지**. 배포/CDK/SSM/S3 호출 일체 금지.

---

## 0. 범위

UI (단일 HTML) 가 Cognito JWT + Dev1 라우터와 end-to-end 로 연동될 때의 시험 시나리오와, 로컬 개발용 `LOCAL_TENANT_MOCK` 폴백을 운영 시 제거하는 절차를 정의.

담당 산출물:
- `chatbot-ui/qa_pipeline_reactflow.html` 내 `TenantProvider` / `tenantFetch` / `TenantBrand` / `TenantSwitcher`

---

## 1. UI 측 가정 (Dev1 합의된 계약)

| 항목 | 값 |
|---|---|
| 엔드포인트 | `/api/me`, `/api/tenants`, `/evaluate`, `/evaluate/stream`, `/evaluate/pentagon`, `/analyze-manual-compare`, `/analyze-compare`, `/save-xlsx`, `/health` |
| Authorization | `Authorization: Bearer <JWT>` (exempt: `/health /ping /readyz /docs /redoc /openapi.json /ui`) |
| 테넌트 override | `X-Tenant-Override: <tid>` — `role==='admin'` + 스위처 선택 시에만 |
| 요청 추적 | `X-Request-ID: <uuid>` (tenantFetch 가 자동 생성, 서버가 echo) |
| 에러 envelope | `{error: {code, message, tenant_id, request_id}}` — §10.2 |
| `/api/me` 미등록 테넌트 | **200 + `config: null`** (PL 확정안 A) |
| `/save-xlsx` 토스트 | `relative_path` 권위 소스 사용, `path`(절대경로) **금지** |

---

## 2. 시험 시나리오 (통합 테스트 Task #9 에서 실행)

### S1. 로컬 mock 폴백 (JWT 없음)

**목적**: 백엔드 없이 UI 단독 구동 가능 여부 확인.

| Step | 조작 | 기대 결과 |
|---|---|---|
| 1 | `localStorage.jwt` 비움, 쿠키에도 jwt 없음 | — |
| 2 | HTML 로드 | Provider 가 `LOCAL_TENANT_MOCK` 주입 (tenant_id=kolon_default, role=admin, 프리뷰 3개 테넌트) |
| 3 | 좌상단 헤더 | "코오롱산업 (Local)" + ADMIN 뱃지 |
| 4 | 우상단 드롭다운 | 3개 테넌트 (kolon_default, insur_demo, ecom_demo) + "자신의 테넌트" |
| 5 | 스위처에서 `insur_demo` 선택 | 드롭다운 닫힘, 버튼에 "데모 보험사" 표시 |
| 6 | DevTools Network 에서 `/health` 확인 | `X-Request-ID` 헤더 있음, Authorization 없음 (skipAuth:true) |

### S2. JWT 로그인 (member)

**전제**: `localStorage.setItem('jwt', '<member-jwt>')` — JWT 는 Cognito 에서 발급 (`custom:tenant_id=kolon_default`, `custom:role=member`).

| Step | 조작 | 기대 결과 |
|---|---|---|
| 1 | 페이지 새로고침 | `/api/me` 호출, 200 응답 수신 |
| 2 | 좌상단 | display_name 표시 (mock 이 아닌 DB 값), ADMIN 뱃지 없음 |
| 3 | 우상단 | TenantSwitcher 렌더 안 됨 (role ≠ admin) |
| 4 | `/evaluate/stream` 호출 | `Authorization: Bearer ...`, `X-Request-ID: <uuid>` 헤더 존재, `X-Tenant-Override` 없음 |
| 5 | SSE 이벤트 수신 | 정상 — 파이프라인 노드 동작 |

### S3. JWT 로그인 (admin) + 스위처

**전제**: admin JWT, DB 에 `kolon_default`, `insur_a`, `ecom_b` 등록.

| Step | 조작 | 기대 결과 |
|---|---|---|
| 1 | 페이지 로드 | `/api/me` 200, `/api/tenants` 200 |
| 2 | 좌상단 | display_name + ADMIN 뱃지 |
| 3 | 우상단 스위처 열기 | 등록된 테넌트 전체 표시 (`tenant_id` + `display_name`) |
| 4 | `insur_a` 선택 | 이후 모든 요청에 `X-Tenant-Override: insur_a` |
| 5 | `/evaluate` 실행 | 서버 응답에 `result.tenant_id === "insur_a"` |
| 6 | Results 패널 | admin 전용 tenant_id 칩에 `insur_a` 표시 |
| 7 | 스위처에서 "자신의 테넌트" 선택 | `X-Tenant-Override` 헤더 제거, 기본 테넌트로 복귀 |

### S4. 브랜딩 (CSS 변수)

| Step | 조작 | 기대 결과 |
|---|---|---|
| 1 | branding.primary_color=`#0ea5e9` 인 테넌트 로드 | `.header-title`, `.tenant-brand__name` 이 해당 색상 |
| 2 | branding.logo_url 유효 | `<img>` 렌더 |
| 3 | branding.logo_url 무효/404 | 폴백 뱃지 (이니셜) |
| 4 | `config: null` 테넌트 | :root 기본 브랜딩 유지 (--tenant-primary 제거) |

### S5. /save-xlsx (relative_path)

| Step | 조작 | 기대 결과 |
|---|---|---|
| 1 | Matrix 탭에서 저장 | 토스트: `💾 서버 저장 완료 · Saving to: kolon_default/2026-04-17/report.xlsx` |
| 2 | relative_path 없음 가정 (방어 폴백) | `{tid}/{subfolder}/{filename}` 로 조립, 절대경로 노출 없음 |
| 3 | 동명 파일 재저장 | 서버가 `_(2)` 접미사 반영, 토스트도 최종 relative_path 표시 |

### S6. 에러 핸들링

| Case | UI 기대 동작 |
|---|---|
| `/api/me` 401 | 콘솔 warn + LOCAL_TENANT_MOCK 폴백 (현재 구현) — Phase 2 이후 로그인 페이지 리다이렉트로 교체 검토 |
| `/evaluate` 429 RATE_LIMITED | `errorAlert` 배너에 message 표시, Retry-After 존중 |
| `/api/tenants` 403 TENANT_MISMATCH | 스위처 드롭다운에 "(목록 없음)" |
| `/save-xlsx` 5xx | 백엔드 분기 실패 → FSA → anchor download 폴백 (기존 로직) |

### S7. X-Request-ID 전파

**목적**: Dev1 의 end-to-end grep 과 정합.

| Step | 기대 |
|---|---|
| 1 | tenantFetch 매 호출마다 새 UUID 생성 | DevTools 에서 Request Header `X-Request-ID` 확인 |
| 2 | 서버 응답 헤더 | 동일 UUID echo (Dev1 `_resolve_request_id()`) |
| 3 | 서버 `audit_log` | 해당 UUID 로 1행 기록 |
| 4 | 에러 응답 envelope | `error.request_id` 에 동일 UUID |

---

## 3. Mock 폴백 제거 절차 (Phase 4 이후)

### 3.1 현재 구조

```js
// qa_pipeline_reactflow.html — TenantProvider 내부
const LOCAL_TENANT_ID = "kolon_default";
const LOCAL_TENANT_MOCK = { tenant_id, role:"admin", email, config:{...} };

// /api/me 로딩 useEffect
if (!jwt) {
  setMe(LOCAL_TENANT_MOCK); // ← 제거 대상 1
  return;
}
try { ... }
catch (err) {
  setMe(LOCAL_TENANT_MOCK); // ← 제거 대상 2 (네트워크 실패 폴백)
  setLoadError(...);
}

// /api/tenants 로딩 useEffect
if (!jwt) {
  setTenantList([/* 프리뷰 3개 */]); // ← 제거 대상 3
  return;
}
```

### 3.2 트리거 조건 (제거 시점)

**전부 충족 시에만** 제거:
1. Dev1 `/api/me` 배포 완료 + smoke test 통과
2. Cognito 로그인 UI 또는 CLI 로그인 스크립트 배포 완료 (JWT 주입 경로 확정)
3. QA 환경에서 admin + member 로 각각 30분 검증 통과
4. PL 승인

### 3.3 제거 커밋 단위 (원자적)

한 커밋에서 아래 변경 전체 수행:

1. `LOCAL_TENANT_ID`, `LOCAL_TENANT_MOCK` 상수 삭제.
2. `/api/me` 로딩 useEffect:
   ```diff
   - if (!jwt) {
   -   setMe(LOCAL_TENANT_MOCK); setLoading(false); return;
   - }
   + if (!jwt) {
   +   setLoadError("NO_JWT");
   +   setMe(null);
   +   setLoading(false);
   +   // 로그인 페이지 리다이렉트 훅: window.location.href = LOGIN_URL;
   +   return;
   + }
   ```
3. catch 분기:
   ```diff
   - setMe(LOCAL_TENANT_MOCK);
   + setMe(null);
   ```
4. `/api/tenants` 로딩 useEffect `!jwt` 프리뷰 블록 삭제.
5. TenantBrand / TenantSwitcher / ResultsContent 의 `me == null` 분기 추가:
   ```jsx
   // 빈 상태 렌더 (로딩 인디케이터 or 로그인 버튼)
   if (!me) return <div className="tenant-login-gate">로그인이 필요합니다</div>;
   ```
6. `effectiveTenantId` 폴백 제거: `tenantOverride || me?.tenant_id || LOCAL_TENANT_ID` → `tenantOverride || me?.tenant_id`
7. tenantFetch 의 `!jwt` 를 hard error 로 전환 (옵션 B: 현재처럼 JWT 없이도 요청은 허용하되 401 받아 UI 가 gate 로 전환하는 편이 낮은 결합도).

### 3.4 제거 후 리그레션 체크리스트

- [ ] HTML 을 file:// 로 열어도 깨지지 않음 (빈 상태 또는 로그인 gate)
- [ ] S2~S7 시나리오 전부 통과
- [ ] DevTools 에서 `LOCAL_TENANT_MOCK` 참조 0건 (Ctrl+F 전체 검색)
- [ ] 404/401 시 로그인 페이지 리다이렉트 동작 (경로는 Dev1/PL 협의)

### 3.5 롤백

문제 발생 시 커밋 revert 만으로 복구 가능. mock 제거는 HTML 단일 파일 수정이므로 배포·DB 변경 없음.

---

## 4. Phase 1 제약 준수 선언

이 문서의 모든 명령/체크리스트는 **계획**이며, 본 Phase 1 기간 중 다음은 일체 실행하지 않음:
- AWS API 호출 (CDK/SSM/DynamoDB/S3/Cognito 포함)
- 실제 JWT 발급/검증
- EC2 / AgentCore Runtime 배포
- 자격증명 사용
- dry-run 포함 boto3 호출 전체 금지

실행은 Phase 2 이후 PL 승인 후 진행.

---

## 5. 참조

- `ARCHITECTURE.md` §6 (라우터 인터페이스), §8 (UI), §10.2 (에러)
- Dev1 계약 메모 (2026-04-17 확정): `/api/me` response schema, `/save-xlsx` relative_path, X-Request-ID
- 산출 HTML: `packages/qa-pipeline-multitenant/chatbot-ui/qa_pipeline_reactflow.html`
