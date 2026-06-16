#!/usr/bin/env python3
"""
CDK stacks — BPEL2Orkes infrastructure

Two parallel compute options during the serverless migration:
  - Bpel2OrkesService (ECS Fargate + ALB) — original, bills for uptime
  - Bpel2OrkesServerless (Lambda + API Gateway REST API v1 + WAFv2) — bills
    per-request, ~$0 idle cost. This is the migration target; ECS stacks are
    torn down once it's verified stable in production.

Both reuse the existing ACM certs / custom domains, so OAuth redirect URIs
never need to change — only the DNS CNAME target moves between them.

Usage:
  pip install aws-cdk-lib constructs
  cdk deploy --all
  cdk deploy Bpel2OrkesStagingServerless   # staging serverless stack only
  cdk deploy Bpel2OrkesProductionServerless

Note: DNS (CNAME in Spaceship) must be added/updated manually after deploy.
      CDK outputs the domain target at the end of each serverless stack deploy.
"""

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    SecretValue,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_certificatemanager as acm,
    aws_elasticloadbalancingv2 as elbv2,
    aws_secretsmanager as secretsmanager,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_wafv2 as wafv2,
    aws_ecr_assets as ecr_assets,
    CfnOutput,
    Duration,
    RemovalPolicy,
)
from constructs import Construct

ACCOUNT = "835422347653"
REGION  = "ap-southeast-2"
IMAGE_TAG_STAGING = "staging-latest"
IMAGE_TAG_PROD    = "prod-latest"

# Existing ACM certs (DNS-validated, ISSUED) — reused by both the ECS and the
# serverless stacks since the public domain names don't change in the migration.
CERT_ARN_STAGING = "arn:aws:acm:ap-southeast-2:835422347653:certificate/f2e98ff2-ae48-4d5c-b03d-7f69c40f9484"
CERT_ARN_PROD    = "arn:aws:acm:ap-southeast-2:835422347653:certificate/6f8411b4-c816-4a39-b670-99765f26eeb1"


class EcrStack(Stack):
    """Single ECR repository shared by staging and prod."""

    def __init__(self, scope: Construct, **kwargs):
        super().__init__(scope, "Bpel2OrkesEcr", **kwargs)

        self.repo = ecr.Repository(
            self, "Repo",
            repository_name="bpel2orkes",
            removal_policy=RemovalPolicy.RETAIN,   # never delete images on cdk destroy
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 20 staging images",
                    tag_prefix_list=["staging-"],
                    max_image_count=20,
                ),
                ecr.LifecycleRule(
                    description="Keep last 10 prod images",
                    tag_prefix_list=["prod-"],
                    max_image_count=10,
                ),
            ],
        )

        CfnOutput(self, "EcrUri", value=self.repo.repository_uri,
                  description="ECR repository URI — use in docker push commands")


