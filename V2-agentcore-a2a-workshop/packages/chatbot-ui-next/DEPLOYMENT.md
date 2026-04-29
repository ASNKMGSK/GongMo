# 배포 가이드 — chatbot-ui-next

QA Pipeline V2 의 Next.js 프론트엔드 배포 가이드.

## 사전 요구사항

- Node.js >= 20.18.1
- pnpm 10.14.0 (`npm install -g pnpm@10.14.0`)
- Python 3.13 (conda env `py313` 권장 — `~/.conda/envs/py313/python.exe`)
- AWS Bedrock 권한 (Claude Sonnet 4 호출용 + 토론 모드 사용 시 추가 호출)
- 백엔드 1개 (Phase 2 부터 단일 프로세스):
  - `qa-pipeline` FastAPI (포트 8000) — AG2 토론 모듈 인프로세스 흡수됨
  - **qa-debate:8001 은 폐기** (Phase 1 mock 단계에서만 사용. Phase 2 에서 qa-pipeline 안으로 통합)

## 로컬 개발

```bash
cd packages/chatbot-ui-next
cp .env.local.example .env.local  # NEXT_PUBLIC_API_BASE_URL 확인
pnpm install
pnpm dev   # http://localhost:3000
```

2-터미널 운영 (Phase 2 — 토론 모드 포함):

```bash
# 터미널 1 — 평가 + 토론 백엔드 (:8000)
cd packages/qa-pipeline-multitenant/qa-pipeline
pip install -r requirements.txt   # ag2[anthropic,bedrock]>=0.9.7 포함
cd packages/agentcore-agents/qa-pipeline
& "$HOME/.conda/envs/py313/python.exe" -m v2.serving.main_v2

# 터미널 2 — 프론트 (:3000)
cd packages/chatbot-ui-next
pnpm dev
```

## Phase 2 — 토론 모드 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `QA_DEBATE_ENABLED` | `true` | `false` 면 debate_node 즉시 skip (기존 reconciler 만 사용) |
| `QA_DEBATE_SPREAD_THRESHOLD` | `3` | 페르소나 점수 step_spread 가 이 값 이상이면 토론 발동 |
| `QA_DEBATE_MAX_ROUNDS` | `2` | SSoT: `v2.debate.schemas.DEFAULT_MAX_ROUNDS`. 라운드당 Bedrock 4 호출 (3 페르소나 + 1 모더레이터) |
| `QA_DEBATE_MAX_ITEMS` | `0` | 0=무제한. quota 보호용 (예: 5 → 한 상담당 최대 5 항목 토론) |
| `QA_DEBATE_TEMPERATURE` | `0.3` | 토론 페르소나 LLM 온도 |
| `BEDROCK_MODEL_ID` | `anthropic.claude-sonnet-4-20250514-v1:0` | 토론용 모델 (배치 평가와 공유) |
| `NEXT_PUBLIC_DEBATE_PANEL_ENABLED` | `true` | 프론트 DebatePanel/DebateRecord 표시 토글 |

## 프로덕션 빌드

### 옵션 A — Standalone Node 서버 (권장, EC2 배포)

```bash
pnpm build              # Turbopack 빌드 → .next/
pnpm start              # 기본 :3000, PORT 환경변수로 변경 가능
```

`package.json` 에 `"start": "next start"` 가 이미 정의되어 있음.

### 옵션 B — pm2 데몬화 (EC2 권장)

```bash
pnpm build
pnpm add -g pm2
pm2 start "pnpm start" --name qa-webapp -- --port 3000
pm2 save
pm2 startup     # 시스템 부팅 시 자동 기동 (출력된 명령 실행)
```

### 옵션 C — 정적 export (제한적)

ReactFlow + SSE 가 동적 라우트(`app/result/[cid]`) 와 결합되어 있어 **권장하지 않음**. 정적 호스팅 필요 시 `app/result/[cid]/page.tsx` 의 동적 라우트 제거 후 `next.config.ts` 에 `output: 'export'` 추가 필요.

## 환경변수

| 변수 | 필수 | 기본값 | 용도 |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | ✅ | `http://localhost:8000` | qa-pipeline 백엔드 |
| `NEXT_PUBLIC_QA_SERVER_URL` | (legacy) | 없음 | `BASE_URL` 폴백, 동일 값 권장 |
| `PORT` | ❌ | `3000` | Next 서버 포트 |

`.env.local.example` 참조.

## EC2 배포 (인플레이스 패턴)

`packages/agentcore-agents/qa-pipeline` 와 동일한 인플레이스 패턴(boto3+S3+SSM) 으로 배포 가능. IP 고정 유지 — CDK 재배포 금지 (메모리 룰 `feedback_qa_ec2_ip_preservation`).

```bash
# 1. 로컬 빌드
cd packages/chatbot-ui-next
pnpm build
tar -czf /tmp/chatbot-ui-next.tar.gz .next public package.json pnpm-lock.yaml next.config.ts

# 2. S3 업로드
aws s3 cp /tmp/chatbot-ui-next.tar.gz s3://<bucket>/deploy/chatbot-ui-next-$(date +%Y%m%d-%H%M%S).tar.gz

# 3. SSM Run Command 로 EC2 에서 추출 + pm2 reload
aws ssm send-command --instance-ids i-0cfa13fc99fcd4dfa \
  --document-name AWS-RunShellScript \
  --parameters 'commands=[
    "cd /opt/qa-webapp && aws s3 cp s3://<bucket>/deploy/chatbot-ui-next-latest.tar.gz - | tar -xz",
    "cd /opt/qa-webapp && pnpm install --prod --frozen-lockfile",
    "pm2 reload qa-webapp"
  ]'
```

