# kolon 인바운드 CS 부서 (샘플)

고객센터(Customer Service) 팀 전용 RAG 자원 위치 샘플. 이 폴더는 3단계 멀티테넌트 구조의
**가장 구체 레이어** (site × channel × department) 를 시연합니다.

## 폴더 내 샘플

- `golden_set/01_initial_greeting.json` — CS 팀 특화 첫인사 판정 샘플 2건
- `tenant_config.yaml` — CS 팀 전용 파라미터 override 샘플

## 운영 가이드

실제 운영 시 이 폴더를 채우는 순서:
1. `tenant_config.yaml` — t1_sample_rate, item_weights 등 CS 팀 특화 수치 정의
2. `golden_set/` 전체 18개 항목 Few-shot JSON (상위 _shared 와 다른 케이스만)
3. `reasoning_index/` — CS 팀 과거 판정 근거
4. `business_knowledge/manual.md` — CS 팀 전용 매뉴얼 (결제/반품/배송 등)
5. `mandatory_scripts/` — CS 팀 필수 안내 (예: 녹취 고지)

이 레이어에 없는 자원은 `../_shared/` → `../../_shared/` → site 루트 순으로 자동 탐색됩니다.
