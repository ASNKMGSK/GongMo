# QA 파이프라인 API 문서

V2 QA 파이프라인의 공개 API 전체 명세는 **`v2/API.md`** 에 정리되어 있습니다.

- 경로: [`v2/API.md`](v2/API.md)
- 대상 컴포넌트: `v2/graph_v2.py`, `v2/serving/server_v2.py`, `v2/serving/main_v2.py`, `v2/scripts/run_direct_batch_v2.py`
- 포함 내용:
  - HTTP 엔드포인트 (`/ping` / `/health` / `/readyz` / `/evaluate` / `/invocations`)
  - 요청 / 응답 스키마 (`QAStateV2` → `QAOutputV2`)
  - Sub Agent 공통 응답 포맷 (`SubAgentResponse` / `ItemVerdict` / `EvidenceQuote`)
  - `evaluation_mode` / `ALLOWED_STEPS` / `Tier` / `Confidence` / `Override` 참조 표
  - RAG 3종 시그니처 (`retrieve_fewshot` / `retrieve_reasoning` / `retrieve_knowledge`)
  - Python 모듈 import 가이드 (`build_graph_v2`, `QAOutputV2` 등)
  - 배치 실행 가이드 (`run_direct_batch_v2.py`, 환경 변수 목록)
  - 환경 변수 전체 목록
  - Tenant 추가 방법 (`v2/tenants/<tenant_id>/`)

> V1 3-Phase 파이프라인 (`greeting` / `understanding` / `courtesy` / `mandatory` / `scope` / `proactiveness` / `work_accuracy` / `incorrect_check` / `consistency_check` / `report_generator`) 관련 legacy API 문서는 V2 전환 (2026-04-20) 과 함께 제거되었습니다. V2 4-Layer 아키텍처만 공식 지원합니다.

개요는 `README.md` 를 참조하세요.
