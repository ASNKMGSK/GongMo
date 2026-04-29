# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""/wiki/* — 멀티테넌트 확장.

테넌트별 위키 격리를 위해 파일시스템 루트를 테넌트별로 분리한다:

  <PIPELINE_DIR>/wiki/tenants/{tenant_id}/       — 위키 페이지
  <PIPELINE_DIR>/raw/tenants/{tenant_id}/        — 원본 업로드 파일

단일 테넌트 원본(packages/agentcore-agents/qa-pipeline/routers/wiki.py) 의 로직은 그대로 유지하고
`_tenant_wiki_dir(tid)`, `_tenant_raw_dir(tid)` 로 경로만 테넌트별로 분기한다.

향후 Dev2 (data-isolation) 가 S3 prefix 로 승격할 때 이 모듈의 경로 해석만 교체하면 됨.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from ._tenant_deps import require_tenant_id
from .schemas import WikiIngestRequest, WikiQueryRequest, WikiSaveAnswerRequest
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from typing import Any


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wiki", tags=["wiki"])

_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WIKI_ROOT = os.path.join(_PIPELINE_DIR, "wiki")
_RAW_ROOT = os.path.join(_PIPELINE_DIR, "raw")

_TID_RE = re.compile(r"^[a-z0-9_]{2,64}$")


def _safe_tenant_id(tid: str) -> str:
    if not tid or not _TID_RE.match(tid):
        raise ValueError(f"invalid tenant_id: {tid!r}")
    return tid


def _tenant_wiki_dir(tid: str) -> str:
    path = os.path.join(_WIKI_ROOT, "tenants", _safe_tenant_id(tid))
    os.makedirs(path, exist_ok=True)
    return path


def _tenant_raw_dir(tid: str) -> str:
    path = os.path.join(_RAW_ROOT, "tenants", _safe_tenant_id(tid))
    os.makedirs(path, exist_ok=True)
    return path


def _ingested_meta_path(wiki_dir: str) -> str:
    return os.path.join(wiki_dir, ".ingested.json")


def _safe_path(base_dir: str, filename: str) -> str:
    resolved = os.path.realpath(os.path.join(base_dir, filename))
    base_resolved = os.path.realpath(base_dir)
    if not resolved.startswith(base_resolved + os.sep) and resolved != base_resolved:
        raise ValueError(f"Path traversal blocked: {filename}")
    return resolved


_file_hash_cache: dict[str, tuple[int, float, str]] = {}


def _compute_file_hash(filepath: str) -> str:
    import hashlib

    st = os.stat(filepath)
    key = os.path.realpath(filepath)
    cached = _file_hash_cache.get(key)
    if cached is not None:
        c_size, c_mtime, c_hash = cached
        if c_size == st.st_size and c_mtime == st.st_mtime:
            return c_hash
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _file_hash_cache[key] = (st.st_size, st.st_mtime, digest)
    return digest


