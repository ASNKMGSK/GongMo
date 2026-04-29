# 신한 부서특화 Layer 2 백엔드 구현 계획 (2026-04-28)

## 배경

- 프론트(`chatbot-ui-next/lib/pipeline.ts`)는 신한 5팀 부서특화 노드 10개 (xlsx 대분류 단위 통합) 시각화 완료 (2026-04-28).
- 백엔드 V2 (`v2/graph_v2.py`)는 fixed 8 sub-agent fan-out 으로 동작 중 → 신한 부서특화 평가는 미동작.
- 본 문서는 backend 동기화 계획만 정의 (실행 X). 사용자 트리거 시 실행.

## 출처 / 권위 소스

- xlsx: `C:\Users\META M\Desktop\0424 QA 참고자료\신한 AI QA 1\부서별_AI_QA_평가표_통합.xlsx`
  (5 시트: 컬렉션관리부 / 심사발급부 / CRM부 / 소비자보호부 / 준법관리부)
- 프론트 SSoT (이미 완료): `chatbot-ui-next/lib/pipeline.ts`
  - `NODE_DEFS` (대분류 통합 10 부서특화 노드)
  - `TENANT_PIPELINE_OVERRIDES` (5팀 별 extraSubAgents · hiddenSubAgents)
  - `NODE_TO_DEBATE_ITEMS` (debate-capable 6 부서특화 노드)
- 신한 STT 샘플: `Desktop\0424 QA 참고자료\신한 STT 샘플\<ID>_shinhan-inbound-<team>_<scenario>.json` × 150

## 매핑 (대분류 = sub-agent 노드 / 평가항목 = 노드 내 sub-items)

### 공통 7 대분류 (60점 + 개인정보 10점) — 5팀 공유, base sub-agent 재사용

| 노드 ID | 대분류 | 평가항목 | 점수 |
|---|---|---|---:|
| `greeting` | 인사 예절 | 첫인사 · 끝인사 | 10 |
| `listening_comm` | 경청 및 소통 | 호응공감 · 대기멘트 | 10 |
| `language` | 언어 표현 | 정중표현 · 쿠션어 | 10 |
| `needs` | 니즈 파악 | 문의파악·복창 · 고객정보확인 | 10 |
| `explanation` | 설명력 | 명확성·두괄식 | 10 |
| `proactiveness` | 적극성 | 해결의지 · 사후안내 | 10 |
| `privacy` | 개인정보 보호 | 정보확인절차 · 정보보호준수 | 10 |

> 신한은 default V2 점수와 일부 다름 (explanation/proactiveness 가 15→10).
> 라벨/배점 오버라이드는 프론트 `SHINHAN_COMMON_OVERRIDES` 에 이미 적용. 백엔드 rubric.md 도 동일 수치 사용.

### 부서특화 대분류 (30점) — 부서별 고유

| 부서 | 노드 ID | 대분류 | 평가항목 (sub-items) | 점수 | LLM-based | 비고 |
|---|---|---|---|---:|:---:|---|
| 컬렉션 | `coll_accuracy` | 업무 정확도 | 정확한 안내 · 필수 안내 이행 | 20 | ✓ | LLM+RAG / Intent+script |
| 컬렉션 | `coll_debt_compliance` | 채권추심 법규 준수 | 불공정 금지 · 정당 절차 고지 | 10 | – | T3 강제 / Rule+LLM verify |
| 심사 | `iss_accuracy` | 업무 정확도 | 정확한 안내 · 필수 안내 이행 | 20 | ✓ | LLM+RAG / Intent+script |
| 심사 | `iss_terms_compliance` | 약관 및 동의 절차 | 약관 설명·동의 · 가족카드 동의 | 10 | – | Rule+LLM+script / Rule+LLM verify |
| CRM | `crm_accuracy` | 업무 정확도 | 정확한 안내 · 필수 안내 이행 | 20 | ✓ | LLM+RAG / Intent+script |
| CRM | `crm_tm_compliance` | TM 준수사항 | 전화목적·녹취 · 청약철회·가입의사 | 10 | – | Rule+LLM verify |
| 소비자 | `cons_complaint` | 민원 대응 | 유형 분류 · 공감·사과 표현 | 20 | ✓ | LLM+분류기 / LLM+감정분석 |
| 소비자 | `cons_resolution` | 민원 해결 품질 | 해결책 제시·이관·사후 관리 | 5 | ✓ | LLM+Few-shot |
| 소비자 | `cons_protection` | 소비자보호 준수 | 부당응대·2차 가해 방지 | 5 | – | T3 강제 |
| 준법 | `comp_unfair_sale_check` | 불완전판매 점검 | 설명의무 · 취약계층 · 부당권유 · 청약철회 | 30 | ✓ | LLM+RAG (설명의무) 우선 |

