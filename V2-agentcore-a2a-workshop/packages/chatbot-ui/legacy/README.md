# Legacy — 단일 HTML 프론트

`qa_pipeline_reactflow.html` (11,851줄) 은 Next.js 16 으로 마이그레이션 전까지 사용된 **단일 파일 React 18 UMD + Babel-in-browser** 프론트.

## 마이그레이션

2026-04-23 부로 `packages/chatbot-ui-next/` (Next.js 16 + TypeScript + Tailwind v4 + Turbopack) 로 전면 교체.

### 매핑

| 옛 (단일 HTML) | 새 (Next.js) |
|---|---|
| `qa_pipeline_reactflow.html` 의 ReactFlow 영역 | `app/page.tsx` + `components/PipelineFlow.tsx` + `components/nodes/` |
| 검토 큐 / 일괄 확정 / 신호등 | `app/review/page.tsx` |
| 결과 상세 + HITL 편집 | `app/result/[cid]/page.tsx` + `components/ReviewItemCard.tsx` |
| 3-페르소나 점수 표시 | `components/PersonaScores.tsx` |
| 신호등 인디케이터 | `components/StatusLight.tsx` |
| `reactflow@11.11.4` UMD | `@xyflow/react@12.10.2` (npm) |
| `fetch('/v2/...')` 직접 호출 | `lib/api.ts` 의 `apiGet/apiPost/apiSSE` |
| Babel 브라우저 변환 | TypeScript + Turbopack 빌드 |

### 보존 사유

1. **롤백 가능성** — Next.js 마이그레이션 후 회귀 발생 시 즉시 복귀 가능
2. **참조** — Phase 2 (토론 모드 UI 등) 추가 작업 시 원본 비교
3. **히스토리** — 11,851줄 안에 축적된 UX 결정 기록

### 삭제 시점

다음 조건 모두 만족 시 삭제 권장:
- chatbot-ui-next 가 EC2 운영 배포되어 1~2주 안정 운영
- 모든 기능 패리티 검증 완료 (특히 BusEdge / PhaseGroupNode / node_trace 이벤트 등 1차 미완료 부분)
- 사용자 피드백 안정화

## 실행 방법 (legacy)

```bash
# 단순 정적 호스팅
python -m http.server 8888 --directory packages/chatbot-ui/legacy
# → http://localhost:8888/qa_pipeline_reactflow.html
```

또는 브라우저에서 파일 직접 열기 (CORS 우회 위해 `--disable-web-security` 필요할 수 있음).
