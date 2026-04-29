# kolon 인바운드 — 채널 공통 자원

이 폴더 직하 자원은 **kolon 인바운드 모든 부서 공통**. fallback 체인 2단계 (channel-level).

## Fallback

`
1. tenants/kolon/inbound/{department}/    (가장 구체)
2. tenants/kolon/inbound/                  ← 이 폴더 (채널 공통)
3. tenants/kolon/                          (사이트 공통)
4. tenants/generic/                        (최종 fallback)
`

## 활용

- 인바운드 모든 부서가 동일하게 쓰는 자원 (예: 끝인사 평가 기준 golden_set/02_*.json)
- 부서별 차이 항목만 `cs/`, `vip/` 등 부서 폴더에 별도 자원으로 두면 자동 override

## 부서명 충돌 회피

다음은 자원 폴더 예약어 — 부서 폴더로 사용 금지: `golden_set` · `reasoning_index` · `business_knowledge` · `mandatory_scripts`
