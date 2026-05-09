"""GT xlsx 파일을 로컬 → S3 → EC2 /opt/qa-pipeline/data/gt/ 동기화.

사용:
    python sync_gt.py

로직:
1. 로컬에서 GT 후보 파일 탐색 (Desktop / 참고자료 등)
2. boto3 S3 (qa-deploy-919359878144-us-east-1) 에 업로드
3. SSM SendCommand 로 EC2 에서 aws s3 cp → /opt/qa-pipeline/data/gt/ 배치
4. ls 검증

EC2 코드의 _gt_loader.py 가 자동으로 /opt/qa-pipeline/data/gt/ 를 탐색.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config

# v3재평가_fixed 우선 + 신규 STT 원문 추가본 + auto criteria
GT_FILES_LOCAL = [
    Path(
        r"C:\Users\META M\Desktop\업무\qa\참고자료\QA 정답\QA정답-STT_기반_통합_상담평가표_v3재평가_fixed.xlsx"
    ),
    Path(
        r"C:\Users\META M\Desktop\STT QA 정답표_재채점 및 근거 작성(STT 원문 및 상담유형 추가).xlsx"
    ),
    Path(r"C:\Users\META M\Desktop\코오롱 업무 정확도 auto_qa_criteria.xlsx"),
]

INSTANCE_ID = "i-01e0ee91f3c5f8bb4"
S3_BUCKET = "qa-deploy-919359878144-us-east-1"
S3_PREFIX = "gt-sync/"
EC2_DIR = "/opt/qa-pipeline/data/gt"
REGION = "us-east-1"


def main() -> int:
    s3 = boto3.client("s3", region_name=REGION, config=Config(signature_version="s3v4"))
    ssm = boto3.client("ssm", region_name=REGION)

    available = [f for f in GT_FILES_LOCAL if f.exists()]
    if not available:
        print("[ERR] 로컬에 GT 파일 없음 — 후보:")
        for f in GT_FILES_LOCAL:
            print(f"  - {f}")
        return 1

    print(f"[*] 로컬 발견 {len(available)} / {len(GT_FILES_LOCAL)} 개:")
    for f in available:
        size_kb = f.stat().st_size / 1024
        print(f"  ✓ {f.name} ({size_kb:,.1f} KB)")

    s3_objects: list[tuple[str, str]] = []
    for src in available:
        key = f"{S3_PREFIX}{src.name}"
        print(f"[*] S3 업로드 → s3://{S3_BUCKET}/{key}")
        s3.upload_file(str(src), S3_BUCKET, key)
        s3_objects.append((src.name, key))

    cmds = [
        f"sudo mkdir -p {EC2_DIR}",
        f"sudo chown -R ubuntu:ubuntu {EC2_DIR}",
    ]
    for fname, key in s3_objects:
        local_dst = f"{EC2_DIR}/{fname}"
        cmds.append(
            f'aws s3 cp "s3://{S3_BUCKET}/{key}" "{local_dst}" --region {REGION}'
        )
    cmds.append(f"ls -la {EC2_DIR}")
    cmds.append(f"echo GT_SYNC_OK")

    print(f"[*] SSM SendCommand → {INSTANCE_ID}")
    res = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": cmds, "executionTimeout": ["300"]},
    )
    cmd_id = res["Command"]["CommandId"]
    print(f"  CommandId: {cmd_id}")

    for _ in range(40):
        time.sleep(3)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=INSTANCE_ID)
        status = inv["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            print(f"[*] SSM 종료 status={status}")
            print("--- stdout ---")
            print(inv.get("StandardOutputContent", "")[:4000])
            err = inv.get("StandardErrorContent", "")
            if err.strip():
                print("--- stderr ---")
                print(err[:2000])
            for fname, key in s3_objects:
                try:
                    s3.delete_object(Bucket=S3_BUCKET, Key=key)
                except Exception:
                    pass
            return 0 if status == "Success" else 1
        print(f"  …status={status}")
    print("[ERR] SSM 타임아웃")
    return 1


if __name__ == "__main__":
    sys.exit(main())
