#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# QA Pipeline V3 — EC2 초기 부트스트랩. Ubuntu 22.04 LTS 전제.
# 이 스크립트는 EC2 위에서 실행됨 (deploy.py 가 SSM 으로 전송).
#
# 설치/구성:
#   - Python 3.13 (deadsnakes PPA)
#   - Node.js 20 + pnpm 10 + pm2
#   - nginx (리버스 프록시, 포트 80 → 3000 / 8081)
#   - /opt/qa-pipeline (백엔드) + /opt/qa-webapp (프론트) 디렉토리
#   - systemd unit: qa-pipeline.service
#   - pm2 ecosystem: qa-webapp

set -euo pipefail

log() { echo "[bootstrap] $*"; }

log "apt 업데이트"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  curl wget unzip git build-essential pkg-config \
  software-properties-common ca-certificates gnupg lsb-release \
  nginx

# --- AWS CLI v2 (S3 다운로드 + SSM 후속 단계에서 필요) ---
if ! command -v aws >/dev/null 2>&1; then
  log "AWS CLI v2 설치"
  cd /tmp
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
  unzip -q awscliv2.zip
  sudo ./aws/install
  rm -rf awscliv2.zip aws
fi
aws --version

# --- Python 3.13 ---
if ! command -v python3.13 >/dev/null 2>&1; then
  log "Python 3.13 설치"
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -y
  sudo apt-get install -y python3.13 python3.13-venv python3.13-dev
fi
python3.13 --version

# --- Node.js 20 ---
if ! command -v node >/dev/null 2>&1 || ! node --version | grep -q "^v20"; then
  log "Node.js 20 설치"
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
node --version

# --- pnpm 10 + pm2 ---
if ! command -v pnpm >/dev/null 2>&1; then
  log "pnpm 10 설치"
  sudo npm install -g pnpm@10.14.0
fi
if ! command -v pm2 >/dev/null 2>&1; then
  log "pm2 설치"
  sudo npm install -g pm2
fi

# --- swap 2GB (Next.js 빌드 OOM 방지) ---
if ! swapon --show | grep -q '/swapfile'; then
  log "swap 2GB 추가 (Node 빌드 OOM 방지)"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# --- 디렉토리 ---
sudo mkdir -p /opt/qa-pipeline /opt/qa-webapp /var/log/qa-pipeline
sudo chown -R ubuntu:ubuntu /opt/qa-pipeline /opt/qa-webapp /var/log/qa-pipeline

# --- Python venv (백엔드용) ---
if [ ! -d /opt/qa-pipeline/.venv ]; then
  log "Python venv 생성"
  python3.13 -m venv /opt/qa-pipeline/.venv
fi
/opt/qa-pipeline/.venv/bin/python -m pip install --upgrade pip wheel setuptools

# --- systemd unit — qa-pipeline.service ---
log "systemd unit 생성"
sudo tee /etc/systemd/system/qa-pipeline.service > /dev/null <<'UNIT'
[Unit]
Description=QA Pipeline V3 - FastAPI / LangGraph / AG2 debate
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/qa-pipeline
Environment=PYTHONPATH=/opt/qa-pipeline
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/opt/qa-pipeline/.env
ExecStart=/opt/qa-pipeline/.venv/bin/python -m v2.serving.main_v2
Restart=on-failure
RestartSec=3
StandardOutput=append:/var/log/qa-pipeline/backend.log
StandardError=append:/var/log/qa-pipeline/backend.err.log
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable qa-pipeline.service

# --- pm2 ecosystem — qa-webapp ---
log "pm2 ecosystem 생성"
tee /opt/qa-webapp/ecosystem.config.cjs > /dev/null <<'JS'
module.exports = {
  apps: [
    {
      name: "qa-webapp",
      cwd: "/opt/qa-webapp",
      script: "node",
      args: "server.js",
      env_file: "/opt/qa-webapp/.env.production",
      autorestart: true,
      max_memory_restart: "900M",
      error_file: "/var/log/qa-pipeline/webapp.err.log",
      out_file: "/var/log/qa-pipeline/webapp.out.log",
      env: {
        NODE_ENV: "production",
        PORT: "3000",
        HOSTNAME: "0.0.0.0"
      }
    }
  ]
};
JS

# --- nginx 리버스 프록시 ---
log "nginx 구성"
sudo tee /etc/nginx/sites-available/qa-pipeline > /dev/null <<'NGX'
server {
    listen 80 default_server;
    server_name _;

    # 프론트 (Next.js)
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }

    # 백엔드 API (SSE 포함)
    location /api/ {
        proxy_pass http://127.0.0.1:8081/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header X-Accel-Buffering no;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 600s;
    }
}
NGX
sudo ln -sf /etc/nginx/sites-available/qa-pipeline /etc/nginx/sites-enabled/qa-pipeline
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx

log "부트스트랩 완료"
log "  - 백엔드 venv: /opt/qa-pipeline/.venv"
log "  - 프론트: /opt/qa-webapp"
log "  - nginx: 80 → :3000 (/), :8081 (/api/)"
log "  - 다음: deploy.py --target both 로 코드 업로드 후 서비스 기동"
