# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Isolated CDK app for the QA Pipeline stack only.

Use when other agent directories (orchestrator-agent, summary-agent, rag-agent,
weather-mcp, …) are not present locally and `python app.py` synth fails.

Deploy:
    cdk deploy A2AWorkshopQaPipelineEc2 --app "python app_qa.py" --require-approval never
"""

import os

import aws_cdk as cdk

from src.stacks.qa_pipeline_ec2_stack import QaPipelineEc2Stack


app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

QaPipelineEc2Stack(
    app,
    "A2AWorkshopQaPipelineEc2",
    description="QA Pipeline on EC2 (t3.medium, SageMaker Qwen3-8B)",
    env=env,
)

app.synth()
