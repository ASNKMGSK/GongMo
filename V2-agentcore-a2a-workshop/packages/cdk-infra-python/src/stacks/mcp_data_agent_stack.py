"""
CDK Stack for the MCP Data Agent — AgentCore Runtime.

The MCP Data Agent connects to external MCP servers (e.g. weather) via
the AgentCore Gateway and exposes its capabilities over the A2A protocol.
"""

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    aws_bedrock_agentcore_alpha as agentcore,
    aws_cognito as cognito,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_ssm as ssm,
    custom_resources as cr,
)
from cdk_nag import NagSuppressions
from constructs import Construct


class McpDataAgentStack(Stack):
    """CDK Stack for MCP Data Agent using AgentCore Runtime."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        agentcore_context = self.node.try_get_context("mcp-data-agent-agentcore") or {}
        self.tool_name = agentcore_context.get("tool-name", "mcp_data_agent")
        test_username = os.getenv("COGNITO_TEST_USERNAME", "testuser")
        test_password = os.getenv("COGNITO_TEST_PASSWORD", "MyPassword123!")

        self.agentcore_role = self._create_agentcore_role()
        self.log_group = self._create_log_group()
        self.user_pool, self.user_pool_client, self.test_user = self._create_cognito_user_pool(
            test_username, test_password
        )
        self._create_secret_update_resource(test_username, test_password)
        self.runtime = self._create_agentcore_runtime()
        self._create_ssm_parameters()
        self._apply_cdk_nag_suppressions()
        self._create_outputs()

    def _create_agentcore_role(self) -> iam.Role:
        role = iam.Role(
            self, "AgentCoreRole",
            role_name=f"{self.region}-agentcore-{self.tool_name}-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        self.agentcore_policy = iam.Policy(
            self, "AgentCorePolicy", policy_name="AgentCorePolicy",
            statements=[
                iam.PolicyStatement(sid="BedrockPermissions", actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], resources=["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:*:*:inference-profile/*"]),
                iam.PolicyStatement(sid="ECRImageAccess", actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:GetAuthorizationToken"], resources=["*"]),
                iam.PolicyStatement(actions=["logs:DescribeLogStreams", "logs:CreateLogGroup"], resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*"]),
                iam.PolicyStatement(actions=["logs:CreateLogStream", "logs:PutLogEvents"], resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]),
                iam.PolicyStatement(sid="XRayTracingPermissions", actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], resources=["*"]),
                iam.PolicyStatement(actions=["cloudwatch:PutMetricData"], resources=["*"], conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}),
                iam.PolicyStatement(sid="GetAgentAccessToken", actions=["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"], resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default*"]),
                iam.PolicyStatement(sid="ParameterStoreReadOnly", actions=["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:DescribeParameters"], resources=["*"]),
                iam.PolicyStatement(sid="SecretsManagerReadOnly", actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"], resources=["*"]),
                iam.PolicyStatement(sid="GatewayInvokeAccess", actions=["bedrock-agentcore:InvokeGateway"], resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/*"]),
            ],
        )
        role.attach_inline_policy(self.agentcore_policy)
        return role

    def _create_log_group(self) -> logs.LogGroup:
        return logs.LogGroup(self, "AgentCoreLogGroup", log_group_name=f"/aws/bedrock-agentcore/runtimes/{self.tool_name}", retention=logs.RetentionDays.ONE_WEEK, removal_policy=RemovalPolicy.DESTROY)

    def _create_cognito_user_pool(self, username, password):
        user_pool = cognito.UserPool(self, "UserPool", user_pool_name=f"{self.tool_name}.Pool", self_sign_up_enabled=False, password_policy=cognito.PasswordPolicy(min_length=8), removal_policy=RemovalPolicy.DESTROY)
        user_pool_client = user_pool.add_client("Client", auth_flows=cognito.AuthFlow(user_password=True, user_srp=True), generate_secret=False)
        test_user = cognito.CfnUserPoolUser(self, "TestUser", user_pool_id=user_pool.user_pool_id, username=username, user_attributes=[{"name": "email", "value": "test@example.com"}])
        return user_pool, user_pool_client, test_user

    def _create_secret_update_resource(self, username, password):
        update_role = iam.Role(
            self, "UpdateSecretRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
            inline_policies={"SecretsAccess": iam.PolicyDocument(statements=[
                iam.PolicyStatement(actions=["secretsmanager:UpdateSecret", "secretsmanager:CreateSecret", "secretsmanager:DescribeSecret"], resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{self.tool_name}/cognito/credentials-*"]),
                iam.PolicyStatement(actions=["cognito-idp:AdminSetUserPassword"], resources=[self.user_pool.user_pool_arn]),
            ])},
        )
        fn = lambda_.Function(self, "UpdateSecretFn", runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler", role=update_role, timeout=Duration.minutes(2), code=lambda_.Code.from_inline("""
