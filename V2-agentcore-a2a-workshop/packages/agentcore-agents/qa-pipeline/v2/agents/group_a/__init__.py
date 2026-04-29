# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V2 Group A Sub Agents — Dev2 담당.

4개 Sub Agent (인사예절/경청소통/언어표현/니즈파악) 를 1 LLM 호출로 카테고리 내 항목 동시 평가.

Sub Agent 구성 (CATEGORY_META 와 정합):
  - greeting: greeting_etiquette (#1, #2)
  - listening_comm: listening_communication (#3, #4, #5)
  - language: language_expression (#6, #7)
  - needs: needs_identification (#8, #9)
"""

from v2.agents.group_a.greeting import greeting_sub_agent
from v2.agents.group_a.language import language_sub_agent
from v2.agents.group_a.listening_comm import listening_comm_sub_agent
from v2.agents.group_a.needs import needs_sub_agent


__all__ = [
    "greeting_sub_agent",
    "listening_comm_sub_agent",
    "language_sub_agent",
    "needs_sub_agent",
]
