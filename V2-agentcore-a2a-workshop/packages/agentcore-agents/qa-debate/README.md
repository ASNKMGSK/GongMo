# QA Debate Service — [Deprecated · Phase 1 mock]

> **⚠ 이 폴더는 레거시입니다.** Phase 2 부터 토론 기능은
> `packages/agentcore-agents/qa-pipeline/v2/debate/` 로 **인프로세스 흡수**
> 되어 기존 LangGraph 파이프라인의 노드로 동작합니다.
>
> - 구 `server.py` (:8001 FastAPI SSE 서버) → **사용 중지**. `/evaluate/stream`
>   (qa-pipeline :8000) 이 토론 이벤트도 함께 emit.
> - 구 `debate/personas.py` · `debate/schemas.py` → Phase 2 통합 시 신규
>   위치로 **이식 완료** (Dev4 Task #7, 2026-04-22).
> - 구 `requirements.txt` (`agent-framework` / `sse-starlette`) → 미사용.
>   새 의존성은 `packages/qa-pipeline-multitenant/qa-pipeline/requirements.txt`
>   의 `ag2[anthropic,bedrock]>=0.9.7` 한 줄로 통합.
>
> **Phase 2 시점(2026-04-23 이후)** 이 폴더는 삭제 예정입니다.
> Dev4 의 Task #7 완료 + 통합 테스트 통과 후 PL 이 일괄 제거.
>
> Phase 2 토론 동작 / 환경변수 / SSE 이벤트 세부는 루트 `CLAUDE.md`
> 의 "### QA Debate (Phase 2)" 섹션 참조.

---

## (참고) Phase 1 mock 동작 요약

독립 :8001 FastAPI 서비스로, 실제 LLM 호출 없이 SSE 이벤트 스트림만 재현하던 mock 스캐폴딩이었습니다. 프론트엔드 토론 뷰 UI 배선 검증이 목적.

### Phase 1 엔드포인트 (참고용, 실사용 금지)

- `POST /debate/stream` — round_start / persona_turn × 3 / moderator_verdict / final
- `GET /health` — `{"status": "healthy", "mode": "mock"}`

### Phase 1 → Phase 2 매핑

| Phase 1 | Phase 2 |
|---|---|
| `qa-debate/server.py` (:8001) | `qa-pipeline/v2/serving/server_v2.py` (:8000) 내 `/evaluate/stream` 이 겸용 |
| `qa-debate/debate/personas.py` | `qa-pipeline/v2/debate/personas.py` (이식 완료) |
| `qa-debate/debate/schemas.py` | `qa-pipeline/v2/debate/schemas.py` (이식 완료 + DebateRecord/RoundRecord 추가) |
| `agent-framework>=1.0.0` | `ag2[anthropic,bedrock]>=0.9.7` 로 교체 |
| SSE events (`round_start` / `persona_turn` / `moderator_verdict` / `final`) | `debate_round_start` / `persona_turn` / `moderator_verdict` / `debate_final` 로 네임스페이스 통일 |
