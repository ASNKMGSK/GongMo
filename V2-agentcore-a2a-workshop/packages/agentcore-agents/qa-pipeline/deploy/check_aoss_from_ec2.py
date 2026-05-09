"""SSM 으로 EC2 에서 직접 _cat/indices 호출 — backend role 권한 검증."""

from __future__ import annotations
import sys
import time
import boto3

REGION = "us-east-1"
INSTANCE_ID = "i-01e0ee91f3c5f8bb4"

PY_SCRIPT = '''#!/opt/qa-pipeline/.venv/bin/python
import os, json
from urllib.parse import urlparse
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

ssm = boto3.client("ssm", region_name="us-east-1")
ep = ssm.get_parameter(Name="/a2a_rag/opensearch_endpoint")["Parameter"]["Value"].rstrip("/")
host = urlparse(ep if "://" in ep else "https://" + ep).hostname
creds = boto3.Session().get_credentials()
auth = AWSV4SignerAuth(creds, "us-east-1", "aoss")
c = OpenSearch(
    hosts=[{"host": host, "port": 443}],
    http_auth=auth,
    use_ssl=True, verify_certs=True,
    connection_class=RequestsHttpConnection,
    timeout=30,
)

print("endpoint:", ep)
print("--- _cat/indices ---")
try:
    cat = c.cat.indices(format="json")
    if not cat:
        print("  (empty — collection 에 인덱스 0개)")
    for idx in cat:
        print("  %-40s docs=%s size=%s" % (idx.get("index"), idx.get("docs.count"), idx.get("store.size")))
except Exception as e:
    print("  err:", e)

print("--- 4 indices count ---")
for idx_name in ("qa-golden-set","qa-reasoning-index","qa-business-knowledge","qa-hitl-cases"):
    try:
        if not c.indices.exists(index=idx_name):
            print("  %-30s NOT EXISTS" % idx_name)
            continue
        r = c.count(index=idx_name)
        print("  %-30s docs=%d" % (idx_name, r.get("count", 0)))
    except Exception as e:
        print("  %-30s err: %s" % (idx_name, e))
'''


def main() -> int:
    ssm = boto3.client("ssm", region_name=REGION)
    cmds = [
        f"cat > /tmp/_check_aoss.py <<'PYEOF'\n{PY_SCRIPT}\nPYEOF",
        "/opt/qa-pipeline/.venv/bin/python /tmp/_check_aoss.py",
        "rm -f /tmp/_check_aoss.py",
    ]
    res = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": cmds, "executionTimeout": ["120"]},
    )
    cmd_id = res["Command"]["CommandId"]
    print(f"CommandId: {cmd_id}")
    for _ in range(40):
        time.sleep(3)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=INSTANCE_ID)
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            print(f"status={inv['Status']}\n--- stdout ---")
            print(inv.get("StandardOutputContent", ""))
            err = inv.get("StandardErrorContent", "")
            if err.strip():
                print("--- stderr ---")
                print(err[:2000])
            return 0 if inv["Status"] == "Success" else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
