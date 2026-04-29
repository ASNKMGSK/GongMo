# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""IAM Role + tenant-isolation policies.

ARCHITECTURE.md 4절: 테넌트 격리는 IAM Condition
``${aws:PrincipalTag/tenant_id}`` 로 강제한다 (Phase 5 강화용).

- 앱(EC2) 역할은 모든 qa_* 테이블과 테넌트 버킷 prefix 에 접근 가능
- 각 요청은 호출 시 ``sts:AssumeRole`` 로 ``aws:PrincipalTag/tenant_id`` 를 부여해
  DynamoDB / S3 접근을 해당 테넌트 prefix 로 좁힐 수 있다.
- 본 스택은 Phase 5 에서 실 앱 역할과 교체 가능한 기반 정책을 생성한다.
  기존 운영 EC2 역할은 유지하고, 신규 Role 은 관찰용으로 생성 (공존).
"""

from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class QaTenantIamStack(Stack):
    """IAM Role + 테넌트 격리 정책.

    Args:
        tables: QaTenantTableStack 이 생성한 DynamoDB 테이블 리스트.
        bucket_name: 멀티테넌트 S3 버킷 이름 (``tenants/{tid}/*`` prefix 사용).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tables: list[dynamodb.ITable],
        bucket_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # EC2 앱이 Assume 할 역할. sts:TagSession 으로 tenant_id 태그 부여 허용.
        self.app_role = iam.Role(
            self,
            "QaMultiTenantAppRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="QA pipeline multi-tenant app role — tenant-tagged sessions",
            role_name="qa-multitenant-app-role",
        )

        # CloudWatch Logs + PutMetricData (관찰성)
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=["arn:aws:logs:*:*:log-group:/qa-multitenant/*:*"],
            )
        )
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={
                    "StringEquals": {"cloudwatch:namespace": "QaMultiTenant"},
                },
            )
        )

        # DynamoDB — 모든 qa_* 테이블에 접근 가능. 테넌트 격리는 LeadingKeys + PrincipalTag.
        table_arns: list[str] = []
        for t in tables:
            table_arns.append(t.table_arn)
            table_arns.append(f"{t.table_arn}/index/*")

        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="TenantScopedDynamoDb",
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:BatchGetItem",
                    "dynamodb:Query",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:BatchWriteItem",
                ],
                resources=table_arns,
                conditions={
                    # LeadingKeys 로 PK=tenant_id 를 Principal Tag 와 강제 일치.
                    "ForAllValues:StringEquals": {
                        "dynamodb:LeadingKeys": ["${aws:PrincipalTag/tenant_id}"],
                    },
                },
            )
        )

        # S3 — 테넌트 prefix 격리 (tenants/{tid}/*)
        bucket_arn = f"arn:aws:s3:::{bucket_name}"
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="TenantScopedS3Object",
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                resources=[f"{bucket_arn}/tenants/${{aws:PrincipalTag/tenant_id}}/*"],
            )
        )
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="TenantScopedS3List",
                actions=["s3:ListBucket"],
                resources=[bucket_arn],
                conditions={
                    "StringLike": {
                        "s3:prefix": ["tenants/${aws:PrincipalTag/tenant_id}/*"],
                    },
                },
            )
        )

        # Secrets Manager — 테넌트별 시크릿 조회 (/qa/{tid}/* — Dev2 secrets.py:SECRET_PREFIX 와 일치)
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="TenantScopedSecretsRead",
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret",
                ],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:"
                    "secret:/qa/${aws:PrincipalTag/tenant_id}/*"
                ],
            )
        )

        # OpenSearch — Dev2 opensearch.py 지원 (aoss 또는 es).
        # Phase 5: collection/index 태그 + PrincipalTag 로 인덱스 격리 강화.
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="OpenSearchServerlessAccess",
                actions=["aoss:APIAccessAll"],
                resources=["*"],
            )
        )
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="OpenSearchDomainHttp",
                actions=[
                    "es:ESHttpGet",
                    "es:ESHttpPost",
                    "es:ESHttpPut",
                    "es:ESHttpDelete",
                    "es:ESHttpHead",
                ],
                resources=[f"arn:aws:es:{self.region}:{self.account}:domain/*"],
            )
        )

        # Secrets Manager write (Phase 1 setup — Dev2 put_tenant_secret / delete_tenant_secret 허용)
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="TenantScopedSecretsWrite",
                actions=[
                    "secretsmanager:CreateSecret",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:DeleteSecret",
                    "secretsmanager:TagResource",
                ],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:"
                    "secret:/qa/${aws:PrincipalTag/tenant_id}/*"
                ],
            )
        )

        # TagSession 허용 — 세션마다 tenant_id 태그 부여
        self.app_role.add_to_policy(
            iam.PolicyStatement(
                sid="AllowTagSession",
                actions=["sts:TagSession"],
                resources=["*"],
                conditions={
                    "StringLike": {"aws:RequestTag/tenant_id": "*"},
                    "ForAllValues:StringEquals": {
                        "sts:TransitiveTagKeys": ["tenant_id"],
                    },
                },
            )
        )
