# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""QA Pipeline V3 — EC2 신규 프로비저닝 스크립트.

사용자 자체 AWS 계정에 독립 배포. `default` 프로파일 / us-east-1 기준.

동작:
  1. IAM Role + InstanceProfile 생성 (Bedrock Invoke + S3 Read + SSM)
  2. S3 배포 버킷 생성 (qa-deploy-{AccountId}-us-east-1)
  3. SecurityGroup 생성 (22/80/3000/8081 0.0.0.0/0)
  4. EC2 인스턴스 생성 (Ubuntu 22.04 LTS t3.medium 30GB)
  5. Tag + Name + 결과 출력

Idempotent: 동일 이름 리소스 있으면 재사용 (중복 생성 방지).
실행: python provision.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError


REGION = os.environ.get("AWS_REGION", "us-east-1")
# Next.js 16 + Turbopack 빌드가 EC2 위에서 돌아가므로 4GB 는 부족 → t3.large(8GB) 권장.
# `EC2_INSTANCE_TYPE=t3.medium` 으로 명시하면 강제로 작은 인스턴스 사용 가능.
INSTANCE_TYPE = os.environ.get("EC2_INSTANCE_TYPE", "t3.large")
VOLUME_SIZE_GB = int(os.environ.get("EC2_VOLUME_SIZE", "30"))
NAME_PREFIX = os.environ.get("QA_NAME_PREFIX", "qa-pipeline-v3")

# Canonical Ubuntu 22.04 LTS AMI owner
UBUNTU_AMI_OWNER = "099720109477"
UBUNTU_AMI_NAME_PATTERN = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"

ROLE_NAME = f"{NAME_PREFIX}-role"
INSTANCE_PROFILE_NAME = f"{NAME_PREFIX}-instance-profile"
SG_NAME = f"{NAME_PREFIX}-sg"
KEY_NAME = None  # SSM 만 사용 — SSH key 불필요


def _log(msg: str) -> None:
    print(f"[provision] {msg}", flush=True)


def get_account_id() -> str:
    sts = boto3.client("sts", region_name=REGION)
    return sts.get_caller_identity()["Account"]


def ensure_s3_bucket(account_id: str) -> str:
    bucket = f"qa-deploy-{account_id}-{REGION}"
    s3 = boto3.client("s3", region_name=REGION)
    try:
        s3.head_bucket(Bucket=bucket)
        _log(f"S3 bucket 재사용: {bucket}")
        return bucket
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise
    if REGION == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    _log(f"S3 bucket 생성: {bucket}")
    return bucket


def ensure_iam_role(bucket: str) -> str:
    """EC2 에 붙일 IAM Role + InstanceProfile. Bedrock + S3 + SSM."""
    iam = boto3.client("iam")

    assume_role = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        iam.get_role(RoleName=ROLE_NAME)
        _log(f"IAM Role 재사용: {ROLE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(assume_role),
            Description="QA Pipeline V3 EC2 - Bedrock + S3 deploy + SSM",
        )
        _log(f"IAM Role 생성: {ROLE_NAME}")

    # SSM AWS-managed policy
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    )
    # CloudWatch logs (optional but 유용)
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    )

    # Inline: Bedrock + S3 deploy bucket
    bedrock_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket}",
                    f"arn:aws:s3:::{bucket}/*",
                ],
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName=f"{NAME_PREFIX}-bedrock-s3",
        PolicyDocument=json.dumps(bedrock_policy),
    )

    # Instance Profile
    try:
        iam.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
        _log(f"InstanceProfile 재사용: {INSTANCE_PROFILE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        iam.create_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
        iam.add_role_to_instance_profile(
            InstanceProfileName=INSTANCE_PROFILE_NAME, RoleName=ROLE_NAME
        )
        _log(f"InstanceProfile 생성: {INSTANCE_PROFILE_NAME}")
        # Role 이 instance profile 에 붙는데 시간 필요
        time.sleep(8)
    return INSTANCE_PROFILE_NAME


def ensure_security_group(ec2: Any, vpc_id: str) -> str:
    existing = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [SG_NAME]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )["SecurityGroups"]
    if existing:
        sg_id = existing[0]["GroupId"]
        _log(f"SecurityGroup 재사용: {sg_id}")
        return sg_id

    sg = ec2.create_security_group(
        GroupName=SG_NAME,
        Description="QA Pipeline V3 - SSH/UI/API ingress",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]

    ingress = [
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "SSH"}]},
        {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTP"}]},
        {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS"}]},
        {"IpProtocol": "tcp", "FromPort": 3000, "ToPort": 3000,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "Next.js UI"}]},
        {"IpProtocol": "tcp", "FromPort": 8081, "ToPort": 8081,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "QA Pipeline API"}]},
    ]
    ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=ingress)
    _log(f"SecurityGroup 생성: {sg_id}")
    return sg_id


