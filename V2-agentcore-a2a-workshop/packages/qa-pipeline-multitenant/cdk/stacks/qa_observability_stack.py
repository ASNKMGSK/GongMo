# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CloudWatch Logs + 테넌트 단위 메트릭 필터.

ARCHITECTURE.md 9절 — CloudWatch 메트릭 ``Dimension=TenantId``:
- EvaluationCount
- TokenUsage
- LatencyP95
- FailureRate

본 스택은 두 가지를 제공한다.
1. CloudWatch Log Group ``/qa-multitenant/app`` (RETAIN_FOREVER).
2. 해당 Log Group 을 대상으로 하는 MetricFilter 4종 — Dimension 은 TenantId 만.

앱에서 ``observability/metrics.py:put_metric`` 로 직접 PutMetricData 하는 방식도 병행
지원 (실시간 Embedded Metric 형식과 혼용 가능).
"""

from __future__ import annotations

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_logs as logs
from constructs import Construct


LOG_GROUP_NAME = "/qa-multitenant/app"
METRIC_NAMESPACE = "QaMultiTenant"


class QaObservabilityStack(Stack):
    """CloudWatch Log Group + MetricFilters (Dimension=TenantId)."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.log_group = logs.LogGroup(
            self,
            "QaMultiTenantAppLogGroup",
            log_group_name=LOG_GROUP_NAME,
            retention=logs.RetentionDays.INFINITE,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # 공통 JSON 필터 전제: 앱은 Embedded Metric Format 또는 일반 JSON 을 로그함.
        # 메트릭 필터 pattern 은 JSON 로그에서 metric_name / tenant_id / value 를 추출한다.

        self._metric_filter(
            "EvaluationCount",
            pattern=logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.metric", "=", "EvaluationCount"),
                logs.FilterPattern.exists("$.tenant_id"),
            ),
            metric_name="EvaluationCount",
            metric_value="1",
            unit=cloudwatch.Unit.COUNT,
        )

        self._metric_filter(
            "TokenUsage",
            pattern=logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.metric", "=", "TokenUsage"),
                logs.FilterPattern.exists("$.tenant_id"),
                logs.FilterPattern.exists("$.value"),
            ),
            metric_name="TokenUsage",
            metric_value="$.value",
            unit=cloudwatch.Unit.COUNT,
        )

        self._metric_filter(
            "LatencyP95",
            pattern=logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.metric", "=", "LatencyMs"),
                logs.FilterPattern.exists("$.tenant_id"),
                logs.FilterPattern.exists("$.value"),
            ),
            metric_name="LatencyP95",
            metric_value="$.value",
            unit=cloudwatch.Unit.MILLISECONDS,
        )

        self._metric_filter(
            "FailureRate",
            pattern=logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.metric", "=", "Failure"),
                logs.FilterPattern.exists("$.tenant_id"),
            ),
            metric_name="FailureRate",
            metric_value="1",
            unit=cloudwatch.Unit.COUNT,
        )

    def _metric_filter(
        self,
        construct_id: str,
        *,
        pattern: logs.IFilterPattern,
        metric_name: str,
        metric_value: str,
        unit: cloudwatch.Unit,
    ) -> logs.MetricFilter:
        return logs.MetricFilter(
            self,
            f"Mf{construct_id}",
            log_group=self.log_group,
            filter_pattern=pattern,
            metric_namespace=METRIC_NAMESPACE,
            metric_name=metric_name,
            metric_value=metric_value,
            unit=unit,
            dimensions={"TenantId": "$.tenant_id"},
        )
