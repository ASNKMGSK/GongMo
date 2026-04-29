# SECRETS_MIGRATION_CHECKLIST — 단일 테넌트 → `/qa/kolon_default/` 경로 이관

> **Owner**: Dev2 (`data-isolation`)
> **연결**: `ARCHITECTURE.md` §? (Secrets 경로 규약 — `DATA_ISOLATION.md` §4),
> `PHASE1_MIGRATION_PLAN.md` §2 (보조 체크리스트)
> **상태**: **실행 금지**. 본 문서는 mapping 표 + 절차만 기술한다.

---

## 1. 원칙

- 신규 규약: `/qa/{tenant_id}/{secret_name}` (DATA_ISOLATION.md §4)
- `kolon_default` 단일 테넌트 구간은 `/qa/kolon_default/<name>` 로 이관
- **비파괴**: 기존 시크릿을 삭제하지 않는다. 신규 경로에 값 복사만 수행 (수동 or 승인된 스크립트)
- 자격증명/시크릿 값은 문서/메모리에 절대 기록하지 않는다.

---

## 2. Mapping 표 (실제 값 금지 — 이름/경로만)

> 실제 시크릿 이름 중 PL 플랜 §5.1 "미결"이라 확정되지 않은 항목은 `<LEGACY_SECRET_*>` placeholder 로 둔다.

| # | 기존 경로 (placeholder) | 신규 경로 | 용도 | 담당 이관 주체 |
|---|---|---|---|---|
| 1 | `/a2a_gateway/cognito/credentials` | `/qa/kolon_default/cognito/credentials` | Gateway Cognito M2M | 운영자 수동 (콘솔) |
| 2 | `<LEGACY_SECRET_LLM_API>` | `/qa/kolon_default/llm/anthropic-api-key` | Anthropic Sonnet 4 호출용 | 운영자 수동 |
| 3 | `<LEGACY_SECRET_BEDROCK>` | `/qa/kolon_default/bedrock/profile` | Bedrock 추론 프로파일 | 확인 필요 |
| 4 | `<LEGACY_SECRET_OPENSEARCH>` | `/qa/kolon_default/opensearch/basic-auth` | OpenSearch basic auth (사용 시) | 확인 필요 |
| 5 | `<LEGACY_SECRET_GATEWAY_OAUTH>` | `/qa/kolon_default/gateway/oauth-client` | Gateway OAuth client_credentials | 확인 필요 |

**실제 시크릿 이름을 이 표에 적지 않는다** — 나중에 운영팀이 PL 을 통해 전달할 때만 별도 비공개 채널로 업데이트.

---

## 3. 이관 절차 (실행 금지 — 설계 기술만)

### 3.1 사전 조건
- [ ] 운영 Secrets Manager 의 실 시크릿 목록 확보 (운영팀 → PL, 별도 채널)
- [ ] AWS 계정 `<ACCOUNT_ID>` 및 리전 확정
- [ ] `/qa/*` 경로에 대한 IAM 권한 (Dev6 `TenantScopedSecretsWrite` 정책) 배포 완료
- [ ] 모든 값이 JSON 인지 단순 string 인지 확인 (`data/secrets.py::_parse_secret_value` 는 양쪽 모두 지원)

### 3.2 수동 이관 (권장 — 운영자 콘솔)
1. 기존 시크릿 조회 (`GetSecretValue`)
2. 신규 경로에 동일 값으로 `CreateSecret` (`Tags=[{Key:tenant_id, Value:kolon_default}]`)
3. 애플리케이션 설정(SSM Parameter or 환경변수)을 신규 경로로 스위치
4. **신규 경로에서 최소 1회 정상 동작 확인 후** — 구 시크릿은 30일 대기 후 삭제 (별도 승인)

### 3.3 스크립트 이관 (선택 — 본 문서 범위 아님)
- 향후 필요 시 `scripts/secrets/migrate_to_qa_prefix.py` 추가 예정
- 현재는 작성하지 않음 (수동 이관 권장 + 검증 용이)

---

## 4. 검증 체크리스트

- [ ] `aws secretsmanager list-secrets --filter "Key=tag-key,Values=tenant_id"` 결과에 신규 5건 존재
- [ ] 애플리케이션 로그에서 `KeyError: secret not found: /qa/kolon_default/...` 없음
- [ ] `data.secrets.get_tenant_secret("kolon_default", "cognito/credentials")` 로 로컬 smoke 성공 (운영 환경에서만, Phase 1 완료 시점)
- [ ] 구 시크릿 접근은 여전히 성공 (롤백 경로 유지 확인)

---

## 5. 롤백

- 신규 시크릿 삭제 (`DeleteSecret RecoveryWindowInDays=7`)
- 애플리케이션 설정을 구 경로로 되돌림
- 30일 내 복구 가능

---

## 6. 금지 사항 (2026-04-17 freeze)

- 자격증명 또는 실 시크릿 값을 본 문서/메모리/로그에 기록 금지
- 스크립트 실행/스케줄/예약 금지
- 구 시크릿 삭제 금지 (최소 30일 병행 운영 후, PL 별도 승인)
- 자동화 도구(예: AWS CLI 배치) 로 일괄 복제 금지 — 수동 콘솔로 항목별 확인