def _load_ingested_meta(wiki_dir: str) -> dict[str, Any]:
    path = _ingested_meta_path(wiki_dir)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_ingested_meta(wiki_dir: str, meta: dict[str, Any]) -> None:
    path = _ingested_meta_path(wiki_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _check_ingested_status(wiki_dir: str, fname: str, fpath: str) -> dict[str, Any]:
    current_hash = _compute_file_hash(fpath)
    meta = _load_ingested_meta(wiki_dir)
    entry = meta.get(fname)
    if entry and entry.get("hash") == current_hash:
        return {"ingested": True, "changed": False, "hash": current_hash}
    elif entry:
        return {"ingested": True, "changed": True, "hash": current_hash}
    return {"ingested": False, "changed": False, "hash": current_hash}


def _mark_as_ingested(wiki_dir: str, fname: str, file_hash: str, pages_created: int) -> None:
    import datetime

    meta = _load_ingested_meta(wiki_dir)
    meta[fname] = {
        "hash": file_hash,
        "ingested_at": datetime.datetime.now().isoformat(),
        "pages_created": pages_created,
    }
    _save_ingested_meta(wiki_dir, meta)


def _extract_text(file_bytes: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        try:
            import pymupdf

            doc = pymupdf.open(stream=file_bytes, filetype="pdf")
            pages = []
            for i, page in enumerate(doc):
                text = page.get_text()
                if text.strip():
                    pages.append(f"<!-- page {i + 1} -->\n{text.strip()}")
            doc.close()
            if pages:
                return "\n\n---\n\n".join(pages)
            return "(PDF 텍스트 추출 실패 — 이미지 기반 PDF일 수 있음)"
        except Exception as e:
            logger.warning("PDF extraction failed for %s: %s", filename, e)
            return f"(PDF 텍스트 추출 오류: {e})"

    for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return file_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return file_bytes.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# GET /wiki/raw
# ---------------------------------------------------------------------------


@router.get("/raw")
async def wiki_raw_list(request: Request) -> JSONResponse:
    tid = require_tenant_id(request)
    raw_dir = _tenant_raw_dir(tid)
    wiki_dir = _tenant_wiki_dir(tid)

    def _list_sync() -> dict[str, Any]:
        files = []
        for fname in sorted(os.listdir(raw_dir)):
            try:
                fpath = _safe_path(raw_dir, fname)
            except ValueError:
                continue
            if os.path.isfile(fpath):
                status = _check_ingested_status(wiki_dir, fname, fpath)
                files.append(
                    {
                        "name": fname,
                        "size": os.path.getsize(fpath),
                        "modified": os.path.getmtime(fpath),
                        "ingested": status["ingested"] and not status["changed"],
                        "changed": status["changed"],
                    }
                )
        pending = sum(1 for f in files if not f["ingested"])
        return {"files": files, "total": len(files), "pending": pending, "tenant_id": tid}

    result = await asyncio.to_thread(_list_sync)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /wiki/upload
# ---------------------------------------------------------------------------


@router.post("/upload")
async def wiki_upload(request: Request):
    tid = require_tenant_id(request)
    raw_dir = _tenant_raw_dir(tid)
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload_file = form.get("file")
        if not upload_file:
            return JSONResponse(status_code=400, content={"error": "No file provided", "tenant_id": tid})
        filename = upload_file.filename
        file_bytes = await upload_file.read()
    else:
        body = await request.json()
        filename = body.get("filename", "untitled.md")
        text_content = body.get("content", "")
        if not text_content:
            return JSONResponse(status_code=400, content={"error": "No content provided", "tenant_id": tid})
        file_bytes = text_content.encode("utf-8")

    original_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    extracted_text = _extract_text(file_bytes, filename)

    base_name = filename.rsplit(".", 1)[0] if "." in filename else filename
    safe_name = "".join(c for c in base_name if c.isalnum() or c in "-_ ()가-힣ㄱ-ㅎㅏ-ㅣ").strip()
    if not safe_name:
        safe_name = "uploaded_file"
    safe_name = safe_name + ".md"

    header = (
        f"---\n"
        f"source_file: {filename}\n"
        f"original_format: {original_ext}\n"
        f"extracted_chars: {len(extracted_text)}\n"
        f"tenant_id: {tid}\n"
        f"---\n\n"
    )
    save_content = header + extracted_text

    try:
        save_path = _safe_path(raw_dir, safe_name)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid filename", "tenant_id": tid})

    def _write():
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(save_content)

    await asyncio.to_thread(_write)

    logger.info(
        "Wiki upload: %s → %s (%d chars, tenant=%s, from %s)",
        filename,
        safe_name,
        len(extracted_text),
        tid,
        original_ext or "text",
    )

    return JSONResponse(
        {
            "status": "uploaded",
            "filename": safe_name,
            "original_file": filename,
            "original_format": original_ext,
            "extracted_chars": len(extracted_text),
            "size": len(file_bytes),
            "path": f"raw/tenants/{tid}/{safe_name}",
            "tenant_id": tid,
        }
    )


# ---------------------------------------------------------------------------
# POST /wiki/ingest
# ---------------------------------------------------------------------------


@router.post("/ingest")
async def wiki_ingest(payload: WikiIngestRequest, request: Request) -> EventSourceResponse:
    import datetime
    from langchain_core.messages import HumanMessage, SystemMessage
    from nodes.llm import get_chat_model, invoke_and_parse

    tid = require_tenant_id(request)
    raw_dir = _tenant_raw_dir(tid)
    wiki_dir = _tenant_wiki_dir(tid)

    body = payload.model_dump()
    filename = body.get("filename", "")
    if filename.startswith("raw/"):
        filename = filename[4:]

    try:
        filepath = _safe_path(raw_dir, filename)
    except ValueError:

        async def _err_traversal():
            yield {"event": "error", "data": json.dumps({"message": "Invalid filename", "tenant_id": tid})}

        return EventSourceResponse(_err_traversal())
    if not os.path.isfile(filepath):

        async def _err():
            yield {
                "event": "error",
                "data": json.dumps({"message": f"File not found: {filename}", "tenant_id": tid}),
            }

        return EventSourceResponse(_err())

    status = _check_ingested_status(wiki_dir, filename, filepath)
    if status["ingested"] and not status["changed"] and not body.get("force"):

        async def _skip():
            meta = _load_ingested_meta(wiki_dir)
            entry = meta.get(filename, {})
            yield {
                "event": "wiki_done",
                "data": json.dumps(
                    {
                        "filename": filename,
                        "skipped": True,
                        "message": f"동일 파일 — 이미 Ingest 완료 ({entry.get('pages_created', '?')}페이지, {entry.get('ingested_at', '?')})",
                        "pages_written": 0,
                        "tenant_id": tid,
                    },
                    ensure_ascii=False,
                ),
            }

        return EventSourceResponse(_skip())

    file_hash = status["hash"]

    async def _stream():
        ingest_start = time.time()

        with open(filepath, encoding="utf-8", errors="replace") as f:
            source_content = f.read()

        yield {
            "event": "wiki_progress",
            "data": json.dumps(
                {
                    "step": 1,
                    "total": 3,
                    "label": "소스 파일 읽기" + (" (내용 변경 감지)" if status["changed"] else ""),
                    "detail": f"{filename} ({len(source_content)} chars)",
                    "tenant_id": tid,
                },
                ensure_ascii=False,
            ),
        }

        existing_pages = []
        for _root, _dirs, _files in os.walk(wiki_dir):
            for _f in _files:
                if _f.endswith(".md") and _f not in ("SCHEMA.md", "index.md", "log.md"):
                    rel = os.path.relpath(os.path.join(_root, _f), wiki_dir).replace("\\", "/")
                    existing_pages.append(rel)
        existing_list = ", ".join(existing_pages[:50])

        llm = get_chat_model(temperature=0.1, max_tokens=1024)

        analysis_prompt = (
            "QA 위키 Ingest 분석. 소스 문서를 읽고 위키 페이지 계획을 수립하라.\n\n"
            "JSON으로만 응답 (마크다운 코드 펜스 없이):\n"
            '{"doc_type":"qa_rules|case|policy|general",'
            '"summary":"요약 1-2문장",'
            '"pages":[{"path":"카테고리/영문파일명.md","title":"한국어 제목"}],'
            '"update_pages":["기존 페이지 중 업데이트 필요한 경로"]}\n\n'
            "규칙: path 카테고리는 qa_rules/policy/case/guides 중 선택. "
            "여러 주제면 여러 페이지, 단일 주제면 1개. 파일명은 영문 snake_case.\n"
            "update_pages: 기존 위키 페이지 중 이 소스와 관련되어 내용 보강이 필요한 페이지."
        )

        try:
            analysis = await invoke_and_parse(
                llm,
                [
                    SystemMessage(content=analysis_prompt),
                    HumanMessage(
                        content=f"파일명: {filename}\n\n기존 위키 페이지: {existing_list}\n\n내용:\n{source_content[:7000]}"
                    ),
                ],
            )
        except Exception as e:
            logger.error("Wiki ingest analysis error (tenant=%s): %s", tid, e)
            base = filename.rsplit(".", 1)[0]
            analysis = {
                "doc_type": "general",
                "summary": str(e),
                "pages": [{"path": f"sources/{base}.md", "title": filename}],
            }

        page_plan = analysis.get("pages", [])
        if not page_plan:
            base = filename.rsplit(".", 1)[0]
            doc_type = analysis.get("doc_type", "general")
            page_plan = [{"path": f"{doc_type}/{base}.md", "title": filename}]

        yield {
            "event": "wiki_progress",
            "data": json.dumps(
                {
                    "step": 2,
                    "total": 3,
                    "label": f"분석 완료 — {len(page_plan)}개 페이지 생성 중",
                    "detail": f"유형: {analysis.get('doc_type', '?')}",
                    "tenant_id": tid,
                },
                ensure_ascii=False,
            ),
        }

        gen_llm = get_chat_model(temperature=0.1, max_tokens=4096)

        gen_system = (
            "QA 위키 페이지 작성기. 주어진 소스에서 해당 주제의 위키 페이지를 마크다운으로 작성하라.\n"
            "첫 줄부터 YAML frontmatter(---로 감싸기: title, type, sources) 시작.\n"
            "한국어 작성. 핵심 정보 위주로 간결하게. QA 체크포인트 포함."
        )

        async def _gen_page(page_info: dict) -> tuple[str, str]:
            resp = await gen_llm.ainvoke(
                [
                    SystemMessage(content=gen_system),
                    HumanMessage(
                        content=(
                            f"위키 페이지 제목: {page_info['title']}\n"
                            f"원본 소스: raw/tenants/{tid}/{filename}\n\n"
                            f"소스 문서 내용:\n{source_content[:6000]}"
                        )
                    ),
                ]
            )
            return page_info["path"], resp.content

        try:
            results = await asyncio.gather(*[_gen_page(p) for p in page_plan], return_exceptions=True)
        except Exception as e:
            logger.error("Wiki ingest parallel generation error (tenant=%s): %s", tid, e)
            results = []

        pages_written = 0
        for res in results:
            if isinstance(res, Exception):
                logger.error("Wiki ingest page gen error: %s", res)
                continue
            page_path, content = res
            if not page_path or not content:
                continue
            try:
                full_path = os.path.join(wiki_dir, page_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                pages_written += 1
            except Exception as e:
                logger.error("Wiki ingest page write error for %s: %s", page_path, e)

        index_path = os.path.join(wiki_dir, "index.md")
        if os.path.isfile(index_path):
            with open(index_path, "a", encoding="utf-8") as f:
                f.write(
                    f"\n- [{filename}] — Ingested {datetime.date.today().isoformat()}, "
                    f"{analysis.get('doc_type', '?')} ({pages_written} pages)"
                )

        log_path = os.path.join(wiki_dir, "log.md")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"\n## [{datetime.date.today().isoformat()}] ingest | {filename}\n"
                f"- 유형: {analysis.get('doc_type', '?')}\n"
                f"- 요약: {analysis.get('summary', 'N/A')}\n"
                f"- 생성 페이지: {pages_written}개\n"
            )

        _mark_as_ingested(wiki_dir, filename, file_hash, pages_written)

        elapsed = round(time.time() - ingest_start, 1)

        yield {
            "event": "wiki_progress",
            "data": json.dumps(
                {
                    "step": 3,
                    "total": 3,
                    "label": f"완료 — {pages_written}개 페이지 저장",
                    "detail": "index.md, log.md, .ingested.json 갱신",
                    "tenant_id": tid,
                },
                ensure_ascii=False,
            ),
        }

        yield {
            "event": "wiki_done",
            "data": json.dumps(
                {
                    "filename": filename,
                    "doc_type": analysis.get("doc_type", "?"),
                    "summary": analysis.get("summary", ""),
                    "pages_written": pages_written,
                    "elapsed_seconds": elapsed,
                    "message": f"Ingest 완료: {filename} → {pages_written}개 위키 페이지 ({elapsed}초)",
                    "tenant_id": tid,
                },
                ensure_ascii=False,
            ),
        }

    return EventSourceResponse(_stream())


# ---------------------------------------------------------------------------
# DELETE /wiki/raw/{filename}
# ---------------------------------------------------------------------------


@router.delete("/raw/{filename}")
async def wiki_raw_delete(filename: str, request: Request) -> JSONResponse:
    tid = require_tenant_id(request)
    raw_dir = _tenant_raw_dir(tid)
    try:
        filepath = _safe_path(raw_dir, filename)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid filename", "tenant_id": tid})
    if not os.path.isfile(filepath):
        return JSONResponse(status_code=404, content={"error": f"File not found: {filename}", "tenant_id": tid})
    os.remove(filepath)
    return JSONResponse({"status": "deleted", "filename": filename, "tenant_id": tid})


# ---------------------------------------------------------------------------
# GET /wiki/status
# ---------------------------------------------------------------------------


@router.get("/status")
async def wiki_status(request: Request) -> JSONResponse:
    tid = require_tenant_id(request)
    wiki_dir = _tenant_wiki_dir(tid)
    pages: dict[str, int] = {}
    total_bytes = 0
    for root, _dirs, files in os.walk(wiki_dir):
        md_files = [f for f in files if f.endswith(".md") and f not in ("SCHEMA.md", "index.md", "log.md")]
        if md_files:
            rel = os.path.relpath(root, wiki_dir).replace("\\", "/")
            pages[rel] = len(md_files)
        for f in files:
            if f.endswith(".md"):
                total_bytes += os.path.getsize(os.path.join(root, f))

    log_path = os.path.join(wiki_dir, "log.md")
    last_build = None
    if os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("## ["):
                    last_build = line.split("]")[0].replace("## [", "")

    total_pages = sum(pages.values())
    return JSONResponse(
        {
            "status": "built" if total_pages > 0 else "empty",
            "total_pages": total_pages,
            "total_bytes": total_bytes,
            "pages_by_category": pages,
            "last_build": last_build,
            "tenant_id": tid,
        }
    )


# ---------------------------------------------------------------------------
# GET /wiki/search
# ---------------------------------------------------------------------------


@router.get("/search")
async def wiki_search(request: Request, q: str = "") -> JSONResponse:
    tid = require_tenant_id(request)
    wiki_dir = _tenant_wiki_dir(tid)
    if not q.strip():
        return JSONResponse({"results": [], "query": q, "tenant_id": tid})

    import re as _re

    def _search_sync() -> dict[str, Any]:
        query_terms = q.lower().split()
        results = []
        for root, _dirs, files in os.walk(wiki_dir):
            for fname in files:
                if not fname.endswith(".md") or fname in ("SCHEMA.md", "log.md", ".ingested.json"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, wiki_dir).replace("\\", "/")
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read()

                content_lower = content.lower()
                score = sum(content_lower.count(t) for t in query_terms)
                title = ""
                title_match = _re.search(r"^#\s+(.+)", content, _re.MULTILINE)
                if title_match:
                    title = title_match.group(1).strip()
                    if any(t in title.lower() for t in query_terms):
                        score += 10

                if score > 0:
                    snippet = ""
                    for t in query_terms:
                        idx = content_lower.find(t)
                        if idx >= 0:
                            start = max(0, idx - 60)
                            end = min(len(content), idx + 100)
                            snippet = content[start:end].replace("\n", " ").strip()
                            break

                    results.append(
                        {"path": rel, "title": title or fname, "score": score, "snippet": snippet, "size": len(content)}
                    )

        results.sort(key=lambda x: x["score"], reverse=True)
        return {"results": results[:20], "query": q, "total": len(results), "tenant_id": tid}

    result = await asyncio.to_thread(_search_sync)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /wiki/query
# ---------------------------------------------------------------------------


@router.post("/query")
async def wiki_query(payload: WikiQueryRequest, request: Request) -> EventSourceResponse:
    from langchain_core.messages import HumanMessage, SystemMessage
    from nodes.llm import get_chat_model

    tid = require_tenant_id(request)
    wiki_dir = _tenant_wiki_dir(tid)
    question = payload.question.strip()
    if not question:

        async def _err():
            yield {"event": "error", "data": json.dumps({"message": "question is required.", "tenant_id": tid})}

        return EventSourceResponse(_err())

    async def _stream():
        query_start = time.time()

        index_path = os.path.join(wiki_dir, "index.md")
        index_content = ""
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as f:
                index_content = f.read()

        yield {
            "event": "wiki_progress",
            "data": json.dumps(
                {"step": 1, "total": 3, "label": "인덱스 검색 중", "tenant_id": tid}, ensure_ascii=False
            ),
        }

        llm = get_chat_model(temperature=0.1, max_tokens=1024)

        select_prompt = (
            "위키 인덱스를 읽고, 질문에 답하기 위해 읽어야 할 페이지를 선택하라.\n"
            'JSON으로만 응답: {"pages": ["경로1.md", "경로2.md", ...], "reason": "선택 이유"}\n'
            "최대 5개 페이지. 관련 없으면 빈 리스트."
        )

        try:
            from nodes.llm import invoke_and_parse

            selection = await invoke_and_parse(
                llm,
                [
                    SystemMessage(content=select_prompt),
                    HumanMessage(content=f"질문: {question}\n\n위키 인덱스:\n{index_content}"),
                ],
            )
            selected_pages = selection.get("pages", [])
        except Exception as e:
            logger.error("Wiki query page selection error (tenant=%s): %s", tid, e)
            selected_pages = []

        pages_content = []
        for page_rel in selected_pages:
            page_path = os.path.join(wiki_dir, page_rel)
            if os.path.isfile(page_path):
                with open(page_path, encoding="utf-8", errors="replace") as f:
                    pages_content.append(f"--- {page_rel} ---\n{f.read()}")

        if not pages_content:
            terms = question.lower().split()
            for root, _dirs, files in os.walk(wiki_dir):
                for fname in files:
                    if not fname.endswith(".md") or fname in ("SCHEMA.md", "log.md", "index.md"):
                        continue
                    fpath = os.path.join(root, fname)
                    with open(fpath, encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    if any(t in content.lower() for t in terms):
                        rel = os.path.relpath(fpath, wiki_dir).replace("\\", "/")
                        pages_content.append(f"--- {rel} ---\n{content}")
                    if len(pages_content) >= 5:
                        break

        yield {
            "event": "wiki_progress",
            "data": json.dumps(
                {"step": 2, "total": 3, "label": f"{len(pages_content)}개 페이지 참조", "tenant_id": tid},
                ensure_ascii=False,
            ),
        }

        answer_llm = get_chat_model(temperature=0.2, max_tokens=4096)
        answer_prompt = (
            "QA 위키 전문가. 위키 페이지를 참조하여 질문에 답변하라.\n\n"
            "규칙:\n"
            "- 위키 내용을 근거로 답변 (출처 페이지 명시)\n"
            "- 위키에 없는 내용은 '위키에 해당 정보 없음'으로 표시\n"
            "- 한국어로 답변\n"
            "- 마크다운 형식\n"
        )

        wiki_context = "\n\n".join(pages_content) if pages_content else "(관련 위키 페이지 없음)"

        try:
            resp = await answer_llm.ainvoke(
                [
                    SystemMessage(content=answer_prompt),
                    HumanMessage(content=f"질문: {question}\n\n참조 위키 페이지:\n{wiki_context[:12000]}"),
                ]
            )
            answer = resp.content
        except Exception as e:
            logger.error("Wiki query answer error (tenant=%s): %s", tid, e)
            answer = f"답변 생성 오류: {e}"

        elapsed = round(time.time() - query_start, 1)

        yield {
            "event": "wiki_progress",
            "data": json.dumps(
                {"step": 3, "total": 3, "label": "답변 완료", "tenant_id": tid}, ensure_ascii=False
            ),
        }

        yield {
            "event": "wiki_answer",
            "data": json.dumps(
                {
                    "question": question,
                    "answer": answer,
                    "pages_referenced": selected_pages,
                    "elapsed_seconds": elapsed,
                    "tenant_id": tid,
                },
                ensure_ascii=False,
            ),
        }

    return EventSourceResponse(_stream())


# ---------------------------------------------------------------------------
# POST /wiki/save-answer
# ---------------------------------------------------------------------------


@router.post("/save-answer")
async def wiki_save_answer(payload: WikiSaveAnswerRequest, request: Request) -> JSONResponse:
    import datetime

    tid = require_tenant_id(request)
    wiki_dir = _tenant_wiki_dir(tid)

    question = payload.question
    answer = payload.answer
    title = payload.title

    if not answer:
        return JSONResponse(status_code=400, content={"error": "answer is required", "tenant_id": tid})

    if not title:
        title = question[:50] if question else "Untitled"
    safe_name = "".join(c for c in title if c.isalnum() or c in "-_ ").strip().replace(" ", "_").lower()
    if not safe_name:
        safe_name = f"query_{int(time.time())}"
    safe_name = safe_name[:60] + ".md"

    today = datetime.date.today().isoformat()
    page_content = (
        f'---\ntitle: "{title}"\ntype: query\n'
        f'question: "{question}"\ntenant_id: "{tid}"\ncreated: {today}\n---\n\n'
        f"# {title}\n\n"
        f"> 질문: {question}\n\n"
        f"{answer}\n"
    )

    queries_dir = os.path.join(wiki_dir, "queries")
    os.makedirs(queries_dir, exist_ok=True)
    page_path = os.path.join(queries_dir, safe_name)
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(page_content)

    index_path = os.path.join(wiki_dir, "index.md")
    if os.path.isfile(index_path):
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(f"\n- [queries/{safe_name}](queries/{safe_name}) — Query: {question[:80]}")

    log_path = os.path.join(wiki_dir, "log.md")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n## [{today}] query_saved | {title}\n- 질문: {question}\n")

    rel_path = f"queries/{safe_name}"
    return JSONResponse({"status": "saved", "path": rel_path, "title": title, "tenant_id": tid})
