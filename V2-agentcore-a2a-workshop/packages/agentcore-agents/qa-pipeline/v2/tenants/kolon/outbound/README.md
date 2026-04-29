# kolon 아웃바운드 — 채널 공통 자원

직하 자원은 **kolon 아웃바운드 모든 부서 공통**. 인바운드와 프로세스/스크립트 완전 분리.

## Fallback

`
1. tenants/kolon/outbound/{department}/   (가장 구체)
2. tenants/kolon/outbound/                 ← 이 폴더 (채널 공통)
3. tenants/kolon/                          (사이트 공통)
4. tenants/generic/                        (최종 fallback)
`

## 아웃바운드 특화

- **#1 첫인사**: 역발신 본인확인 스크립트
- **#16 필수 안내**: 영업 목적 / 녹취 / 개인정보 수집 동의 고지 (법적 의무)
- **#17/#18 개인정보**: 역발신 본인확인 엄격
