# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V2 서버 진입점. V1 `main.py` 와 독립적으로 기동.

사용 예:
  python -m v2.serving.main_v2
  PORT=8081 python -m v2.serving.main_v2
"""

import logging
import os

import uvicorn


# 로깅 + Bedrock 훅은 `logging_setup.configure()` 에 집약 — server_v2 도 동일 호출을
# 모듈 상단에서 수행하므로 entry point 가 바뀌어도 동일한 설정이 적용된다.
from v2.serving.logging_setup import configure as _configure_logging  # noqa: E402

_configure_logging()
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

from v2.serving.server_v2 import app  # noqa: E402,F401


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))  # V1 8080 충돌 방지
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=_LOG_LEVEL.lower())
