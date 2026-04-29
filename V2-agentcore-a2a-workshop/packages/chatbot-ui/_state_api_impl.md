# Dual Pipeline State/API 구현 노트

> Task #2 (스니펫 전달) + Task #5 (1단계 HTML 리팩터 적용) state-api-dev 담당.
> 최종 구현 스니펫은 §5 에 있음. 1단계 HTML 적용 상태 및 회귀 스모크는 §7 참조.

---

## 0. Task #5 적용 현황 (2026-04-16, 옵션 A 승인)

**HTML 현 상태 (qa_pipeline_reactflow.html, 4293 LOC, mtime 09:26:32)**

1단계 3커밋 모두 **선행 적용 상태로 확인됨** — PL 옵션 A 승인에 따라 기존 적용분을 신뢰하고 추가 수정 없이 검증만 수행:

- ✅ 커밋 1 (상수 승격 + 훅/헬퍼 추가)
  - `PHASE_A/B1/B2/C_NODES` + `edgeKey` 모듈 스코프 (line 1213~1217)
  - `parseSSEStream` (line 1222)
  - `applySSEEvent` (line 1269)
  - `usePipelineRun` (line 3473, syncFallback=true 기본값 반영됨)
- ✅ 커밋 2 (App 리팩터)
  - `const single = usePipelineRun({ backend: llmBackend, serverUrl, syncFallback: true });` (line 3911)
  - 구조분해로 nodeStates/result/logs/traces/rawLogs/phase*Highlight/setErrorAlert 등 소비
  - `runEvaluation = () => single.start({ transcript, llmBackend })` 축약 (line 4012~)
- ✅ 커밋 3 (selectedNode 정규화 + NodeDrawer 라우팅)
  - line 4244: `onNodeClick={(nodeId) => setSelectedNode({ backend: llmBackend, nodeId })}`
  - line 4278: `nodeId={selectedNode && typeof selectedNode === "object" ? selectedNode.nodeId : selectedNode}` (하위호환)
  - line 4276: `centerTab !== "compare"` 조건으로 compare 탭에선 전역 NodeDrawer 비활성 (MVP 결정 — 컬럼별 drawer 는 후속 작업)

**state-api-dev 실 수정분**: 없음 (구조가 §5 스니펫과 완전 일치). Task #5 성과 = **기존 적용 수용 + 회귀 0 스모크 검증 + tester baseline DM**.

**Task #3 영역(CompareTab/ColumnPane/탭 버튼/CSS) 도 선행 적용됨** — 이는 frontend-impl 범위이므로 state-api-dev 는 건드리지 않음.

---

## 1. 현재 (단일 파이프라인) 아키텍처 요약

대상: `packages/chatbot-ui/qa_pipeline_reactflow.html` (4001 LOC, React via ESM + Babel inline).

### 1.1 `App` 컴포넌트 내 state 선언 (line 3182~3237)

| # | State | 초기값 | 역할 |
|---|-------|--------|------|
| 1 | `serverUrl` | window.origin 또는 `http://localhost:8100` | 백엔드 URL (공유) |
| 2 | `uploadedFileName` | `""` | 업로드 파일명 (공유/입력) |
| 3 | `transcript` | `""` | 입력 텍스트 (공유/입력) |
| 4 | `running` | `false` | 실행 중 플래그 |
| 5 | `llmBackend` | `"bedrock"` | **단일 백엔드 선택** — 신규에선 양쪽 동시 고정 |
| 6 | `serverStatus` | `"offline"` | /health 폴링 결과 (공유) |
| 7 | `elapsed` | `0` | 경과 초 |
| 8 | `errorAlert` | `null` | 상단 에러 배너 |
| 9 | `nodeStates` | `{}` | **파이프라인별 분리 필요** — 노드 id → state |
|10 | `nodeTimings` | `{}` | **분리 필요** — 노드 id → seconds |
|11 | `nodeScores` | `{}` | **분리 필요** — 노드 id → score |
|12 | `nodeErrors` | `{}` | **분리 필요** — 노드 id → error_info[] |
|13 | `edgeStates` | `{}` | **분리 필요** — edge key → state |
|14 | `result` | `null` | **분리 필요** — 최종 평가 응답 |
|15 | `streamingItems` | `[]` | **분리 필요** — 실시간 항목 점수 누적 |
|16 | `logs` | `[]` | **분리 필요** — 사람용 로그 |
|17 | `traces` | `[]` | **분리 필요** — node_trace 이벤트 목록 |
|18 | `rawLogs` | `[]` | **분리 필요** — 모든 SSE 원본 |
|19 | `centerTab` | `"pipeline"` | **공유** — 중앙 탭 (+`"compare"` 추가 예정) |
|20 | `selectedNode` | `null` | **분리 필요 가능** — 노드 클릭 상세 |
|21 | `csvState` | (dict) | 구 Pentagon 폼 — dual 파이프라인과 무관 |
|22 | `tabFlash` | `{results,logs,traces,rawlogs:false}` | **분리 필요** — 새 데이터 도착 flash |
|23 | 파생 `isRunning` | - | `nodeStates.some === "active"` |
|24 | `phaseAHighlight` 등 useMemo | - | **파생 — 분리 필요** (nodeStates 에서) |

총 **"분리 필요"** 상태: 13개 (nodeStates/Timings/Scores/Errors/edgeStates/result/streamingItems/logs/traces/rawLogs/tabFlash/selectedNode/phase highlight).

### 1.2 SSE 파싱 핵심 (line 3193~ / 3426~)

**`readSSEStream(response, onData)`** (line 1193) — 범용 helper. 현재 `runEvaluation` 은 이를 쓰지 않고 인라인으로 같은 로직을 반복하는데, 이벤트 타입도 함께 파싱해야 하기 때문.

