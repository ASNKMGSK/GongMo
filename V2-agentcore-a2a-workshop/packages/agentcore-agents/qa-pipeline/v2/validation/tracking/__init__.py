# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""QA 파이프라인 성능 변화 지속 추적.

run_id 별 메트릭 (MAE/RMSE/Bias/MAPE/MaxAbs/Over%/Under%) 을 시계열로 적재하고,
이전 run 대비 회귀 감지 + HTML 트렌드 리포트 생성.
"""

from v2.validation.tracking.tracker import RunRecord, record_run  # noqa: F401
from v2.validation.tracking.trend import (  # noqa: F401
    detect_regressions,
    load_history,
)
