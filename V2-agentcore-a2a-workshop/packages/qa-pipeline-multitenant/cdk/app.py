# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CDK app entrypoint for the multi-tenant QA pipeline.

⚠️ EC2 인플레이스 배포 정책 — 본 파일은 ``cdk synth`` 검증 전용이다.
``cdk deploy`` 는 PL 승인 후에만 수동 실행 (``docs/DEPLOY.md`` 참조).

환경 변수:
  CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION — 표준 CDK 환경
  QA_BUCKET_NAME — 테넌트 격리 S3 버킷 이름 (Dev2 s3.py 와 동일).
  QA_MT_BUCKET (fallback) — QA_BUCKET_NAME 미설정 시 (기본: ``qa-multitenant-artifacts``)
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.qa_observability_stack import QaObservabilityStack
from stacks.qa_tenant_iam_stack import QaTenantIamStack
from stacks.qa_tenant_table_stack import QaTenantTableStack


def main() -> None:
    app = cdk.App()

    env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1",
    )

    tables_stack = QaTenantTableStack(app, "QaMultiTenantTables", env=env)

    QaObservabilityStack(app, "QaMultiTenantObservability", env=env)

    # 버킷 이름은 Dev2 의 s3.py 와 동일한 env 이름(QA_BUCKET_NAME) 사용.
    bucket_name = os.environ.get("QA_BUCKET_NAME") or os.environ.get(
        "QA_MT_BUCKET", "qa-multitenant-artifacts"
    )
    iam_stack = QaTenantIamStack(
        app,
        "QaMultiTenantIam",
        tables=tables_stack.all_tables,
        bucket_name=bucket_name,
        env=env,
    )
    iam_stack.add_dependency(tables_stack)

    app.synth()


if __name__ == "__main__":
    main()
