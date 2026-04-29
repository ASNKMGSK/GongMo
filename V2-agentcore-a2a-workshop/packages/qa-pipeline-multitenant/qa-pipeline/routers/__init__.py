# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-tenant QA pipeline 라우터 묶음.

각 라우터는 `request.state.tenant_id` (TenantMiddleware 가 주입) 를 전제로 동작한다.
ARCHITECTURE.md §6 — 라우터 인터페이스 계약.
"""

from __future__ import annotations

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    """FastAPI app 에 라우터 일괄 등록.

    server.py 에서 호출 — 순서는 라우터 간 경로 충돌이 없으므로 임의.
    """
    # 지연 import — 라우터가 langgraph/boto3 를 import 하는 경우 lazy 로딩
    from .compare import router as compare_router
    from .evaluate import router as evaluate_router
    from .me import router as me_router
    from .wiki import router as wiki_router
    from .xlsx_save import router as xlsx_save_router

    app.include_router(evaluate_router)
    app.include_router(wiki_router)
    app.include_router(compare_router)
    app.include_router(xlsx_save_router)
    app.include_router(me_router)
