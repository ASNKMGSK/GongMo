"""
CDK Stack for Memory Infrastructure — DynamoDB tables for long-term and short-term memory.
"""

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import aws_cdk as cdk
from aws_cdk import (
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_ssm as ssm,
)
from constructs import Construct


class MemoryInfrastructureStack(Stack):
    """CDK Stack for long-term and short-term memory DynamoDB tables."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.long_term_table = self._create_long_term_table()
        self.session_table = self._create_session_table()
        self._create_ssm_parameters()
        self._create_outputs()

    # ------------------------------------------------------------------
    # Long-term memory (user preferences, facts, summaries)
    # ------------------------------------------------------------------

    def _create_long_term_table(self) -> dynamodb.Table:
        table = dynamodb.Table(
            self,
            "LongTermMemoryTable",
            table_name="a2a-workshop-memory",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )
        table.add_global_secondary_index(
            index_name="type-index",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="memory_type", type=dynamodb.AttributeType.STRING),
        )
        return table

    # ------------------------------------------------------------------
    # Session / short-term memory (conversation turns within a session)
    # ------------------------------------------------------------------

    def _create_session_table(self) -> dynamodb.Table:
        table = dynamodb.Table(
            self,
            "SessionMemoryTable",
            table_name="a2a-workshop-sessions",
            partition_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="turn_index", type=dynamodb.AttributeType.NUMBER),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )
        return table

    # ------------------------------------------------------------------
    # SSM
    # ------------------------------------------------------------------

    def _create_ssm_parameters(self) -> None:
        ssm.StringParameter(
            self, "LongTermTableParam",
            parameter_name="/a2a_memory/long_term_table",
            string_value=self.long_term_table.table_name,
        )
        ssm.StringParameter(
            self, "SessionTableParam",
            parameter_name="/a2a_memory/session_table",
            string_value=self.session_table.table_name,
        )

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------

    def _create_outputs(self) -> None:
        cdk.CfnOutput(self, "LongTermTableName", value=self.long_term_table.table_name, description="Long-term memory DynamoDB table")
        cdk.CfnOutput(self, "SessionTableName", value=self.session_table.table_name, description="Session memory DynamoDB table")
