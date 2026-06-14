#!/usr/bin/env python3
"""
CDK stack — BPEL2Orkes on ECS Express (Fargate + ALB)

Deploys:
  - ECR repository
  - ECS cluster (Express mode)
  - Fargate service + ALB for staging
  - Fargate service + ALB for production
  - ACM certificate for bpel2orkes.kshetra.studio + staging subdomain

Usage:
  pip install aws-cdk-lib constructs
  cdk deploy --all
  cdk deploy Bpel2OrkesStaging   # staging only
  cdk deploy Bpel2OrkesProd      # prod only

Note: DNS (CNAME in Spaceship) must be added manually after first deploy.
      CDK outputs the ALB DNS name at the end of each stack deploy.
"""

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_certificatemanager as acm,
    aws_elasticloadbalancingv2 as elbv2,
    CfnOutput,
    Duration,
    RemovalPolicy,
)
from constructs import Construct

ACCOUNT = "835422347653"
REGION  = "ap-southeast-2"
IMAGE_TAG_STAGING = "staging-latest"
IMAGE_TAG_PROD    = "prod-latest"


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
            ),
            public_load_balancer=True,
            listener_port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            # HTTPS: uncomment after ACM cert is issued and DNS is pointed at the ALB
            # listener_port=443,
            # protocol=elbv2.ApplicationProtocol.HTTPS,
            # certificate=acm.Certificate.from_certificate_arn(
            #     self, "Cert", "arn:aws:acm:ap-southeast-2:835422347653:certificate/YOUR-CERT-ARN"
            # ),
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

app.synth()
