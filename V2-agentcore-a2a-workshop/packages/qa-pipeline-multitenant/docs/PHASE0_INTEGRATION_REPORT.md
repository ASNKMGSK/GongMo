# Phase 0 Integration Report

> PL: `pl-architect` / Date: 2026-04-17

## 1. 팀 산출물 요약

| Task | Dev | 산출물 | 경로 |
|---|---|---|---|
| #2 | backend-core | middleware/tenant.py + 6 라우터 + `/api/me` + error_response | `qa-pipeline/{middleware/tenant.py, middleware/errors.py, routers/, server.py}` |
| #3 | data-isolation | dynamo/s3/secrets/opensearch 격리 헬퍼 | `qa-pipeline/data/*.py` |
| #4 | pipeline-state | state.py (TenantContext) + graph.py + 20 노드 패치 | `qa-pipeline/{state.py, graph.py, nodes/, config.py}` |
| #5 | tenant-config | TenantConfig + 4 프리셋 + load_prompt | `qa-pipeline/{tenant/, prompts/}` |
| #6 | frontend | TenantProvider + tenantFetch + 스위처 + 브랜딩 | `chatbot-ui/qa_pipeline_reactflow.html` |
| #7 | devops | CDK 3 스택 + rate_limit + audit_log + metrics | `cdk/`, `qa-pipeline/{middleware/rate_limit.py, middleware/audit_log.py, observability/}` |

## 2. 인터페이스 호환성 검증

| 호출측 → 피호출측 | 확인 결과 |
|---|---|
| Dev1 routers → Dev4 `tenant.store.get_config` | OK (KeyError 전파 → 404 `TENANT_NOT_FOUND` 매핑) |
| Dev1 routers → Dev3 `build_initial_state` | OK (tenant dict snapshot 주입 일치) |
| Dev3 nodes → Dev4 `load_prompt(name, *, tenant_id=...)` | OK (20 호출부 일치) |
| Dev2 helpers → Dev6 CDK 테이블명/SK/TTL | OK (완전 일치) |
| Dev1 미들웨어 체인 → Dev6 rate_limit/audit_log | OK (CORS → Tenant → RateLimit → Audit 실측 검증) |
| Dev5 UI `tenantFetch` → Dev1 엔드포인트 | OK (모든 경로 일치, `/health` allowlist) |
| Dev6 rate_limit 429 → Dev1 error_response | OK (통일 envelope) |

## 3. 결함 + 해결

| # | 결함 | 담당 | 해결 |
|---|---|---|---|
| #1 | `nodes/graph` import 실패 (`config.py` 미복사) | Dev3 | 원본 config.py 복사 |
| #2 | `data/s3.py` 가드 순서 (bucket 검사가 tenant_id 선행) | Dev2 | 각 함수 첫 줄에 `_require_tenant_id` 추가 |

## 4. 문서 갱신

- `ARCHITECTURE.md` §2 TenantConfig 메서드 계약 / §7 load_prompt 시그니처 / §10.1 미들웨어 체인 / §10.2 에러 응답 envelope
- `docs/DATA_ISOLATION.md` (Dev2) / `docs/DEPLOY.md` (Dev6) / `docs/STATE_MIGRATION.md` (Dev3) / `docs/TENANT_CONFIG.md` (Dev4)

## 5. Phase 1 권장사항

1. **백필 스크립트 작성** — 기존 단일 테넌트 데이터에 `tenant_id=kolon_default` 를 일괄 주입 (DynamoDB + S3).
2. **Cognito 커스텀 속성 주입 절차** — `custom:tenant_id` / `custom:role` attribute 추가 → 기존 사용자 마이그레이션.
3. **EC2 인플레이스 배포** — 현 코드를 boto3+S3+SSM 방식으로 운영 EC2 (`100.29.183.137`) 에 배포, IP 유지.
4. **IAM Role 승격 (Phase 5)** — CDK 로 생성된 `qa-multitenant-app-role` 을 EC2 인스턴스 프로필로 교체.
5. **통합 QA** — 실제 Bedrock + DynamoDB 경로에서 end-to-end 평가 1회 실행.

## 6. 미해결 TODO (Phase 이후)

- Phase 3 프리셋 3종 (banking/healthcare/telco)
- Phase 5 IAM 태그 기반 격리 실 적용
- `POST /admin/tenants` 를 통한 `kolon_default` 초기 시드 자동화
- OpenSearch 통합 테스트 (실 쿼리 경로)

## 7. 결론

Phase 0 (설계+뼈대+단위 검증) 목표 달성. 인터페이스 충돌 없이 6개 영역 합류. Phase 1 진입 가능.
