# 모델 비교 UI 구현 노트 (Task #3)

> 대상 파일: `packages/chatbot-ui/qa_pipeline_reactflow.html`
> 설계 기반: `_model_compare_design.md` §6.2 / §3.3 + `_state_api_impl.md` §5
> 구현 전략: PL 지시 2단계 분리
> **주의**: 범위 재정의 이전에 frontend-impl 이 1단계(훅 추출 + App 리팩터)도 동시 적용함 — Task #5 범위와 중첩. PL 3안 수용으로 현 상태 유지, state-api-dev 는 1단계 사인오프만 담당.

---

## 1. 변경 요약

### 1단계 — `usePipelineRun` 훅 추출 + 기존 App 리팩터 (회귀 0)

- 모듈 스코프로 승격: `PHASE_A_NODES / PHASE_B1_NODES / PHASE_B2_NODES / PHASE_C_NODES`, `edgeKey(from,to)`
- 신규 helper: `parseSSEStream(response, {signal, onEvent})` — 기존 인라인 SSE 리더 루프를 AbortSignal 지원 순수 함수로 추출
- 신규 helper: `applySSEEvent(setters, evt, elapsedFallback)` — routing/status/result/node_trace/done/error 이벤트별 state dispatch 를 순수 함수화
- 신규 훅: `usePipelineRun({backend, serverUrl, endpoint="/evaluate/stream", syncFallback=true})`
  - 기존 App 의 13개 분리 state (`nodeStates`, `nodeTimings`, `nodeScores`, `nodeErrors`, `edgeStates`, `result`, `streamingItems`, `logs`, `traces`, `rawLogs`, `running`, `elapsed`, `errorAlert`) 전부 캡슐화
  - derived: `isRunning`, `phaseAHighlight`, `phaseB1Highlight`, `phaseB2Highlight`
  - actions: `start({transcript, llmBackend?}) -> Promise<{ok, reason?}>`, `reset()`, `abort()` (각 훅 인스턴스가 독립 `AbortController` 소유)
  - `syncFallback: true` (기본값) — `/evaluate/stream` 실패 시 `/evaluate` JSON 재호출. 기존 단일 탭 회귀 방지용. 비교 탭은 `false` 지정
- 기존 `App()` 내부:
  - 13개 state + helper(`addLog`, `setEdge`, `resetPipeline`, `activateEdgesTo/From`, `completeEdgesTo`, `activatePhaseGroup`) + `runEvaluation` SSE 루프 + phaseHighlight useMemo → **모두 제거** (약 18KB dead code)
  - `const single = usePipelineRun({backend: llmBackend, serverUrl, syncFallback: true})` 1회 호출로 치환
  - destructure: `running`, `elapsed`, `errorAlert`, `nodeStates`, … (JSX 변수 참조 0 변경)
  - `runEvaluation` 은 1줄 래퍼: `single.start({transcript, llmBackend})`

### 2단계 — "모델 비교" 탭 추가

- CSS 추가 (기존 스타일 무수정):
  - `.compare-root`, `.compare-grid` (2컬럼 grid), `.compare-col` (backend 별 border-left 색: sagemaker=var(--blue), bedrock=var(--green))
  - `.compare-header` (Run Both 버튼 + backend 배지 2개)
  - `.compare-col .tabs-bar .tab-btn` 컴팩트 (padding 6×10, 12px)
  - `.compare-mode-banner` 안내 문구
- 신규 컴포넌트:
  - `SubTabStrip({value, onChange})` — 5개 서브탭 버튼
  - `CompareHeader({qwen, bedrock, onRunBoth, onAbortBoth, transcriptReady})` — Run Both / Abort / backend 배지 (status dot + elapsed)
  - `ColumnPane({run, subTab, setSubTab, ...})` — 컬럼 헤더 + SubTabStrip + ProgressBar + ReactFlowPipeline (display 토글) + ResultsContent
  - `CompareTab({serverUrl, transcript, ...})` — 훅 2회 호출 + `Promise.all([qwen.start, bedrock.start])` 병렬 실행
- 상단 탭 바: "Pentagon 간편 평가" 우측에 "모델 비교" 버튼 추가 (`centerTab === "compare"`)
- 렌더 분기: `centerTab === "compare"` 일 때 CompareTab 렌더 + compare-mode-banner
- 좌측 패널:
  - LLM Backend 섹션 + 단일 Run 버튼을 `{centerTab !== "compare" && (...)}` 로 감싸 숨김 (설계 §5.2 Q4: disabled 아닌 숨김)
  - transcript textarea / Server URL / Sample Data 는 유지 (공유)