class Bpel2OrkesService(Stack):
    """
    ECS Fargate service with ALB for one environment.
    ECS Express Mode provisions ALB + auto-scaling automatically.
    """

    def __init__(
        self,
        scope: Construct,
        env_name: str,           # "staging" or "production"
        image_tag: str,
        ecr_repo: ecr.Repository,
        cpu: int = 512,
        memory: int = 1024,
        desired_count: int = 1,
        **kwargs,
    ):
        stack_id = f"Bpel2Orkes{env_name.capitalize()}"
        super().__init__(scope, stack_id, **kwargs)

        cluster = ecs.Cluster(
            self, "Cluster",
            cluster_name=f"bpel2orkes-{env_name}",
            enable_fargate_capacity_providers=True,
        )

        image = ecs.ContainerImage.from_ecr_repository(ecr_repo, tag=image_tag)

        # OAuth + session secrets — created out-of-band via scripts/push-secrets.sh
        oauth_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "OAuthSecret", f"bpel2orkes/{env_name}/oauth",
        )

        # ApplicationLoadBalancedFargateService is the CDK pattern closest to
        # ECS Express Mode — Express Mode wraps the same underlying resources.
        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "Service",
            cluster=cluster,
            service_name=f"bpel2orkes-{env_name}",
            cpu=cpu,
            memory_limit_mib=memory,
            desired_count=desired_count,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=image,
                container_port=8000,
                environment={
                    "BPEL2ORKES_ENV": env_name,
                    "BPEL_MAX_SIZE_MB": "5" if env_name == "production" else "10",
                },
                secrets={
                    "GOOGLE_CLIENT_ID": ecs.Secret.from_secrets_manager(oauth_secret, "GOOGLE_CLIENT_ID"),
                    "GOOGLE_CLIENT_SECRET": ecs.Secret.from_secrets_manager(oauth_secret, "GOOGLE_CLIENT_SECRET"),
                    "GITHUB_CLIENT_ID": ecs.Secret.from_secrets_manager(oauth_secret, "GITHUB_CLIENT_ID"),
                    "GITHUB_CLIENT_SECRET": ecs.Secret.from_secrets_manager(oauth_secret, "GITHUB_CLIENT_SECRET"),
                    "SESSION_SECRET": ecs.Secret.from_secrets_manager(oauth_secret, "SESSION_SECRET"),
                },
            ),
            public_load_balancer=True,
            listener_port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificate=acm.Certificate.from_certificate_arn(
                self, "Cert",
                CERT_ARN_STAGING if env_name == "staging" else CERT_ARN_PROD,
            ),
        )

        # DynamoDB access for the users table (auth.py reads/writes via boto3)
        users_table_arn = f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/bpel2orkes-users-{env_name}"
        service.task_definition.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query",
                ],
                resources=[users_table_arn, f"{users_table_arn}/index/*"],
            )
        )

        # Health check — ECS uses this to know the container is ready
        service.target_group.configure_health_check(
            path="/api/v1/health",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        # Auto-scaling — scale out at 70% CPU, back in when below 30%
        scaling = service.service.auto_scale_task_count(
            min_capacity=1,
            max_capacity=4 if env_name == "production" else 2,
        )
        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.seconds(120),
            scale_out_cooldown=Duration.seconds(30),
        )

        # Output the ALB DNS name — paste this into Spaceship as CNAME value
        subdomain = "staging.bpel2orkes" if env_name == "staging" else "bpel2orkes"
        CfnOutput(
            self, "AlbDnsName",
            value=service.load_balancer.load_balancer_dns_name,
            description=(
                f"ALB DNS name → add CNAME in Spaceship: "
                f"{subdomain}.kshetra.studio → <this value>"
            ),
        )
        CfnOutput(
            self, "AlbUrl",
            value=f"http://{service.load_balancer.load_balancer_dns_name}",
            description="Direct ALB URL (HTTP) — use to test before DNS is set up",
        )


