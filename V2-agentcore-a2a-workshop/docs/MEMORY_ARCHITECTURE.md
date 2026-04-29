# 메모리 아키텍처

> AgentCore A2A Workshop의 장기/단기 메모리 시스템

## 개요

멀티 에이전트 시스템에서 메모리는 두 가지 레벨로 동작합니다:

1. **장기 메모리 (Long-term Memory)**: 세션 간 지속되는 사용자 선호도, 학습된 사실, 대화 요약
2. **단기 메모리 (Short-term Memory)**: 현재 세션 내 대화 컨텍스트

## 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                     Orchestrator Agent                    │
│                                                           │
│  ┌──────────────────────────────────────────────────┐    │
│  │              Memory Manager                       │    │
│  │                                                    │    │
│  │  ┌────────────────┐  ┌────────────────────────┐  │    │
│  │  │ Short-term     │  │ Long-term              │  │    │
│  │  │ (In-Memory)    │  │ (DynamoDB)             │  │    │
│  │  │                │  │                        │  │    │
│  │  │ - 최근 N개     │  │ - 사용자 선호도       │  │    │
│  │  │   메시지       │  │ - 학습된 사실         │  │    │
│  │  │ - 세션 종료    │  │ - 대화 요약           │  │    │
│  │  │   시 삭제      │  │ - TTL: 30일           │  │    │
│  │  └────────────────┘  └────────────────────────┘  │    │
│  └──────────────────────────────────────────────────┘    │
│                                                           │
│  ┌──────────────────────────────────────────────────┐    │
│  │           AgentCore Memory (Managed)              │    │
│  │                                                    │    │
│  │  - Semantic Memory Strategy                       │    │
│  │  - User Preference Strategy                       │    │
│  │  - 자동 메모리 추출 및 검색                       │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## DynamoDB 테이블 설계

### 장기 메모리 테이블 (`a2a-workshop-memory`)

| 속성 | 타입 | 설명 |
|------|------|------|
| `user_id` (PK) | String | 사용자 식별자 |
| `timestamp` (SK) | String | ISO 8601 형식 타임스탬프 |
| `memory_type` | String | `preference` / `fact` / `summary` |
| `content` | String | 메모리 내용 |
| `metadata` | Map | 추가 메타데이터 |
| `ttl` | Number | Unix 타임스탬프 (30일 후 자동 삭제) |

**GSI**: `type-index` (user_id + memory_type)

### 세션 메모리 테이블 (`a2a-workshop-sessions`)

| 속성 | 타입 | 설명 |
|------|------|------|
| `session_id` (PK) | String | 세션 식별자 |
| `turn_index` (SK) | Number | 대화 턴 순서 |
| `role` | String | `user` / `assistant` |
| `content` | String | 메시지 내용 |
| `agent_name` | String | 응답한 에이전트 이름 |
| `ttl` | Number | Unix 타임스탬프 (24시간 후 자동 삭제) |

## AgentCore Managed Memory

AgentCore CDK 구성을 통해 관리형 메모리를 사용합니다:

```python
agentcore.Memory(
    memory_name="orchestrator_agent_memory",
    memory_strategies=[
        agentcore.MemoryStrategy.using_built_in_semantic(),
        agentcore.MemoryStrategy.using_built_in_user_preference(),
    ],
)
```

### Semantic Memory Strategy
- 대화에서 의미 있는 정보를 자동 추출
- 벡터 임베딩으로 유사도 기반 검색

### User Preference Strategy
- 사용자 선호도를 자동 감지하고 저장
- 후속 대화에서 자동 적용

## 메모리 흐름

### 1. 요청 수신 시

```
사용자 요청 → 단기 메모리에서 최근 컨텍스트 로드
            → 장기 메모리에서 관련 정보 검색
            → 시스템 프롬프트에 컨텍스트 주입
```

### 2. 응답 생성 후

```
에이전트 응답 → 단기 메모리에 대화 턴 추가
             → 장기 메모리에 중요 정보 저장
             → AgentCore Memory에 이벤트 전송
```

### 3. 세션 종료 시

```
세션 종료 → 대화 요약 생성
          → 장기 메모리에 요약 저장
          → 단기 메모리 정리
```

## Memory Hooks (Strands Lifecycle)

Orchestrator의 `memory/memory_hooks.py`에서 Strands Agent의 라이프사이클 훅을 활용합니다:

```python
class MemoryHooks:
    def before_invocation(self, agent, prompt):
        # 장기 메모리에서 관련 정보 검색
        # 단기 메모리에서 최근 컨텍스트 로드
        
    def after_invocation(self, agent, response):
        # 대화 턴을 단기 메모리에 저장
        # 중요 정보를 장기 메모리에 저장
```

## 성능 고려사항

| 항목 | 설정 | 이유 |
|------|------|------|
| 단기 메모리 윈도우 | 최근 10개 메시지 | 토큰 제한 및 성능 |
| 장기 메모리 TTL | 30일 | 스토리지 비용 최적화 |
| 세션 TTL | 24시간 | 임시 데이터 정리 |
| 메모리 검색 Top-K | 5개 | 관련성 유지 |

## 보안

- DynamoDB 암호화: AWS managed key 사용
- IAM 역할 기반 접근 제어
- TTL을 통한 자동 데이터 삭제
- 사용자 ID 기반 데이터 격리