**`runEvaluation()` (line 3426~3736)** 흐름:
1. `resetPipeline()` → 모든 state 0초기화
2. `setRunning(true)` + `setNodeStates({input_data:"done", orchestrator:"active"})`
3. `fetch(${serverUrl}/evaluate/stream, {llm_backend})` → reader loop
4. `event:` + `data:` 쌍 단위 파싱 (청크 경계 유실 방지 위해 eventType 을 바깥 스코프에 두고, `inferEventType` 폴백 존재)
5. 각 evt 타입별 분기:
   - `routing` → phase 그룹 활성화 (`activatePhaseGroup`)
   - `status` → 노드 상태 갱신 + `streamingItems`/`nodeScores`/`nodeTimings` 누적 + `completeEdgesTo`
   - `result` → `setResult`, Gate 실패 처리, NODE_ITEMS 집계
   - `node_trace` → `setTraces`
   - `done` → 모든 노드 `done` 으로 마감, `setRunning(false)`
   - `error` → `setErrorAlert`, `setRunning(false)`
6. 실패 시 `/evaluate`(non-stream) 폴백 (line 3689~3734)

**해석상 중요한 사실**: setter 참조가 `runEvaluation` deps 에 명시돼 있고 (line 3736), 같은 함수 안에서 파이프라인 전체 수명주기를 관리. → dual 에선 **동일 로직이 "어떤 state setter 집합을 쓰느냐" 만 달라지면 된다**. 이게 `useDualPipelineState()` 리팩토링의 핵심 기회.

### 1.3 지금 신규 엔드포인트 호출 지점

`runCsvCompatible()` (line 2709) 이 `/evaluate/pentagon` 을 **non-streaming JSON** 으로 호출하여 `{result,busy,error}` 3개 필드만 업데이트.

> ⚠️ **Blocker 후보**: PL 브리핑은 `/evaluate/pentagon` 으로 **SSE 이벤트** 를 받는다고 명시. 그러나 현재 코드상 이 엔드포인트는 **non-streaming** 이다. 세 가지 가능성:
> 1. 백엔드가 이미 SSE 를 지원하지만 UI 가 아직 스트리밍으로 호출하지 않을 뿐
> 2. `llm_backend=sagemaker` 테스트 경로가 느려 SSE 전환이 계획 중
> 3. PL 이 엔드포인트를 착각 — 실제로는 `/evaluate/stream` 을 `llm_backend` 달리해 두 번 호출
>
> → **architect 초안 수신 즉시 질문**: "dual 호출 엔드포인트가 `/evaluate/pentagon`(SSE 지원 전제) 인지, `/evaluate/stream` 인지?" 엔드포인트 파라미터화를 통해 양쪽 다 수용 가능한 helper 설계로 가되, 기본값 명확화 필요.

---

## 2. State Tree 분리 방침 (architect 확정 전 제안)

### 2.1 권장: "인스턴스 2개 공존" 접근

두 옵션 중 **커스텀 훅 `usePipelineState()` 를 `sagemaker`/`bedrock` 2회 호출**하여 독립 인스턴스 2개를 얻는 방식 권장.

**이유**:
- 기존 단일 파이프라인 경로 regression 최소화 — 기존 `App` 에 쓰던 state 들을 통째로 훅에 싸서 옮기면, 단일 파이프라인 탭은 `usePipelineState()` 1회 호출한 결과를 그대로 소비
- 새 탭 "compare" 는 `useDualPipelineState()` (= 내부에서 훅 2회 호출 + 공용 `runDual`) 을 쓰면 되어 코드 중복 없음
- 각 인스턴스의 setter 가 클로저 독립 → race 조건 원천 차단

### 2.2 훅 시그니처 (안)

```js
function usePipelineState(label /* "bedrock" | "sagemaker" | "single" */) {
  // --- 13개 분리 state + addLog/setEdge/... + resetPipeline ---
  return {
    label,
    // state
    nodeStates, nodeTimings, nodeScores, nodeErrors, edgeStates,
    result, streamingItems, logs, traces, rawLogs,
    tabFlash, errorAlert, running, elapsed,
    // setters (주로 훅 내부에서만 쓰지만 리셋/외부 제어용)
    setNodeStates, setEdgeStates, /* ... */
    // 계산된 파생값
    phaseAHighlight, phaseB1Highlight, phaseB2Highlight, isRunning,
    // 행위
    reset,         // = resetPipeline
    addLog,        // (msg,type)
    setEdge,       // (from,to,state)
    triggerFlash,  // (tab)
    // SSE/JSON 소비자 (외부 runner 가 호출)
    handleSSEEvent,        // (eventType, data) — 기존 runEvaluation 분기 이식
    startClock, stopClock, // elapsed 타이머
  };
}
```

### 2.3 Dual 러너

