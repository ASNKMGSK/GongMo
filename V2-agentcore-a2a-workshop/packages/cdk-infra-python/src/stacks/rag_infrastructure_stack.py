"""
CDK Stack for RAG Infrastructure — OpenSearch Serverless, S3, DynamoDB, and upload API.
"""

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import tempfile

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigw,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_ssm as ssm,
    custom_resources as cr,
)
from constructs import Construct


class RAGInfrastructureStack(Stack):
    """CDK Stack for RAG infrastructure."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.document_bucket = self._create_document_bucket()
        self.metadata_table = self._create_metadata_table()
        self.collection_name = "a2a-rag-documents"
        self._create_opensearch_serverless_collection()
        self.upload_lambda = self._create_upload_lambda()
        self.api = self._create_api_gateway()
        self._create_ssm_parameters()
        self._create_outputs()

    def _create_document_bucket(self) -> s3.Bucket:
        return s3.Bucket(
            self, "RAGDocumentBucket",
            bucket_name=f"a2a-rag-documents-{self.account}-{self.region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            cors=[s3.CorsRule(
                allowed_methods=[s3.HttpMethods.PUT, s3.HttpMethods.POST, s3.HttpMethods.GET],
                allowed_origins=["*"], allowed_headers=["*"],
            )],
        )

    def _create_metadata_table(self) -> dynamodb.Table:
        table = dynamodb.Table(
            self, "RAGDocumentMetadata",
            table_name="a2a-rag-document-metadata",
            partition_key=dynamodb.Attribute(name="document_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="uploaded_at", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="uploaded_at", type=dynamodb.AttributeType.STRING),
        )
        return table

    def _create_opensearch_serverless_collection(self) -> None:
        enc_policy = cdk.aws_opensearchserverless.CfnSecurityPolicy(
            self, "RAGEncryptionPolicy", name="a2a-rag-enc-policy", type="encryption",
            policy=json.dumps({"Rules": [{"ResourceType": "collection", "Resource": [f"collection/{self.collection_name}"]}], "AWSOwnedKey": True}),
        )
        net_policy = cdk.aws_opensearchserverless.CfnSecurityPolicy(
            self, "RAGNetworkPolicy", name="a2a-rag-net-policy", type="network",
            policy=json.dumps([{"Rules": [
                {"ResourceType": "collection", "Resource": [f"collection/{self.collection_name}"]},
                {"ResourceType": "dashboard", "Resource": [f"collection/{self.collection_name}"]},
            ], "AllowFromPublic": True}]),
        )
        self.collection = cdk.aws_opensearchserverless.CfnCollection(
            self, "RAGCollection", name=self.collection_name, type="VECTORSEARCH",
            description="Vector search collection for A2A workshop RAG documents",
        )
        self.collection.add_dependency(enc_policy)
        self.collection.add_dependency(net_policy)

    def _create_upload_lambda(self) -> lambda_.Function:
        upload_role = iam.Role(self, "RAGUploadLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
            inline_policies={"RAGUploadPolicy": iam.PolicyDocument(statements=[
                iam.PolicyStatement(actions=["s3:PutObject", "s3:GetObject", "s3:ListBucket"], resources=[self.document_bucket.bucket_arn, f"{self.document_bucket.bucket_arn}/*"]),
                iam.PolicyStatement(actions=["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"], resources=[self.metadata_table.table_arn, f"{self.metadata_table.table_arn}/index/*"]),
                iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{self.account}:inference-profile/*"]),
                iam.PolicyStatement(actions=["aoss:APIAccessAll"], resources=[f"arn:aws:aoss:{self.region}:{self.account}:collection/*"]),
            ])},
        )
        fn = lambda_.Function(self, "RAGUploadFunction",
            runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler",
            code=self._get_upload_lambda_asset(), role=upload_role,
            timeout=Duration.minutes(10), memory_size=1024,
            environment={
                "DOCUMENT_BUCKET": self.document_bucket.bucket_name,
                "METADATA_TABLE": self.metadata_table.table_name,
                "OPENSEARCH_ENDPOINT": self.collection.attr_collection_endpoint,
                "OPENSEARCH_COLLECTION_NAME": self.collection_name,
                "AWS_REGION_NAME": self.region,
            },
        )

        # Data access policy for OpenSearch
        principals = [upload_role.role_arn]
        admin_user = self.node.try_get_context("admin_user")
        if admin_user:
            principals.append(f"arn:aws:iam::{self.account}:user/{admin_user}")

        cdk.aws_opensearchserverless.CfnAccessPolicy(
            self, "RAGDataAccessPolicy", name="a2a-rag-data-policy", type="data",
            policy=json.dumps([{"Rules": [
                {"ResourceType": "index", "Resource": [f"index/{self.collection_name}/*"], "Permission": ["aoss:CreateIndex", "aoss:DeleteIndex", "aoss:UpdateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument"]},
                {"ResourceType": "collection", "Resource": [f"collection/{self.collection_name}"], "Permission": ["aoss:CreateCollectionItems", "aoss:DeleteCollectionItems", "aoss:DescribeCollectionItems", "aoss:UpdateCollectionItems"]},
            ], "Principal": principals}]),
        ).add_dependency(self.collection)

        return fn

    def _get_upload_lambda_asset(self) -> lambda_.Code:
        import subprocess
        code = self._get_upload_lambda_code()
        asset_dir = os.path.join(tempfile.gettempdir(), "a2a_rag_upload_lambda")
        os.makedirs(asset_dir, exist_ok=True)
        with open(os.path.join(asset_dir, "index.py"), "w") as f:
            f.write(code)
        subprocess.run(["pip", "install", "requests", "requests_aws4auth", "-t", asset_dir, "--quiet", "--upgrade"], check=True, capture_output=True)
        return lambda_.Code.from_asset(asset_dir)

    def _get_upload_lambda_code(self) -> str:
        return '''
import boto3, json, os, uuid, base64
from datetime import datetime
import requests as http_requests
from requests_aws4auth import AWS4Auth

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
bedrock = boto3.client("bedrock-runtime")
BUCKET = os.environ["DOCUMENT_BUCKET"]
TABLE = os.environ["METADATA_TABLE"]
OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
COLLECTION_NAME = os.environ["OPENSEARCH_COLLECTION_NAME"]
REGION = os.environ["AWS_REGION_NAME"]
INDEX_NAME = "rag-documents"
table = dynamodb.Table(TABLE)
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
EMBEDDING_DIMENSION = 1024

def _get_auth():
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    return AWS4Auth(creds.access_key, creds.secret_key, REGION, "aoss", session_token=creds.token)

def _opensearch_request(method, path, body=None):
    url = f"{OPENSEARCH_ENDPOINT}/{path}"
    headers = {"Content-Type": "application/json"}
    data = json.dumps(body) if body else None
    resp = http_requests.request(method, url, auth=_get_auth(), headers=headers, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.text else {}

def _ensure_index():
    try:
        _opensearch_request("HEAD", INDEX_NAME)
        return
    except Exception:
        pass
    _opensearch_request("PUT", INDEX_NAME, {
        "settings": {"index": {"knn": True}},
        "mappings": {"properties": {
            "embedding": {"type": "knn_vector", "dimension": EMBEDDING_DIMENSION, "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "faiss", "parameters": {"ef_construction": 512, "m": 16}}},
            "text": {"type": "text"}, "document_id": {"type": "keyword"}, "filename": {"type": "keyword"}, "chunk_index": {"type": "integer"},
        }}
    })

def _chunk_text(text):
    words = text.split()
    if not words: return []
    chunks, start = [], 0
    while start < len(words):
        end = start + CHUNK_SIZE
        chunks.append(" ".join(words[start:end]))
        if end >= len(words): break
        start = end - CHUNK_OVERLAP
    return chunks

def _embed_text(text):
    resp = bedrock.invoke_model(modelId="amazon.titan-embed-text-v2:0", contentType="application/json", accept="application/json", body=json.dumps({"inputText": text[:8000], "dimensions": EMBEDDING_DIMENSION, "normalize": True}))
    return json.loads(resp["body"].read())["embedding"]

def _index_document(document_id, filename, text):
    _ensure_index()
    chunks = _chunk_text(text)
    for i, chunk in enumerate(chunks):
        _opensearch_request("POST", f"{INDEX_NAME}/_doc", {"embedding": _embed_text(chunk), "text": chunk, "document_id": document_id, "filename": filename, "chunk_index": i})
    return len(chunks)

def handler(event, context):
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type,Authorization", "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Content-Type": "application/json"}
    if method == "OPTIONS": return {"statusCode": 200, "headers": headers, "body": ""}
    if method == "POST" and "/upload" in path: return handle_upload(event, headers)
    if method == "GET" and "/documents" in path: return handle_list(event, headers)
    if method == "GET" and "/search" in path: return handle_search(event, headers)
    return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "Not found"})}

def handle_upload(event, headers):
    try:
        body = json.loads(event.get("body", "{}"))
        filename, content = body.get("filename", ""), body.get("content", "")
        if not filename or not content: return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "filename and content required"})}
        document_id = str(uuid.uuid4())
        s3_key = f"uploads/{document_id}/{filename}"
        file_bytes = base64.b64decode(content) if body.get("is_base64") else content.encode("utf-8")
        s3.put_object(Bucket=BUCKET, Key=s3_key, Body=file_bytes)
        now = datetime.utcnow().isoformat() + "Z"
        table.put_item(Item={"document_id": document_id, "uploaded_at": now, "filename": filename, "s3_key": s3_key, "status": "uploaded", "size_bytes": len(file_bytes), "created_at": now})
        try:
            text = file_bytes.decode("utf-8") if isinstance(file_bytes, bytes) else file_bytes
            chunk_count = _index_document(document_id, filename, text)
            table.update_item(Key={"document_id": document_id, "uploaded_at": now}, UpdateExpression="SET #s = :s, chunk_count = :c", ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "indexed", ":c": chunk_count})
            return {"statusCode": 200, "headers": headers, "body": json.dumps({"document_id": document_id, "status": "indexed", "chunk_count": chunk_count})}
        except Exception as e:
            return {"statusCode": 200, "headers": headers, "body": json.dumps({"document_id": document_id, "status": "embedding_failed", "error": str(e)})}
    except Exception as e:
        return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": str(e)})}

def handle_search(event, headers):
    try:
        params = event.get("queryStringParameters") or {}
        query = params.get("q", "").strip()
        top_k = int(params.get("k", "5"))
        if not query: return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "q parameter required"})}
        query_embedding = _embed_text(query)
        resp = _opensearch_request("POST", f"{INDEX_NAME}/_search", {"size": top_k, "_source": ["text", "document_id", "chunk_index", "filename"], "query": {"knn": {"embedding": {"vector": query_embedding, "k": top_k}}}})
        hits = resp.get("hits", {}).get("hits", [])
        sources = [{"text": h["_source"].get("text", ""), "filename": h["_source"].get("filename", ""), "score": h.get("_score", 0)} for h in hits]
        context = "\\n\\n---\\n\\n".join([f"[{s['filename']}] {s['text']}" for s in sources])
        if context:
            llm_resp = bedrock.invoke_model(modelId="us.anthropic.claude-sonnet-4-6", body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1024, "messages": [{"role": "user", "content": f"Based on the following documents, answer the user question. Cite sources.\\n\\n<documents>{context}</documents>\\n\\n<question>{query}</question>"}]}))
            answer = json.loads(llm_resp["body"].read())["content"][0]["text"]
        else:
            answer = "No relevant documents found."
        return {"statusCode": 200, "headers": headers, "body": json.dumps({"answer": answer, "sources": sources, "query": query})}
    except Exception as e:
        return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": str(e)})}

def handle_list(event, headers):
    try:
        items = table.scan(Limit=100).get("Items", [])
        for item in items:
            for k, v in item.items():
                if hasattr(v, "__int__"): item[k] = int(v)
        return {"statusCode": 200, "headers": headers, "body": json.dumps({"documents": items})}
    except Exception as e:
        return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": str(e)})}
'''

    def _create_api_gateway(self) -> apigw.RestApi:
        api = apigw.RestApi(self, "RAGDocumentApi", rest_api_name="a2a-rag-document-api", description="API for A2A RAG document upload and search",
            default_cors_preflight_options=apigw.CorsOptions(allow_origins=apigw.Cors.ALL_ORIGINS, allow_methods=apigw.Cors.ALL_METHODS, allow_headers=["Content-Type", "Authorization"]))
        integration = apigw.LambdaIntegration(self.upload_lambda)
        api.root.add_resource("documents").add_method("GET", integration)
        api.root.add_resource("upload").add_method("POST", integration)
        api.root.add_resource("search").add_method("GET", integration)
        return api

    def _create_ssm_parameters(self) -> None:
        ssm.StringParameter(self, "BucketParam", parameter_name="/a2a_rag/document_bucket", string_value=self.document_bucket.bucket_name)
        ssm.StringParameter(self, "TableParam", parameter_name="/a2a_rag/metadata_table", string_value=self.metadata_table.table_name)
        ssm.StringParameter(self, "OSEndpointParam", parameter_name="/a2a_rag/opensearch_endpoint", string_value=self.collection.attr_collection_endpoint)
        ssm.StringParameter(self, "CollectionParam", parameter_name="/a2a_rag/opensearch_collection_name", string_value=self.collection_name)
        ssm.StringParameter(self, "ApiUrlParam", parameter_name="/a2a_rag/api_url", string_value=self.api.url)

    def _create_outputs(self) -> None:
        cdk.CfnOutput(self, "DocumentBucketName", value=self.document_bucket.bucket_name)
        cdk.CfnOutput(self, "MetadataTableName", value=self.metadata_table.table_name)
        cdk.CfnOutput(self, "OpenSearchEndpoint", value=self.collection.attr_collection_endpoint)
        cdk.CfnOutput(self, "DocumentApiUrl", value=self.api.url)