class Bpel2OrkesServerless(Stack):
    """
    Lambda + API Gateway (REST API v1) + WAFv2 — serverless replacement for
    Bpel2OrkesService. Bills per-request instead of per-uptime: ~$0 when idle.

    Reuses the existing custom domain + ACM cert, so OAuth redirect URIs and
    the Stripe webhook URL (once configured) never need to change — only the
    DNS CNAME target changes, from the ALB DNS name to this API's regional
    domain target.

    REST API v1 (not HTTP API v2) is deliberate: confirmed empirically that
    AWS WAFv2 only supports direct association with REST API v1 stages (and
    ALB/AppSync/Cognito/AppRunner/VerifiedAccess/CloudFront) — HTTP API v2 is
    not a valid WAF association target. REST API v1 + Lambda proxy + Mangum
    is also the most battle-tested combination in the FastAPI-on-Lambda
    ecosystem, so this isn't a compromise.
    """

    def __init__(
        self,
        scope: Construct,
        env_name: str,           # "staging" or "production"
        cert_arn: str,
        rate_limit: int = 300,   # requests per 5-minute window per IP before WAF blocks
        **kwargs,
    ):
        stack_id = f"Bpel2Orkes{env_name.capitalize()}Serverless"
        super().__init__(scope, stack_id, **kwargs)

        oauth_secret_arn = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:bpel2orkes/{env_name}/oauth"

        def secret_env(json_field: str) -> str:
            return SecretValue.secrets_manager(oauth_secret_arn, json_field=json_field).to_string()

        fn = _lambda.DockerImageFunction(
            self, "Function",
            function_name=f"bpel2orkes-{env_name}",
            code=_lambda.DockerImageCode.from_image_asset(
                directory=".",
                file="Dockerfile.lambda",
                platform=ecr_assets.Platform.LINUX_ARM64,
            ),
            architecture=_lambda.Architecture.ARM_64,
            memory_size=1024,
            timeout=Duration.seconds(30),
            environment={
                "BPEL2ORKES_ENV": env_name,
                "BPEL_MAX_SIZE_MB": "5" if env_name == "production" else "10",
                "GOOGLE_CLIENT_ID": secret_env("GOOGLE_CLIENT_ID"),
                "GOOGLE_CLIENT_SECRET": secret_env("GOOGLE_CLIENT_SECRET"),
                "GITHUB_CLIENT_ID": secret_env("GITHUB_CLIENT_ID"),
                "GITHUB_CLIENT_SECRET": secret_env("GITHUB_CLIENT_SECRET"),
                "SESSION_SECRET": secret_env("SESSION_SECRET"),
            },
        )

        # DynamoDB access for the users table — same actions as the ECS task role
        users_table_arn = f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/bpel2orkes-users-{env_name}"
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"],
                resources=[users_table_arn, f"{users_table_arn}/index/*"],
            )
        )

        domain_name = "staging.bpel2orkes.kshetra.studio" if env_name == "staging" else "bpel2orkes.kshetra.studio"

        api = apigw.LambdaRestApi(
            self, "Api",
            rest_api_name=f"bpel2orkes-{env_name}",
            handler=fn,
            proxy=True,
            # .bpel isn't a recognised MIME extension, so StaticFiles serves it as
            # application/octet-stream — Mangum correctly base64-encodes that as binary,
            # but API Gateway only decodes base64 responses for content types listed
            # here. "*/*" covers all of them (JSON/text responses are unaffected, since
            # Mangum's isBase64Encoded flag per-response still controls actual decoding).
            binary_media_types=["*/*"],
            domain_name=apigw.DomainNameOptions(
                domain_name=domain_name,
                certificate=acm.Certificate.from_certificate_arn(self, "Cert", cert_arn),
            ),
        )

        # WAFv2 — managed rule set for baseline protection + a rate-based rule
        # that is the actual circuit breaker on worst-case (abuse/DDoS) cost.
        web_acl = wafv2.CfnWebACL(
            self, "WebAcl",
            name=f"bpel2orkes-{env_name}",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                sampled_requests_enabled=True,
                cloud_watch_metrics_enabled=True,
                metric_name=f"bpel2orkes{env_name.capitalize()}WebAcl",
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWS-CommonRuleSet",
                    priority=0,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                            # Two confirmed false positives on legitimate BPEL/XML request bodies
                            # (caught via WAF sampled requests during staging/production testing):
                            #  - CrossSiteScripting_BODY: XML tags/content resemble XSS patterns
                            #  - SizeRestrictions_BODY: WAF's default 8KB body inspection limit
                            #    blocks any BPEL file larger than that (3 of our 4 sample files
                            #    are >8KB; real customer BPEL is routinely tens of KB). The app
                            #    already enforces its own size cap (BPEL_MAX_SIZE_MB, 5-10MB) via
                            #    limit_request_size in api.py, so WAF's stricter default here is
                            #    redundant, not protective — Count instead of Block.
                            rule_action_overrides=[
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="CrossSiteScripting_BODY",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(count={}),
                                ),
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="SizeRestrictions_BODY",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(count={}),
                                ),
                            ],
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name=f"bpel2orkes{env_name.capitalize()}CommonRuleSet",
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitPerIp",
                    priority=1,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=rate_limit,
                            aggregate_key_type="IP",
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name=f"bpel2orkes{env_name.capitalize()}RateLimit",
                    ),
                ),
            ],
        )

        stage_arn = (
            f"arn:aws:apigateway:{REGION}::/restapis/{api.rest_api_id}"
            f"/stages/{api.deployment_stage.stage_name}"
        )
        wafv2.CfnWebACLAssociation(
            self, "WebAclAssociation",
            web_acl_arn=web_acl.attr_arn,
            resource_arn=stage_arn,
        )

        CfnOutput(
            self, "ApiInvokeUrl",
            value=api.url,
            description="Default API Gateway invoke URL — use to validate before DNS cutover",
        )
        CfnOutput(
            self, "ApiDomainTarget",
            value=api.domain_name.domain_name_alias_domain_name,
            description=(
                f"API Gateway regional domain target → add/update CNAME in Spaceship: "
                f"{domain_name} → <this value>"
            ),
        )


# ── App ────────────────────────────────────────────────────────────────────────

env = cdk.Environment(account=ACCOUNT, region=REGION)

app = cdk.App()

ecr_stack = EcrStack(app, env=env)

Bpel2OrkesService(
    app,
    env_name="staging",
    image_tag=IMAGE_TAG_STAGING,
    ecr_repo=ecr_stack.repo,
    cpu=256,
    memory=512,
    desired_count=1,
    env=env,
)

Bpel2OrkesService(
    app,
    env_name="production",
    image_tag=IMAGE_TAG_PROD,
    ecr_repo=ecr_stack.repo,
    cpu=512,
    memory=1024,
    desired_count=2,
    env=env,
)

# Serverless replacement stacks — deployed alongside the ECS stacks above
# during migration. ECS stacks are torn down once these are verified stable
# in production (see BACKLOG.md serverless migration item).
Bpel2OrkesServerless(
    app,
    env_name="staging",
    cert_arn=CERT_ARN_STAGING,
    env=env,
)

Bpel2OrkesServerless(
    app,
    env_name="production",
    cert_arn=CERT_ARN_PROD,
    env=env,
)

app.synth()
