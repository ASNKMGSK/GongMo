#!/usr/bin/env python3
"""
CDK Application entry point for the AgentCore A2A Multi-Agent Workshop.

Deploys all agent runtimes, MCP servers, gateway, RAG infrastructure,
and memory infrastructure stacks.

Architecture:
  Orchestrator Agent (HTTP) → AgentCore Gateway (MCP) → {Weather MCP, RAG MCP, Summary MCP}

The Gateway acts as a single entry point. The Orchestrator connects to the
Gateway as an MCP client and discovers all tools from all registered targets.
"""

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(**kwargs):
        pass
import aws_cdk as cdk

from src.stacks.orchestrator_agent_stack import OrchestratorAgentStack
from src.stacks.weather_mcp_stack import WeatherMcpStack
from src.stacks.rag_agent_stack import RagAgentStack
from src.stacks.summary_agent_stack import SummaryAgentStack
from src.stacks.agentcore_gateway_stack import AgentCoreGatewayStack
from src.stacks.rag_infrastructure_stack import RAGInfrastructureStack
from src.stacks.memory_infrastructure_stack import MemoryInfrastructureStack
from src.stacks.qa_pipeline_stack import QaPipelineStack

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

app = cdk.App()

# --- 1. RAG Infrastructure (OpenSearch Serverless, S3, DynamoDB) ---
rag_infra_stack = RAGInfrastructureStack(
    app,
    "A2AWorkshopRAGInfrastructure",
    description="RAG Infrastructure with OpenSearch Serverless for A2A Workshop",
)

# --- 2. Memory Infrastructure (DynamoDB tables) ---
memory_infra_stack = MemoryInfrastructureStack(
    app,
    "A2AWorkshopMemoryInfrastructure",
    description="Memory Infrastructure (DynamoDB) for A2A Workshop",
)

# --- 3. Weather MCP Server (MCP protocol) ---
weather_mcp_stack = WeatherMcpStack(
    app,
    "A2AWorkshopWeatherMcp",
    description="Weather MCP Server for AgentCore A2A Workshop",
)

# --- 4. RAG Agent (MCP protocol) ---
rag_agent_stack = RagAgentStack(
    app,
    "A2AWorkshopRagAgent",
    description="RAG Agent MCP Server for AgentCore A2A Workshop",
)
rag_agent_stack.add_dependency(rag_infra_stack)

# --- 5. Summary Agent (MCP protocol) ---
summary_agent_stack = SummaryAgentStack(
    app,
    "A2AWorkshopSummaryAgent",
    description="Summary Agent MCP Server for AgentCore A2A Workshop",
)

# --- 6. AgentCore Gateway (routes MCP tool calls to all targets) ---
gateway_stack = AgentCoreGatewayStack(
    app,
    "A2AWorkshopGateway",
    description="AgentCore Gateway — single entry point for all MCP targets",
    weather_mcp_stack=weather_mcp_stack,
    rag_agent_stack=rag_agent_stack,
    summary_agent_stack=summary_agent_stack,
)
gateway_stack.add_dependency(weather_mcp_stack)
gateway_stack.add_dependency(rag_agent_stack)
gateway_stack.add_dependency(summary_agent_stack)

# --- 7. Orchestrator Agent (HTTP protocol, uses Gateway for tools) ---
orchestrator_stack = OrchestratorAgentStack(
    app,
    "A2AWorkshopOrchestratorAgent",
    description="Orchestrator Agent for AgentCore A2A Workshop",
)
orchestrator_stack.add_dependency(gateway_stack)

# --- 8. QA Pipeline Agent (HTTP protocol, SageMaker Qwen3-8B backend) ---
qa_pipeline_stack = QaPipelineStack(
    app,
    "A2AWorkshopQaPipeline",
    description="QA Pipeline Agent (HTTP) backed by SageMaker Qwen3-8B for A2A Workshop",
)

app.synth()
