# tenants/generic — Generic CS Tenant (V2)

범용 CS 콜센터용 디폴트 테넌트. 특정 업종(보험/IT/통신/은행) 에 종속되지 않는
공통 rubric · 골든셋 · 업무지식 · 스크립트 · 금지어 집합.

## 디렉토리 구조

```
tenants/generic/
├── tenant_config.yaml          # 테넌시 설정: 버전, PII 엄격도, 감점 정책, intent, segment 전략
├── rubric.md                   # 평가 루브릭 (phase_a2_v1 — PL 최종 확정)
├── prohibited_terms.txt        # 금지어 / 부정어 / 민감 표현
├── golden_set/                 # 18 개 평가항목별 Few-shot JSON
│   ├── 01_first_greeting.json
│   ├── 02_closing_greeting.json
│   └── ... (18_privacy_compliance.json)
├── business_knowledge/
│   └── manual.md               # #15 정확한 안내 RAG sample chunk
├── mandatory_scripts/
│   └── intent_to_script.yaml   # #16 intent → 필수 안내 매핑
└── README.md                   # 본 파일
```

## 사용 주체

| 파일 | 소비하는 V2 컴포넌트 |
|---|---|
| `tenant_config.yaml` | Layer 3 Orchestrator (부트스트랩 시 전역 주입) |
| `rubric.md` | Sub Agent 프롬프트 빌더 |
| `golden_set/*.json` | RAG-1 (Golden-set RAG) — Dev4 `v2/rag/golden_set.py` |
| `business_knowledge/manual.md` | RAG-3 (업무지식 RAG) — Dev4 `v2/rag/business_knowledge.py` |
| `mandatory_scripts/intent_to_script.yaml` | #16 Sub Agent (Dev2) |
| `prohibited_terms.txt` | #6, #17, #18 Sub Agent (Dev2 / Dev3) |

## Production 전 교체 항목

- [x] **rubric.md** — Phase A2 최종 확정본 (`phase_a2_v1`) 반영 완료.
- [ ] **golden_set/*.json** — **현재 기능 구현용 stub**. 실 예시는 Phase 0 시니어 합의 세션 후 채움. LLM synthetic 생성은 평가자 편향 오염 방지 차원에서 보류. RAG 기능 동작 검증만 완료 (`versions.golden_set = "empty_v0.0"`).
- [ ] **business_knowledge/manual.md** — 실제 고객사 매뉴얼 chunk 교체 (embedding 재구축 필요)
- [ ] **mandatory_scripts/intent_to_script.yaml** — 고객사 콜 플로우 반영 (intent 추가/삭제 가능)
- [ ] **prohibited_terms.txt** — 업종별 민감어 보강

## 버전 관리

`tenant_config.yaml::version` 블록에서 단일 소스 관리:

```yaml
version:
  schema: "v2.0.0"
  rubric: "phase_a2_v1"      # PL 최종 확정 (2026-04-20)
  golden_set: "empty_v0.0"   # 기능 구현만. Phase 0 시니어 세션 후 교체.
  last_updated: "2026-04-20"
```

## 새 테넌트 추가 방법

1. `cp -r tenants/generic tenants/<new_tenant_id>`
2. `tenant_config.yaml::tenant_id` / `tenant_name` / `domain` 수정
3. `rubric.md` 고객사 정책 반영
4. `golden_set/` 실제 데이터 시딩
5. `business_knowledge/` 고객사 매뉴얼 교체
6. Layer 3 Orchestrator 에 테넌트 ID 등록

---

**문의**: Dev4 (#5, #6 담당). 블로커: 없음 (Phase A1/A2 확정 완료).

## Dev3 호환 어댑터

`#15 정확한 안내` Sub Agent (Dev3 `v2/agents/group_b/work_accuracy.py`) 는
`_rag_mock.lookup_business_knowledge_mock` 대신 실제 RAG 로 1줄 치환 가능:

```python
from v2.rag import lookup_business_knowledge as lookup_business_knowledge_mock
```

어댑터는 `v2/rag/business_knowledge.py::lookup_business_knowledge(consultation_type,
intent, product, transcript_slice, top_k=5)` — `{available, hits, coverage, confidence}`
반환. 매뉴얼 범위 밖 intent 이면 `available=False` 로 unevaluable 분기 유발.