- NodeDrawer:
  - `selectedNode` shape 을 `{backend, nodeId}` 로 확장 (기존 string 도 허용 — drawer 에서 `typeof === "object" ? .nodeId : val` 로 호환 처리)
  - 기존 단일 탭 `onNodeClick={(id) => setSelectedNode({backend: llmBackend, nodeId: id})}` 로 정규화
  - compare 탭에선 드로어를 **비표시** (좌/우 컬럼 데이터 분리돼 App 스코프 `nodeStates/result` 로는 어느 컬럼 데이터인지 판단 불가) — MVP 범위 제한, 추후 컬럼별 드로어로 확장 여지

---

## 2. 컴포넌트 트리 (2단계 적용 후)

```
App
├── (compareTab 이 아닐 때) 기존 단일 탭 흐름 — 100% 동일
│   ├── 좌측 패널: Server URL / Transcript / Sample Data / LLM Backend / Run
│   ├── 중앙: 탭 바 + ReactFlowPipeline(display 토글) / ResultsContent
│   └── NodeDrawer (selectedNode.nodeId 기반)
└── (centerTab === "compare") 비교 탭
    ├── 좌측 패널: Server URL / Transcript / Sample Data
    │              (LLM Backend 섹션 + 단일 Run 버튼 숨김)
    └── 중앙:
        └── compare-mode-banner ("비교 모드 — 상단 Run Both 사용")
        └── CompareTab
            ├── CompareHeader
            │   ├── Run Both / Abort 버튼
            │   └── backend 배지 × 2 (qwen=sagemaker, bedrock)
            └── compare-grid
                ├── ColumnPane run={qwen}
                │   ├── col-header
                │   ├── SubTabStrip (독립)
                │   ├── ProgressBar
                │   ├── ReactFlowPipeline key="sagemaker" (display 토글)
                │   └── ResultsContent (pipeline 외 서브탭)
                └── ColumnPane run={bedrock}
                    └── (동일 구조, key="bedrock")
```

---

## 3. 변경 파일 / 주요 라인

| 변경 지점 | 내용 |
|---|---|
| ~L1210 뒤 | `PHASE_*_NODES`, `edgeKey`, `parseSSEStream`, `applySSEEvent` 모듈 스코프 삽입 |
| ~L290 뒤 (CSS) | `.compare-*` 스타일 추가 |
| ~L3420 (`usePipelineRun`) | 훅 본체 삽입 |
| ~L3729 (App 앞) | `SubTabStrip`, `CompareHeader`, `ColumnPane`, `CompareTab` 컴포넌트 삽입 |
| App state 블록 | 13개 state + helper + `runEvaluation` SSE 루프 + phaseHighlight useMemo 제거 → `const single = usePipelineRun(...)` 로 치환 |
| Pentagon 탭 버튼 뒤 | "모델 비교" 탭 버튼 추가 |
| 탭 렌더 분기 | `centerTab === "compare"` 분기에 CompareTab 렌더 |
| 좌측 패널 | LLM Backend 섹션 + Run 버튼을 `centerTab !== "compare"` 조건부 렌더 |
| NodeDrawer | **2차 작업**: `backend` prop + 헤더 배지. CompareTab 내부에 `compareSelectedNode` state + backend 맵으로 훅 데이터 선택해 독립 drawer 렌더 (A안). App 전역 drawer 는 단일 탭 전용. |
| data-testid | **2차 작업**: `center-tab-*`, `run-btn`, `progress-bar`, `graph-container`, `center-panel-content`, `compare-root`, `compare-col-{backend}`, `compare-graph-{backend}`, `compare-panel-{backend}`, `compare-subtab-{backend}-{key}`, `compare-run-both`, `compare-abort-both`, `compare-badge-{backend}`, `node-drawer`, `drawer-backend-badge` 부여 (tester 회귀 자동화용) |
| CompareTab 주석 | **2차 작업**: "의도: 탭 전환 시 진행 중 fetch 계속" 명시 |

최종 라인 수: **4359** (원본 4001 대비 +358)

---

## 4. 제약 / 알려진 한계