## nginx 리버스 프록시 (선택)

같은 도메인에서 프론트 + 백엔드 서빙 시:

```nginx
server {
    listen 80;
    server_name qa.example.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    # SSE 통과 — 버퍼링 비활성화
    location /evaluate/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header X-Accel-Buffering no;
        proxy_read_timeout 600s;
    }

    location /v2/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    location /debate/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_buffering off;
        proxy_set_header X-Accel-Buffering no;
        proxy_read_timeout 600s;
    }
}
```

같은 origin 사용 시 `NEXT_PUBLIC_API_BASE_URL=""` (상대 경로) 로 설정 가능.

## 검증 체크리스트

배포 후 다음 확인:

- [ ] `curl http://localhost:3000/` → 200
- [ ] `curl http://localhost:3000/review` → 200
- [ ] `curl -i -X OPTIONS http://localhost:8000/v2/review/queue -H "Origin: http://localhost:3000"` → `Access-Control-Allow-Origin` 헤더 존재
- [ ] 브라우저에서 `/review` → 검토 큐 로드
- [ ] 항목 편집 → 확정 → `~/Desktop/QA평가결과/HITL_수정/<cid>.json` 파일 생성 확인
- [ ] 대시보드 → "평가 실행" → SSE 진행 표시

## Phase 2 토론 모드 — 시연 시나리오 (E2E 검증)

배포 후 다음 시나리오로 토론 흐름 검증:

1. **편차 큰 샘플 준비**: 페르소나 의견 충돌이 자주 발생하는 샘플 (예: #6 언어 표현 끝인사 누락, #7 호칭 부재 같이 평가자 주관 따라 갈리는 항목)
2. **`QA_DEBATE_SPREAD_THRESHOLD=2`** 로 임시 낮춰 발동 확률 ↑
3. 대시보드 → "평가 실행" 클릭
4. SSE 진행 → Phase B1/B2 (reconciler) 통과 후 debate_node 진입
5. **DebatePanel 라이브 진행 확인**:
   - `debate_round_start` 수신 → 라운드 dot 갱신
   - `persona_turn` 3회 (strict/neutral/loose) → 페르소나 카드 점수 + argument 표시 + fade-up 애니메이션
   - `moderator_verdict` → consensus/미합의 배지
   - `debate_final` → 최종 점수 카드 + merge_rule 배지
6. 결과 페이지 (`/result/<cid>`) → DebateRecord 블록에 완료 발언록 표시
7. JSON 폴더 (`~/Desktop/QA평가결과/JSON/<cid>.json`) 의 payload 에 `debates` 필드 존재 확인

## Phase 2 — 장애 시나리오 + 안전장치

Dev4 가 구현한 5중 안전장치 검증:

| 시나리오 | 동작 | 검증 방법 |
|---|---|---|
| AG2 import 실패 | `_get_run_debate()` → fallback_median DebateRecord + `debate_final` SSE 1회 emit | `pip uninstall ag2` 후 평가 실행 |
| `build_debate_team` 실패 | 동일 fallback | LLMConfig 잘못된 모델명 주입 |
| `initiate_chat` 실패 (Bedrock throttle) | 동일 fallback | Bedrock quota 소진 시 자동 |
| 개별 토론 항목 실패 | 해당 item 만 skip, 다른 토론은 계속 | 한 item 의 transcript 일부러 손상 |
| LLM 점수 ALLOWED_STEPS 위반 | `snap_score_v2(item, raw)` 로 강제 스냅 | `state["debates"][n].rounds[k].turns[i].score` 가 `allowed_steps` 안에 있는지 확인 |

**완전 비활성화** (Bedrock quota 보호):
```bash
export QA_DEBATE_ENABLED=false
```
debate_node 가 즉시 `{"debates": {}}` 반환. Phase B/C 노드는 그대로 동작 (downstream 영향 없음).

**부분 활성화** (특정 상담 1건만 토론):
```bash
export QA_DEBATE_MAX_ITEMS=3   # 한 상담당 최대 3 항목
```

## 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| CORS 에러 | server_v2.py CORS origin 미일치 | `allow_origins` 에 실제 도메인 추가 |
| SSE 끊김 | nginx/프록시 buffering | `proxy_buffering off` + `X-Accel-Buffering no` |
| 빌드 시 module 에러 | `node_modules` stale | `rm -rf node_modules .next && pnpm install` |
| ReactFlow 빈 화면 | SSR 시도 | `dynamic(..., { ssr: false })` 누락 확인 |
| pnpm 권한 에러 | corepack 권한 부족 | `npm install -g pnpm@10.14.0` |
| 토론 발동 안 됨 | spread < threshold 또는 ENABLED=false | `QA_DEBATE_SPREAD_THRESHOLD` 낮추기, ENABLED=true 확인 |
| DebatePanel 빈 상태 | SSE 이벤트 미도달 | 브라우저 DevTools Network 탭에서 `event: persona_turn` 텍스트 확인. CORS / proxy buffering 점검 |
| DebateRecord 빈 상태 | `data.debates` 없음 | `~/Desktop/QA평가결과/JSON/<cid>.json` 에 `debates` 필드 존재 확인 (queue_populator.py 가 저장) |
| AG2 첫 실행 TypeError | `process_message_before_send` 훅 시그니처 v0.9.7 불일치 | `qa-pipeline/v2/debate/team.py` 의 훅 시그니처 (sender, message, recipient, silent) 조정 |