## 작업 항목

### Phase 1 — 인프라 / SSoT (1.5h)

- [ ] **1-A** `v2/tenants/shinhan/<dept>/tenant_config.yaml` 5개 작성
  - 이미 placeholder 6 yaml 존재 (`v2/tenants/shinhan/`). 점수 / pii_policy / routing 본격 구성.
- [ ] **1-B** `v2/tenants/shinhan/<dept>/rubric.md` 5개 작성
  - xlsx 시트별 17 항목 마크다운 직렬화. 평가항목 / 평가 기준 / 배점 / 평가모드 / 처리방식 / 비고 컬럼 보존.
- [ ] **1-C** SSoT — 노드 ↔ 항목 매핑 확정
  - `v2/sub_agents/_shinhan_node_item_map.py` 신규 (프론트 `pipeline.ts` 와 1:1).
  - `SHINHAN_NODE_ITEMS = {"coll_accuracy": [{"item": "정확한 안내", "score_max": 15, "mode": "partial_with_review", "method": "LLM+RAG"}, ...], ...}`

### Phase 2 — Sub-agent 구현 (3h)

각 sub-agent 는 `v2/sub_agents/<base>.py` 와 동일한 인터페이스 (`async def evaluate(state) → SubAgentOutput`).

#### 디렉토리 구조

```
v2/sub_agents/shinhan/
  __init__.py                       # SHINHAN_SUB_AGENTS = {<id>: <fn>}
  collection/
    accuracy.py                     # coll_accuracy
    debt_compliance.py              # coll_debt_compliance
  review/
    accuracy.py
    terms_compliance.py
  crm/
    accuracy.py
    tm_compliance.py
  consumer/
    complaint.py
    resolution.py
    protection.py
  compliance/
    unfair_sale_check.py
```

- [ ] **2-A** Sub-agent 베이스 패턴 추출 — 공통 LLM 호출 / RAG 조회 / rule 패턴 검사 헬퍼
  - `v2/sub_agents/_shinhan_base.py` — `BaseShinhanAgent` 클래스 (multi-item evaluation, snap_score, reconciler)
- [ ] **2-B** 컬렉션 2 노드 구현
  - `coll_accuracy`: LLM + 채권관리 RAG (정확한 안내 1 항목) + Intent 분류 + 스크립트 매칭 (필수 안내 1 항목)
  - `coll_debt_compliance`: Rule 패턴 + LLM 검증 + T3 라우팅 (불공정 위반) / Rule + LLM verify (정당 고지)
- [ ] **2-C** 심사 2 노드 구현
- [ ] **2-D** CRM 2 노드 구현
- [ ] **2-E** 소비자 3 노드 구현
- [ ] **2-F** 준법 1 노드 구현 (4 항목 통합 — 설명의무 LLM+RAG 우선, 나머지는 rule+T3)

### Phase 3 — 그래프 동적 빌드 (1h)

- [ ] **3-A** `v2/graph_v2.py::_load_sub_agents` → tenant-aware 변환
  - 현재 시그니처: `_load_sub_agents() → dict[str, fn]` (8개 고정)
  - 변경: `_load_sub_agents_for_tenant(tenant_id: str) → dict[str, fn]`
    - 코오롱 / 제네릭: 기존 8개 (회귀 보호)
    - 신한 5팀: SHINHAN_COMMON_OVERRIDES 의 hidden 적용 + extraSubAgents 추가
- [ ] **3-B** `LRU-cached build_graph_for_tenant(tenant_id) → CompiledGraph`
  - 테넌트별 fan-out 노드 set + edges 동적 구성
  - LangGraph `add_node` / `Send fan-out` API 활용
  - 캐시 invalidation: 테넌트 config 변경 시
- [ ] **3-C** `serving/server_v2.py` — invoke 시 `state.tenant_id` 로 graph 선택
  - 기존: `app_v2.invoke(state)` (단일 컴파일 그래프)
  - 변경: `build_graph_for_tenant(state.tenant_id).ainvoke(state)`

### Phase 4 — RAG / Golden-set / Knowledge (1h)

- [ ] **4-A** `v2/tenants/shinhan/collection/business_knowledge/`
  - `채권관리_원장_샘플.md` (연체경과일수 구간별 필수안내 매트릭스)
  - `채권추심법_조문.md` (§8의2 정당 절차 / §9 불공정 금지)
- [ ] **4-B** `v2/tenants/shinhan/review/business_knowledge/`
  - `카드_상품DB_샘플.md` / `약관규제법_조문.md` / `여신금융_감독규정.md`
