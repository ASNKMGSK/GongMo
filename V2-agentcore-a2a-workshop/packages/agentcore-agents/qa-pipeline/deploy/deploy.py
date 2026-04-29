# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""QA Pipeline V3 — 인플레이스 배포 스크립트.

provision.py 로 만든 EC2 에 backend/frontend 코드를 반복적으로 올리고 재기동.

타겟:
  --target bootstrap   # bootstrap.sh 를 SSM 으로 실행 (최초 1회)
  --target backend     # qa-pipeline 코드 + requirements 재배포 + systemctl restart
  --target frontend    # chatbot-ui-next 빌드 → 업로드 + pm2 reload
  --target both        # 백+프론트 동시

동작:
  1. 로컬에서 tar.gz 생성
  2. S3 업로드 (provision.out.json 의 bucket 사용)
  3. SSM SendCommand 로 EC2 에서 받아 압축 해제 + 서비스 재기동
  4. /health 스모크 테스트

Idempotent · 취소 안전 (S3 key 버전 보존).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError


HERE = pathlib.Path(__file__).parent.resolve()
REPO_ROOT = HERE.parent.parent.parent.parent.resolve()  # .../V2-agentcore-a2a-workshop/
BACKEND_SRC = HERE.parent  # .../packages/agentcore-agents/qa-pipeline/
FRONTEND_SRC = REPO_ROOT / "packages" / "chatbot-ui-next"

PROVISION_OUT = HERE / "provision.out.json"


def _log(msg: str) -> None:
    print(f"[deploy] {msg}", flush=True)


def load_provision() -> dict[str, Any]:
    if not PROVISION_OUT.exists():
        raise SystemExit(f"provision.out.json 없음 — 먼저 `python provision.py` 실행: {PROVISION_OUT}")
    return json.loads(PROVISION_OUT.read_text(encoding="utf-8"))


