# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
LinearRAG Tri-Graph 시각화 — 디스크 인덱스 → 인터랙티브 HTML.

출력: kms_data/tri_graph_visualization.html (vis.js CDN, 추가 패키지 불필요)

흐름:
  1. %TEMP%/qa_kms_linear_rag/kms_intent/linear_rag/ 에서 Tri-Graph 로드
  2. passages (16) + sentences (62) + entities (251) → 노드 추출
  3. C 매트릭스 (P-E links) + M 매트릭스 (S-E links) → 엣지 추출
  4. JSON 직렬화 후 vis.js HTML 템플릿에 임베드
  5. 자동으로 브라우저 열기 (옵션)

사용:
  python visualize_tri_graph.py
  python visualize_tri_graph.py --no-open  # 자동 열기 비활성
  python visualize_tri_graph.py --intent 교환  # 특정 인텐트만 필터
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import webbrowser
from pathlib import Path

# qa-pipeline 루트
SCRIPT_DIR = Path(__file__).parent
QA_PIPELINE_DIR = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(QA_PIPELINE_DIR))


def load_tri_graph_safe(tenant_id: str, tenant_root: Path):
    """Tri-Graph 로드 — load_tri_graph 헬퍼 사용."""
    from v2.rag.linear_rag.tri_graph import load_tri_graph, tri_graph_exists
    if not tri_graph_exists(tenant_id, tenant_root):
        raise FileNotFoundError(
            f"Tri-Graph 인덱스 없음: tenant_id={tenant_id} tenant_root={tenant_root}\n"
            "먼저 LinearRAG 모드로 한 번 평가를 실행해 인덱스를 빌드해주세요."
        )
    return load_tri_graph(tenant_id, tenant_root)