- [ ] **4-C** `v2/tenants/shinhan/crm/business_knowledge/`
  - `TM_상품정보.md` / `방문판매법.md` / `여신전문금융업법.md`
- [ ] **4-D** `v2/tenants/shinhan/consumer/business_knowledge/`
  - `VOC_카테고리.md` / `소비자보호기준.md`
- [ ] **4-E** `v2/tenants/shinhan/compliance/business_knowledge/`
  - `금소법_조문.md` (§19/§21/§46) / `상품_핵심설명서.md` / `취약계층_발화패턴.md`
- [ ] **4-F** `golden_set/` 시드 (각 부서 5~10건) — STT 150 샘플 중 quality_label="good" 일부 선별

### Phase 5 — API / 통합 (0.5h)

- [ ] **5-A** `serving/server_v2.py` — `GET /v2/tenants/{tid}/rubric` 엔드포인트
  - 응답: `{tenant_id, items: [{item_number, category, name, score_max, mode, method, criteria_md}], score_structure}`
  - 프론트 `getEffectivePipeline` 의 SubAgentOverride 와 정합
- [ ] **5-B** `chatbot-ui-next/lib/AppStateContext.tsx` — `/v2/tenants/{tid}/rubric` 호출 → 동적 라벨 갱신
  - 현재: 프론트 하드코드 라벨. 향후 API 응답 반영.
  - (선택) 이번 phase 에 포함하지 않고 후속으로 분리 가능.

### Phase 6 — 테스트 / 검증 (1h)

- [ ] **6-A** 단위 테스트 — 각 신한 sub-agent (10개) input/output contract
  - `tests/v2/sub_agents/shinhan/test_<dept>_<node>.py`
- [ ] **6-B** 통합 테스트 — `build_graph_for_tenant("shinhan_collection").ainvoke(state)` × 30 STT 샘플
  - 기대: Layer 2 fan-out 9개 (base 7 + 2 부서특화), barrier 9 수렴
- [ ] **6-C** 회귀 — kolon/generic 8 sub-agent 결과 동일성 검증
- [ ] **6-D** e2e — 프론트 업로드 → 백엔드 라우팅 → ReactFlow 노드 상태 정상 표시
  - shinhan_collection STT → coll_accuracy / coll_debt_compliance 노드 active → done

## 의존성 / 위험

- **위험 1**: V2 graph 핵심부 (`_load_sub_agents`) 변경 → 코오롱 회귀 가능. Phase 3 진행 전 6-C 회귀 fixture 먼저 확보.
- **위험 2**: LRU 캐시 invalidation — 테넌트 config 변경 시 graph 재빌드 트리거 필요. 운영 중 reload signal 설계 필요.
- **위험 3**: `comp_unfair_sale_check` 가 4 항목 통합 (LLM+RAG / compliance / T3 / Rule+LLM) — 단일 노드 안에서 sub-item 별 다른 처리 흐름 → 노드 내부 라우팅 복잡. 분리 고려 검토 필요.
- **의존성**: AOSS 인덱스 (`golden`, `reasoning`, `knowledge`) 신한 5팀 신규 색인 — Phase 4 완료 후 별도 인덱싱 작업 필요.

## 예상 소요 시간

- Phase 1: 1.5h
- Phase 2: 3.0h
- Phase 3: 1.0h
- Phase 4: 1.0h
- Phase 5: 0.5h
- Phase 6: 1.0h
- **총 약 8h**

## 실행 순서 권고

1. Phase 1 (yaml + rubric.md) → SSoT 고정
2. Phase 6-C 회귀 fixture 우선 확보 (Phase 3 안전망)
3. Phase 2 (sub-agent 구현) — 컬렉션 부서 1팀 먼저 완성 + 테스트 → 이후 4팀 병렬 진행 가능
4. Phase 3 (graph 동적 빌드) — 기존 회귀 확인 후 진행
5. Phase 4 (RAG corpus) — Phase 2 와 병렬 가능 (의존 없음)
6. Phase 5 + Phase 6 마무리 검증

## 미결정 항목

- 백엔드 sub-agent 가 frontend 의 `extraSubAgents` 에 정의된 ID 와 어떻게 alias 되는지 (이름 통일 vs 별도 매핑 테이블)
- 신한 평가표의 `partial_with_review` 모드 (정확한 안내 ★) 의 review_required 플래그 활용 — Layer 4 HITL 큐 라우팅 정합 확인 필요
- KSQI 는 신한 비활성 (frontend 에서 회색 처리). 백엔드도 신한 그래프에서 KSQI 분기 제외 처리 필요.
