# kolon 아웃바운드 텔레세일즈 부서 (샘플)

아웃바운드 영업(Telesales) 팀 전용 RAG 자원 위치 샘플. 3단계 구조 시연용.

## 폴더 내 샘플

- `tenant_config.yaml` — 텔레세일즈 팀 전용 파라미터 override 샘플

## 운영 가이드

텔레세일즈는 **설명력 · 적극성 · 필수 고지** 비중이 CS 대비 훨씬 큽니다. 운영 시 채울 순서:
1. `tenant_config.yaml` — #10~#14 (설명력/적극성), #16 (필수 고지) 항목 가중치 상향
2. `golden_set/10_explanation_clarity.json`, `14_follow_up.json` 등 텔레세일즈 특화 예시
3. `business_knowledge/manual.md` — 상품 카탈로그/프로모션 스크립트
4. `mandatory_scripts/` — "이 통화는 영업 목적입니다" 등 의무 고지 전문