def pick_ubuntu_ami(ec2: Any) -> str:
    imgs = ec2.describe_images(
        Owners=[UBUNTU_AMI_OWNER],
        Filters=[
            {"Name": "name", "Values": [UBUNTU_AMI_NAME_PATTERN]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
        ],
    )["Images"]
    imgs.sort(key=lambda i: i["CreationDate"], reverse=True)
    if not imgs:
        raise RuntimeError("Ubuntu 22.04 AMI not found")
    _log(f"AMI: {imgs[0]['ImageId']} ({imgs[0]['Name']})")
    return imgs[0]["ImageId"]


def ensure_default_vpc_subnet(ec2: Any) -> tuple[str, str]:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("Default VPC 없음 — 수동 VPC 지정 필요")
    vpc_id = vpcs[0]["VpcId"]

    subnets = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "default-for-az", "Values": ["true"]},
        ]
    )["Subnets"]
    if not subnets:
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    if not subnets:
        raise RuntimeError("Subnet 없음")
    subnet_id = subnets[0]["SubnetId"]
    _log(f"VPC/Subnet: {vpc_id} / {subnet_id}")
    return vpc_id, subnet_id


def find_existing_instance(ec2: Any) -> str | None:
    r = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [NAME_PREFIX]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopped"]},
        ]
    )
    for res in r["Reservations"]:
        for inst in res["Instances"]:
            return inst["InstanceId"]
    return None


def launch_instance(
    ec2: Any, ami_id: str, subnet_id: str, sg_id: str, profile_name: str
) -> tuple[str, str]:
    existing = find_existing_instance(ec2)
    if existing:
        _log(f"EC2 재사용: {existing}")
        info = ec2.describe_instances(InstanceIds=[existing])["Reservations"][0]["Instances"][0]
        pub = info.get("PublicIpAddress") or ""
        return existing, pub

    _log("EC2 신규 생성 중…")
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        NetworkInterfaces=[
            {
                "DeviceIndex": 0,
                "SubnetId": subnet_id,
                "Groups": [sg_id],
                "AssociatePublicIpAddress": True,
            }
        ],
        IamInstanceProfile={"Name": profile_name},
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": VOLUME_SIZE_GB,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                },
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": NAME_PREFIX},
                    {"Key": "Project", "Value": "qa-pipeline-v3"},
                ],
            }
        ],
        MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
    )
    inst_id = resp["Instances"][0]["InstanceId"]
    _log(f"InstanceId: {inst_id} — running 대기 중…")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[inst_id])
    info = ec2.describe_instances(InstanceIds=[inst_id])["Reservations"][0]["Instances"][0]
    pub = info.get("PublicIpAddress") or ""
    _log(f"EC2 running: {inst_id} / public_ip={pub}")
    return inst_id, pub


def main() -> int:
    _log(f"Region: {REGION}")
    account_id = get_account_id()
    _log(f"Account: {account_id}")

    bucket = ensure_s3_bucket(account_id)
    profile_name = ensure_iam_role(bucket)

    ec2 = boto3.client("ec2", region_name=REGION)
    vpc_id, subnet_id = ensure_default_vpc_subnet(ec2)
    sg_id = ensure_security_group(ec2, vpc_id)
    ami_id = pick_ubuntu_ami(ec2)
    inst_id, public_ip = launch_instance(ec2, ami_id, subnet_id, sg_id, profile_name)

    # 결과 저장 → deploy.py 가 읽음
    out = {
        "region": REGION,
        "account_id": account_id,
        "instance_id": inst_id,
        "public_ip": public_ip,
        "security_group": sg_id,
        "vpc": vpc_id,
        "subnet": subnet_id,
        "s3_bucket": bucket,
        "iam_role": ROLE_NAME,
        "instance_profile": INSTANCE_PROFILE_NAME,
        "ami_id": ami_id,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provision.out.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    _log(f"결과 저장: {out_path}")
    _log("다음 단계: python deploy.py --target bootstrap  # (EC2 초기 셋업)")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
