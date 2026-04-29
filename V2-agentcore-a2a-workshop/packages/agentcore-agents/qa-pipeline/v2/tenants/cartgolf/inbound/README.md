# cartgolf 인바운드 — 채널 공통 자원

직하 자원은 **cartgolf 인바운드 모든 부서 공통**. 라운딩 예약 / 장비 / 회원제 관련.

## Fallback

`
1. tenants/cartgolf/inbound/{department}/  (가장 구체)
2. tenants/cartgolf/inbound/                ← 이 폴더 (채널 공통)
3. tenants/cartgolf/                        (사이트 공통)
4. tenants/generic/                         (최종 fallback)
`
"@
  "C:\Users\META M\Desktop\업무\qa\V3 QA개발\V2-agentcore-a2a-workshop\packages\agentcore-agents\qa-pipeline\v2\tenants\cartgolf\outbound\README.md" = @"
# cartgolf 아웃바운드 — 채널 공통 자원

직하 자원은 **cartgolf 아웃바운드 모든 부서 공통**. 회원 유지 / 재예약 / 만기 안내 영업 목적.

## 평가 포인트

- **#1 첫인사**: 역발신 스크립트
- **#10 설명력**: 프로모션 전달력
- **#14 후속조치**: 재예약 유도
- **#16 필수 고지**: 영업 목적 / 녹취 / 개인정보 수집 고지
