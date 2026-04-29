# Gateway 중심 멀티에이전트 아키텍처

> AgentCore Gateway를 Single Entry Point로 사용하는 MCP 기반 에이전트 통신

## 개요

모든 에이전트 간 통신과 외부 도구 호출이 **AgentCore Gateway**를 통해 라우팅됩니다. Orchestrator는 Gateway에만 연결하며, Gateway가 각 MCP 서버로 요청을 분배합니다.

## 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                        Orchestrator Agent                        │
│                                                                   │
│  1. 사용자 요청 수신 (HTTP/SSE)                                  │
│  2. MCPClient로 Gateway 연결 (SigV4 인증)                       │
│  3. Gateway에서 도구 자동 발견 (tool discovery)                  │
│  4. LLM이 적절한 도구 선택 및 호출                              │
│  5. 결과를 사용자에게 스트리밍 응답                              │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              MCPClient (SigV4 Auth)                         │  │
│  │  - Gateway URL: SSM /a2a_gateway/gateway_url               │  │
│  │  - streamablehttp_client + SigV4 서명                      │  │
│  │  - 연결 시 모든 MCP 도구 자동 발견                         │  │
│  └────────────────────────┬───────────────────────────────────┘  │
└───────────────────────────┼──────────────────────────────────────┘
                            │ MCP Protocol (SigV4)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     AgentCore Gateway                            │
│                   (Single Entry Point)                            │
│                                                                   │
│  - SigV4 인증 (Orchestrator → Gateway)                          │
│  - OAuth client_credentials (Gateway → MCP Servers)              │
│  - Semantic 검색으로 최적 도구 매칭                              │
│  - 3개 MCP Target 등록                                           │
│                                                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ weather-    │  │ rag-        │  │ summary-                │  │
│  │ target      │  │ target      │  │ target                  │  │
│  │             │  │             │  │                         │  │
│  │ OAuth:      │  │ OAuth:      │  │ OAuth:                  │  │
│  │ weather_mcp │  │ rag_agent   │  │ summary_agent           │  │
│  │ /cognito/   │  │ /cognito/   │  │ /cognito/               │  │
│  │ credentials │  │ credentials │  │ credentials             │  │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘  │
└─────────┼────────────────┼──────────────────────┼────────────────┘
          │                │                      │
          ▼                ▼                      ▼
┌─────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ Weather MCP     │ │ RAG Agent        │ │ Summary Agent    │
│ Server          │ │ (MCP Server)     │ │ (MCP Server)     │
│                 │ │                  │ │                  │
│ Protocol: MCP   │ │ Protocol: MCP    │ │ Protocol: MCP    │
│ Runtime:        │ │ Runtime:         │ │ Runtime:         │
│ AgentCore       │ │ AgentCore        │ │ AgentCore        │
│                 │ │                  │ │                  │
│ Tools:          │ │ Tools:           │ │ Tools:           │
│ - get_current_  │ │ - search_        │ │ - summarize_text │
│   weather       │ │   knowledge_base │ │                  │
│ - get_forecast  │ │                  │ │                  │
│ - get_weather_  │ │ Backend:         │ │ Backend:         │
│   alerts        │ │ - OpenSearch     │ │ - Bedrock LLM    │
│                 │ │ - Titan Embed V2 │ │                  │
│ Backend:        │ │ - S3 + DynamoDB  │ │                  │
│ - Open-Meteo    │ │                  │ │                  │
└─────────────────┘ └──────────────────┘ └──────────────────┘
```

## Gateway가 Single Entry Point인 이유 (Best Practice)

### 이전 구조 (직접 A2A 호출)
```
Orchestrator ──A2A──→ MCP Data Agent ──→ Gateway ──→ Weather MCP
             ──A2A──→ RAG Agent
             ──A2A──→ Summary Agent
```
- 각 에이전트마다 별도의 Cognito 인증 관리
- 에이전트별 개별 엔드포인트 관리
- 모니터링 분산

### 현재 구조 (Gateway 중심)
```
Orchestrator ──MCP──→ Gateway ──→ Weather MCP
                              ──→ RAG Agent (MCP)
                              ──→ Summary Agent (MCP)
```

**장점:**
| 항목 | 설명 |
|------|------|
| **보안 통합** | SigV4 인증이 Gateway 한 곳에서 관리, OAuth도 Gateway가 처리 |
| **관찰성** | 모든 트래픽이 Gateway를 거쳐 로깅/모니터링 일원화 |
| **도구 자동 발견** | MCPClient가 Gateway 연결 시 등록된 모든 도구를 자동 발견 |
| **라우팅 유연성** | 새 에이전트 추가 시 Gateway에 target만 등록하면 됨 |
| **확장성** | Gateway가 라우팅/로드밸런싱/서킷브레이커 역할 |

## 인증 흐름

```
┌──────┐     ┌──────────┐     ┌─────────┐     ┌───────────┐
│ User │────→│ Cognito  │────→│Orchestr.│────→│ Gateway   │
│      │ JWT │          │Token│         │SigV4│           │
└──────┘     └──────────┘     └─────────┘     └─────┬─────┘
                                                     │ OAuth
                                               ┌─────┼─────┐
                                               ▼     ▼     ▼
                                            Weather  RAG  Summary
```

1. **사용자 → Orchestrator**: Cognito AccessToken (JWT)
2. **Orchestrator → Gateway**: SigV4 (IAM Role 자동 서명)
3. **Gateway → MCP Servers**: OAuth client_credentials (Cognito M2M)

## MCP 도구 발견 (Tool Discovery)

Orchestrator가 Gateway에 MCPClient로 연결하면, 등록된 모든 MCP target의 도구가 자동 발견됩니다:

```python
# Orchestrator 코드
async with mcp_client as tools:
    # tools에 자동으로 포함됨:
    # - get_current_weather (from weather-target)
    # - get_forecast (from weather-target)
    # - get_weather_alerts (from weather-target)
    # - search_knowledge_base (from rag-target)
    # - summarize_text (from summary-target)
    
    agent = Agent(model=bedrock_model, tools=tools)
    agent("서울 날씨 알려줘")  # → LLM이 get_current_weather 선택
```

## 새 에이전트 추가 방법

Gateway 중심 아키텍처에서 새 에이전트를 추가하려면:

1. **MCP 서버 구현** (FastMCP)
```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("new-agent")

@mcp.tool()
def my_new_tool(query: str) -> str:
    """새로운 도구 설명"""
    return do_something(query)
```

2. **CDK 스택 생성** (`ProtocolType.MCP` + Cognito OAuth)

3. **Gateway에 target 등록** (`add_mcp_server_target`)

4. **Orchestrator 변경 불필요!** — Gateway에 연결하면 자동 발견

## CDK 스택 구조

```
app.py
 ├── OrchestratorAgentStack    # Orchestrator Runtime (HTTP/SSE)
 ├── WeatherMcpStack           # Weather MCP Runtime + OAuth Provider
 ├── RagAgentStack             # RAG MCP Runtime + OAuth Provider  
 ├── SummaryAgentStack         # Summary MCP Runtime + OAuth Provider
 ├── GatewayStack              # Gateway + 3 MCP Targets
 ├── RAGInfrastructureStack    # OpenSearch, S3, DynamoDB
 └── MemoryInfrastructureStack # Memory DynamoDB Tables

의존성:
 Gateway ──depends──→ WeatherMcp, RagAgent, SummaryAgent
 RagAgent ──depends──→ RAGInfrastructure
 Orchestrator ──depends──→ Gateway
```
