"""
CDK Stack for the AgentCore Gateway — single entry point for all MCP tools.

Routes MCP tool calls to:
  - Weather MCP Server (weather, forecasts, alerts)
  - RAG Agent MCP Server (document search, knowledge base)
  - Summary Agent MCP Server (summarization, aggregation)
"""

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import Optional

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_bedrock_agentcore_alpha as agentcore,
    aws_iam as iam,
    aws_ssm as ssm,
)
from cdk_nag import NagSuppressions
from constructs import Construct


class AgentCoreGatewayStack(Stack):
    """CDK Stack for AgentCore Gateway — single entry point for all agent MCP targets."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        weather_mcp_stack: Optional[object] = None,
        rag_agent_stack: Optional[object] = None,
        summary_agent_stack: Optional[object] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.gateway = agentcore.Gateway(
            self,
            "WorkshopGateway",
            gateway_name="a2a-workshop-gateway",
            description="A2A Workshop Gateway — single entry point for Weather, RAG, and Summary MCP servers",
            protocol_configuration=agentcore.McpProtocolConfiguration(
                search_type=agentcore.McpGatewaySearchType.SEMANTIC,
            ),
            authorizer_configuration=agentcore.GatewayAuthorizer.using_aws_iam(),
        )

        # Broad permissions for gateway operation
        self.gateway.role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock-agentcore:*"], resources=["*"])
        )
        self.gateway.role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:bedrock-agentcore-identity*"],
            )
        )

        # Register MCP Server targets
        if weather_mcp_stack:
            self._add_mcp_target("weather", weather_mcp_stack)
        if rag_agent_stack:
            self._add_mcp_target("rag", rag_agent_stack)
        if summary_agent_stack:
            self._add_mcp_target("summary", summary_agent_stack)

        self._create_ssm_parameters()
        self._create_outputs()
        self._apply_cdk_nag_suppressions()

    def _add_mcp_target(self, name: str, mcp_stack: object) -> None:
        """Add an MCP Server target using OAuth provider from the stack."""
        self.gateway.add_mcp_server_target(
            f"{name.capitalize()}McpTarget",
            gateway_target_name=f"{name}-target",
            description=f"{name.capitalize()} MCP Server",
            endpoint=mcp_stack.runtime_endpoint_url,
            credential_provider_configurations=[
                agentcore.GatewayCredentialProvider.from_oauth_identity_arn(
                    provider_arn=mcp_stack.oauth_provider_arn,
                    secret_arn=mcp_stack.oauth_secret_arn,
                    scopes=[f"{mcp_stack.tool_name}-api/invoke"],
                )
            ],
        )

    def _create_ssm_parameters(self) -> None:
        ssm.StringParameter(self, "GatewayIdParam", parameter_name="/a2a_gateway/gateway_id", string_value=self.gateway.gateway_id)
        ssm.StringParameter(self, "GatewayUrlParam", parameter_name="/a2a_gateway/gateway_url", string_value=self.gateway.gateway_url)

    def _create_outputs(self) -> None:
        CfnOutput(self, "GatewayId", value=self.gateway.gateway_id)
        CfnOutput(self, "GatewayUrl", value=self.gateway.gateway_url)
        CfnOutput(self, "GatewayArn", value=self.gateway.gateway_arn)

    def _apply_cdk_nag_suppressions(self) -> None:
        NagSuppressions.add_stack_suppressions(self, [
            {"id": "AwsSolutions-IAM5", "reason": "Gateway requires broad permissions for MCP targets."},
            {"id": "AwsSolutions-IAM4", "reason": "Using AWS managed policies for Gateway."},
        ])
