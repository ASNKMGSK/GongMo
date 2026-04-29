"""
CDK Stack for the Summary Agent — AgentCore Runtime (MCP protocol).

The Summary Agent aggregates outputs from other agents and produces
consolidated summaries. Deployed as an MCP server so it can be registered
as a target on the AgentCore Gateway.
"""

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from typing import Optional

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


class SummaryAgentStack(Stack):
    """CDK Stack for Summary Agent using AgentCore Runtime (MCP protocol)."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        agentcore_context = self.node.try_get_context("summary-agent-agentcore") or {}
        self.tool_name = agentcore_context.get("tool-name", "a2a_summary_agent")

        self.agentcore_role = self._create_agentcore_role()
        self.log_group = self._create_log_group()
        self.user_pool, self.user_pool_client, self.user_pool_domain = self._create_cognito()
        self._store_cognito_credentials()
        self.runtime = self._create_agentcore_runtime()
        self.runtime_endpoint_url = self._create_endpoint_url()
        self.oauth_provider_arn, self.oauth_secret_arn = self._create_oauth_provider()
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
            ],
        )
        role.attach_inline_policy(self.agentcore_policy)
        return role

    def _create_log_group(self) -> logs.LogGroup:
        return logs.LogGroup(self, "AgentCoreLogGroup", log_group_name=f"/aws/bedrock-agentcore/runtimes/{self.tool_name}", retention=logs.RetentionDays.ONE_WEEK, removal_policy=RemovalPolicy.DESTROY)

    def _create_cognito(self):
        """Create Cognito User Pool with OAuth2 client_credentials flow for Gateway."""
        user_pool = cognito.UserPool(self, "UserPool", user_pool_name=f"{self.tool_name}.Pool", self_sign_up_enabled=False, removal_policy=RemovalPolicy.DESTROY)
        resource_server = user_pool.add_resource_server("ResourceServer", identifier=f"{self.tool_name}-api", scopes=[cognito.ResourceServerScope(scope_name="invoke", scope_description="Invoke MCP server")])
        user_pool_client = user_pool.add_client("UserPoolClient", generate_secret=True, o_auth=cognito.OAuthSettings(
            flows=cognito.OAuthFlows(client_credentials=True),
            scopes=[cognito.OAuthScope.resource_server(resource_server, cognito.ResourceServerScope(scope_name="invoke", scope_description="Invoke MCP server"))],
        ))
        domain_prefix = f"{self.tool_name.replace('_', '-')}-{self.account}"
        user_pool_domain = user_pool.add_domain("UserPoolDomain", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix))
        return user_pool, user_pool_client, user_pool_domain

    def _store_cognito_credentials(self):
        """Store Cognito client credentials in Secrets Manager."""
        cr_role = iam.Role(self, "StoreCredsRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
            inline_policies={"SecretsAccess": iam.PolicyDocument(statements=[
                iam.PolicyStatement(actions=["secretsmanager:CreateSecret", "secretsmanager:UpdateSecret", "secretsmanager:DeleteSecret", "secretsmanager:DescribeSecret"], resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{self.tool_name}/cognito/*"]),
                iam.PolicyStatement(actions=["cognito-idp:DescribeUserPoolClient"], resources=[self.user_pool.user_pool_arn]),
            ])})
        fn = lambda_.Function(self, "StoreCredsFunction", runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler", timeout=Duration.minutes(2), role=cr_role,
            code=lambda_.Code.from_inline('''
import boto3, json
def handler(event, context):
    props = event["ResourceProperties"]
    secrets = boto3.client("secretsmanager")
    cognito = boto3.client("cognito-idp")
    secret_name = f"{props['ToolName']}/cognito/credentials"
    if event["RequestType"] in ["Create", "Update"]:
        resp = cognito.describe_user_pool_client(UserPoolId=props["UserPoolId"], ClientId=props["ClientId"])
        secret_value = json.dumps({
            "user_pool_id": props["UserPoolId"],
            "client_id": props["ClientId"],
            "client_secret": resp["UserPoolClient"].get("ClientSecret", ""),
            "discovery_url": props["DiscoveryUrl"],
            "token_url": props["TokenUrl"],
            "scope": props["Scope"]
        })
        try:
            secrets.update_secret(SecretId=secret_name, SecretString=secret_value)
        except secrets.exceptions.ResourceNotFoundException:
            secrets.create_secret(Name=secret_name, SecretString=secret_value)
        return {"PhysicalResourceId": secret_name}
    return {"PhysicalResourceId": event.get("PhysicalResourceId", secret_name)}
'''))
        provider = cr.Provider(self, "StoreCredsProvider", on_event_handler=fn)
        domain_prefix = f"{self.tool_name.replace('_', '-')}-{self.account}"
        CustomResource(self, "StoreCreds", service_token=provider.service_token, properties={
            "ToolName": self.tool_name,
            "UserPoolId": self.user_pool.user_pool_id,
            "ClientId": self.user_pool_client.user_pool_client_id,
            "DiscoveryUrl": f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}/.well-known/openid-configuration",
            "TokenUrl": f"https://{domain_prefix}.auth.{self.region}.amazoncognito.com/oauth2/token",
            "Scope": f"{self.tool_name}-api/invoke",
        })
        self._store_creds_role = cr_role
        self._store_creds_fn = fn

    def _create_agentcore_runtime(self) -> agentcore.Runtime:
        agent_path = Path(__file__).parent.parent.parent.parent / "agentcore-agents" / "summary-agent"
        runtime = agentcore.Runtime(
            self, "SummaryAgentRuntime",
            runtime_name=self.tool_name,
            agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_asset(str(agent_path), platform=ecr_assets.Platform.LINUX_ARM64),
            execution_role=self.agentcore_role,
            protocol_configuration=agentcore.ProtocolType.MCP,
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_jwt(
                f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}/.well-known/openid-configuration",
                [self.user_pool_client.user_pool_client_id],
            ),
            environment_variables={"AWS_REGION": self.region, "AWS_DEFAULT_REGION": self.region},
        )
        runtime.node.add_dependency(self.agentcore_policy)
        return runtime

    def _create_endpoint_url(self) -> str:
        """Create endpoint URL using Custom Resource to URL-encode the ARN at deploy time."""
        cr_role = iam.Role(self, "EndpointUrlRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")])
        cr_function = lambda_.Function(self, "EndpointUrlFunction", runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler", timeout=Duration.seconds(30), role=cr_role,
            code=lambda_.Code.from_inline('''
import urllib.parse
def handler(event, context):
    if event["RequestType"] == "Delete":
        return {"PhysicalResourceId": event.get("PhysicalResourceId", "endpoint-url")}
    props = event["ResourceProperties"]
    encoded_arn = urllib.parse.quote(props["RuntimeArn"], safe="")
    endpoint_url = f"https://bedrock-agentcore.{props['Region']}.amazonaws.com/runtimes/{encoded_arn}/invocations"
    return {"PhysicalResourceId": "endpoint-url", "Data": {"EndpointUrl": endpoint_url}}
'''))
        provider = cr.Provider(self, "EndpointUrlProvider", on_event_handler=cr_function)
        endpoint_cr = CustomResource(self, "EndpointUrl", service_token=provider.service_token, properties={
            "RuntimeArn": self.runtime.agent_runtime_arn,
            "Region": self.region,
        })
        endpoint_cr.node.add_dependency(self.runtime)
        self._endpoint_url_role = cr_role
        self._endpoint_url_fn = cr_function
        return endpoint_cr.get_att_string("EndpointUrl")

    def _create_oauth_provider(self):
        """Create OAuth2 credential provider for Gateway."""
        oauth_provider_name = f"gateway-{self.tool_name.replace('_', '-')}-oauth"
        cr_role = iam.Role(self, "OAuthProviderRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
            inline_policies={"OAuthAccess": iam.PolicyDocument(statements=[
                iam.PolicyStatement(actions=["bedrock-agentcore:CreateOauth2CredentialProvider", "bedrock-agentcore:DeleteOauth2CredentialProvider", "bedrock-agentcore:GetOauth2CredentialProvider", "bedrock-agentcore:CreateTokenVault"], resources=["*"]),
                iam.PolicyStatement(actions=["cognito-idp:DescribeUserPoolClient"], resources=[self.user_pool.user_pool_arn]),
                iam.PolicyStatement(actions=["secretsmanager:CreateSecret", "secretsmanager:DeleteSecret"], resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:bedrock-agentcore-identity*"]),
            ])})
        cr_function = lambda_.Function(self, "OAuthProviderFunction", runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler", timeout=Duration.minutes(2), role=cr_role,
            code=lambda_.Code.from_inline('''
import boto3, json, time
def handler(event, context):
    props = event["ResourceProperties"]
    control = boto3.client("bedrock-agentcore-control")
    cognito = boto3.client("cognito-idp")
    name = props["ProviderName"]
    if event["RequestType"] == "Delete":
        try:
            control.delete_oauth2_credential_provider(name=name)
        except: pass
        return {"PhysicalResourceId": name}
    resp = cognito.describe_user_pool_client(UserPoolId=props["UserPoolId"], ClientId=props["ClientId"])
    client_secret = resp["UserPoolClient"].get("ClientSecret", "")
    if event["RequestType"] == "Update":
        try:
            control.delete_oauth2_credential_provider(name=name)
            time.sleep(10)
        except: pass
    try:
        oauth_resp = control.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {"discoveryUrl": props["DiscoveryUrl"]},
                    "clientId": props["ClientId"],
                    "clientSecret": client_secret
                }
            }
        )
    except control.exceptions.ConflictException:
        oauth_resp = control.get_oauth2_credential_provider(name=name)
    provider_arn = oauth_resp.get("credentialProviderArn") or oauth_resp.get("oauth2CredentialProvider", {}).get("credentialProviderArn")
    secret_arn = oauth_resp.get("clientSecretArn", {}).get("secretArn") or oauth_resp.get("oauth2CredentialProvider", {}).get("clientSecretArn", {}).get("secretArn")
    return {"PhysicalResourceId": name, "Data": {"ProviderArn": provider_arn, "SecretArn": secret_arn}}
'''))
        provider = cr.Provider(self, "OAuthProviderProvider", on_event_handler=cr_function)
        oauth_cr = CustomResource(self, "OAuthProvider", service_token=provider.service_token, properties={
            "ProviderName": oauth_provider_name,
            "UserPoolId": self.user_pool.user_pool_id,
            "ClientId": self.user_pool_client.user_pool_client_id,
            "DiscoveryUrl": f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}/.well-known/openid-configuration",
        })
        self._oauth_provider_role = cr_role
        self._oauth_provider_fn = cr_function
        return oauth_cr.get_att_string("ProviderArn"), oauth_cr.get_att_string("SecretArn")

    def _create_ssm_parameters(self):
        ssm.StringParameter(self, "ToolNameParam", parameter_name=f"/{self.tool_name}/runtime/agent_name", string_value=self.tool_name)
        ssm.StringParameter(self, "RuntimeArnParam", parameter_name=f"/{self.tool_name}/runtime/agent_arn", string_value=self.runtime.agent_runtime_arn)
        ssm.StringParameter(self, "RuntimeIdParam", parameter_name=f"/{self.tool_name}/runtime/agent_id", string_value=self.runtime.agent_runtime_id)
        ssm.StringParameter(self, "EndpointUrlParam", parameter_name=f"/{self.tool_name}/runtime/endpoint_url", string_value=self.runtime_endpoint_url)

    def _create_outputs(self):
        CfnOutput(self, "RuntimeArn", value=self.runtime.agent_runtime_arn)
        CfnOutput(self, "RuntimeId", value=self.runtime.agent_runtime_id)
        CfnOutput(self, "RuntimeEndpointUrl", value=self.runtime_endpoint_url)
        CfnOutput(self, "OAuthProviderArn", value=self.oauth_provider_arn)
        CfnOutput(self, "OAuthSecretArn", value=self.oauth_secret_arn)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)

    def _apply_cdk_nag_suppressions(self):
        NagSuppressions.add_resource_suppressions(self.agentcore_policy, [{"id": "AwsSolutions-IAM5", "reason": "Wildcard permissions required for Bedrock, ECR, X-Ray."}])
        NagSuppressions.add_resource_suppressions(self.agentcore_role, [{"id": "AwsSolutions-IAM4", "reason": "AgentCore service requires broad permissions."}])
        NagSuppressions.add_resource_suppressions(self._store_creds_role, [{"id": "AwsSolutions-IAM4", "reason": "Lambda basic execution role required."}])
        NagSuppressions.add_resource_suppressions(self._store_creds_fn, [{"id": "AwsSolutions-L1", "reason": "Python 3.12 is acceptable."}])
        NagSuppressions.add_resource_suppressions(self._endpoint_url_role, [{"id": "AwsSolutions-IAM4", "reason": "Lambda basic execution role required."}])
        NagSuppressions.add_resource_suppressions(self._endpoint_url_fn, [{"id": "AwsSolutions-L1", "reason": "Python 3.12 is acceptable."}])
        NagSuppressions.add_resource_suppressions(self._oauth_provider_role, [{"id": "AwsSolutions-IAM4", "reason": "Lambda basic execution role required."}, {"id": "AwsSolutions-IAM5", "reason": "OAuth provider API requires wildcard."}])
        NagSuppressions.add_resource_suppressions(self._oauth_provider_fn, [{"id": "AwsSolutions-L1", "reason": "Python 3.12 is acceptable."}])
