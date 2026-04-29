# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""DynamoDB tables for the multi-tenant QA pipeline.

ARCHITECTURE.md 3절 기준 5종 테이블을 정의한다.

- 모든 테이블의 PK 는 tenant_id (String).
- BillingMode=PAY_PER_REQUEST (예측 불가능한 테넌트 트래픽 대응).
- RemovalPolicy=RETAIN — 운영 안전성 우선.
- PointInTimeRecovery 활성화.
- ``qa_audit_log`` 는 TTL attribute="ttl" 로 30일 자동 삭제.

테이블 이름은 스택 내부에서 attribute 로 노출된다 (다른 스택이 grant 호출용으로 참조).
GSI 는 Phase 5 요구 발생 시 추가 — 현재 정의 없음.
"""

from __future__ import annotations

from aws_cdk import (
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


TENANT_PK = "tenant_id"


class QaTenantTableStack(Stack):
    """Create 5 DynamoDB tables for the multi-tenant QA pipeline."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        removal_policy: RemovalPolicy = RemovalPolicy.RETAIN,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        common = {
            "billing_mode": dynamodb.BillingMode.PAY_PER_REQUEST,
            "removal_policy": removal_policy,
            "point_in_time_recovery_specification": dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            "encryption": dynamodb.TableEncryption.AWS_MANAGED,
        }

        # 1) qa_tenants — 테넌트 메타/Config (PK only)
        self.tenants_table = dynamodb.Table(
            self,
            "QaTenantsTable",
            table_name="qa_tenants",
            partition_key=dynamodb.Attribute(name=TENANT_PK, type=dynamodb.AttributeType.STRING),
            **common,
        )

        # 2) qa_evaluations_v2 — 평가 결과
        self.evaluations_table = dynamodb.Table(
            self,
            "QaEvaluationsV2Table",
            table_name="qa_evaluations_v2",
            partition_key=dynamodb.Attribute(name=TENANT_PK, type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="evaluation_id", type=dynamodb.AttributeType.STRING),
            **common,
        )

        # 3) qa_sessions — 세션 상태
        self.sessions_table = dynamodb.Table(
            self,
            "QaSessionsTable",
            table_name="qa_sessions",
            partition_key=dynamodb.Attribute(name=TENANT_PK, type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            **common,
        )

        # 4) qa_audit_log — 감사 로그 (TTL 30일)
        self.audit_log_table = dynamodb.Table(
            self,
            "QaAuditLogTable",
            table_name="qa_audit_log",
            partition_key=dynamodb.Attribute(name=TENANT_PK, type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            time_to_live_attribute="ttl",
            **common,
        )

        # 5) qa_quota_usage — 월별 사용량 (Rate Limit 카운터)
        self.quota_usage_table = dynamodb.Table(
            self,
            "QaQuotaUsageTable",
            table_name="qa_quota_usage",
            partition_key=dynamodb.Attribute(name=TENANT_PK, type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="yyyy-mm", type=dynamodb.AttributeType.STRING),
            **common,
        )

    @property
    def all_tables(self) -> list[dynamodb.Table]:
        return [
            self.tenants_table,
            self.evaluations_table,
            self.sessions_table,
            self.audit_log_table,
            self.quota_usage_table,
        ]