1. **tabFlash**: 비교 탭 내부에선 flash 피드백 없음 (`ColumnPane` 독립 flash 는 범위 초과).
2. **csvState**: 비교 탭의 ResultsContent 에도 전달되지만 "Pentagon 간편 평가" 서브탭은 비교 SubTab 에 포함되지 않음 (5개만). CsvCompatiblePanel 미노출.
3. **비교 탭의 `/evaluate/stream` 실패 시**: `syncFallback: false` 이므로 해당 컬럼은 에러 배지만 표시 (반대 컬럼은 정상 진행). 의도된 격리.
4. **Run Both 후 한쪽만 재실행**: 현재 CompareHeader 는 통합 Run Both 만. 개별 재실행 버튼은 §4 설계 원안에 있으나 MVP 제외.
5. **탭 전환 시 진행 중 fetch 계속**: 의도된 정책(설계 §6.3 #11). 비교 탭을 벗어나도 qwen/bedrock SSE 는 완료될 때까지 진행. 명시적 중단은 Abort 버튼 사용.

---

## 5. 회귀 검증 포인트 (tester 용 가이드)

### 1단계 회귀 (훅 리팩터만 — compare 탭 사용 X)

1. `/evaluate/stream` 정상 응답: 파이프라인 그래프 진행, 결과 정상 렌더 — 리팩터 전과 동일
2. `/evaluate/stream` 500/네트워크 오류: sync fallback 으로 `/evaluate` JSON 호출 → 노드 일괄 done
3. 파이프라인 실행 중 Escape: selectedNode 클리어 정상
4. 탭 전환(pipeline↔results↔logs↔traces↔rawlogs): tabFlash 애니메이션, 카운트 배지 동일
5. Pentagon 간편 평가 탭 독립 동작 (변경 없음)
6. 노드 클릭 → NodeDrawer 정상 표시 (shape 은 `{backend, nodeId}` 지만 drawer 는 `nodeId` 만 소비)

### 2단계 (비교 탭)

**진입 경로**: 상단 탭 바 가장 오른쪽 "모델 비교" 버튼 클릭

**기대 동작**:
- 좌측 패널에서 LLM Backend 섹션 + Run 버튼 **사라짐** (transcript / Sample Data / Server URL 은 유지)
- 중앙에 compare-mode-banner + 상단 [▶ Run Both] 버튼 + Qwen/Sonnet 배지 2개 표시
- 배지 상태: idle(회색) / running(파란 dot) / done(초록) / error(빨강)
- 좌=Qwen3-8B (border-left 파란), 우=Sonnet 4.5 (border-left 초록) 2컬럼
- 각 컬럼에 독립 SubTabStrip / ProgressBar / ReactFlowPipeline / ResultsContent

**핵심 시나리오**:
1. transcript 입력 + Run Both → 두 컬럼 각자 진행, elapsed 배지 독립 카운팅
2. 한쪽 서버 에러(Server URL 을 잘못 입력하거나 한쪽 backend 만 fail 되게 조작) → 해당 배지 error, 반대쪽 정상 완료
3. Abort 버튼 → 양쪽 중단
4. 좌 컬럼 SubTab 을 "results", 우를 "traces" 로 설정 → 독립 동작
5. 파이프라인 탭 ↔ 결과 탭 전환해도 ReactFlow viewport 유지 (display 토글 덕)
6. 비교 탭 → 다른 탭으로 돌아갔다 다시 비교 탭으로 → CompareTab 상태 리셋 (의도된 동작: 매 진입 새 실행)

**실패 시나리오 기대**:
- Run Both disabled 조건: `!transcript.trim() || (qwen.running && bedrock.running)`
- transcript 비어 있을 때 Run Both 클릭 → 무반응 (start 내부 early return)

### 2차 작업 (NodeDrawer backend 배지 + compare drawer + data-testid)

1. **단일 탭 노드 클릭**: drawer 헤더에 "Sonnet 4.5" / "Qwen3-8B" 배지 표시 (`data-backend` 색 구분)
2. **비교 탭 좌 컬럼 노드 클릭**: 좌 컬럼 backend 의 훅 데이터만 drawer 에 표시, 헤더 배지 "Qwen3-8B" (파란)
3. **비교 탭 우 컬럼 노드 클릭**: 우 컬럼 backend 데이터만, 헤더 배지 "Sonnet 4.5" (초록)
4. **좌/우 교차 오염 0**: 좌 클릭 후 우 클릭 → drawer 데이터가 우측 훅 인스턴스로 교체되며 좌 데이터 섞이지 않음 (설계 §6.3 #4)
5. **탭 전환 시 fetch 계속 정책** 확인: 비교 탭에서 Run Both 시작 → 다른 탭으로 이동 → 돌아오면 진행 중인 상태 유지 (CompareTab 은 매 마운트 시 새 state 이므로 상태 리셋됨. "계속되는 것은 진행 중 fetch" 가 포인트 — 현 구현은 CompareTab 언마운트 시 훅 인스턴스도 함께 사라져 fetch 도 사라짐. 이 점 주의: 설계 §6.3 #11 과 부분 충돌. 다음 개선 후보)
6. **data-testid 부착** 확인: Playwright 등 자동화에서 `[data-testid="center-tab-compare"]`, `[data-testid="compare-run-both"]`, `[data-testid="compare-col-sagemaker"]`, `[data-testid="node-drawer"][data-backend="bedrock"]` 등으로 셀렉트 가능

---

## 6. 협업자 핸드오프

- architect (`architect`): 설계 §6.2 / §5.2 Q4 / §6.3 A안 + 배지 + data-testid 전부 반영
- state-api-dev (`state-api-dev`): `_state_api_impl.md` §5.2~§5.6 기반 훅 삽입 완료. 2차 작업에서 훅 인터페이스 변경 없음 (NodeDrawer 는 App 레벨)
- tester (`tester`): 위 §5 회귀 검증 포인트 + 2차 작업 6항목 추가 검증 요청
