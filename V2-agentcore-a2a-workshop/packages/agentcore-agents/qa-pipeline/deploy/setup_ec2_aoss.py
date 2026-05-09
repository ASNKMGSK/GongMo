"""EC2 backend 의 AOSS 연동 setup — 1회성.

자동 처리 4 단계:
1. SSM `/a2a_rag/opensearch_endpoint` 에서 AOSS collection ID 추출
2. IAM `qa-pipeline-v3-role` 에 inline policy 추가 (aoss:* + ssm:GetParameter)
3. AOSS Data Access Policy 에 role principal 등록 (collection / index 권한)
4. systemd drop-in 으로 환경변수 주입 (QA_HITL_RAG_ROOT, AWS_REGION) + daemon-reload + restart
5. /v2/hitl-rag/status 검증
"""

from __future__ import annotations

import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
ACCOUNT_ID = "919359878144"
INSTANCE_ID = "i-01e0ee91f3c5f8bb4"
ROLE_NAME = "qa-pipeline-v3-role"
INLINE_POLICY_NAME = "qa-aoss-ssm-access"
DATA_POLICY_NAME = "qa-pipeline-ec2-data"
EC2_RAG_ROOT = "/home/ubuntu/qa-data/HITL_RAG"


def step1_get_collection_id() -> tuple[str, str]:
    """SSM endpoint → collection ID + name."""
    print("[1/5] SSM 에서 AOSS endpoint 가져오기...")
    ssm = boto3.client("ssm", region_name=REGION)
    endpoint = ssm.get_parameter(Name="/a2a_rag/opensearch_endpoint")["Parameter"][
        "Value"
    ].rstrip("/")
    print(f"  endpoint: {endpoint}")

    host = endpoint.replace("https://", "").replace("http://", "")
    coll_id = host.split(".")[0]
    print(f"  collection ID: {coll_id}")

    aoss = boto3.client("opensearchserverless", region_name=REGION)
    cols = aoss.list_collections()["collectionSummaries"]
    target = next((c for c in cols if c["id"] == coll_id), None)
    if not target:
        raise SystemExit(f"AOSS collection ID {coll_id} not found in account")
    coll_name = target["name"]
    print(f"  collection name: {coll_name}")
    return coll_id, coll_name


def step2_iam_policy() -> None:
    print(f"\n[2/5] IAM role {ROLE_NAME} 에 inline policy 추가...")
    iam = boto3.client("iam")
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "aoss:APIAccessAll",
                    "aoss:DashboardsAccessAll",
                    "aoss:ListCollections",
                    "aoss:BatchGetCollection",
                    "aoss:ListAccessPolicies",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["ssm:GetParameter", "ssm:GetParameters"],
                "Resource": [
                    f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/a2a_rag/*",
                    f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/qa_pipeline/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": "*",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(policy_doc),
    )
    print(f"  ✓ inline policy {INLINE_POLICY_NAME} 적용")


def step3_aoss_data_access(coll_name: str) -> None:
    print(f"\n[3/5] AOSS Data Access Policy 에 role principal 등록...")
    aoss = boto3.client("opensearchserverless", region_name=REGION)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{ROLE_NAME}"

    new_policy = [
        {
            "Description": "qa-pipeline EC2 backend access",
            "Rules": [
                {
                    "Resource": [f"collection/{coll_name}"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:DeleteCollectionItems",
                        "aoss:UpdateCollectionItems",
                        "aoss:DescribeCollectionItems",
                    ],
                    "ResourceType": "collection",
                },
                {
                    "Resource": [f"index/{coll_name}/*"],
                    "Permission": [
                        "aoss:CreateIndex",
                        "aoss:DeleteIndex",
                        "aoss:UpdateIndex",
                        "aoss:DescribeIndex",
                        "aoss:ReadDocument",
                        "aoss:WriteDocument",
                    ],
                    "ResourceType": "index",
                },
            ],
            "Principal": [role_arn],
        }
    ]

    try:
        aoss.create_access_policy(
            name=DATA_POLICY_NAME,
            type="data",
            description="qa-pipeline EC2 access",
            policy=json.dumps(new_policy),
        )
        print(f"  ✓ Data Access Policy {DATA_POLICY_NAME} 생성")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConflictException":
            existing = aoss.get_access_policy(name=DATA_POLICY_NAME, type="data")
            detail = existing["accessPolicyDetail"]
            doc = json.loads(detail["policy"]) if isinstance(detail["policy"], str) else detail["policy"]
            modified = False
            for rule in doc:
                if role_arn not in rule.get("Principal", []):
                    rule.setdefault("Principal", []).append(role_arn)
                    modified = True
            if modified:
                aoss.update_access_policy(
                    name=DATA_POLICY_NAME,
                    type="data",
                    policyVersion=detail["policyVersion"],
                    policy=json.dumps(doc),
                )
                print(f"  ✓ Data Access Policy {DATA_POLICY_NAME} 에 role 추가")
            else:
                print(f"  · Data Access Policy 이미 role 포함")
        else:
            raise


def step4_systemd_dropin() -> None:
    print(f"\n[4/5] systemd drop-in 주입 (env: QA_HITL_RAG_ROOT, AWS_REGION) + restart...")
    ssm = boto3.client("ssm", region_name=REGION)
    drop_in_content = "\n".join([
        "[Service]",
        f'Environment="QA_HITL_RAG_ROOT={EC2_RAG_ROOT}"',
        f'Environment="AWS_REGION={REGION}"',
        f'Environment="AWS_DEFAULT_REGION={REGION}"',
    ])
    cmds = [
        "sudo mkdir -p /etc/systemd/system/qa-pipeline.service.d/",
        f"sudo tee /etc/systemd/system/qa-pipeline.service.d/env.conf <<'EOF'\n{drop_in_content}\nEOF",
        "sudo mkdir -p /home/ubuntu/qa-data/HITL_RAG",
        "sudo chown -R ubuntu:ubuntu /home/ubuntu/qa-data",
        "sudo systemctl daemon-reload",
        "sudo systemctl restart qa-pipeline",
        "sleep 4",
        "echo '=== systemctl env after drop-in ==='",
        "sudo systemctl show qa-pipeline | grep -E 'Environment=' | head -5",
        "echo",
        "echo '=== /v2/hitl-rag/status ==='",
        "curl -s http://localhost:8081/v2/hitl-rag/status 2>&1 | head -20",
    ]
    res = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": cmds, "executionTimeout": ["120"]},
    )
    cmd_id = res["Command"]["CommandId"]
    print(f"  CommandId: {cmd_id}")
    for _ in range(30):
        time.sleep(3)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=INSTANCE_ID)
        status = inv["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            print(f"  status={status}")
            print("--- stdout ---")
            print(inv.get("StandardOutputContent", ""))
            err = inv.get("StandardErrorContent", "")
            if err.strip():
                print("--- stderr ---")
                print(err[:1000])
            return
    print("[ERR] SSM 타임아웃")


def main() -> int:
    coll_id, coll_name = step1_get_collection_id()
    step2_iam_policy()
    step3_aoss_data_access(coll_name)
    step4_systemd_dropin()
    print("\n[5/5] 완료. RAG Admin 화면에서 'tenant 빌드' 클릭 시 AOSS 인덱싱 시작 가능.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
