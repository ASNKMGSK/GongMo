# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary

AgentCore A2A Multi-Agent Workshop — a monorepo containing Python agents (Orchestrator, RAG, Summary), an MCP weather server, Python CDK infrastructure, and a chatbot UI. Agents use the Strands Agent framework with Amazon Bedrock, communicate via MCP through an AgentCore Gateway, and deploy as AgentCore Runtime containers.

> **Note:** The MCP Data Agent (`packages/agentcore-agents/mcp-data-agent/`) is legacy — it used direct A2A communication and has been superseded by the Gateway-centric architecture.

### QA Pipeline

`packages/agentcore-agents/qa-pipeline/` — LangGraph 기반 QA 평가 파이프라인. 3-Phase(A: 병렬 5 / B1: 병렬 2 / B2: 직렬 1 / C: 병렬 2) 순차 고정. 8 평가 노드 매핑:

- greeting ↔ 인사 예절 (#1-#2)
- understanding ↔ 경청 및 소통 (#3-#5)
- courtesy ↔ 언어 표현 (#6-#7)
- mandatory ↔ 니즈 파악 (#8-#9)  ← 파일명이 함수와 불일치, 레거시
- scope ↔ 설명력 및 전달력 (#10-#11)  ← 파일명이 함수와 불일치, 레거시
- proactiveness ↔ 적극성 (#12-#14)
- work_accuracy ↔ 업무 정확도 (#15-#16)
- incorrect_check ↔ 개인정보 보호 (#17-#18)  ← 파일명이 함수와 불일치, 레거시

### QA 프롬프트 튜닝 (완료, 2026-04-19)

5회 반복 튜닝 후 **iter03_clean 을 운영 배포 권고 버전** 으로 확정.

| 이터 | MAE | MAPE | 비고 |
|---|---:|---:|---|
| v3 (시작점) | 10.56 | 11.35% | baseline |
| **iter03_clean** | **3.89** | **4.18%** | **최우수** — #2 끝인사 2요소 완화 · #7 refusal-gated · #10 장황 삭제 + reconciler 도입 |
| iter04 | 4.33 | 4.66% | 오버슛 보정 시도, 소폭 회귀 |
| iter05 | 4.89 | 5.26% | #6 돌파 성공 + #17 역주행 (ALLOWED_STEPS[17] 스키마 충돌 발견) |

**핵심 발견 (iter05)**: `CLAUDE.md` `ALLOWED_STEPS[17]=[5,0]` 와 `item_17_iv_procedure.sonnet.md` "3점 스냅" 조항이 명시적 충돌. `snap_score` 가 LLM 3점 반환을 0점으로 강제 변환. iter06 최우선 과제는 `ALLOWED_STEPS[17]` 을 `[5,3,0]` 로 확장.

프로젝트 가이드 및 산출물: `C:\Users\META M\Desktop\프롬프트 튜닝\PROJECT_GUIDE.md` + `668437-batch10_통합리포트_v3.html` (271KB) + `QA프롬프트튜닝_최종요약_iter01-05.xlsx`.

### QA 인프로세스 배치 실행 (HTTP/SSE 우회)

프롬프트 튜닝 시 `POST /evaluate` (JSON) 및 `POST /evaluate/stream` (SSE) 모두 2~5MB `node_traces` 누적 + SSE idle disconnect 로 hang 발생. 해결: `scripts/run_direct_batch.py` 로 `graph.ainvoke()` 인프로세스 직접 호출.

- 스크립트: `packages/agentcore-agents/qa-pipeline/scripts/run_direct_batch.py`
- 환경변수: `BATCH_OUTPUT_SUFFIX`, `BATCH_MAX_CONCURRENT` (기본 2 — Bedrock throttle 완화), `PER_SAMPLE_TIMEOUT` (기본 600초)
- 사용: `python scripts/run_direct_batch.py` (cd qa-pipeline 후)
- Self-Consistency: `scripts/merge_self_consistency.py --folders sc1 sc2 sc3 --output final` 로 N회 median 병합

평가만 필요 시 `QAState.plan.skip_phase_c_and_reporting = True` 주입 → `orchestrator.py` 가 phase_b2 완료 후 즉시 `__end__`. report_generator / consistency_check / score_validation 스킵.

### QA Debate (Phase 2)

3-페르소나(strict / neutral / loose) 그룹채팅 토론으로 점수 합의. **AG2 인프로세스** 통합 — 이전 별도 서비스 `packages/agentcore-agents/qa-debate/` (Phase 1 mock) 를 흡수해서 `packages/agentcore-agents/qa-pipeline/v2/debate/` 로 통합. AG2 는 `autogen` / `pyautogen` 과 동일한 PyPI 패키지.

**발동 조건**: Phase B 이후 reconciler 가 계산한 3-persona 점수의 `step_spread` 가 `QA_DEBATE_SPREAD_THRESHOLD` (기본 3) 이상인 항목만 토론 노드로 진입. `QA_DEBATE_ENABLED=false` 면 토론 비활성화 (기존 reconciler 만).

**QAState 필드** (`packages/agentcore-agents/qa-pipeline/v2/state.py::QAState["debates"]`):

```python
debates: dict[int, DebateRecord]  # key = item_number
# DebateRecord:
#   item_number, item_name, max_score, allowed_steps
#   initial_positions: {strict, neutral, loose}
#   rounds: list[RoundRecord]
#   final_score: float | None
#   final_rationale: str
#   converged: bool
#   ended_at: ISO datetime
# RoundRecord:
#   round, turns: list[{persona, score, argument}]
#   verdict: {consensus, score, rationale}
```

**SSE 이벤트** (`/evaluate/stream` 에서 기존 이벤트와 함께 emit):

| event | payload |
|---|---|
| `debate_round_start` | `{item_number, round, max_rounds}` |
| `persona_turn` | `{item_number, round, persona, score, argument}` |
| `moderator_verdict` | `{item_number, round, consensus: bool, score, rationale}` |
| `debate_final` | `{item_number, final_score, converged, rounds_used, rationale}` |

**비활성화 방법**: `.env` 에 `QA_DEBATE_ENABLED=false` 설정 또는 배포 시 env 로 override. 토론 라운드당 3회 Bedrock 호출 + moderator 1회 = 4 LLM call/round 이므로 Bedrock quota 부족 시 꺼두는 것이 안전.

**의존성**: `ag2[anthropic,bedrock]>=0.9.7` — `packages/qa-pipeline-multitenant/qa-pipeline/requirements.txt`. AG2 / autogen / pyautogen 은 PyPI 동일 alias (v0.9.7 Context7 확인).

## Architecture

```
User → Orchestrator Agent (HTTP/SSE)
         → AgentCore Gateway (MCP, SigV4-signed)
              → Weather MCP Server
              → RAG Agent (MCP, OpenSearch Serverless + Titan Embed V2)
              → Summary Agent (MCP)
```

The Orchestrator is the sole HTTP entry point. It connects to the AgentCore Gateway as an MCP client, discovers all tools from registered targets (Weather, RAG, Summary), and creates a Strands Agent with those tools. Each sub-agent/MCP server is registered as a Gateway target — not invoked directly by HTTP. Gateway URL is resolved from SSM (`/a2a_gateway/gateway_url`) or the `GATEWAY_URL` env var.

Each agent follows an identical structure: `<agent>.py` (core logic with `agent_invocation()`), `main.py` (BedrockAgentCoreApp entrypoint), `a2a/agent_card.py`, `common/prompts.py`, `common/config.py`, `Dockerfile`, `requirements.txt`.

Memory: short-term (AgentCore Memory API via `bedrock-agentcore` SDK, sliding window of 20 turns), long-term (DynamoDB table `a2a-workshop-memory`, PK=user_id SK=timestamp, 30-day TTL).

## Authentication Flow

```
User → Cognito (JWT) → Orchestrator → Gateway (SigV4/IAM) → MCP Servers (OAuth client_credentials)
```

Three layers: (1) User authenticates via Cognito AccessToken, (2) Orchestrator signs Gateway requests with SigV4 (IAM Role), (3) Gateway handles OAuth client_credentials to each MCP server via Cognito M2M. Each MCP server's Cognito credentials are stored in Secrets Manager at `/<agent>/cognito/credentials`.

## Adding New Agents

Gateway-centric architecture means the Orchestrator needs no changes when adding agents:

1. Implement an MCP server using `FastMCP(stateless_http=True)` with `@mcp.tool()` decorated functions
2. Create a CDK stack with `ProtocolType.MCP` and Cognito OAuth provider
3. Register as a Gateway target via `add_mcp_server_target` in `agentcore_gateway_stack.py`
4. The Orchestrator auto-discovers all tools via MCPClient tool discovery — no code changes needed

## Common Commands

### Linting & Formatting
```bash
pnpm lint           # lint JS + Python (auto-fix)
pnpm lint:check     # lint without fixing
pnpm format         # format JS + Python
pnpm format:check   # check formatting
pnpm lint:py        # Python only (ruff check --fix)
pnpm lint:py:check  # Python lint check only
pnpm format:py      # Python only (ruff format)
```

Python lint/format scripts use `uvx ruff` (not bare `ruff`), so `uvx` must be available.

**Pre-commit hooks**: husky + lint-staged auto-run on commit — Prettier + ESLint for JS/TS/JSON/CSS/MD, `uvx ruff format` + `uvx ruff check --fix` for Python. Commits are also validated by commitlint for conventional commit format.

### CDK
```bash
cd packages/cdk-infra-python
pip install -r requirements.txt
cdk synth                                    # synthesize all stacks
cdk deploy --all --require-approval never    # deploy everything
cdk deploy A2AWorkshopOrchestratorAgent      # deploy single stack
```

Stack names: `A2AWorkshopRAGInfrastructure`, `A2AWorkshopMemoryInfrastructure`, `A2AWorkshopWeatherMcp`, `A2AWorkshopRagAgent`, `A2AWorkshopSummaryAgent`, `A2AWorkshopGateway`, `A2AWorkshopOrchestratorAgent`.

### Running an Agent Locally
```bash
cd packages/agentcore-agents/orchestrator-agent
pip install -r requirements.txt
python main.py    # serves on http://localhost:8080
```

```bash
# API endpoints available on any locally-running agent:
curl http://localhost:8080/health                     # health check
curl http://localhost:8080/.well-known/agent.json     # agent card
curl -X POST http://localhost:8080/invoke \
  -H "Content-Type: application/json" \
  -d '{"prompt": "서울 날씨 알려줘"}'                  # invoke (SSE)
```

### Docker
```bash
cd packages/agentcore-agents/orchestrator-agent
docker build -t orchestrator-agent .
docker run -p 8080:8080 -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... orchestrator-agent
```

### Tests
```bash
# Run all tests
pytest tests/

# Run a single test file
pytest tests/unit/test_orchestrator.py

# Run a specific test
pytest tests/unit/test_orchestrator.py::TestOrchestratorConfig::test_config_imports_successfully
```

Note: Tests use two module isolation patterns since agents share package names (`common`, `a2a`). The `_ensure_orch_path()` helper in individual test files prepends agent dirs to `sys.path`, and `tests/conftest.py` provides `load_module_from_path()` / `load_agent_module()` for importlib-based isolation. Both exist to avoid cross-agent import collisions.

## Code Conventions

- **Python**: ruff for linting and formatting. Line length 120, target Python 3.13, double quotes, space indent. Isort with `no-sections` and `combine-as-imports`. See `ruff.toml` for full config.
- **Commits**: conventional commits enforced by commitlint (`@commitlint/config-conventional`). Use `pnpm commit` for interactive commitizen.
- **License**: Apache 2.0. All source files need the copyright header: `# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.` / `# SPDX-License-Identifier: Apache-2.0`.
- **Documentation language**: Korean for README and docs.
- **CDK**: Python CDK with `aws-cdk.aws-bedrock-agentcore-alpha`. One stack per agent/resource. Agent paths resolved relative to stack file via `Path(__file__).parent...`. Docker images built with `Platform.LINUX_ARM64`.
- **Default model**: `us.anthropic.claude-sonnet-4-20250514-v1:0` (set in each agent's `common/config.py`).
- **Package manager**: pnpm 10.14.0, Node >= 20.18.1.
- **QA 파이프라인 — LLM 노드 예외 처리**: `await invoke_and_parse(...)` 블록에는 반드시 `except LLMTimeoutError: raise` 를 generic `except Exception` 앞에 배치. 타임아웃은 파이프라인 중단 시그널이므로 규칙-폴백으로 삼키면 안 됨. 적용 파일: courtesy / incorrect_check / understanding / mandatory / scope / proactiveness / work_accuracy / report_generator / greeting.
- **QA 파이프라인 — 화자 마커 및 공통 패턴 상수**: 새 노드 추가 시 `AGENT_SPEAKER_PREFIXES`, `CUSTOMER_SPEAKER_PREFIXES`, `FIRST_GREETING_KEYWORDS` 등은 `nodes/skills/constants.py` 의 canonical 정의를 import 해서 사용. 로컬 재정의 금지 (DRY).
- **QA 파이프라인 — 점수 snap 의무**: LLM 이 반환한 점수는 반드시 `from nodes.skills.reconciler import snap_score` 후 `snap_score(item_number, raw_score)` 를 거쳐 평가 결과에 저장. 항목별 허용 단계(예: #17/#18 은 [5, 0]) 외 값 저장 금지. 미준수 시 Phase C `score_validation` 이 `invalid_step` 위반 플래그.
- **QA 파이프라인 — 인프라 폴백 정화**: Bedrock `ThrottlingException` 또는 LLM 실패 시 rule fallback 이 무리하게 감점 부여 → `nodes/skills/reconciler.py::reconcile_evaluation` 이 "LLM 실패 / 규칙 폴백 / ThrottlingException" 키워드 감지 후 `points=0` 로 무효화 + `[SKIPPED_INFRA]` 태그. 배치 실행에서 `run_direct_batch.py::extract_result` 가 자동 호출.
- **QA 파이프라인 — skip_phase_c_and_reporting 플래그**: `QAState.plan.skip_phase_c_and_reporting = True` 주입 시 `orchestrator.py` 가 phase_b2 완료 후 즉시 `__end__`. 프롬프트 튜닝 배치 시 report/validation/consistency 생략으로 소요 시간 단축 (약 17초/샘플 절감). `run_direct_batch.py::build_initial_state` 가 기본 주입.
- **QA 파이프라인 — ALLOWED_STEPS 스키마 주의**: `nodes/qa_rules.py::QA_RULES[].deduction_rules[].to_score` 에 정의된 허용 단계와 프롬프트 (item_XX_*.md) 판정 기준 이 **일치해야 함**. 불일치 시 LLM 이 중간 점수 (예: 3점) 반환해도 `snap_score` 가 강제 변환 (예: #17 `[5,0]` 스키마에서 3→0). iter05 에서 #17 이 이 케이스로 회귀 발견. 프롬프트 변경 전 `qa_rules.py` 선행 확인 필수.

## CDK Stack Dependencies

```
RAGInfrastructure (independent)
MemoryInfrastructure (independent)
WeatherMcpStack (independent)
RagAgentStack → RAGInfrastructure
SummaryAgentStack (independent)
GatewayStack → WeatherMcpStack, RagAgentStack, SummaryAgentStack
OrchestratorAgentStack → GatewayStack
```

## Key Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `GATEWAY_URL` | Orchestrator | Override Gateway URL (skips SSM lookup) |
| `AWS_DEFAULT_REGION` / `AWS_REGION` | All agents | AWS region |
| `COGNITO_TEST_USERNAME` | CDK | Test user for Cognito pool (default: `testuser`) |
| `COGNITO_TEST_PASSWORD` | CDK | Test user password (default: `MyPassword123!`) |
| `SAGEMAKER_MAX_CONCURRENT` | QA Pipeline | LLM 동시 요청 세마포어 상한 (기본 10). Bedrock 백엔드(기본)는 10 적정, SageMaker 단일 GPU는 3~4 권장. 실제 영향 파일: `packages/agentcore-agents/qa-pipeline/config.py`, `packages/agentcore-agents/qa-pipeline/nodes/llm.py` |
| `BATCH_OUTPUT_SUFFIX` | QA batch | `run_direct_batch.py` 결과 폴더 접미사 (기본 `direct`) |
| `BATCH_MAX_CONCURRENT` | QA batch | `run_direct_batch.py` 병렬 샘플 수 (기본 2 — Bedrock throttle 완화) |
| `PER_SAMPLE_TIMEOUT` | QA batch | `run_direct_batch.py` 샘플당 타임아웃 초 (기본 600) |
| `BEDROCK_MODEL_ID` | QA batch / QA Debate | 배치 및 Phase 2 debate 노드에서 사용할 Bedrock 모델 (기본 Sonnet 4 — `us.anthropic.claude-sonnet-4-20250514-v1:0`) |
| `QA_DEBATE_ENABLED` | QA Debate | 토론 노드 활성화 플래그 (기본 `true`). `false` 면 debate_node 가 no-op, 기존 reconciler 만 사용 |
| `QA_DEBATE_SPREAD_THRESHOLD` | QA Debate | strict/neutral/loose persona step spread 가 이 값 이상이면 토론 진입 (기본 3) |
| `QA_DEBATE_MAX_ROUNDS` | QA Debate | 토론 최대 라운드 수 (기본 3). 합의 미도달 시 moderator fallback 규칙으로 종료 |

## Reference Project

The original reference project is at `/home/ubuntu/agentcore-multi-agent-workshop/` (EC2 dev environment). Consult it for patterns when adding new agents or stacks. May not exist on all dev machines.
