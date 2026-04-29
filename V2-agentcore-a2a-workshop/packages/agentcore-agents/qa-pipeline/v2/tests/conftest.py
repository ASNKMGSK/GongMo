# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V2 tests conftest — pytest-asyncio auto mode 등록.

Group A / Group B Sub Agent 는 async entrypoint 이므로 @pytest.mark.asyncio
데코레이터가 실제 event loop 를 돌리려면 pytest-asyncio 플러그인 + auto mode 필요.
"""

from __future__ import annotations

import os


# Dev3 Phase D2: Group B Bedrock 실호출 skip (테스트 환경). Dev1 배치 (real Bedrock)
# 는 env unset 으로 실행. `setdefault` 로 외부 override 허용.
os.environ.setdefault("V2_GROUP_B_SKIP_LLM", "1")


def pytest_collection_modifyitems(config, items):
    """async test 함수에 pytest-asyncio 마커 자동 부여 (auto mode)."""
    import inspect
    import pytest

    for item in items:
        if isinstance(item, pytest.Function) and inspect.iscoroutinefunction(item.function):
            item.add_marker(pytest.mark.asyncio)