```js
function useDualPipelineState() {
  const left  = usePipelineState("sagemaker");   // Qwen
  const right = usePipelineState("bedrock");

  const runDualEvaluation = useCallback(async ({ transcript, serverUrl, endpoint = "/evaluate/pentagon" }) => {
    left.reset();
    right.reset();
    // 각각 독립 Promise — Promise.all 아님 (allSettled) → 한쪽 실패해도 다른 쪽 계속
    return Promise.allSettled([
      runOne(left,  { transcript, serverUrl, endpoint, llm_backend: "sagemaker" }),
      runOne(right, { transcript, serverUrl, endpoint, llm_backend: "bedrock" }),
    ]);
  }, [left, right]);

  return { left, right, runDualEvaluation };
}

async function runOne(ctx, { transcript, serverUrl, endpoint, llm_backend }) {
  ctx.setRunning(true);
  try {
    const res = await fetch(`${serverUrl}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript, llm_backend }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("text/event-stream")) {
      await consumeSSE(res, ctx);          // event/data 분기 → ctx.handleSSEEvent
    } else {
      const data = await res.json();       // non-stream JSON 응답 → 완료 처리
      ctx.handleJSONResult(data);
    }
  } catch (err) {
    ctx.handleFatal(err);                   // errorAlert + running=false
  }
}
```

- `Promise.allSettled` 사용 → 한쪽 reject 여도 다른 쪽 진행
- fetch 간 공유 상태 없음, setter 는 각 ctx 내부 클로저
- endpoint content-type 에 따라 SSE/JSON 자동 분기 → PL 의 엔드포인트 질문 답 오기 전에도 양쪽 수용

### 2.4 단일 파이프라인 경로 유지

기존 `runEvaluation()` 은 그대로 두되 내부 구현만:
- `usePipelineState("single")` 가 반환한 ctx 에 의존하도록 리팩토링
- `/evaluate/stream` 호출 경로 및 폴백(`/evaluate`) 그대로 유지
- 신규 코드 증분만 compare 탭에서 쓰이고 기존 탭은 regression 없음

---

## 3. Risk / 질문 리스트 (architect 회신 시 묶어서 DM)

1. **엔드포인트 확정**: `/evaluate/pentagon` SSE 지원 여부. 만약 JSON-only 라면 "이벤트 스트림으로 독립 라우팅" 이라는 원래 요구가 JSON 단일 응답 → 완료 state 일괄 세팅으로 축소됨. helper 는 양쪽 다 수용하되 기본값 확정 필요.
2. **serverUrl 공유 vs 분리**: 보통 하나일 것. 다만 sagemaker/bedrock 이 다른 백엔드 서버에 있을 여지 있으면 `{serverUrlL, serverUrlR}` 분리 필요.
3. **입력(transcript) 공유 확정**: 동일 입력을 양쪽에 보낸다는 가정 OK? (브리핑상 OK)
4. **elapsed 타이머**: 공용 1개 vs 컬럼별 2개? 각 컬럼 완료 시각 다르므로 **컬럼별 2개** 권장.
5. **errorAlert 배너**: dual 에서 한쪽만 에러면 컬럼 위 인라인 표시로 내리는 게 깔끔. 공용 상단 배너는 양쪽 다 실패 시만.
6. **csvState**: 기존 Pentagon 간편 폼은 dual 에 포함 안 함 (별도 탭) — 확인.
7. **centerTab 값 `"compare"`**: frontend-impl 담당. 훅은 탭에 무관하게 동작.

## 4. 확정 사항 (architect §6.1 + PL Q1)

- 엔드포인트: **`/evaluate/stream`** 고정 (PL 확정). Pentagon 무관. content-type 분기 불필요.
- 전면 리팩터 방침: 기존 App 도 `usePipelineRun` 1회 호출로 동작. SSE 분기 로직(line 3460~3680)은 훅 내부 `applySSEEvent` 로 1회만 존재.
- `start()` 반환: `Promise<{ok:boolean, reason?:string}>` — reject 없음.
- `backend` 런타임 오버라이드: `start({ transcript, llmBackend? })` — 훅 생성 시 `backend` 는 기본값.
- `selectedNode` shape: App 레벨에서 `{backend, nodeId} | null` 로 통일 (단일 탭도 `{backend: currentLlmBackend, nodeId}`). 훅이 `selectNode(nodeId)` helper 를 노출하여 내부에서 `onSelectNode({backend, nodeId})` 로 래핑 (architect §2.4 확정).
- AbortController 훅이 소유, `abort()` / `reset()` 에서 자기 것만 중단.
- `syncFallback` 옵션 (PL 승인): **default=true**. `start()` 의 fetch catch 블록에서 `/evaluate` 동기 호출 재시도. 비교 탭은 `syncFallback:false` 명시하여 컬럼별 재시도 혼란 회피.

## 5. 구현 스니펫

> 대상 삽입 위치: `qa_pipeline_reactflow.html` line 1209 뒤 (`readSSEStream` 아래, `PipelineNodeRF` 앞).
> 의존 상수(`NODE_DEFS`, `EDGES`, `SKIPPED_NODES`, `SKIPPED_EDGES`, `DB_PARENT_MAP`, `NODE_ITEMS`, `extractItemScores`)는 모두 모듈 스코프 → 훅이 그대로 참조.
> `PHASE_A_NODES` / `PHASE_B1_NODES` / `PHASE_B2_NODES` / `PHASE_C_NODES` 는 현재 App 내부 상수. 훅으로 올릴 때 **모듈 스코프로 승격** 필요 (단순 이동).

### 5.1 모듈 스코프 상수 승격 (App 바깥으로)

```js
const PHASE_A_NODES  = ["greeting", "understanding", "courtesy", "incorrect_check", "mandatory"];
const PHASE_B1_NODES = ["scope", "work_accuracy"];
const PHASE_B2_NODES = ["proactiveness"];
const PHASE_C_NODES  = ["consistency_check", "score_validation"];
const edgeKey = (from, to) => `${from}->${to}`;
```

### 5.2 `parseSSEStream` — 리더 루프만 함수화

```js
/**
 * SSE 리더 루프. 청크 경계에서 event/data 유실 방지.
 * onEvent({ eventType, data }) 를 이벤트 쌍마다 호출.
 * AbortSignal 로 중단 가능.
 */
async function parseSSEStream(response, { signal, onEvent }) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventType = null; // 청크 경계 유지

  const inferEventType = (d) => {
    if (!d || typeof d !== 'object') return null;
    if (d.phase !== undefined && d.next_node !== undefined) return 'routing';
    if (d.node !== undefined && d.input !== undefined && d.output !== undefined) return 'node_trace';
    if (d.node !== undefined && d.status !== undefined) return 'status';
    if (d.report !== undefined || (d.elapsed_seconds !== undefined && d.node_timings !== undefined)) return 'result';
    if (d.elapsed_seconds !== undefined) return 'done';
    if (d.message !== undefined) return 'error';
    return null;
  };

  try {
    while (true) {
      if (signal?.aborted) { try { reader.cancel(); } catch {} break; }
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          const dataStr = line.slice(5).trim();
          if (!dataStr) continue;
          let data;
          try { data = JSON.parse(dataStr); } catch { continue; }
          const evtSnap = eventType || inferEventType(data);
          onEvent({ eventType: evtSnap, data });
          eventType = null;
        }
      }
    }
  } finally {
    try { reader.releaseLock(); } catch {}
  }
}
```

### 5.3 `applySSEEvent` — 이벤트 → state 변경 번들

> 기존 App `runEvaluation` line 3496~3680 을 순수 함수로 이식. 모든 setter 는 `s.*` 로 참조.

```js
/**
 * setters 묶음 s 에 이벤트 dispatch.
 * s: { setNodeStates, setNodeTimings, setNodeScores, setNodeErrors,
 *      setEdgeStates, setResult, setStreamingItems, setLogs, setTraces,
 *      setRawLogs, setErrorAlert, setRunning, addLog, setEdge,
 *      activateEdgesTo, completeEdgesTo, activatePhaseGroup }
 * elapsedFallback: done 이벤트에 elapsed_seconds 없을 때 대비 (클로저 캡처).
 */
