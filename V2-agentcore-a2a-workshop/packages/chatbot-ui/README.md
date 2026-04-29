# Chatbot UI

AgentCore A2A 멀티에이전트 시스템 및 QA 평가 파이프라인의 프론트엔드 자산을 보관하는 패키지.

## 구성

| 파일 | 용도 |
|------|------|
| `qa_pipeline_reactflow.html` | **QA 파이프라인 V2** (4-Layer) ReactFlow 시각화. 2026-04-20 V2 아키텍처 반영 완료. |
| `테스트용 - qa_pipeline.html` | 스냅샷 / 레거시 시각화 (참조용). |
| `_model_compare_design.md` | 모델 비교 UI 설계 메모 |
| `_state_api_impl.md` | 상태 API 구현 메모 |
| `_ui_impl_notes.md` | UI 구현 노트 |
| `_ui_verification.md` | UI 검증 체크리스트 |

## QA 파이프라인 V2 ReactFlow

`qa_pipeline_reactflow.html` — 브라우저에서 직접 열어 QA 파이프라인 V2 구조를 확인할 수 있는 독립 HTML. ReactFlow 로 4-Layer 구조를 시각화.

- Layer 1 전처리 (quality_gate / segment_splitter / pii_normalizer / rule_pre_verdictor / deduction_trigger_detector)
- Layer 2 8 Sub Agent (Group A: greeting / listening_comm / language / needs, Group B: explanation / proactiveness / work_accuracy / privacy)
- Layer 3 Orchestrator (aggregator / overrides / consistency / grader)
- Layer 4 Post-processing (confidence / tier_router / evidence_refiner / report_generator_v2)

V1 3-Phase (`greeting / understanding / courtesy / mandatory / scope / proactiveness / work_accuracy / incorrect_check / consistency_check / report_generator`) 은 더 이상 사용되지 않으며, 이 HTML 은 **V2 전환이 완료된 상태** 입니다.

대응 파이프라인 코드: [`../agentcore-agents/qa-pipeline/v2/graph_v2.py`](../agentcore-agents/qa-pipeline/v2/graph_v2.py).

## 사용

ReactFlow 시각화 확인:

```bash
# 브라우저에서 열기 (파일 프로토콜)
start "packages/chatbot-ui/qa_pipeline_reactflow.html"
```

또는 로컬 HTTP 서버로 서빙:

```bash
cd packages/chatbot-ui
python -m http.server 8000
# → http://localhost:8000/qa_pipeline_reactflow.html
```

## 관련 문서

- [QA 파이프라인 README](../agentcore-agents/qa-pipeline/README.md) — V2 4-Layer 전체 개요
- [QA 파이프라인 API](../agentcore-agents/qa-pipeline/v2/API.md) — HTTP / Python 공개 API 명세
