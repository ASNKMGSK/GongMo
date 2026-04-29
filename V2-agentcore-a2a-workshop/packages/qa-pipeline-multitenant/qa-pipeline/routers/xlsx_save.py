# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /save-xlsx — 멀티테넌트 확장.

단일 테넌트 원본(packages/agentcore-agents/qa-pipeline/routers/xlsx_save.py) 과 동일한
path traversal 방어 로직을 유지하되, 저장 경로를 다음과 같이 변경한다:

  ~/Desktop/QA평가표 테스트/{tenant_id}/{yyyy-mm-dd}/<filename>.xlsx

`subfolder` 가 명시되면 `{tenant_id}/{subfolder}` 로 사용. 미지정 시 오늘 날짜.
환경변수 `QA_SAVE_ROOT` 로 루트 오버라이드 가능 (기존 동작 유지).
"""

from __future__ import annotations

import logging
import os
import re
from ._tenant_deps import require_tenant_id
from datetime import datetime
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)
router = APIRouter(tags=["save"])

_DEFAULT_ROOT = Path(os.environ.get("QA_SAVE_ROOT") or (Path.home() / "Desktop" / "QA평가표 테스트"))

_SAFE_SEGMENT = re.compile(r"^[\w\s\-\.가-힣()\[\]+,건]+$", re.UNICODE)


def _safe_segment(name: str, label: str) -> str:
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail=f"invalid {label}: {name!r}")
    clean = os.path.basename(name.strip())
    if clean in ("", ".", "..") or "/" in clean or "\\" in clean:
        raise HTTPException(status_code=400, detail=f"invalid {label}: {name!r}")
    if not _SAFE_SEGMENT.match(clean):
        clean = re.sub(r"[^\w\s\-\.가-힣()\[\]+,건]", "_", clean)
        if not clean:
            raise HTTPException(status_code=400, detail=f"invalid {label}: {name!r}")
    return clean


def _safe_tenant_id(tid: str) -> str:
    """tenant_id 는 미들웨어에서 이미 정규식 검증됐지만, 경로 결합 시 한 번 더 방어.

    규칙은 Dev4 TenantConfig 와 동일: ``^[a-z0-9_]{2,64}$``.
    """
    if not re.match(r"^[a-z0-9_]{2,64}$", tid):
        raise HTTPException(status_code=400, detail=f"invalid tenant_id: {tid!r}")
    return tid


@router.post("/save-xlsx")
async def save_xlsx(
    request: Request,
    file: UploadFile = File(...),
    filename: str = Form(...),
    subfolder: str = Form(""),
) -> JSONResponse:
    """xlsx 업로드 → 서버 로컬 디스크에 저장 (테넌트별 디렉토리).

    저장 경로: ``<root>/{tenant_id}/{subfolder or YYYY-MM-DD}/<filename>.xlsx``
    """
    tid = _safe_tenant_id(require_tenant_id(request))

    safe_name = _safe_segment(filename, "filename")
    if not safe_name.lower().endswith((".xlsx", ".xls")):
        safe_name = f"{safe_name}.xlsx"

    if subfolder.strip():
        safe_sub = _safe_segment(subfolder, "subfolder")
    else:
        safe_sub = datetime.now().strftime("%Y-%m-%d")

    root = _DEFAULT_ROOT
    target_dir = root / tid / safe_sub
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error("save-xlsx: mkdir failed (tenant=%s): %s", tid, e)
        raise HTTPException(status_code=500, detail=f"mkdir failed: {e}") from e

    target_path = target_dir / safe_name

    if target_path.exists():
        stem = target_path.stem
        ext = target_path.suffix
        i = 2
        while True:
            candidate = target_dir / f"{stem}_({i}){ext}"
            if not candidate.exists():
                target_path = candidate
                break
            i += 1

    try:
        content = await file.read()
        with open(target_path, "wb") as f:
            f.write(content)
    except Exception as e:
        logger.error("save-xlsx: write failed (tenant=%s): %s", tid, e)
        raise HTTPException(status_code=500, detail=f"write failed: {e}") from e

    # 동명 충돌 시 _(N) 접미사가 붙었을 수 있으므로 최종 저장된 파일명 재계산
    final_filename = target_path.name
    # Dev5 UI 토스트용 상대 경로 — "{tenant_id}/{subfolder}/{filename}"
    relative_path = f"{tid}/{safe_sub}/{final_filename}"

    logger.info("save-xlsx: saved %s (tenant=%s, %d bytes)", target_path, tid, len(content))
    return JSONResponse({
        "ok": True,
        "path": str(target_path),
        "relative_path": relative_path,
        "size": len(content),
        "root": str(root),
        "tenant_id": tid,
        "subfolder": safe_sub,
        "filename": final_filename,
    })


@router.get("/save-xlsx/info")
async def save_xlsx_info(request: Request) -> JSONResponse:
    """저장 루트 정보 — 프론트 표시용. 테넌트 디렉토리 쓰기 가능 여부까지 확인."""
    tid = _safe_tenant_id(require_tenant_id(request))
    root = _DEFAULT_ROOT
    tenant_dir = root / tid
    info: dict[str, Any] = {
        "root": str(root),
        "tenant_dir": str(tenant_dir),
        "tenant_id": tid,
        "exists": tenant_dir.exists(),
        "writable": False,
    }
    try:
        tenant_dir.mkdir(parents=True, exist_ok=True)
        probe = tenant_dir / ".qa_save_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        info["exists"] = True
        info["writable"] = True
    except Exception as e:
        info["error"] = str(e)
    return JSONResponse(info)
