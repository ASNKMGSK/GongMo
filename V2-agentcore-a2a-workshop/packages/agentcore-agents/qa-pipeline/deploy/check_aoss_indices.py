"""AOSS 의 실제 인덱스 + doc count 확인.

EC2 의 backend 가 보는 같은 endpoint 에서 직접 _cat/indices 호출.
RAG Admin 의 "0 / N · 미빌드" 가 실제로 인덱스가 비어 있는지 검증.
"""

from __future__ import annotations

import sys

import boto3
from botocore.config import Config

REGION = "us-east-1"


def main() -> int:
    ssm = boto3.client("ssm", region_name=REGION)
    ep = ssm.get_parameter(Name="/a2a_rag/opensearch_endpoint")["Parameter"]["Value"].rstrip("/")
    print(f"endpoint: {ep}")

    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
    except ImportError:
        print("opensearch-py 미설치 — pip install opensearch-py")
        return 1

    from urllib.parse import urlparse

    parsed = urlparse(ep if "://" in ep else f"https://{ep}")
    host = parsed.hostname

    creds = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(creds, REGION, "aoss")
    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )

    print("\n=== _cat/indices ===")
    try:
        cat = client.cat.indices(format="json")
        if not cat:
            print("(인덱스 0개 — AOSS collection 에 인덱스가 단 하나도 없음)")
        else:
            for idx in cat:
                print(f"  {idx.get('index'):40s}  docs={idx.get('docs.count'):>8s}  size={idx.get('store.size'):>10s}")
    except Exception as e:
        print(f"_cat/indices 실패: {e}")

    print("\n=== 4 종 인덱스 doc count (코드에서 사용하는 이름) ===")
    for idx_name in ("qa-golden-set", "qa-reasoning-index", "qa-business-knowledge", "qa-hitl-cases"):
        try:
            exists = client.indices.exists(index=idx_name)
            if not exists:
                print(f"  {idx_name:30s}  ✗ 인덱스 없음 (한 번도 빌드 안 됨)")
                continue
            count_res = client.count(index=idx_name)
            print(f"  {idx_name:30s}  docs={count_res.get('count'):>8d}")
        except Exception as e:
            print(f"  {idx_name:30s}  err: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