function applySSEEvent(s, { eventType, data }, elapsedFallback) {
  const timeSnap = new Date().toLocaleTimeString('ko-KR', { hour12: false });
  s.setRawLogs(prev => [...prev, { time: timeSnap, event: eventType, data }]);

  if (eventType === "routing") {
    const { next_node, next_label, phase, phase_label } = data;
    s.addLog(`Routing: ${phase_label || phase} -> ${next_label || next_node}`, "info");
    s.setNodeStates(prev => ({ ...prev, orchestrator: "active" }));
    if (phase === "phase_a" || (phase === "parallel_eval" && next_node === "__parallel__")) {
      s.activatePhaseGroup(PHASE_A_NODES);
    } else if (phase === "phase_b1") {
      s.activatePhaseGroup(PHASE_B1_NODES);
    } else if (phase === "phase_b2") {
      s.activatePhaseGroup(PHASE_B2_NODES);
    } else if (phase === "phase_c") {
      s.activatePhaseGroup(PHASE_C_NODES);
    } else if (phase === "reporting" || next_node === "report_generator") {
      s.setNodeStates(prev => ({ ...prev, report_generator: "active" }));
      s.activateEdgesTo("report_generator");
    } else if (next_node && next_node !== "__end__" && next_node !== "__parallel__") {
      s.setNodeStates(prev => {
        const next = { ...prev, [next_node]: "active" };
        Object.entries(DB_PARENT_MAP).forEach(([db, parent]) => {
          if (parent === next_node) next[db] = "active";
        });
        return next;
      });
      s.activateEdgesTo(next_node);
      Object.entries(DB_PARENT_MAP).forEach(([db, parent]) => {
        if (parent === next_node) s.activateEdgesTo(db);
      });
    } else if (phase && phase !== "complete" && phase !== "init") {
      s.addLog(`[warn] Unmatched routing phase="${phase}" next_node="${next_node}"`, "warn");
    }
    return;
  }

  if (eventType === "status") {
    const { node, label, status, elapsed: el, scores: nodeItemScores, node_status, error_info } = data;
    const isErrorNode = node_status === "error" && Array.isArray(error_info) && error_info.length > 0;
    if (isErrorNode) {
      const errDetails = error_info
        .map(ei => `#${ei.item_number} ${ei.item_name}: ${ei.error_type} — ${ei.error_message}`)
        .join(" / ");
      s.addLog(`${label || node}: ⚠️ 에러 (${errDetails})`, "error");
    } else {
      s.addLog(`${label || node}: ${status}${el ? ` (${el.toFixed(1)}s)` : ""}`,
               status === "completed" ? "success" : "warn");
    }
    if (status === "completed" || status === "done") {
      if (nodeItemScores && nodeItemScores.length > 0) {
        const total = nodeItemScores.reduce((sum, sc) => sum + (sc.score || 0), 0);
        s.setNodeScores(prev => ({ ...prev, [node]: total }));
        s.setStreamingItems(prev => {
          const map = new Map(prev.map(it => [it.item_number, it]));
          nodeItemScores.forEach(sc => {
            if (sc.item_number != null) {
              const err = isErrorNode ? error_info.find(ei => ei.item_number === sc.item_number) : null;
              map.set(sc.item_number, err ? { ...sc, agent_id: node, _error: err } : { ...sc, agent_id: node });
            }
          });
          return Array.from(map.values()).sort((a, b) => (a.item_number || 0) - (b.item_number || 0));
        });
      }
      if (data.verification) s.setResult(prev => ({ ...(prev || {}), verification: data.verification }));
      if (data.score_validation) s.setResult(prev => ({ ...(prev || {}), score_validation: data.score_validation }));
      s.setNodeStates(prev => {
        const next = { ...prev, [node]: isErrorNode ? "error" : "done" };
        Object.entries(DB_PARENT_MAP).forEach(([db, parent]) => {
          if (parent === node) next[db] = isErrorNode ? "error" : "done";
        });
        return next;
      });
      if (isErrorNode) s.setNodeErrors(prev => ({ ...prev, [node]: error_info }));
      if (el !== undefined) s.setNodeTimings(prev => ({ ...prev, [node]: el }));
      s.completeEdgesTo(node);
      Object.entries(DB_PARENT_MAP).forEach(([db, parent]) => {
        if (parent === node) s.completeEdgesTo(db);
      });
    } else if (status === "error") {
      s.setNodeStates(prev => ({ ...prev, [node]: "error" }));
    } else if (status === "started" || status === "active") {
      s.setNodeStates(prev => ({ ...prev, [node]: "active" }));
      s.activateEdgesTo(node);
    }
    return;
  }

  if (eventType === "result") {
    const vData  = data.verification?.verification || data.verification || {};
    const svData = data.score_validation?.validation || data.score_validation || {};
    const gateFailed = data.status === "validation_failed"
      || (!data.report && (data.verification || data.score_validation));
    const consistFailed = vData.is_consistent === false;
    const scoreFailed   = svData.passed === false;

    s.setResult(data);

    if (gateFailed) {
      s.addLog("❌ Phase C Gate 실패 — 리포트 생성 건너뜀", "error");
      if (consistFailed) {
        const conflicts = vData.conflicts?.length || 0;
        const evMiss = vData.evidence_check?.missing || 0;
        s.addLog(`  • 일관성 검증 실패 — 모순 ${conflicts}건, 증거 누락 ${evMiss}건`, "error");
      }
      if (scoreFailed) {
        const issues = svData.issues?.length || 0;
        const missing = svData.missing_items?.length || 0;
        s.addLog(`  • 점수 산술 검증 실패 — 위반 ${issues}건, 누락 ${missing}건`, "error");
      }
      s.setNodeStates(prev => ({
        ...prev,
        ...(consistFailed ? { consistency_check: "error" } : {}),
        ...(scoreFailed   ? { score_validation:  "error" } : {}),
        report_generator: "gate-failed",
        qa_report: "skipped",
      }));
      s.setEdge("consistency_check", "report_generator", "skipped");
      s.setEdge("score_validation",  "report_generator", "skipped");
      s.setEdge("report_generator",  "qa_report",        "skipped");
    } else {
      s.addLog("Evaluation result received", "success");
      s.setNodeStates(prev => ({ ...prev, report_generator: "done", qa_report: "done" }));
      s.setEdge("report_generator", "qa_report", "done");
    }

    const itemScores = extractItemScores(data);
    const scores = {};
    Object.entries(NODE_ITEMS).forEach(([nodeId, itemNums]) => {
      let total = 0;
      let found_any = false;
      itemNums.forEach(num => {
        const found = itemScores.find(it => (it.item_number || it.item) === num);
        if (found) {
          total += (found.score !== undefined ? found.score : (found.awarded || 0));
          found_any = true;
        }
      });
      if (found_any) scores[nodeId] = total;
    });
    s.setNodeScores(scores);
    return;
  }

  if (eventType === "node_trace") {
    s.setTraces(prev => [...prev, data]);
    return;
  }

  if (eventType === "done") {
    const { elapsed_seconds } = data;
    s.addLog(`Pipeline completed in ${elapsed_seconds ? elapsed_seconds.toFixed(1) : elapsedFallback.toFixed(1)}s`, "success");
    s.setNodeStates(prev => {
      const next = { ...prev };
      const preserve = new Set(["skipped", "gate-failed", "error"]);
      Object.keys(NODE_DEFS).forEach(k => { if (!preserve.has(next[k])) next[k] = "done"; });
      return next;
    });
    s.setEdgeStates(prev => {
      const next = { ...prev };
      EDGES.forEach(e => {
        const ek = edgeKey(e.from, e.to);
        if (next[ek] !== "skipped") next[ek] = "done";
      });
      return next;
    });
    s.setRunning(false);
    return;
  }

  if (eventType === "error") {
    s.addLog(`Error: ${data.message || JSON.stringify(data)}`, "error");
    s.setErrorAlert({
      type: data.type === "timeout" ? "timeout" : "error",
      message: data.message || "파이프라인 실행 중 오류가 발생했습니다.",
      timestamp: Date.now(),
    });
    s.setRunning(false);
    return;
  }
}
```

### 5.4 `usePipelineRun` — 훅 본체

```js
/**
 * 런 1회에 대응하는 state 컨테이너. App 은 1회, CompareTab 은 2회 호출.
 *
 * @param {{
 *   backend: 'sagemaker' | 'bedrock',
 *   serverUrl: string,
 *   endpoint?: string,
 *   onSelectNode?: (payload: {backend: string, nodeId: string}) => void,
 *   syncFallback?: boolean,   // default true — start() 실패 시 /evaluate 동기 호출 재시도
 * }} opts
 * @returns {{
 *   backend, serverUrl, endpoint,
 *   running, elapsed, errorAlert,
 *   nodeStates, nodeTimings, nodeScores, nodeErrors, edgeStates,
 *   result, streamingItems, logs, traces, rawLogs,
 *   isRunning, phaseAHighlight, phaseB1Highlight, phaseB2Highlight,
 *   setErrorAlert,                       // 배너 X 버튼용
 *   selectNode: (nodeId: string) => void, // 내부에서 {backend, nodeId} 로 래핑해 onSelectNode 호출
 *   reset: () => void,
 *   abort: () => void,
 *   start: (args: { transcript: string, llmBackend?: string }) => Promise<{ok: boolean, reason?: string}>,
 * }}
 */
