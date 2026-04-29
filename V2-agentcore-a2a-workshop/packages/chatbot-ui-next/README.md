# QA Pipeline V2 — Chatbot UI (Next.js)

QA Pipeline V2 용 웹 프론트엔드. 기존 `chatbot-ui/` 의 단일 HTML 페이지들을 Next.js 16 (App Router) 로 이식하며 구축 중.

## 스택

- Next.js 16.2.4 · React 19.2
- TypeScript 5 · Tailwind CSS v4
- App Router · Turbopack

## 실행

```bash
# 1) env 파일 준비
cp .env.local.example .env.local
# 필요 시 NEXT_PUBLIC_API_BASE_URL 수정 (기본 http://localhost:8000)

# 2) 의존성 설치
pnpm install

# 3) 개발 서버
pnpm dev             # http://localhost:3000

# 4) 프로덕션 빌드 검증
pnpm build
pnpm start
```

**전제**: 백엔드 QA Pipeline V2 FastAPI 서버가 `http://localhost:8000` 에서 기동되어 있어야 함.
(`packages/agentcore-agents/qa-pipeline/v2/serving/server_v2.py`)

## 디렉토리 구조

```
chatbot-ui-next/
├─ app/                    # App Router 라우트
│  ├─ layout.tsx           # 헤더 + 사이드바 네비
│  └─ page.tsx             # 대시보드 홈 (Dev2 가 ReactFlow 뷰로 교체 예정)
├─ components/             # 재사용 컴포넌트 (Dev3 진행 중)
├─ lib/
│  ├─ api.ts               # fetch 래퍼 (apiGet/apiPost/apiSSE, 기존 fetchXxx)
│  ├─ types.ts             # 백엔드 응답 타입
│  └─ group.ts             # 검토 큐 그룹핑 유틸
├─ public/
└─ .env.local.example
```

## 담당 페이지 매트릭스

| 경로 | 담당 | 상태 |
|---|---|---|
| `/` · 대시보드 | Dev1 → Dev2 | 임시 홈 (Dev2 가 ReactFlow 이식) |
| `/evaluate` | Dev2 | TODO — Phase 2 에서 `DebatePanel` 추가 |
| `/review` · 검토 큐 | Dev3 | 작업 중 |
| `/result` · 결과 상세 | Dev3 | 작업 중 — Phase 2 에서 `DebateRecord` 추가 |

## 환경 변수

| 이름 | 기본 | 설명 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | `lib/api.ts` 의 `BASE_URL` — 모든 `apiGet/apiPost/apiSSE` 요청 대상 |
| `NEXT_PUBLIC_QA_SERVER_URL` | `http://localhost:8000` | Legacy — 기존 `fetchReviewQueue` 계열이 참조. `NEXT_PUBLIC_API_BASE_URL` 가 있으면 그 값 우선 |
| `NEXT_PUBLIC_DEBATE_PANEL_ENABLED` | `true` | Phase 2 — `DebatePanel` / `DebateRecord` 컴포넌트 표시 토글 |

브라우저에 노출되는 env 는 `NEXT_PUBLIC_` 접두사가 필수 (Next.js 16 기준).

## 토론 진행 표시 (Phase 2)

백엔드 `qa-pipeline` 의 `debate_node` 가 `/evaluate/stream` 에 4종 SSE 이벤트를 추가로 emit 하고, 평가 결과 JSON 에도 `debates` 필드가 포함됩니다. 프론트는 이를 두 곳에서 보여줍니다.

### 실시간 — `DebatePanel` (평가 실행 페이지, Dev2 Task #8)

`apiSSE("/evaluate/stream", ...)` 로 수신하는 토론 이벤트 4종을 라이브로 누적 렌더:

| event | UI 동작 |
|---|---|
| `debate_round_start` | 항목 카드 상단에 "Round N / max" 배지, persona 칸 3개 로딩 상태 |
| `persona_turn` | 해당 persona 칸을 점수 + argument 텍스트로 채움 (strict/neutral/loose 색상 구분) |
| `moderator_verdict` | 라운드 하단 "Moderator" 박스에 합의 여부 + 최종 점수 + 근거 |
| `debate_final` | 항목 배지를 수렴/미수렴으로 확정, 사용 라운드 수 표시 |

### 사후 — `DebateRecord` (결과 상세 페이지, Dev3 Task #9)

`/v2/result/full/{cid}` 응답의 `data.debates` 를 재구성한 읽기전용 뷰:

- 항목(`item_number`) 단위 카드 리스트
- 각 카드에 초기 포지션 → 라운드별 persona 턴 → moderator 판정 → 최종 점수의 타임라인
- `converged=false` 인 경우 빨간 플래그 + 사용 라운드 수 / max_rounds 비교

### 비활성화

- 백엔드 쪽: `.env` 에 `QA_DEBATE_ENABLED=false` → debate 이벤트가 아예 오지 않음
- 프론트 쪽: `NEXT_PUBLIC_DEBATE_PANEL_ENABLED=false` → 컴포넌트 렌더 자체 스킵

프론트 토글은 백엔드 설정과 독립적이며, 이벤트가 오지 않을 때는 자동으로 빈 상태로 렌더링됩니다.

## API 사용 예

```ts
import { apiGet, apiPost, apiSSE } from "@/lib/api";
import type {
  ReviewQueueItem,
  ConsultationFull,
  EvaluationResult,
} from "@/lib/types";

// 검토 큐 조회
const queue = await apiGet<{ items: ReviewQueueItem[] }>(
  "/v2/review/queue?status=pending&limit=100",
);

// 결과 상세
const full = await apiGet<ConsultationFull>(
  `/v2/result/full/${encodeURIComponent(cid)}`,
);

// 평가 실행 (SSE)
const abort = apiSSE(
  "/evaluate/stream",
  { transcript, tenant_id: "generic" },
  (event, data) => console.log(event, data),
  { onError: console.error, onDone: () => console.log("done") },
);
// 중간 취소 시: abort()
```
