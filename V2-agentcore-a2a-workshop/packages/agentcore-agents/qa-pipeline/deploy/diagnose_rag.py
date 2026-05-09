"""EC2 backend 의 AOSS 연동 상태 진단.

확인:
1. systemd qa-pipeline.service env (OPENSEARCH_HOST / AOSS_ENDPOINT / AWS_REGION 등)
2. journalctl 최근 50줄
3. /api/v2/hitl-rag/status 응답
4. boto3 로 AOSS collection 목록 + 인덱스 doc count 확인
"""

from __future__ import annotations

import json
import sys
import time

import boto3

INSTANCE_ID = "i-01e0ee91f3c5f8bb4"
REGION = "us-east-1"


def main() -> int:
    ssm = boto3.client("ssm", region_name=REGION)

    cmds = [
        "echo '=== systemd qa-pipeline 환경변수 ==='",
        "sudo systemctl show qa-pipeline | grep -i 'Environment=' | head -10",
        "echo",
        "echo '=== /etc/systemd/system/qa-pipeline.service drop-in env ==='",
        "ls -la /etc/systemd/system/qa-pipeline.service.d/ 2>/dev/null || echo 'no drop-in'",
        "cat /etc/systemd/system/qa-pipeline.service.d/*.conf 2>/dev/null || echo 'no conf files'",
        "echo",
        "echo '=== /opt/qa-pipeline/.env 확인 ==='",
        "ls -la /opt/qa-pipeline/.env* 2>/dev/null || echo 'no .env'",
        "cat /opt/qa-pipeline/.env 2>/dev/null | grep -iE 'opensearch|aoss|region' | head -10 || true",
        "echo",
        "echo '=== qa-pipeline.service 최근 50줄 ==='",
        "sudo journalctl -u qa-pipeline --no-pager -n 50 | tail -50",
        "echo",
        "echo '=== /api/v2/hitl-rag/status 응답 ==='",
        "curl -s http://localhost:8081/v2/hitl-rag/status 2>&1 | head -50",
        "echo",
        "echo '=== EC2 IAM identity ==='",
        "aws sts get-caller-identity --output json 2>&1 | head -10",
        "echo",
        "echo '=== AOSS collection 목록 ==='",
        "aws opensearchserverless list-collections --region us-east-1 --output json 2>&1 | head -40 || true",
    ]

    print(f"[*] SSM SendCommand → {INSTANCE_ID}")
    res = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": cmds, "executionTimeout": ["120"]},
    )
    cmd_id = res["Command"]["CommandId"]
    print(f"  CommandId: {cmd_id}")

    for _ in range(40):
        time.sleep(3)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=INSTANCE_ID)
        status = inv["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            print(f"\n[*] SSM 종료 status={status}\n")
            print("=" * 70)
            print(inv.get("StandardOutputContent", ""))
            err = inv.get("StandardErrorContent", "")
            if err.strip():
                print("\n--- stderr ---")
                print(err[:3000])
            return 0 if status == "Success" else 1
        print(f"  …status={status}")
    print("[ERR] SSM 타임아웃")
    return 1


if __name__ == "__main__":
    sys.exit(main())
