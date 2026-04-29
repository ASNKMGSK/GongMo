# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CDK Stack for the QA Pipeline on EC2.

Deploys the QA Pipeline FastAPI server (packages/agentcore-agents/qa-pipeline)
onto a single t3.medium EC2 instance in the default VPC. Uploads the app
as an S3 asset, installs Python + deps at first boot via user data, and
runs the server as a systemd service on port 8080.

Rationale: AgentCore Runtime imposes a 120s init timeout that the QA
pipeline (LangGraph cold-start + SageMaker Qwen3-8B warmup) exceeds, so
we run it on a plain EC2 instance instead.
"""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3_assets as s3_assets,
    aws_ssm as ssm,
)
from constructs import Construct


SAGEMAKER_ENDPOINT_NAME = "qwen3-8b-vllm"
SAGEMAKER_ACCOUNT = "919359878144"
SAGEMAKER_REGION = "us-east-1"


class QaPipelineEc2Stack(Stack):
    """QA Pipeline on EC2 (t3.medium, SageMaker Qwen3-8B backend)."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        app_path = (
            Path(__file__).parent.parent.parent.parent
            / "agentcore-agents"
            / "qa-pipeline"
        )
        asset = s3_assets.Asset(
            self,
            "AppAsset",
            path=str(app_path),
            exclude=[
                "_analyst_*.py",
                "_analyst_*.txt",
                "__pycache__",
                "**/__pycache__",
                ".env",
                # 주의: prompts/*.md 는 LLM 프롬프트 파일이므로 반드시 포함.
                # 따라서 *.md 패턴은 사용 금지. 루트 README/API 문서만 개별 제외.
                "README.md",
                "API.md",
                "parser_issues.md",
                "*.html",
                ".git",
                "**/*.pyc",
                "cdk.out",
                "test_outputs",
                "raw",
            ],
        )

        role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy"),
            ],
        )

        role.add_to_policy(
            iam.PolicyStatement(
                sid="SageMakerInvoke",
                actions=[
                    "sagemaker:InvokeEndpoint",
                    "sagemaker:InvokeEndpointWithResponseStream",
                ],
                resources=[
                    f"arn:aws:sagemaker:{SAGEMAKER_REGION}:{SAGEMAKER_ACCOUNT}:endpoint/{SAGEMAKER_ENDPOINT_NAME}"
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockInvoke",
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    "arn:aws:bedrock:*:*:inference-profile/*",
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="SecretsRead",
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret",
                ],
                resources=[
                    f"arn:aws:secretsmanager:{SAGEMAKER_REGION}:{SAGEMAKER_ACCOUNT}:secret:qa_pipeline/*"
                ],
            )
        )

        asset.grant_read(role)

        sg = ec2.SecurityGroup(
            self,
            "Sg",
            vpc=vpc,
            description="QA Pipeline EC2 SG (8080 public, 22 SSH)",
            allow_all_outbound=True,
        )
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(8080), "QA Pipeline HTTP")
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH (restrict later)")

        user_data = ec2.UserData.for_linux()
        self._populate_user_data(user_data, asset)

        instance = ec2.Instance(
            self,
            "Instance",
            instance_type=ec2.InstanceType("t3.medium"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=sg,
            role=role,
            user_data=user_data,
            associate_public_ip_address=True,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        30, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )

        ssm.StringParameter(
            self,
            "PublicIpParam",
            parameter_name="/qa_pipeline_ec2/public_ip",
            string_value=instance.instance_public_ip,
        )
        ssm.StringParameter(
            self,
            "EndpointUrlParam",
            parameter_name="/qa_pipeline_ec2/endpoint_url",
            string_value=f"http://{instance.instance_public_ip}:8080",
        )
        ssm.StringParameter(
            self,
            "InstanceIdParam",
            parameter_name="/qa_pipeline_ec2/instance_id",
            string_value=instance.instance_id,
        )

        CfnOutput(self, "PublicIp", value=instance.instance_public_ip)
        CfnOutput(self, "Endpoint", value=f"http://{instance.instance_public_ip}:8080")
        CfnOutput(self, "InstanceId", value=instance.instance_id)
        CfnOutput(
            self,
            "SSHCommand",
            value=f"aws ssm start-session --target {instance.instance_id} --region {self.region}",
        )

    def _populate_user_data(self, user_data: ec2.UserData, asset: s3_assets.Asset) -> None:
        """Populate user data with install + systemd setup.

        Order:
        1. dnf install python3.11 + build tools + unzip
        2. mkdir /opt/qa-pipeline
        3. aws s3 cp (via add_s3_download_command) app zip to /tmp/app.zip
        4. unzip into /opt/qa-pipeline
        5. create venv + install requirements
        6. write .env + systemd unit + start service

        Note: AL2023 default repos ship python3.9; python3.11 is available
        as the dnf 'python3.11' package. python3.13 is NOT available, so we
        use 3.11. The qa-pipeline code uses PEP 604 union syntax (3.10+)
        and standard typing features, all compatible with 3.11.
        """
        user_data.add_commands(
            "#!/bin/bash",
            "set -euxo pipefail",
            "exec > >(tee -a /var/log/user-data.log) 2>&1",
            "echo '=== QA Pipeline user-data start ==='",
            "dnf update -y",
            "dnf install -y python3.11 python3.11-pip python3.11-devel gcc unzip tar gzip",
            "mkdir -p /opt/qa-pipeline",
        )

        local_zip = "/tmp/qa-pipeline-app.zip"
        user_data.add_s3_download_command(
            bucket=asset.bucket,
            bucket_key=asset.s3_object_key,
            local_file=local_zip,
        )

        user_data.add_commands(
            f"cd /opt/qa-pipeline && unzip -o {local_zip} && rm -f {local_zip}",
            "python3.11 -m venv /opt/qa-pipeline/venv",
            "/opt/qa-pipeline/venv/bin/pip install --upgrade pip setuptools wheel",
            "/opt/qa-pipeline/venv/bin/pip install -r /opt/qa-pipeline/requirements.txt",
            "cat > /opt/qa-pipeline/.env <<'ENVEOF'",
            "AWS_REGION=us-east-1",
            "AWS_DEFAULT_REGION=us-east-1",
            f"SAGEMAKER_ENDPOINT_NAME={SAGEMAKER_ENDPOINT_NAME}",
            "LLM_BACKEND=sagemaker",
            "PORT=8080",
            "ENVEOF",
            "chown -R ec2-user:ec2-user /opt/qa-pipeline",
            "cat > /etc/systemd/system/qa-pipeline.service <<'UNITEOF'",
            "[Unit]",
            "Description=QA Pipeline FastAPI server",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "User=ec2-user",
            "WorkingDirectory=/opt/qa-pipeline",
            "EnvironmentFile=/opt/qa-pipeline/.env",
            "ExecStart=/opt/qa-pipeline/venv/bin/python /opt/qa-pipeline/main.py",
            "Restart=always",
            "RestartSec=10",
            "StandardOutput=append:/var/log/qa-pipeline.log",
            "StandardError=append:/var/log/qa-pipeline.log",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "UNITEOF",
            "systemctl daemon-reload",
            "systemctl enable qa-pipeline",
            "systemctl start qa-pipeline",
            "echo 'READY' >> /var/log/user-data.log",
            "echo '=== QA Pipeline user-data done ==='",
        )
