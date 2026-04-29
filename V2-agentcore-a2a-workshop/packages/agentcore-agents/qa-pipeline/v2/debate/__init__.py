# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""QA Debate — AG2 기반 3-페르소나 그룹채팅 토론 (인프로세스).

Phase 2 에서 별도 서비스 ``qa-debate/`` 를 흡수해 qa-pipeline 안으로 통합.
CLAUDE.md "QA Debate (Phase 2)" 섹션 참조.
"""

from v2.debate.node import debate_node, is_debate_enabled
from v2.debate.run_debate import run_debate
from v2.debate.schemas import (
    DebateRecord,
    DebateRequest,
    DebateResponse,
    ModeratorVerdict,
    PersonaTurn,
    RoundRecord,
    TurnRecord,
    VerdictRecord,
)


__all__ = [
    "DebateRecord",
    "DebateRequest",
    "DebateResponse",
    "ModeratorVerdict",
    "PersonaTurn",
    "RoundRecord",
    "TurnRecord",
    "VerdictRecord",
    "debate_node",
    "is_debate_enabled",
    "run_debate",
]