def run_ssm(
    ssm_client: Any,
    instance_id: str,
    commands: list[str],
    timeout: int = 1800,
    comment: str = "qa-deploy",
) -> dict[str, Any]:
    """SSM SendCommand → 완료까지 polling. 실패 시 raise."""
    resp = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment=comment,
        Parameters={"commands": commands, "executionTimeout": [str(timeout)]},
        TimeoutSeconds=timeout,
    )
    cmd_id = resp["Command"]["CommandId"]
    _log(f"  SSM CommandId={cmd_id}")

    start = time.time()
    while True:
        time.sleep(4)
        try:
            inv = ssm_client.get_command_invocation(
                CommandId=cmd_id, InstanceId=instance_id
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "InvocationDoesNotExist":
                if time.time() - start > 30:
                    raise
                continue
            raise
        status = inv["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            # Windows cp949 콘솔에서 em dash 등 출력 시 UnicodeEncodeError 방지.
            # encode/decode 로 콘솔 인코딩에 맞춰 unsupported 문자 치환.
            def _safe_print(text: str, *, stream=sys.stdout) -> None:
                enc = getattr(stream, "encoding", None) or "utf-8"
                try:
                    stream.write(text.encode(enc, errors="replace").decode(enc, errors="replace"))
                    stream.write("\n")
                    stream.flush()
                except Exception:
                    # 최후수단 — ASCII 폴백
                    stream.write(text.encode("ascii", errors="replace").decode("ascii"))
                    stream.write("\n")
                    stream.flush()
            if inv.get("StandardOutputContent"):
                _safe_print(inv["StandardOutputContent"])
            if inv.get("StandardErrorContent"):
                _safe_print(inv["StandardErrorContent"], stream=sys.stderr)
            if status != "Success":
                raise SystemExit(f"SSM {status}: {inv.get('StatusDetails')}")
            return inv
        if time.time() - start > timeout:
            raise SystemExit("SSM timeout")


def _make_tar(src: pathlib.Path, out_path: pathlib.Path, include_paths: list[str] | None = None, exclude_dirs: set[str] | None = None) -> None:
    """tarball 생성 — exclude 셋 강화 (.next/cache 같은 GB 단위 캐시 폴더 차단)."""
    exclude_dirs = exclude_dirs or {
        ".venv", "node_modules", ".next", "__pycache__",
        ".git", ".pytest_cache", ".turbo", ".ruff_cache",
        ".cache", "cache", "coverage", "dist", "build",
        "test_outputs", "validation/reports", "tests",
    }
    # 큰 단일 파일 (tarball 자체 등) 도 exclude
    exclude_files = {".DS_Store", "Thumbs.db"}

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = tarinfo.name.split("/")
        for p in parts:
            if p in exclude_dirs:
                return None
        if pathlib.Path(tarinfo.name).name in exclude_files:
            return None
        return tarinfo

    # 압축 레벨 6 (default 9 보다 메모리 사용 적음). compresslevel 인자 사용.
    with tarfile.open(out_path, "w:gz", compresslevel=6) as tar:
        if include_paths:
            for rel in include_paths:
                abs_p = src / rel
                if abs_p.exists():
                    tar.add(str(abs_p), arcname=rel, filter=_filter)
        else:
            for item in src.iterdir():
                if item.name in exclude_dirs:
                    continue
                tar.add(str(item), arcname=item.name, filter=_filter)


def upload_to_s3(s3: Any, bucket: str, local: pathlib.Path, key: str) -> None:
    _log(f"  S3 업로드: s3://{bucket}/{key} ({local.stat().st_size / 1024:.1f} KB)")
    s3.upload_file(str(local), bucket, key)


def deploy_backend(cfg: dict[str, Any]) -> None:
    _log("=== 백엔드 배포 ===")
    s3 = boto3.client("s3", region_name=cfg["region"])
    ssm = boto3.client("ssm", region_name=cfg["region"])

    tag = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    key = f"deploy/backend-{tag}.tar.gz"
    with tempfile.TemporaryDirectory() as td:
        tar_path = pathlib.Path(td) / "backend.tar.gz"
        _log(f"  tarball 생성: {BACKEND_SRC}")
        _make_tar(BACKEND_SRC, tar_path)
        upload_to_s3(s3, cfg["s3_bucket"], tar_path, key)

    _log("  SSM: 다운로드 + 압축 해제 + pip install + restart")
    commands = [
        "set -e",
        "cd /opt/qa-pipeline",
        f"aws s3 cp s3://{cfg['s3_bucket']}/{key} /tmp/backend.tar.gz --region {cfg['region']}",
        "mkdir -p /opt/qa-pipeline.new",
        "tar -xzf /tmp/backend.tar.gz -C /opt/qa-pipeline.new",
        # 원자적 치환 — .venv 보존
        "if [ -d /opt/qa-pipeline/.venv ]; then mv /opt/qa-pipeline/.venv /tmp/.venv.keep; fi",
        "rm -rf /opt/qa-pipeline.old",
        "if [ -d /opt/qa-pipeline ] && [ ! -L /opt/qa-pipeline ]; then mv /opt/qa-pipeline /opt/qa-pipeline.old; fi",
        "mv /opt/qa-pipeline.new /opt/qa-pipeline",
        "if [ -d /tmp/.venv.keep ]; then mv /tmp/.venv.keep /opt/qa-pipeline/.venv; fi",
        "chown -R ubuntu:ubuntu /opt/qa-pipeline",
        # requirements 설치
        "cd /opt/qa-pipeline",
        "if [ -f requirements.txt ]; then sudo -u ubuntu /opt/qa-pipeline/.venv/bin/pip install -r requirements.txt; fi",
        # 환경변수 파일 없으면 디폴트 생성
        "if [ ! -f /opt/qa-pipeline/.env ]; then cp /opt/qa-pipeline/deploy/.env.backend /opt/qa-pipeline/.env 2>/dev/null || true; fi",
        "sudo systemctl restart qa-pipeline.service",
        "sleep 4",
        "sudo systemctl status qa-pipeline.service --no-pager | head -20",
        "curl -sf http://127.0.0.1:8081/health || (echo 'health check failed' && exit 1)",
        "echo 'backend deploy OK'",
    ]
    run_ssm(ssm, cfg["instance_id"], commands, timeout=1200, comment="qa-backend-deploy")
    _log("백엔드 배포 완료")


FRONTEND_BUILD_EXCLUDE = {
    "node_modules", ".next", ".turbo", ".cache", "cache",
    "coverage", "dist", "build", "__pycache__", ".pytest_cache",
    ".git", ".ruff_cache", "test_outputs",
}


def _make_frontend_source_tar(src: pathlib.Path, out_path: pathlib.Path) -> None:
    """프론트 *소스 코드만* tarball — node_modules / .next 등 빌드 산출물 전부 제외.
    EC2 가 받아서 pnpm install + pnpm build 를 직접 수행한다."""

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = tarinfo.name.split("/")
        for p in parts:
            if p in FRONTEND_BUILD_EXCLUDE:
                return None
        if pathlib.Path(tarinfo.name).name in {".DS_Store", "Thumbs.db"}:
            return None
        return tarinfo

    with tarfile.open(out_path, "w:gz", compresslevel=6) as tar:
        for item in src.iterdir():
            if item.name in FRONTEND_BUILD_EXCLUDE:
                continue
            tar.add(str(item), arcname=item.name, filter=_filter)


def _sha256_file(p: pathlib.Path) -> str:
    """파일 해시 — pnpm-lock.yaml 변화 감지용."""
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def deploy_frontend(cfg: dict[str, Any]) -> None:
    """프론트 배포 — 로컬 빌드 + 스마트 EC2 캐싱.

    최적화:
      (2) lockfile hash 비교 → pnpm-lock.yaml 안 바뀌었으면 EC2 의 기존
          node_modules 재사용 (pnpm install 스킵, ~60s 절감)
      (3) tarball 에서 `.next/{cache, standalone, trace, diagnostics}` 제외 →
          560MB → 수십 MB 수준. standalone 은 어차피 사용하지 않음 (pnpm
          symlink 이식 문제로 `next start` + pnpm install 방식 사용)
    """
    _log("=== 프론트 배포 (로컬 빌드 + 스마트 캐싱) ===")
    s3 = boto3.client("s3", region_name=cfg["region"])
    ssm = boto3.client("ssm", region_name=cfg["region"])

    if not FRONTEND_SRC.exists():
        raise SystemExit(f"FRONTEND_SRC 없음: {FRONTEND_SRC}")

    # 1. 로컬 빌드
    env = dict(os.environ)
    env.setdefault("NODE_OPTIONS", "--max-old-space-size=4096")
    env["NEXT_PUBLIC_API_BASE_URL"] = env.get("NEXT_PUBLIC_API_BASE_URL", "/api")
    env["NEXT_PUBLIC_QA_SERVER_URL"] = env.get("NEXT_PUBLIC_QA_SERVER_URL", "/api")

    _log(f"  pnpm install @ {FRONTEND_SRC}")
    subprocess.check_call(
        ["pnpm", "install", "--frozen-lockfile"],
        cwd=str(FRONTEND_SRC),
        env=env,
        shell=(os.name == "nt"),
    )
    _log("  pnpm build (로컬)")
    subprocess.check_call(
        ["pnpm", "build"],
        cwd=str(FRONTEND_SRC),
        env=env,
        shell=(os.name == "nt"),
    )

    next_dir = FRONTEND_SRC / ".next"
    public_dir = FRONTEND_SRC / "public"
    lockfile = FRONTEND_SRC / "pnpm-lock.yaml"
    if not next_dir.exists():
        raise SystemExit(f"{next_dir} 없음 — pnpm build 실패")
    if not lockfile.exists():
        alt = FRONTEND_SRC.parent.parent / "pnpm-lock.yaml"
        if alt.exists():
            lockfile = alt

    # lockfile 해시 — EC2 에서 이전 해시와 비교해 pnpm install 스킵 판정
    lockfile_hash = _sha256_file(lockfile)
    _log(f"  lockfile hash: {lockfile_hash[:16]}…")

    # 2. 필요한 파일만 stage → tar. .next/{cache,standalone,trace,diagnostics} 제외
    tag = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    key = f"deploy/frontend-{tag}.tar.gz"
    with tempfile.TemporaryDirectory() as td:
        stage = pathlib.Path(td) / "stage"
        stage.mkdir()

        import shutil
        for fname in ("package.json", "pnpm-lock.yaml", "next.config.ts", "tsconfig.json", ".npmrc"):
            src_p = FRONTEND_SRC / fname
            if src_p.exists():
                shutil.copy2(src_p, stage / fname)
        if lockfile.name == "pnpm-lock.yaml" and not (stage / "pnpm-lock.yaml").exists():
            shutil.copy2(lockfile, stage / "pnpm-lock.yaml")

        # lockfile hash 파일 — EC2 가 캐시 판정에 사용
        (stage / ".deploy-lockfile-hash").write_text(lockfile_hash, encoding="utf-8")

        # .next 복사 (cache + standalone + trace + diagnostics 제외)
        def _copy_excl(src: pathlib.Path, dst: pathlib.Path, exclude: set[str]) -> None:
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                if item.name in exclude:
                    continue
                tgt = dst / item.name
                if item.is_dir():
                    if os.name == "nt":
                        r = subprocess.run(
                            ["robocopy", str(item), str(tgt), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP"],
                            capture_output=True,
                        )
                        if r.returncode >= 8:
                            raise SystemExit(f"robocopy {item} failed: {r.returncode}")
                    else:
                        shutil.copytree(item, tgt)
                else:
                    shutil.copy2(item, tgt)

        _log("  .next 복사 (dev/cache/standalone/trace/diagnostics 제외)")
        _copy_excl(
            next_dir,
            stage / ".next",
            exclude={"dev", "cache", "standalone", "trace", "diagnostics"},
        )
        if public_dir.exists():
            _log("  public 복사")
            _copy_excl(public_dir, stage / "public", exclude=set())

        tar_path = pathlib.Path(td) / "frontend.tar.gz"
        _log("  tarball 생성 (python tarfile)")
        # Windows git-bash tar 가 'C:\' 를 remote host 로 오인해 실패 →
        # Python 내장 tarfile 로 일관 처리 (크로스플랫폼).
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(str(stage), arcname=".", recursive=True)
        size_mb = tar_path.stat().st_size / 1024 / 1024
        _log(f"  tarball 크기: {size_mb:.1f} MB")
        upload_to_s3(s3, cfg["s3_bucket"], tar_path, key)

    # 3. EC2 배치 — lockfile 해시 비교 후 node_modules 재사용 / 신규 설치
    _log("  SSM: 다운로드 + 해시 비교 + (조건부) pnpm install + pm2 restart")
    commands = [
        "set -e",
        f"aws s3 cp s3://{cfg['s3_bucket']}/{key} /tmp/frontend.tar.gz --region {cfg['region']}",
        "rm -rf /opt/qa-webapp.new && mkdir -p /opt/qa-webapp.new",
        "tar -xzf /tmp/frontend.tar.gz -C /opt/qa-webapp.new",
        # --- lockfile 해시 비교 ---
        'NEW_HASH=$(cat /opt/qa-webapp.new/.deploy-lockfile-hash 2>/dev/null || echo none)',
        'OLD_HASH=$(cat /opt/qa-webapp/.deploy-lockfile-hash 2>/dev/null || echo none)',
        'echo "new_hash=$NEW_HASH old_hash=$OLD_HASH"',
        # 해시 일치 + 기존 node_modules 존재 시 재사용 (pnpm install 스킵)
        'SKIP_INSTALL=0',
        'if [ "$NEW_HASH" = "$OLD_HASH" ] && [ -d /opt/qa-webapp/node_modules ]; then echo "lockfile unchanged — reusing node_modules (pnpm install skipped)"; mv /opt/qa-webapp/node_modules /opt/qa-webapp.new/node_modules; SKIP_INSTALL=1; else echo "lockfile changed or no prior install — will run pnpm install"; fi',
        # 원자적 swap
        "rm -rf /opt/qa-webapp.old",
        "if [ -d /opt/qa-webapp ] && [ ! -L /opt/qa-webapp ]; then mv /opt/qa-webapp /opt/qa-webapp.old; fi",
        "mv /opt/qa-webapp.new /opt/qa-webapp",
        "chown -R ubuntu:ubuntu /opt/qa-webapp",
        # 조건부 pnpm install
        'if [ "$SKIP_INSTALL" = "0" ]; then cd /opt/qa-webapp && echo "--- pnpm install --prod ---" && sudo -u ubuntu env HOME=/home/ubuntu pnpm install --prod --config.strict-peer-dependencies=false; else echo "--- pnpm install SKIPPED (cache hit) ---"; fi',
        # ecosystem / env.production — 이미 있으면 스킵 (변경 없을 시 pm2 reload 가 더 빠름)
        "if [ ! -f /opt/qa-webapp/ecosystem.config.cjs ]; then cat > /opt/qa-webapp/ecosystem.config.cjs <<'JS'\nmodule.exports = {\n  apps: [{\n    name: 'qa-webapp',\n    cwd: '/opt/qa-webapp',\n    script: '/usr/bin/pnpm',\n    args: 'start',\n    autorestart: true,\n    max_memory_restart: '900M',\n    error_file: '/var/log/qa-pipeline/webapp.err.log',\n    out_file: '/var/log/qa-pipeline/webapp.out.log',\n    env: { NODE_ENV: 'production', PORT: '3000', HOSTNAME: '0.0.0.0' }\n  }]\n};\nJS\nchown ubuntu:ubuntu /opt/qa-webapp/ecosystem.config.cjs; fi",
        "if [ ! -f /opt/qa-webapp/.env.production ]; then echo 'NEXT_PUBLIC_API_BASE_URL=/api' > /opt/qa-webapp/.env.production; chown ubuntu:ubuntu /opt/qa-webapp/.env.production; fi",
        "rm -f /tmp/frontend.tar.gz",
        # pm2 reload — 이미 running 이면 무중단, 없으면 start
        "sudo -u ubuntu pm2 describe qa-webapp > /dev/null 2>&1 && sudo -u ubuntu pm2 reload qa-webapp || sudo -u ubuntu pm2 start /opt/qa-webapp/ecosystem.config.cjs",
        "sudo -u ubuntu pm2 save",
        "sleep 3",
        "curl -sf http://127.0.0.1:3000/ -o /dev/null || (echo 'frontend health check failed' && sudo tail -30 /var/log/qa-pipeline/webapp.err.log && exit 1)",
        "echo 'frontend deploy OK'",
    ]
    run_ssm(ssm, cfg["instance_id"], commands, timeout=1200, comment="qa-frontend-deploy")
    _log("프론트 배포 완료")


def run_bootstrap(cfg: dict[str, Any]) -> None:
    _log("=== bootstrap.sh 실행 (EC2 초기 셋업) ===")
    ssm = boto3.client("ssm", region_name=cfg["region"])
    s3 = boto3.client("s3", region_name=cfg["region"])

    # 1. bootstrap.sh 를 S3 에 업로드 → presigned URL 로 다운로드 (aws CLI 없이 curl 사용)
    key = f"deploy/bootstrap-{int(time.time())}.sh"
    upload_to_s3(s3, cfg["s3_bucket"], HERE / "bootstrap.sh", key)
    # SigV4 presigned URL — Ubuntu 22.04 에 aws CLI 기본 미설치라 curl 로 대체
    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg["s3_bucket"], "Key": key},
        ExpiresIn=900,
    )
    commands = [
        "set -e",
        # curl 은 기본 설치돼 있음 — S3 presigned URL 로 bootstrap.sh 다운로드
        f"curl -fsSL '{presigned_url}' -o /tmp/bootstrap.sh",
        "chmod +x /tmp/bootstrap.sh",
        "/tmp/bootstrap.sh",
    ]
    run_ssm(ssm, cfg["instance_id"], commands, timeout=1800, comment="qa-bootstrap")
    _log("bootstrap 완료")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--target",
        choices=["bootstrap", "backend", "frontend", "both"],
        required=True,
    )
    args = ap.parse_args()

    cfg = load_provision()
    _log(f"대상 EC2: {cfg['instance_id']} ({cfg['public_ip']})")

    if args.target == "bootstrap":
        run_bootstrap(cfg)
    elif args.target == "backend":
        deploy_backend(cfg)
    elif args.target == "frontend":
        deploy_frontend(cfg)
    elif args.target == "both":
        deploy_backend(cfg)
        deploy_frontend(cfg)

    _log("완료.")
    _log(f"  http://{cfg['public_ip']}/         ← 프론트")
    _log(f"  http://{cfg['public_ip']}/api/health  ← 백엔드")
    return 0


if __name__ == "__main__":
    sys.exit(main())