import boto3, json
cognito = boto3.client('cognito-idp')
secrets = boto3.client('secretsmanager')
def handler(event, context):
    if event['RequestType'] in ['Create', 'Update']:
        p = event['ResourceProperties']
        cognito.admin_set_user_password(UserPoolId=p['UserPoolId'], Username=p['Username'], Password=p['Password'], Permanent=True)
        sv = json.dumps({'user_pool_id': p['UserPoolId'], 'client_id': p['ClientId'], 'username': p['Username'], 'password': p['Password'], 'discovery_url': p['DiscoveryUrl']})
        try: secrets.update_secret(SecretId=p['SecretName'], SecretString=sv)
        except secrets.exceptions.ResourceNotFoundException: secrets.create_secret(Name=p['SecretName'], SecretString=sv)
        return {'PhysicalResourceId': f"{p['UserPoolId']}-secret"}
    return {'PhysicalResourceId': event.get('PhysicalResourceId', 'default')}
"""))
        provider = cr.Provider(self, "UpdateSecretProvider", on_event_handler=fn)
        resource = CustomResource(self, "UpdateSecretResource", service_token=provider.service_token, properties={
            "UserPoolId": self.user_pool.user_pool_id, "Username": username, "Password": password,
            "ClientId": self.user_pool_client.user_pool_client_id,
            "DiscoveryUrl": f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}/.well-known/openid-configuration",
            "SecretName": f"{self.tool_name}/cognito/credentials",
        })
        resource.node.add_dependency(self.test_user)
        self._update_secret_role = update_role
        self._update_secret_fn = fn

    def _create_agentcore_runtime(self) -> agentcore.Runtime:
        agent_path = Path(__file__).parent.parent.parent.parent / "agentcore-agents" / "mcp-data-agent"
        runtime = agentcore.Runtime(
            self, "McpDataAgentRuntime",
            runtime_name=self.tool_name,
            agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_asset(str(agent_path), platform=ecr_assets.Platform.LINUX_ARM64),
            execution_role=self.agentcore_role,
            protocol_configuration=agentcore.ProtocolType.HTTP,
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_jwt(
                f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}/.well-known/openid-configuration",
                [self.user_pool_client.user_pool_client_id],
            ),
            environment_variables={"AWS_REGION": self.region, "AWS_DEFAULT_REGION": self.region},
        )
        runtime.node.add_dependency(self.agentcore_policy)
        return runtime

    def _create_ssm_parameters(self):
        ssm.StringParameter(self, "ToolNameParam", parameter_name=f"/{self.tool_name}/runtime/agent_name", string_value=self.tool_name)
        ssm.StringParameter(self, "RuntimeArnParam", parameter_name=f"/{self.tool_name}/runtime/agent_arn", string_value=self.runtime.agent_runtime_arn)
        ssm.StringParameter(self, "RuntimeIdParam", parameter_name=f"/{self.tool_name}/runtime/agent_id", string_value=self.runtime.agent_runtime_id)

    def _create_outputs(self):
        CfnOutput(self, "RuntimeArn", value=self.runtime.agent_runtime_arn)
        CfnOutput(self, "RuntimeId", value=self.runtime.agent_runtime_id)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)

    def _apply_cdk_nag_suppressions(self):
        NagSuppressions.add_resource_suppressions(self.agentcore_policy, [{"id": "AwsSolutions-IAM5", "reason": "Wildcard permissions required for Bedrock, ECR, X-Ray, CloudWatch."}])
        NagSuppressions.add_resource_suppressions(self.agentcore_role, [{"id": "AwsSolutions-IAM4", "reason": "AgentCore service requires broad permissions."}])
