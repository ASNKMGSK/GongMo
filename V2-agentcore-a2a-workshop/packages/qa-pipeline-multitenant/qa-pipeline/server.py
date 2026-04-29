# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI server — Multi-tenant QA evaluation pipeline.

단일 테넌트 원본(`packages/agentcore-agents/qa-pipeline/server.py`)의 구조·엔드포인트를 그대로 유지하며
테넌트 식별 미들웨어와 테넌트 관리 라우터를 추가한다.

라우터:
  routers/evaluate.py   -- /evaluate, /evaluate/stream, /evaluate/csv-compatible, /evaluate/pentagon
  routers/wiki.py       -- /wiki/* (테넌트별 wiki 디렉토리)
  routers/compare.py    -- /analyze-compare, /analyze-manual-compare
  routers/xlsx_save.py  -- /save-xlsx (테넌트별 저장 경로)
  routers/me.py         -- /api/me, /api/tenants, /admin/tenants

미들웨어 스택 (ARCHITECTURE.md §10.1 — 요청 도착 → 응답 발송):
  1) CORS                  (최외부)
  2) TenantMiddleware      — JWT → request.state.tenant_id
  3) RateLimitMiddleware   — tenant_id 기반 분당 카운터 (Dev6)
  4) AuditLogMiddleware    — 응답 후 1행 기록 (Dev6)
  5) Routers

주의: Starlette `add_middleware` 는 **마지막에 add 한 것이 가장 바깥** 에 위치한다.
따라서 위 순서를 만들려면 **역순** 으로 add 해야 한다 (Audit → RateLimit → Tenant → CORS).

에러 응답: ARCHITECTURE.md §10.2 규격. `middleware/errors.py::error_response` 를 통해
HTTPException / 미처리 Exception / 미들웨어 거부 응답 모두 동일한 JSON 스키마 사용.
"""

# ---------------------------------------------------------------------------
# 표준 라이브러리 및 서드파티 임포트
# ---------------------------------------------------------------------------
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from typing import Any


# qa-pipeline 패키지 루트를 sys.path에 추가하여 하위 모듈(graph, nodes 등)을 임포트할 수 있게 함
_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

# AgentCore Runtime 120초 초기화 제한 대응 — 무거운 의존성은 첫 호출 시점에 lazy-load.
build_graph = None  # type: ignore[assignment]
LLMTimeoutError = None  # type: ignore[assignment]
to_csv_compatible = None  # type: ignore[assignment]
evaluate_pentagon_direct = None  # type: ignore[assignment]


def _lazy_deps() -> None:
    """첫 호출 시점에 무거운 모듈 import (graph + langgraph + nodes + transforms)."""
    global build_graph, LLMTimeoutError, to_csv_compatible, evaluate_pentagon_direct
    if build_graph is not None:
        return
    from graph import build_graph as _bg  # noqa: WPS433
    from nodes.llm import LLMTimeoutError as _LTE  # noqa: WPS433
    from pentagon_direct import evaluate_pentagon_direct as _epd  # noqa: WPS433
    from transforms import to_csv_compatible as _tcc  # noqa: WPS433

    build_graph = _bg
    LLMTimeoutError = _LTE
    evaluate_pentagon_direct = _epd
    to_csv_compatible = _tcc


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

SHUTDOWN_GRACE_SECONDS: float = float(os.environ.get("SHUTDOWN_GRACE_SECONDS", "630"))


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    """FastAPI lifespan — startup/shutdown 훅.

    Shutdown: routers.evaluate._active_runs 에 등록된 모든 태스크가 끝날 때까지 대기.
    """
    logger.info("QA pipeline (multi-tenant) starting up")
    yield

    from routers.evaluate import _active_runs

    if not _active_runs:
        logger.info("QA pipeline shutdown — no active runs")
        return

    logger.info(
        "QA pipeline shutdown — waiting for %d active run(s) (grace %ss)",
        len(_active_runs),
        SHUTDOWN_GRACE_SECONDS,
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(*list(_active_runs), return_exceptions=True), timeout=SHUTDOWN_GRACE_SECONDS
        )
        logger.info("All active runs completed")
    except TimeoutError:
        remaining = len(_active_runs)
        logger.warning("Shutdown grace period expired — %d run(s) still active", remaining)


# ---------------------------------------------------------------------------
# FastAPI 앱 초기화
# ---------------------------------------------------------------------------

app = FastAPI(title="QA Evaluation Pipeline (Multi-Tenant)", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 미들웨어 등록 — ARCHITECTURE.md §10.1
# Starlette add_middleware 는 마지막 add 가 가장 바깥. 따라서 요청 순서
# CORS → Tenant → RateLimit → Audit → Routers 를 만들려면 역순 add:
#   1) AuditLogMiddleware   (최내부, 라우터 직전)
#   2) RateLimitMiddleware
#   3) TenantMiddleware
#   4) CORSMiddleware       (최외부)
# ---------------------------------------------------------------------------

from middleware.audit_log import AuditLogMiddleware  # noqa: E402
from middleware.rate_limit import RateLimitMiddleware  # noqa: E402
from middleware.tenant import TenantMiddleware  # noqa: E402


app.add_middleware(AuditLogMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# ---------------------------------------------------------------------------
# 전역 Exception Handler — §10.2 규격 통일
# ---------------------------------------------------------------------------

from fastapi import HTTPException  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from middleware.errors import error_response  # noqa: E402


_HTTP_STATUS_TO_CODE: dict[int, str] = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "TENANT_MISMATCH",
    404: "TENANT_NOT_FOUND",
    429: "RATE_LIMITED",
}


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Raise 된 HTTPException 을 §10.2 규격으로 변환. Starlette 기본 핸들러 대체."""
    code = _HTTP_STATUS_TO_CODE.get(exc.status_code, "INTERNAL" if exc.status_code >= 500 else "INVALID_REQUEST")
    return error_response(
        code,
        str(exc.detail) if exc.detail is not None else "",
        request=request,
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    """Pydantic 검증 실패 → 400 INVALID_REQUEST."""
    logger.info("validation error on %s: %s", request.url.path, exc.errors())
    return error_response("INVALID_REQUEST", "request validation failed", request=request)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """미처리 예외 → 500 INTERNAL."""
    logger.error("unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
    return error_response("INTERNAL", "internal server error", request=request)

# ---------------------------------------------------------------------------
# Router 등록
# ---------------------------------------------------------------------------

from routers import register_routers  # noqa: E402


register_routers(app)


# ---------------------------------------------------------------------------
# LangGraph 컴파일 — lazy init
# ---------------------------------------------------------------------------

_graph_lock = asyncio.Lock()
_compiled_graph: Any | None = None
_graph_build_error: str | None = None


async def _get_compiled_graph() -> Any:
    global _compiled_graph, _graph_build_error
    if _compiled_graph is not None:
        return _compiled_graph
    async with _graph_lock:
        if _compiled_graph is not None:
            return _compiled_graph
        try:
            _lazy_deps()
            _compiled_graph = build_graph()
            _graph_build_error = None
            logger.info("LangGraph compiled successfully")
            return _compiled_graph
        except Exception as e:
            _graph_build_error = str(e)
            logger.error("LangGraph build failed: %s", e, exc_info=True)
            raise


# ---------------------------------------------------------------------------
# AgentCore Runtime (HTTP protocol) 규약 — tenant middleware exempt paths
# ---------------------------------------------------------------------------


@app.get("/ping")
async def ping() -> JSONResponse:
    """AgentCore Runtime health probe — 200 OK 만 반환."""
    return JSONResponse({"status": "ok"})


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    """AgentCore Runtime invoke entrypoint — /evaluate 로 위임.

    주의: /invocations 는 tenant middleware 를 반드시 통과해야 한다.
    TenantMiddleware._EXEMPT_PATHS 에 포함되지 않으므로 JWT 또는 LOCAL_TENANT_ID 필요.
    """
    from routers._tenant_deps import tenant_context
    from routers.evaluate import _evaluate_impl

    body = await request.json()
    return await _evaluate_impl(body, tenant_context(request))


# ---------------------------------------------------------------------------
# GET /health / GET /readyz — 헬스/레디니스 프로브 (tenant middleware exempt)
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "qa-pipeline-multitenant", "version": "2.0.0"})


def _check_filesystem() -> dict[str, Any]:
    """wiki/raw 루트 존재 + 쓰기 가능 여부 확인."""
    from routers.wiki import _RAW_ROOT, _WIKI_ROOT

    results: dict[str, Any] = {}
    for name, path in [("wiki_root", _WIKI_ROOT), ("raw_root", _RAW_ROOT)]:
        entry: dict[str, Any] = {"path": path}
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".readyz_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            entry["ok"] = True
            entry["error"] = None
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)
        results[name] = entry
    return results


@app.get("/readyz")
async def readyz() -> JSONResponse:
    graph_built = False
    graph_error: str | None = None
    try:
        await _get_compiled_graph()
        graph_built = True
    except Exception as e:
        graph_error = _graph_build_error or str(e)

    fs = await asyncio.to_thread(_check_filesystem)
    fs_ok = all(entry.get("ok") for entry in fs.values())

    ready = graph_built and fs_ok
    payload: dict[str, Any] = {
        "status": "ready" if ready else "not_ready",
        "graph_built": graph_built,
        "filesystem": fs,
    }
    if graph_error:
        payload["error"] = graph_error

    return JSONResponse(status_code=200 if ready else 503, content=payload)


# ---------------------------------------------------------------------------
# 정적 HTML 서빙
# ---------------------------------------------------------------------------

_UI_DIR = os.path.join(os.path.dirname(_PIPELINE_DIR), "chatbot-ui")
_DESKTOP_DIR = os.path.expanduser("~/Desktop")

_HTML_FILES: dict[str, str] = {}


def _find_html(name: str) -> str | None:
    if name in _HTML_FILES:
        return _HTML_FILES[name]
    for d in [_UI_DIR, os.path.join(_DESKTOP_DIR, "업무", "qa", "agentcore-a2a-workshop", "packages", "chatbot-ui")]:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            _HTML_FILES[name] = p
            return p
    p = os.path.join(_DESKTOP_DIR, name)
    if os.path.isfile(p):
        _HTML_FILES[name] = p
        return p
    return None


@app.get("/ui/{filename}")
async def serve_html(filename: str):
    if not filename.endswith(".html"):
        return JSONResponse(status_code=400, content={"error": "Only .html files"})
    path = _find_html(filename)
    if path:
        headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        return FileResponse(path, media_type="text/html", headers=headers)
    return JSONResponse(status_code=404, content={"error": f"{filename} not found"})


@app.get("/ui")
async def ui_index():
    files = []
    for d in [
        _UI_DIR,
        os.path.join(_DESKTOP_DIR, "업무", "qa", "agentcore-a2a-workshop", "packages", "chatbot-ui"),
        _DESKTOP_DIR,
    ]:
        if os.path.isdir(d):
            files.extend(f for f in os.listdir(d) if f.endswith(".html") and "qa" in f.lower())
    unique = sorted(set(files))
    links = "".join(f'<li><a href="/ui/{f}">{f}</a></li>' for f in unique)
    return HTMLResponse(f"<h2>QA Dashboard Pages</h2><ul>{links}</ul>")


# ---------------------------------------------------------------------------
# 직접 실행 시 uvicorn 기동 (기본 포트: 8100)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8100"))
    logger.info("Starting QA Pipeline (Multi-Tenant) server on port %s", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
