# tenants/kolon — 코오롱몰 CS Tenant (V2)

한국 의류 쇼핑몰 **코오롱몰** 고객센터용 pilot 테넌트. 자사 브랜드
(코오롱스포츠 / 헨리코튼 / 넥스 / 왁 / 골든베어) 교환·반품·사이즈 변경 상담을
주요 범위로 한다.

## 브랜드 범위

- 코오롱스포츠 (아웃도어)
- 헨리코튼 (클래식 캐주얼)
- 넥스 (여성 라이프스타일)
- 왁 (골프웨어)
- 골든베어 (캐주얼)

## 주요 Intent 분포

pilot 샘플 기준 **환불취소** (교환/반품) 와 **상품문의** (사이즈/불량) 중심.
`주문배송`, `결제문의`, `변경해지` 가 뒤를 잇는다. `장애문의` 는 거의 등장하지
않음 (의류 도메인 특성).

## #15 평가 전제

`business_knowledge/` 에 **코오롱몰 정책 매뉴얼 chunk** (1회 무상 교환, 반송장
보관, 회수 기사 2~3영업일 방문, 택배비 정책) 가 필수로 존재해야 한다. chunk
부재 시 `#15` 는 `unevaluable` 로 반환된다.

## 디렉토리 구조

```
tenants/kolon/
├── tenant_config.yaml          # kolon_v1 — tenant_id, intent 라벨(한국어), PII blocked_scope 확장
├── rubric.md                   # kolon_v1 — 교환/반품 맥락 반영한 18 항목 요약
├── prohibited_terms.txt        # generic 기반 + 의류 쇼핑몰 맥락 추가
├── golden_set/                 # 18 항목 Few-shot pilot seed (각 4~6 record)
├── business_knowledge/         # 코오롱몰 매뉴얼 chunk (외부 시딩)
├── mandatory_scripts/          # intent → 필수 안내 매핑 (외부 시딩)
└── README.md
```

## 버전

- `rubric: kolon_v1`
- `golden_set: kolon_v1`
- `last_updated: 2026-04-20`

시니어 합의 후 실 평가 데이터로 교체되면 `kolon_v2` 로 승격 예정.
