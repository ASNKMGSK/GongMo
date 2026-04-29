# 개발자 가이드

> AgentCore A2A Multi-Agent Workshop 개발 및 배포 가이드

## 목차

1. [환경 설정](#환경-설정)
2. [프로젝트 구조 이해](#프로젝트-구조-이해)
3. [에이전트 개발 패턴](#에이전트-개발-패턴)
4. [MCP 서버 개발](#mcp-서버-개발)
5. [CDK 인프라 배포](#cdk-인프라-배포)
6. [로컬 개발 및 테스트](#로컬-개발-및-테스트)
7. [트러블슈팅](#트러블슈팅)

---

## 환경 설정

### AWS 설정

```bash
# AWS CLI 프로필 설정
aws configure --profile deploy

# Bedrock 모델 접근 확인
aws bedrock list-foundation-models --region us-east-1 --query "modelSummaries[?modelId=='anthropic.claude-sonnet-4-20250514-v1:0'].modelId"
```

### Python 환경

```bash
# Python 3.12+ 확인
python3 --version

# 가상 환경 생성 (각 에이전트 디렉토리에서)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### CDK 설정

```bash
# CDK CLI 설치
npm install -g aws-cdk

# CDK Bootstrap (최초 1회)
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1
```

---

## 프로젝트 구조 이해

### 에이전트 공통 구조

각 에이전트는 동일한 디렉토리 구조를 따릅니다:

```
<agent-name>/
├── <agent_name>.py          # 핵심 에이전트 로직 (Strands Agent)
├── main.py                  # FastAPI/BedrockAgentCoreApp 엔트리포인트
├── a2a/
│   ├── __init__.py
│   └── agent_card.py        # A2A Agent Card 정의
├── common/
│   ├── __init__.py
│   ├── prompts.py           # 시스템 프롬프트
│   └── config.py            # 설정 (선택)
├── Dockerfile               # 컨테이너 빌드
└── requirements.txt         # Python 의존성
```

### 주요 파일 역할

| 파일 | 설명 |
|------|------|
| `<agent>.py` | Strands Agent 생성 및 `agent_invocation()` 함수 정의 |
| `main.py` | `BedrockAgentCoreApp`으로 SSE 엔드포인트 노출 |
| `a2a/agent_card.py` | `/.well-known/agent.json` 응답 데이터 |
| `common/prompts.py` | 에이전트의 시스템 프롬프트 (행동 지침) |

---

## 에이전트 개발 패턴

### 1. 시스템 프롬프트 작성 (`common/prompts.py`)

```python
def get_agent_system_prompt() -> str:
    return """You are the <Role> Agent.
    ## PURPOSE
    <에이전트의 목적 설명>
    ## INSTRUCTIONS
    <구체적인 행동 지침>
    """
```

### 2. 에이전트 로직 (`<agent>.py`)

```python
from strands import Agent
from strands.models import BedrockModel

async def agent_invocation(payload: dict, context: dict | None = None):
    prompt = payload.get("prompt", "")
    
    bedrock_model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0")
    agent = Agent(
        model=bedrock_model,
        system_prompt=get_system_prompt(),
        tools=[...],  # 필요한 도구 목록
    )
    
    stream = agent.stream_async(prompt)
    async for event in stream:
        yield event
```

### 3. 엔트리포인트 (`main.py`)

```python
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
async def invoke(payload, context):
    async for event in agent_invocation(payload, context):
        yield event

if __name__ == "__main__":
    app.run()
```

### 4. Agent Card (`a2a/agent_card.py`)

```python
AGENT_CARD = AgentCard(
    name="my-agent",
    description="에이전트 설명",
    capabilities=["capability1", "capability2"],
    input_schema={...},
    output_schema={...},
)
```

---

## MCP 서버 개발

MCP 서버는 FastMCP 프레임워크를 사용합니다:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(host="0.0.0.0", stateless_http=True)

@mcp.tool()
def my_tool(param: str) -> dict:
    """도구 설명"""
    return {"result": "..."}

mcp.run(transport="streamable-http")
```

---

## CDK 인프라 배포

### 배포 순서

```bash
cd packages/cdk-infra-python

# 1. 전체 배포
cdk deploy --all --require-approval never

# 2. 또는 개별 스택 배포
cdk deploy A2AWorkshopOrchestratorAgent
cdk deploy A2AWorkshopMcpDataAgent
cdk deploy A2AWorkshopRagAgent
cdk deploy A2AWorkshopSummaryAgent
cdk deploy A2AWorkshopRAGInfrastructure
cdk deploy A2AWorkshopMemoryInfrastructure
cdk deploy A2AWorkshopGateway
```

### 스택 의존성

```
OrchestratorAgent
    ├── McpDataAgent → Gateway
    ├── RagAgent → RAGInfrastructure
    └── SummaryAgent
MemoryInfrastructure (독립)
```

---

## 로컬 개발 및 테스트

### 에이전트 로컬 실행

```bash
cd packages/agentcore-agents/orchestrator-agent
pip install -r requirements.txt
python main.py
# → http://localhost:8080 에서 실행
```

### Docker 빌드 및 테스트

```bash
cd packages/agentcore-agents/orchestrator-agent
docker build -t orchestrator-agent .
docker run -p 8080:8080 \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  orchestrator-agent
```

### API 테스트

```bash
# Health check
curl http://localhost:8080/health

# Agent Card
curl http://localhost:8080/.well-known/agent.json

# Invoke (SSE)
curl -X POST http://localhost:8080/invoke \
  -H "Content-Type: application/json" \
  -d '{"prompt": "서울 날씨 알려줘"}'
```

---

## 트러블슈팅

### 일반적인 문제

| 문제 | 해결 |
|------|------|
| Bedrock 모델 접근 오류 | AWS 리전에서 모델 활성화 확인 |
| Cognito 인증 실패 | Secrets Manager에 자격증명 확인 |
| OpenSearch 타임아웃 | 네트워크 정책 및 데이터 접근 정책 확인 |
| Docker 빌드 실패 | `requirements.txt` 의존성 버전 확인 |

### 로그 확인

```bash
# AgentCore Runtime 로그
aws logs tail /aws/bedrock-agentcore/runtimes/orchestrator_agent --follow

# Lambda 로그 (RAG Upload)
aws logs tail /aws/lambda/RAGUploadFunction --follow
```