def build_graph_data(graph, intent_filter: str | None = None) -> dict:
    """Tri-Graph → vis.js 노드/엣지 형식.

    노드 속성:
      - passage: blue, group="passage", label=branch, title=text 일부
      - sentence: orange, group="sentence", label=짧은 텍스트
      - entity: green/red, group="entity", label=canonical
    """
    import scipy.sparse as sp

    # 인텐트 색상 매핑 (passage 의 metadata.intent 별)
    intent_colors = {
        "회원정보": "#10b981",  # emerald
        "환불": "#f59e0b",      # amber
        "교환": "#3b82f6",      # blue
        "반품": "#ef4444",      # red
        "수선": "#a855f7",      # purple
        "배송": "#06b6d4",      # cyan
        "취소": "#6b7280",      # gray
    }

    nodes: list[dict] = []
    edges: list[dict] = []
    node_id_set: set[str] = set()

    # ── Passage 노드 ──
    pid_to_intent: dict[str, str] = {}
    for p in graph.passages:
        intent = (p.metadata or {}).get("intent") or "기타"
        if intent_filter and intent != intent_filter:
            continue
        pid_to_intent[p.pid] = intent
        text_preview = (p.text[:120] + "…") if len(p.text) > 120 else p.text
        nodes.append({
            "id": f"P:{p.pid}",
            "label": f"P · {(p.metadata or {}).get('branch', p.pid)}",
            "title": f"<b>[{intent}] {(p.metadata or {}).get('branch', '')}</b><br/>{text_preview}",
            "group": "passage",
            "color": intent_colors.get(intent, "#94a3b8"),
            "size": 28,
            "shape": "dot",
            "_intent": intent,
        })
        node_id_set.add(f"P:{p.pid}")

    # ── Sentence 노드 ──
    sid_to_intent: dict[str, str] = {}
    for s in graph.sentences:
        parent_intent = pid_to_intent.get(s.parent_pid)
        if intent_filter and parent_intent != intent_filter:
            continue
        if not parent_intent:
            continue
        sid_to_intent[s.sid] = parent_intent
        text_preview = (s.text[:80] + "…") if len(s.text) > 80 else s.text
        nodes.append({
            "id": f"S:{s.sid}",
            "label": f"S · {text_preview[:30]}",
            "title": f"<b>Sentence (from {s.parent_pid})</b><br/>{text_preview}",
            "group": "sentence",
            "color": intent_colors.get(parent_intent, "#94a3b8"),
            "size": 14,
            "shape": "diamond",
            "_intent": parent_intent,
        })
        node_id_set.add(f"S:{s.sid}")

    # ── Entity 노드 — 어떤 passage 와도 연결되지 않으면 skip ──
    # C 매트릭스로 entity 가 어느 passage 에 연결되는지 미리 파악 → 노출 entity 결정
    contain_csr = sp.csr_matrix(graph.contain_matrix)
    mention_csr = sp.csr_matrix(graph.mention_matrix)

    pid_idx = {p.pid: i for i, p in enumerate(graph.passages)}
    eid_idx = {e.eid: i for i, e in enumerate(graph.entities)}
    sid_idx = {s.sid: i for i, s in enumerate(graph.sentences)}

    # 노출할 entity index 결정 — 인텐트 필터 시 해당 passage 와 연결된 것만
    if intent_filter:
        active_pids = [p.pid for p in graph.passages if (p.metadata or {}).get("intent") == intent_filter]
        active_p_idx = [pid_idx[p] for p in active_pids if p in pid_idx]
        if active_p_idx:
            sub = contain_csr[active_p_idx, :]
            entity_active = set(sub.nonzero()[1].tolist())
        else:
            entity_active = set()
    else:
        entity_active = set(range(len(graph.entities)))

    for i, e in enumerate(graph.entities):
        if i not in entity_active:
            continue
        # entity 가 어느 인텐트에 가장 많이 연결되는지 (색상 결정)
        connected_pids_idx = contain_csr[:, i].nonzero()[0].tolist()
        intent_count: dict[str, int] = {}
        for p_idx in connected_pids_idx:
            ip = (graph.passages[p_idx].metadata or {}).get("intent") or "기타"
            intent_count[ip] = intent_count.get(ip, 0) + 1
        dominant_intent = max(intent_count.items(), key=lambda kv: kv[1])[0] if intent_count else "기타"
        nodes.append({
            "id": f"E:{e.eid}",
            "label": e.canonical_form,
            "title": (
                f"<b>Entity: {e.canonical_form}</b><br/>"
                f"Surface: {e.surface}<br/>"
                f"P 연결: {len(connected_pids_idx)} / S 연결: {mention_csr[:, i].nnz}"
            ),
            "group": "entity",
            "color": intent_colors.get(dominant_intent, "#94a3b8"),
            "size": min(40, 8 + len(connected_pids_idx) * 3),  # 연결 많을수록 크게
            "shape": "triangle",
            "_intent": dominant_intent,
        })
        node_id_set.add(f"E:{e.eid}")

    # ── 엣지 (P-E links from C, S-E links from M) ──
    # P-E
    for p_idx, e_idx in zip(*contain_csr.nonzero()):
        p = graph.passages[int(p_idx)]
        e = graph.entities[int(e_idx)]
        n1 = f"P:{p.pid}"
        n2 = f"E:{e.eid}"
        if n1 in node_id_set and n2 in node_id_set:
            edges.append({"from": n1, "to": n2, "color": {"color": "#94a3b8", "opacity": 0.4}, "width": 0.8})

    # S-E
    for s_idx, e_idx in zip(*mention_csr.nonzero()):
        s = graph.sentences[int(s_idx)]
        e = graph.entities[int(e_idx)]
        n1 = f"S:{s.sid}"
        n2 = f"E:{e.eid}"
        if n1 in node_id_set and n2 in node_id_set:
            edges.append({"from": n1, "to": n2, "color": {"color": "#cbd5e1", "opacity": 0.25}, "width": 0.5, "dashes": True})

    # P-S (parent-child) 엣지 — sentence 가 parent passage 와 연결
    for s in graph.sentences:
        if s.sid not in sid_to_intent:
            continue
        n1 = f"P:{s.parent_pid}"
        n2 = f"S:{s.sid}"
        if n1 in node_id_set and n2 in node_id_set:
            edges.append({"from": n1, "to": n2, "color": {"color": "#475569", "opacity": 0.7}, "width": 1.5})

    # 통계
    stats = {
        "tenant_id": graph.tenant_id,
        "node_counts": {
            "passages": len([n for n in nodes if n["group"] == "passage"]),
            "sentences": len([n for n in nodes if n["group"] == "sentence"]),
            "entities": len([n for n in nodes if n["group"] == "entity"]),
        },
        "edge_counts": {
            "P-E": int(contain_csr.nnz) if not intent_filter else len([e for e in edges if e["from"].startswith("P:") and e["to"].startswith("E:")]),
            "S-E": int(mention_csr.nnz) if not intent_filter else len([e for e in edges if e["from"].startswith("S:") and e["to"].startswith("E:")]),
            "P-S": len([e for e in edges if e["from"].startswith("P:") and e["to"].startswith("S:")]),
        },
        "intent_filter": intent_filter,
        "intent_breakdown": {},
    }
    for intent in intent_colors.keys():
        intent_passages = [n for n in nodes if n["group"] == "passage" and n.get("_intent") == intent]
        intent_entities = [n for n in nodes if n["group"] == "entity" and n.get("_intent") == intent]
        stats["intent_breakdown"][intent] = {
            "passages": len(intent_passages),
            "entities (dominant)": len(intent_entities),
            "color": intent_colors[intent],
        }

    return {"nodes": nodes, "edges": edges, "stats": stats, "intent_colors": intent_colors}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>LinearRAG Tri-Graph — KMS Intent</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; background: #fafaf6; }}
  #app {{ display: flex; height: 100vh; }}
  #sidebar {{ width: 320px; background: #fff; border-right: 1px solid #e7e3d4; padding: 16px; overflow-y: auto; }}
  #network {{ flex: 1; background: linear-gradient(180deg, #fdfcf8 0%, #fbfaf5 100%); }}
  h1 {{ font-size: 16px; margin: 0 0 4px; color: #1f2937; }}
  .sub {{ font-size: 11px; color: #64748b; margin-bottom: 12px; }}
  .stats {{ background: #f9f7ee; border: 1px solid #ece8d8; border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; font-size: 12px; }}
  .stat-row {{ display: flex; justify-content: space-between; padding: 2px 0; }}
  .stat-label {{ color: #475569; }}
  .stat-val {{ font-weight: 700; color: #0f172a; font-variant-numeric: tabular-nums; }}
  .legend {{ font-size: 11px; }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; padding: 3px 0; }}
  .legend-color {{ width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; }}
  .legend-text {{ color: #1f2937; }}
  .legend-count {{ margin-left: auto; color: #64748b; font-size: 10px; font-variant-numeric: tabular-nums; }}
  .section-title {{ font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: #475569; margin: 12px 0 6px; }}
  .controls button {{ padding: 6px 10px; margin: 0 4px 4px 0; border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; font-size: 11px; cursor: pointer; color: #1f2937; }}
  .controls button:hover {{ background: #f1f5f9; }}
  .controls button.active {{ background: #3b82f6; color: white; border-color: #3b82f6; }}
  .node-shape-row {{ display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 11px; }}
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h1>🕸️ LinearRAG Tri-Graph</h1>
    <div class="sub">tenant=<b>{tenant_id}</b> · KMS 인텐트 분류 인덱스</div>

    <div class="stats">
      <div class="section-title">📊 노드 통계</div>
      <div class="stat-row"><span class="stat-label">Passages</span><span class="stat-val">{n_passages}</span></div>
      <div class="stat-row"><span class="stat-label">Sentences</span><span class="stat-val">{n_sentences}</span></div>
      <div class="stat-row"><span class="stat-label">Entities</span><span class="stat-val">{n_entities}</span></div>
      <div class="section-title" style="margin-top:8px">🔗 엣지 통계</div>
      <div class="stat-row"><span class="stat-label">P–E (contain)</span><span class="stat-val">{n_pe}</span></div>
      <div class="stat-row"><span class="stat-label">S–E (mention)</span><span class="stat-val">{n_se}</span></div>
      <div class="stat-row"><span class="stat-label">P–S (parent)</span><span class="stat-val">{n_ps}</span></div>
    </div>

    <div class="section-title">🎨 인텐트별 색상</div>
    <div class="legend">{legend_html}</div>

    <div class="section-title">🔷 노드 모양</div>
    <div class="node-shape-row"><span style="font-size:14px">●</span> Passage (KMS 행)</div>
    <div class="node-shape-row"><span style="font-size:14px">◆</span> Sentence (문장)</div>
    <div class="node-shape-row"><span style="font-size:14px">▲</span> Entity (핵심 단어)</div>

    <div class="section-title">🎚️ 인텐트 필터</div>
    <div class="controls" id="filter-controls">
      <button class="active" data-intent="">전체</button>
      {filter_buttons}
    </div>

    <div class="section-title" style="margin-top:14px">📋 사용법</div>
    <div style="font-size:11px; color:#475569; line-height:1.6;">
      • 마우스 휠 → 줌<br/>
      • 드래그 → 이동<br/>
      • 노드 hover → 상세 정보<br/>
      • 노드 클릭 → 강조<br/>
      • 인텐트 버튼 → sub-graph 필터
    </div>
  </div>
  <div id="network"></div>
</div>

<script>
const ALL_NODES = {nodes_json};
const ALL_EDGES = {edges_json};

let network = null;

function render(intentFilter) {{
  const nodes = intentFilter
    ? ALL_NODES.filter(n => n._intent === intentFilter)
    : ALL_NODES;
  const nodeIds = new Set(nodes.map(n => n.id));
  const edges = ALL_EDGES.filter(e => nodeIds.has(e.from) && nodeIds.has(e.to));

  const data = {{
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges),
  }};
  const options = {{
    nodes: {{
      font: {{ size: 11, color: '#1f2937', face: 'Segoe UI, Malgun Gothic' }},
      borderWidth: 1.5,
      shadow: false,
    }},
    edges: {{
      smooth: {{ type: 'continuous', roundness: 0.2 }},
      shadow: false,
    }},
    physics: {{
      barnesHut: {{
        gravitationalConstant: -4500,
        springLength: 110,
        springConstant: 0.05,
        damping: 0.6,            // 진동 빨리 감쇠 (기본 0.09 → 0.6)
        avoidOverlap: 0.4,
      }},
      stabilization: {{
        enabled: true,
        iterations: 400,         // 좀 더 충분히 안정화
        updateInterval: 25,
        fit: true,
      }},
      timestep: 0.35,
      adaptiveTimestep: true,
    }},
    interaction: {{
      hover: true,
      tooltipDelay: 150,
      navigationButtons: true,
      dragNodes: true,           // 사용자가 드래그로 위치 조정 가능
    }},
    groups: {{
      passage: {{ borderWidth: 3 }},
      sentence: {{ borderWidth: 1 }},
      entity: {{ borderWidth: 1 }},
    }},
  }};
  if (network) network.destroy();
  network = new vis.Network(document.getElementById('network'), data, options);

  // ★ 안정화 완료되면 physics 끔 — 그 이후 흔들림 0. 노드 드래그 시에만 일시 활성.
  network.once('stabilizationIterationsDone', () => {{
    network.setOptions({{ physics: {{ enabled: false }} }});
  }});
  // 사용자가 노드 드래그하면 잠깐 physics 켜서 자연스러운 reposition,
  // dragEnd 직후 1초 후 다시 끔 (계속 흔들림 방지).
  let _dragRePhysicsTimer = null;
  network.on('dragStart', () => {{
    if (_dragRePhysicsTimer) clearTimeout(_dragRePhysicsTimer);
    network.setOptions({{ physics: {{ enabled: true }} }});
  }});
  network.on('dragEnd', () => {{
    if (_dragRePhysicsTimer) clearTimeout(_dragRePhysicsTimer);
    _dragRePhysicsTimer = setTimeout(() => {{
      network.setOptions({{ physics: {{ enabled: false }} }});
    }}, 800);
  }});
}}

document.querySelectorAll('#filter-controls button').forEach(btn => {{
  btn.addEventListener('click', e => {{
    document.querySelectorAll('#filter-controls button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    render(btn.dataset.intent || null);
  }});
}});

render(null);
</script>
</body>
</html>
"""


def build_html(graph_data: dict) -> str:
    s = graph_data["stats"]
    legend_rows = []
    for intent, info in s["intent_breakdown"].items():
        if info["passages"] == 0 and info["entities (dominant)"] == 0:
            continue
        legend_rows.append(
            f'<div class="legend-row">'
            f'<span class="legend-color" style="background:{info["color"]}"></span>'
            f'<span class="legend-text">{intent}</span>'
            f'<span class="legend-count">P {info["passages"]} · E {info["entities (dominant)"]}</span>'
            f'</div>'
        )
    filter_buttons = "\n      ".join(
        f'<button data-intent="{intent}">{intent}</button>'
        for intent, info in s["intent_breakdown"].items()
        if info["passages"] > 0
    )
    return HTML_TEMPLATE.format(
        tenant_id=s["tenant_id"],
        n_passages=s["node_counts"]["passages"],
        n_sentences=s["node_counts"]["sentences"],
        n_entities=s["node_counts"]["entities"],
        n_pe=s["edge_counts"]["P-E"],
        n_se=s["edge_counts"]["S-E"],
        n_ps=s["edge_counts"]["P-S"],
        legend_html="\n      ".join(legend_rows),
        filter_buttons=filter_buttons,
        nodes_json=json.dumps(graph_data["nodes"], ensure_ascii=False),
        edges_json=json.dumps(graph_data["edges"], ensure_ascii=False),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 비활성")
    ap.add_argument("--intent", default=None, help="특정 인텐트만 시각화 (예: 교환)")
    ap.add_argument("--out", default=None, help="출력 HTML 경로 (기본: kms_data/tri_graph_visualization.html)")
    args = ap.parse_args()

    tenant_root = Path(tempfile.gettempdir()) / "qa_kms_linear_rag"
    tenant_id = "kms_intent"
    print(f"[1/4] Tri-Graph 로드 — tenant_root={tenant_root}")
    graph = load_tri_graph_safe(tenant_id, tenant_root)
    print(f"      passages={graph.num_passages()} sentences={graph.num_sentences()} entities={graph.num_entities()}")

    print(f"[2/4] 그래프 데이터 빌드 (intent_filter={args.intent or 'None'})")
    graph_data = build_graph_data(graph, intent_filter=args.intent)
    print(f"      nodes={len(graph_data['nodes'])} edges={len(graph_data['edges'])}")

    print(f"[3/4] HTML 생성")
    html = build_html(graph_data)
    out_path = Path(args.out) if args.out else SCRIPT_DIR / "tri_graph_visualization.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"      saved: {out_path} ({len(html):,}B)")

    print(f"[4/4] 완료")
    if not args.no_open:
        print(f"      브라우저 열기 시도...")
        try:
            webbrowser.open(out_path.as_uri())
        except Exception as e:
            print(f"      자동 열기 실패: {e}")
            print(f"      직접 열기: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
