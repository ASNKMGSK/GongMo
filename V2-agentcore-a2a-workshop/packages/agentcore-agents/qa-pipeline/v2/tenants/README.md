# 3단계 멀티테넌트 디렉토리 가이드

**도입일**: 2026-04-24 · **단순화**: 2026-04-27 (`_shared` 메타 폴더 폐기, 실무 표준 "직하 = 공통" 패턴으로 전환)

## 계층 구조

```
tenants/
├── {site_id}/                    # 대분류: 업체 (예: kolon, cartgolf, generic)
│   ├── tenant_config.yaml          (사이트 공통 설정 — 직하)
│   ├── golden_set/                 (사이트 공통 RAG)
│   ├── reasoning_index/
│   ├── business_knowledge/
│   ├── rubric.md
│   ├── prohibited_terms.txt
│   │
│   ├── {channel}/                # 중분류: "inbound" | "outbound"
│   │   ├── tenant_config.yaml      (채널 공통 — 있으면 site override)
│   │   ├── golden_set/             (채널 공통 RAG — 있으면 site override)
│   │   │
│   │   └── {department}/         # 소분류: 부서 자유 문자열
│   │       ├── tenant_config.yaml   (부서 전용 — 있으면 channel override)
│   │       └── golden_set/          (부서 전용 RAG)
│   │
│   └── README.md
└── generic/                       # 최종 fallback
```

## Fallback 탐색 순서 (단순화: 4단계)

```
1. tenants/{site}/{channel}/{department}/   (가장 구체)
2. tenants/{site}/{channel}/                 (채널 직하 = 채널 공통)
3. tenants/{site}/                            (사이트 직하 = 사이트 공통)
4. tenants/generic/                           (최종 fallback)
```

각 자원(`golden_set`, `reasoning_index`, `business_knowledge`, `mandatory_scripts`, `tenant_config.yaml`, `rubric.md`, `prohibited_terms.txt`)은 가장 구체적인 위치부터 탐색하여 **첫 번째로 존재하는 파일/폴더** 가 사용됩니다 (merge 아님).

## 부서 폴더명 충돌 회피 (Reserved)

채널 직하에 부서 폴더 만들 때 다음 이름은 예약어이므로 사용 금지 — 자원 폴더로 인식됩니다:

```
golden_set, reasoning_index, business_knowledge, mandatory_scripts
```

(예: `kolon/inbound/golden_set/` 은 부서가 아니라 인바운드 공통 Few-shot 자원)

## 운영 가이드

- **모든 부서가 동일한 자원** → site 직하에 둠 (`tenants/{site}/golden_set/...`)
- **같은 채널 내 모든 부서 공통** → channel 직하 (`tenants/{site}/{channel}/golden_set/...`)
- **부서마다 다른 자원** → 부서 폴더 직하 (`tenants/{site}/{channel}/{dept}/golden_set/...`)

신규 부서 추가 시 부서 폴더만 만들면 됨. 폴더 없으면 자동으로 channel/site 공통 자원이 사용됨.

## 현재 샘플 범위

| 사이트 | 채널 | 부서 | 자원 |
|---|---|---|---|
| kolon | (직하) | — | tenant_config + rubric + prohibited_terms + golden_set 등 (전사 공통) |
| kolon | inbound (직하) | — | golden_set/02_closing_greeting (인바운드 공통 끝인사) |
| kolon | inbound | cs | 풀 세트 (golden 7항목 · reasoning 5항목 · manual + tenant_config override) |
| kolon | outbound | telesales | 풀 세트 (golden 4 · reasoning 2 · manual + tenant_config override) |
| cartgolf | (직하) | — | 사이트 공통 자원 |
| cartgolf | inbound | reservation | golden 3 · manual · mandatory_scripts · tenant_config override |
| generic | (직하) | — | 최종 fallback 자원 |

## Fallback 동작 예시

요청: `kolon / inbound / vip` (부서 폴더 없음)
1. ❌ `tenants/kolon/inbound/vip/golden_set/` 없음
2. ✅ `tenants/kolon/inbound/golden_set/` (인바운드 공통 — 끝인사 등) 사용
3. (도달 안 함) `tenants/kolon/golden_set/`
4. (도달 안 함) `tenants/generic/golden_set/`

요청: `kolon / inbound / cs` (전용 폴더 있음)
1. ✅ `tenants/kolon/inbound/cs/golden_set/` 직접 사용 (1단계에서 매칭)

요청: `unknown_site / inbound / default`
1. ❌ `tenants/unknown_site/inbound/default/...` 없음
2. ❌ `tenants/unknown_site/inbound/...` 없음
3. ❌ `tenants/unknown_site/...` 없음
4. ✅ `tenants/generic/...` 사용

## 7.1~7.4 스펙 반영

- **Few-shot retrieval key** = item_number + intent + dialog_stage (각 golden_set JSON 의 `intents` / `dialog_stages` 메타)
- **Segment 추출 전략** = `segment_extraction_strategy` 필드로 항목별 명시 (`fixed_window` / `keyword_trigger_window` / `long_utterance_block` / `qa_pair` / `intent_script_crosscheck` / `pii_token_window`)
- **Reasoning RAG** = transcript 가 아닌 rationale 문장을 embedding 대상으로 (각 reasoning_index JSON 상단 `description` 명시)
- **Business Knowledge 버전** = manual.md 상단 `<!-- meta: {"version", "snapshot_date"} -->` 표기
