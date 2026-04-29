#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CDK Application for QA Evaluation Agents — 기존 환경과 완전히 독립.

기존 app.py의 스택에는 영향을 주지 않습니다.
별도 스택 이름(QAWorkshop*)을 사용하여 격리합니다.
"""

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(**kwargs):
        pass

import aws_cdk as cdk

from src.stacks.incorrect_check_agent_stack import IncorrectCheckAgentStack

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

app = cdk.App()

# --- QA Incorrect Check Agent (MCP protocol) ---
incorrect_check_stack = IncorrectCheckAgentStack(
    app,
    "QAWorkshopIncorrectCheckAgent",
    description="QA Incorrect Check Agent — process compliance evaluation",
)

app.synth()
