# Phase 0.5 Integration Test Report

> PL: `pl-architect` / Date: 2026-04-17
> Scope: `packages/qa-pipeline-multitenant/`

## 1. Summary

| 단계 | 결과 |
|---|---|
| Static import 검증 (18 모듈) | PASS 18/18 |
| Unit tests (pytest) | PASS 42/42 |
| Integration 격리 시뮬 | PASS (tenant A/B 상호 누수 없음) |
| CDK synth (Dev6 selfcheck) | PASS 3 스택 |

## 2. 테스트 카테고리

| 파일 | 테스트 수 | 담당 | 커버리지 |
|---|---|---|---|
| `tests/test_tenant_middleware.py` | 7 | Dev1 | JWT/401/403/400/override/LOCAL_TENANT_ID/health |
| `tests/test_tenant_config.py` | 10 | Dev4 | validate 양/부, to/from_dict, 프리셋 4종 |
| `tests/test_data_isolation.py` | 10 | Dev2 | TENANT_PK, SK 매핑, dynamo/s3/secrets 가드 |
| `tests/test_state_propagation.py` | 5 | Dev3 | require_tenant, build_initial_state |
| `tests/test_prompt_loader.py` | 4 | Dev4 | kwargs-only, 오버라이드 우선순위, 폴백 |
| `tests/test_integration_isolation.py` | 6 | PL | 2-테넌트 격리 시나리오 |
| 합계 | **42** | — | — |

## 3. 결함 라우팅 이력

| # | 결함 | 담당 | 재시도 | 상태 |
|---|---|---|---|---|
| #1 | `config.py` 미복사 → `nodes/graph` import 실패 | Dev3 (pipeline-state) | 1회 | 해결 |
| #2 | `data/s3.py` 의 `_resolve_bucket` 이 tenant_id 가드보다 먼저 실행 | Dev2 (data-isolation) | 1회 | 해결 |

두 결함 모두 1회차 fixup 으로 통과. 중복 라우팅 없음.

## 4. 보조 개선 (ARCHITECTURE.md 반영)

- §10.1 미들웨어 체인 순서 (CORS → Tenant → RateLimit → Audit → Routers) — Dev1/Dev6 모두 반영
- §10.2 에러 응답 JSON envelope (`middleware/errors.py::error_response`) — Dev1 구현, Dev6 의 429 도 통일
- §2 TenantConfig 메서드 계약 명시 (get_config/put_config/to_dict/from_dict/validate)
- §7 load_prompt 시그니처 확정 (`tenant_id` keyword-only + include_preamble + backend)
- §7 업종 프리셋 4종 (industrial/insurance/ecommerce/generic), 3종 (banking/healthcare/telco) 은 Phase 3 예약

## 5. 의존성 (requirements 집계)

| 범주 | 패키지 |
|---|---|
| 필수 | `boto3>=1.34,<2.0`, `botocore`, `fastapi`, `pyjwt[crypto]`, `python-multipart`, `pymupdf` |
| 옵션 | `opensearch-py`, `requests-aws4auth` |
| CDK | `aws-cdk-lib`, `constructs` |

## 6. 미해결 이슈 (Phase 1+ 이관)

1. Phase 5 — IAM `aws:PrincipalTag/tenant_id` 기반 실 EC2 Role 교체 (현재는 기반 정책만 생성).
2. Phase 3 — 업종 프리셋 추가 3종 (banking/healthcare/telco) 온보딩 시 구현.
3. 운영 — `POST /admin/tenants` 로 `kolon_default` 등 초기 테넌트 시드 등록 절차 문서화.
4. OpenSearch 통합 테스트 — 실제 쿼리 경로 추가 (현재는 import + 가드만 검증).

## 7. 결론

Phase 0.5 범위 내 모든 테스트 통과. 구현체 간 인터페이스 호환성 확인 완료. Phase 1 진입 가능.