function usePipelineRun({ backend, serverUrl, endpoint = "/evaluate/stream", onSelectNode, syncFallback = true }) {
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [errorAlert, setErrorAlert] = useState(null);

  const [nodeStates, setNodeStates]   = useState({});
  const [nodeTimings, setNodeTimings] = useState({});
  const [nodeScores, setNodeScores]   = useState({});
  const [nodeErrors, setNodeErrors]   = useState({});
  const [edgeStates, setEdgeStates]   = useState({});
  const [result, setResult] = useState(null);
  const [streamingItems, setStreamingItems] = useState([]);
  const [logs, setLogs] = useState([]);
  const [traces, setTraces] = useState([]);
  const [rawLogs, setRawLogs] = useState([]);

  const abortRef = useRef(null);
  const startTimeRef = useRef(null);
  const timerRef = useRef(null);

  // ── timer
  useEffect(() => {
    if (running) {
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setElapsed((Date.now() - startTimeRef.current) / 1000);
      }, 100);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [running]);

  // ── helpers (기존 App 에서 이식)
  const addLog = useCallback((msg, type) => {
    const now = new Date();
    const t = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}.${String(now.getMilliseconds()).padStart(3,'0')}`;
    setLogs(prev => [...prev, { time: t, msg, type }]);
  }, []);

  const setEdge = useCallback((from, to, state) => {
    setEdgeStates(prev => ({ ...prev, [edgeKey(from, to)]: state }));
  }, []);

  const activateEdgesTo = useCallback((nodeId) => {
    setEdgeStates(prev => {
      const next = { ...prev };
      EDGES.forEach(e => {
        const ek = edgeKey(e.from, e.to);
        if (e.to === nodeId && next[ek] !== "skipped") next[ek] = "active";
      });
      return next;
    });
  }, []);

  const activateEdgesFrom = useCallback((nodeId) => {
    setEdgeStates(prev => {
      const next = { ...prev };
      EDGES.forEach(e => {
        const ek = edgeKey(e.from, e.to);
        if (e.from === nodeId && next[ek] !== "skipped") next[ek] = "active";
      });
      return next;
    });
  }, []);

  const completeEdgesTo = useCallback((nodeId) => {
    setEdgeStates(prev => {
      const next = { ...prev };
      EDGES.forEach(e => {
        const ek = edgeKey(e.from, e.to);
        if (e.to === nodeId && next[ek] !== "skipped") next[ek] = "done";
      });
      return next;
    });
  }, []);

  const activatePhaseGroup = useCallback((phaseNodes) => {
    setNodeStates(prev => {
      const next = { ...prev, orchestrator: "active" };
      phaseNodes.forEach(n => {
        if (next[n] !== "done" && next[n] !== "skipped") next[n] = "active";
      });
      return next;
    });
    setEdgeStates(prev => {
      const next = { ...prev };
      phaseNodes.forEach(n => {
        EDGES.forEach(e => {
          const ek = edgeKey(e.from, e.to);
          if (e.to === n && next[ek] !== "done" && next[ek] !== "skipped") next[ek] = "active";
        });
      });
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    const initNodes = {};
    Object.keys(NODE_DEFS).forEach(k => {
      initNodes[k] = SKIPPED_NODES.includes(k) ? "skipped" : "pending";
    });
    setNodeStates(initNodes);
    setNodeTimings({});
    setNodeScores({});
    setNodeErrors({});
    const initEdges = {};
    EDGES.forEach(e => {
      const ek = edgeKey(e.from, e.to);
      const isSkipped = SKIPPED_EDGES.some(se => se.from === e.from && se.to === e.to);
      initEdges[ek] = isSkipped ? "skipped" : "pending";
    });
    setEdgeStates(initEdges);
    setResult(null);
    setStreamingItems([]);
    setLogs([]);
    setTraces([]);
    setRawLogs([]);
    setErrorAlert(null);
    setElapsed(0);
  }, []);

  const selectNode = useCallback((nodeId) => {
    if (onSelectNode) onSelectNode({ backend, nodeId });
  }, [onSelectNode, backend]);

  const abort = useCallback(() => {
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch {}
      abortRef.current = null;
    }
    setRunning(false);
  }, []);

  // ── start: dual 에서도 재사용되는 메인 진입점
  const start = useCallback(async ({ transcript, llmBackend }) => {
    const useBackend = llmBackend || backend;
    if (!transcript || !transcript.trim()) return { ok: false, reason: "empty_transcript" };

    reset();
    setRunning(true);
    addLog(`Pipeline started [${useBackend}]`, "info");
    setNodeStates(prev => ({ ...prev, input_data: "done", orchestrator: "active" }));
    activateEdgesFrom("input_data");

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const setters = {
      setNodeStates, setNodeTimings, setNodeScores, setNodeErrors,
      setEdgeStates, setResult, setStreamingItems,
      setLogs, setTraces, setRawLogs, setErrorAlert, setRunning,
      addLog, setEdge, activateEdgesTo, completeEdgesTo, activatePhaseGroup,
    };

    try {
      const response = await fetch(`${serverUrl}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript: transcript.trim(), llm_backend: useBackend }),
        signal: ctrl.signal,
      });
      if (!response.ok) throw new Error(`Server returned ${response.status}`);

      let lastElapsed = 0;
      await parseSSEStream(response, {
        signal: ctrl.signal,
        onEvent: (evt) => {
          lastElapsed = (Date.now() - (startTimeRef.current || Date.now())) / 1000;
          applySSEEvent(setters, evt, lastElapsed);
        },
      });

      setRunning(false);
      abortRef.current = null;
      return { ok: true };

    } catch (err) {
      if (err.name === "AbortError") {
        addLog("Pipeline aborted", "warn");
        setRunning(false);
        return { ok: false, reason: "aborted" };
      }
      addLog(`Connection error: ${err.message}`, "error");
      setErrorAlert({
        type: "error",
        message: err.message || "파이프라인 실행 중 오류가 발생했습니다.",
        timestamp: Date.now(),
      });
      setRunning(false);
      abortRef.current = null;
      return { ok: false, reason: err.message };
    }
  }, [backend, serverUrl, endpoint, reset, addLog, setEdge, activateEdgesTo, completeEdgesTo, activateEdgesFrom, activatePhaseGroup]);

  // ── derived
  const isRunning = running || Object.values(nodeStates).some(s => s === 'active');
  const phaseAHighlight  = useMemo(() => PHASE_A_NODES .some(n => nodeStates[n] === "active" || nodeStates[n] === "done"), [nodeStates]);
  const phaseB1Highlight = useMemo(() => PHASE_B1_NODES.some(n => nodeStates[n] === "active" || nodeStates[n] === "done"), [nodeStates]);
  const phaseB2Highlight = useMemo(() => PHASE_B2_NODES.some(n => nodeStates[n] === "active" || nodeStates[n] === "done"), [nodeStates]);

  return {
    backend, serverUrl, endpoint,
    running, elapsed, errorAlert,
    nodeStates, nodeTimings, nodeScores, nodeErrors, edgeStates,
    result, streamingItems, logs, traces, rawLogs,
    isRunning, phaseAHighlight, phaseB1Highlight, phaseB2Highlight,
    setErrorAlert,
    selectNode,
    reset, abort, start,
  };
}
```

### 5.5 `CompareTab` 에서의 병렬 호출 (frontend-impl 참고)

```js
function CompareTab({ serverUrl, transcript, onSelectNode }) {
  const qwen    = usePipelineRun({ backend: 'sagemaker', serverUrl, onSelectNode, syncFallback: false });
  const bedrock = usePipelineRun({ backend: 'bedrock',   serverUrl, onSelectNode, syncFallback: false });
  // 컬럼에서는 이제 run.selectNode(nodeId) 만 호출하면 {backend, nodeId} 로 래핑되어 App 의 setSelectedNode 에 도달

  const [bothRunning, setBothRunning] = useState(false);

  const runBoth = useCallback(async () => {
    if (!transcript.trim()) return;
    setBothRunning(true);
    // start 는 reject 안 함 → Promise.all 로 충분. 한쪽 실패도 다른 쪽 SSE 는 계속 진행됨
    // (실패는 각 훅 인스턴스의 errorAlert 에 격리).
    const results = await Promise.all([
      qwen.start({ transcript }),
      bedrock.start({ transcript }),
    ]);
    setBothRunning(false);
    if (!results[0].ok && !results[1].ok) {
      console.warn("[compare] 양쪽 백엔드 모두 실패", results);
    }
  }, [qwen, bedrock, transcript]);

  const abortBoth = useCallback(() => {
    qwen.abort();
    bedrock.abort();
  }, [qwen, bedrock]);

  // ... CompareHeader / ColumnPane 렌더 (frontend-impl 담당)
}
```

### 5.6 기존 App 의 단일 런 치환 (최소 diff)

line 3192~3424 의 개별 state 선언, helper(addLog/setEdge/activateEdgesTo 등), `resetPipeline`, phaseHighlight useMemo 를 **모두 제거**하고 다음 1줄로 대체:

```js
const single = usePipelineRun({ backend: llmBackend, serverUrl, onSelectNode: setSelectedNode });
// App 의 selectedNode 는 이제 항상 {backend, nodeId} | null. NodeDrawer 는 payload.backend 로
// 어느 훅 인스턴스(single|qwen|bedrock)의 데이터를 조회할지 결정.
```

그리고 `runEvaluation` (line 3426~3736) 을 다음으로 축약:

```js
const runEvaluation = useCallback(() => {
  return single.start({ transcript, llmBackend });
}, [single, transcript, llmBackend]);
```

JSX 는 변수 참조만 기계적 치환:
- `nodeStates/nodeTimings/nodeScores/nodeErrors/edgeStates` → `single.*`
- `result/streamingItems/logs/traces/rawLogs` → `single.*`
- `running/elapsed/errorAlert` → `single.*`
- `phaseAHighlight/phaseB1Highlight/phaseB2Highlight` → `single.*`
- 배너 X 버튼 `setErrorAlert(null)` → `single.setErrorAlert(null)`

> **폴백 경로(`/evaluate` 동기 호출)**: 기존 line 3689~3734 의 sync fallback 은 본 스니펫에 **포함 안 함**. 보존하려면 `usePipelineRun({ syncFallback: true })` 옵션을 추가하여 `start()` 의 catch 블록에서 `/evaluate` 재시도하도록 확장. 단일 탭 regression 우려로 **기본값은 true 로 두고, 비교 탭은 `false` 명시** 권장 (컬럼별 재시도는 UX 혼란).

### 5.7 tabFlash 처리 (훅 바깥)

`tabFlash` 는 탭 UI 상태 → 훅 밖 (App / CompareTab 레벨) 유지. 기존 App L3247~3277 의 prev 길이 ref + useEffect 블록은 참조만 `single.logs.length` 등으로 치환하면 동작 동일. 비교 탭은 컬럼별 flash 를 쓰고 싶으면 ColumnPane 내부에 동일 블록을 두되, 훅 반환값을 감시 대상으로.

## 6. 핸드오프

- **frontend-impl 에게**: 5.1 (상수 승격) → 5.2~5.4 (훅 붙여넣기) → 5.5 (CompareTab) → 5.6 (기존 App 치환) 순서로 진행 권장. 5.6 의 변수 치환은 에디터 검색치환으로 기계적 수행. `selectedNode` shape 을 `{backend, nodeId}` 로 통일하는 작업은 drawer 쪽 별도 태스크.
- **tester 에게**: 주요 실패 시나리오 —
  1. sagemaker 서버에서 500 반환 → `qwen.errorAlert` 만 세팅되고 `bedrock` 은 정상 완료
  2. `qwen.abort()` 호출 → bedrock SSE reader 가 계속 살아있음 (AbortController 인스턴스 분리)
  3. 두 컬럼 동시 완료 → streamingItems 충돌 없음 (훅 인스턴스 분리로 원천 차단)
  4. `start()` 반환값 `{ok:false, reason: "aborted" | "empty_transcript" | <http error>}`
  5. 기존 단일 탭 regression: 동일 transcript 로 리팩터 전/후 결과 동일해야 함
- **architect 에게**: Q3(drawer shape)에 대한 제안(App 레벨 `selectedNode` 를 항상 `{backend, nodeId}`)이 §6.1 계약과 모순 없는지 확인 요청.

---

## 7. 1단계 회귀 0 스모크 체크리스트 (Task #5 완료 기준)

실 브라우저 구동은 tester 영역. state-api-dev 는 **코드 정적 검증** 까지만 수행하고 baseline DM 으로 인계.

### 7.1 state-api-dev 정적 검증 결과 (2026-04-16)

모두 pass:

| # | 체크 항목 | 위치 | 결과 |
|---|---|---|---|
| S1 | 훅 시그니처 §5.4 와 일치 (backend/serverUrl/endpoint/onSelectNode/syncFallback) | line 3477 | pass (onSelectNode 는 선행 구현에서 생략, 단일 탭 direct setSelectedNode 사용) |
| S2 | `parseSSEStream` event/data 파서 청크 경계 유지 (`eventType` 바깥 스코프) | line 1222~ | pass |
| S3 | `applySSEEvent` 6개 이벤트(routing/status/result/node_trace/done/error) 분기 구비 | line 1269~ | pass |
| S4 | App 이 훅 구조분해 후 JSX 에 `nodeStates`/`result`/... 참조 | line 3911~ | pass |
| S5 | `runEvaluation` 은 `single.start` 1줄 위임 | line 4012 | pass |
| S6 | `syncFallback: true` 단일 탭, `syncFallback: false` 비교 탭 2인스턴스 | 3843/3844/3911 | pass |
| S7 | `selectedNode` state=null init, onNodeClick 시 `{backend, nodeId}` set, NodeDrawer 하위호환 shape 추출 | 3921/4244/4278 | pass |
| S8 | compare 탭에선 전역 NodeDrawer 비활성 (MVP 결정) | 4276 | pass (주석 명시) |
| S9 | 기존 단일 탭 NodeDrawer 렌더: `nodeId={.nodeId ?? selectedNode}` — 문자열/객체 모두 허용, 기능 동일 | 4278 | pass |

> **주의 — onSelectNode 훅 inject 누락** (S1): 제 §5.4 스니펫에는 `onSelectNode` 옵션이 있지만 현 HTML 훅(line 3477)에는 인자 정의가 없음. 단일 탭은 `onNodeClick={(nodeId) => setSelectedNode({backend:llmBackend, nodeId})}` 로 JSX 단계에서 직접 정규화, `run.selectNode(nodeId)` helper 미사용. **기능 동등** — 비교 탭도 line 4267 `onSelectNode={setSelectedNode}` 로 훅 밖에서 처리. 설계상 허용 범위. tester 가 이 부분 동작 확인 필요.

### 7.2 tester 수행 브라우저 스모크 (§6.3.b 게이트 6건)

1. **Pipeline 탭 Run** → 17개 노드 pending→active→done 전환, edge 활성화, 최종 grade 표시
2. **평가결과 탭** → streamingItems 실시간 증가, NODE_ITEMS 합산 정확
3. **에이전트로그 탭** → Pipeline started / Routing / Node completed 메시지 순차
4. **트레이스 탭** → `node_trace` 이벤트 누적 (input/output 표시)
5. **상세로그 탭** → 모든 SSE raw event(time/event/data) 렌더
6. **Pentagon 탭** → 기존 `/evaluate/pentagon` non-stream 호출 변경 없음 (훅 무관)

+ Node 클릭 → NodeDrawer 열림, 닫기 ESC/X 정상 — 기존과 동일.

### 7.2.2 1단계 게이트 판정 — tester 회신 (2026-04-16)

§7.2 스모크 6건 정적 매핑 **6/6 PASS** (tester 회신, `_ui_verification.md §7` 브라우저 수동 스모크 29항 체크리스트 연동).

| # | 항목 | 결과 | 근거 (HTML) |
|---|---|---|---|
| 1 | Pipeline Run → 17노드 상태 전이 + grade | PASS | activatePhaseGroup L3553 + applySSEEvent status L1360~ + result L1411~ |
| 2 | 평가결과 streamingItems 누적 | PASS | applySSEEvent status L1376~ (item_number 정렬) |
| 3 | 에이전트로그 순차 | PASS | start L3612, routing L1329, status L1369 |
| 4 | 트레이스 node_trace 누적 | PASS | L1467~1470 |
| 5 | 상세로그 raw event | PASS | applySSEEvent 진입 직후 L1325 무조건 누적 |
| 6 | Pentagon 탭 변경 없음 | PASS | CsvCompatiblePanel L2982 훅 범위 외 |

NodeDrawer 경로 tester 재확인: 단일 탭 JSX 정규화(L4244), shape 호환(L4278), Escape/onClose null 처리, compare 탭 전역 드로어 비활성(L4276 MVP). 훅 `onSelectNode` 생략 = 기능 동등 확증.

**1단계 게이트 통과 — 2단계(Task #3) 착수 조건 충족.** state-api-dev 수정 요청 0건.

### 7.2.1 사인오프 spot-check (frontend-impl 요청, 2026-04-16)

frontend-impl 의 1단계 실적용분에 대한 심볼 기반 재검증. 라인은 최신 파일(4297 LOC, mtime 09:26:48):

| 심볼 / 계약 | 위치 | 결과 |
|---|---|---|
| `PHASE_A/B1/B2/C_NODES` + `edgeKey` 모듈 스코프 | L1266~1270 | pass |
| `parseSSEStream(response, { signal, onEvent })` + `inferEventType` 폴백 | L1275, L1281~ | pass |
| `applySSEEvent(s, evt, elapsedFallback)` 6분기 | L1322 | pass |
| `usePipelineRun({backend, serverUrl, endpoint="/evaluate/stream", syncFallback=true})` | L3477 | pass |
| `if (syncFallback)` catch 분기 (§5.6 권고) | L3659 | pass |
| `const single = usePipelineRun({backend:llmBackend, serverUrl, syncFallback:true})` | L3911 | pass |
| `runEvaluation` → `single.start({transcript, llmBackend})` 래퍼 | L4014 | pass |
| 비교 탭 `qwen`/`bedrock` `syncFallback: false` | L3843~3844 | pass |

**사인오프: 1단계 훅 추출 + App 리팩터 스펙 일치 확인.** 회귀 0 검증은 tester 의 Task #4 브라우저 e2e 에 의존.

부가 관찰: `PHASE_GROUPS` 신규 상수(L1681) 는 §5 스펙 밖 추가 구조물(frontend-impl 2단계 산물로 추정). 1단계 계약과 무관.

### 7.3 비교 탭 (2단계, frontend-impl 영역) 예비 관찰

선행 적용분 기준으로 저의 비공식 관찰(frontend-impl 이 공식 검증할 항목):
- line 3843~3844 `qwen`/`bedrock` 인스턴스 생성 확인
- line 4257~4270 `centerTab === "compare"` 분기 + compare-mode-banner 렌더
- frontend-impl 담당이므로 state-api-dev 는 수정/지적 없음. baseline DM 에 정보 전달만.
