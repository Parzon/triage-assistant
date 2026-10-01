"""Stages 4-5: the app on ECS Fargate behind an ALB, with RDS Postgres.

  internet -> ALB :80 -+- /api/metrics, /api/alerts/alertmanager -> 404 (as nginx does)
                       +- /api/*  -> rewrite to /* -> api task :8010   (target health: /health)
                       +- else    -> web task :8080 (nginx, static)   (target health: /healthz)

  api task (one network namespace, so the containers talk over localhost):
    dbinit   (exits)   creates the least-privileged app role in RDS, idempotently
    migrate  (exits)   alembic as the schema owner; a code-only rollback when the DB is ahead
    mock-llm sidecar   stands in for the model provider
    api      starts only after dbinit and migrate succeed and the sidecar is healthy

  Valkey (cache, rate limits): ElastiCache, shared by every api task. `-c cache=sidecar` puts a
  private Valkey container in each task instead: free, but each task then counts rate limits on
  its own, so two tasks allow twice the limit (measured: 60 logins/min through a limit of 30).

  2 api and 2 web tasks, one per AZ; the api scales out to 4 on requests per target.

Deploy a version: `cdk deploy triage-app -c tag=0.8.1`. A failing release is rolled back by the
ECS deployment circuit breaker; `-c drill=bad-config` ships one on purpose.

Cost while up (ap-south-1, approx.): Fargate 2 api x 0.5 vCPU/1 GB $0.052/h + 2 web x 0.25 vCPU/
0.5 GB $0.026/h, ALB $0.024/h, RDS db.t4g.micro $0.022/h, ElastiCache cache.t4g.micro $0.016/h,
public IPv4 6 x $0.005/h, 4 secrets ~$0.002/h. About $0.17/hour: tear it down after each session.
"""

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecr_assets as assets,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_elasticache as elasticache,
)
from aws_cdk import (
    aws_elasticloadbalancingv2 as elbv2,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_rds as rds,
)
from aws_cdk import (
    aws_secretsmanager as sm,
)
from constructs import Construct

HERE = Path(__file__).parent
REPO = HERE.parents[1]  # the repository root: the mock LLM is built from it, it is not released
DB_NAME = "triage"
X86 = ecs.RuntimePlatform(
    cpu_architecture=ecs.CpuArchitecture.X86_64,
    operating_system_family=ecs.OperatingSystemFamily.LINUX,
)


def script(name: str) -> str:
    return (HERE / "container" / name).read_text()


def generated_secret(scope: Construct, cid: str, username: str | None = None) -> sm.Secret:
    # No punctuation: the api builds a URL from these, and @ : / % would need encoding.
    return sm.Secret(
        scope,
        cid,
        generate_secret_string=sm.SecretStringGenerator(
            secret_string_template=json.dumps({"username": username} if username else {}),
            generate_string_key="password",
            exclude_punctuation=True,
            password_length=32,
        ),
        removal_policy=RemovalPolicy.DESTROY,
    )


def secret(s: sm.ISecret, field: str) -> ecs.Secret:
    # Injected by ECS at start from Secrets Manager: never in the task definition or the template.
    return ecs.Secret.from_secrets_manager(s, field)


class AppStack(Stack):
    def __init__(
        self, scope: Construct, cid: str, *, vpc: ec2.IVpc, db_subnets: list, repos: dict, **kw
    ) -> None:
        super().__init__(scope, cid, **kw)
        tag = self.node.try_get_context("tag") or "0.8.1"
        drill = self.node.try_get_context("drill")
        cache = self.node.try_get_context("cache") or "elasticache"
        public = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)

        # --- Who may talk to whom: security groups reference each other, not IP ranges ---------
        alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=vpc, description="internet to ALB port 80")
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")
        api_sg = ec2.SecurityGroup(self, "ApiSg", vpc=vpc, description="ALB to api tasks port 8010")
        api_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8010), "from the ALB only")
        web_sg = ec2.SecurityGroup(self, "WebSg", vpc=vpc, description="ALB to web tasks port 8080")
        web_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8080), "from the ALB only")
        db_sg = ec2.SecurityGroup(
            self,
            "DbSg",
            vpc=vpc,
            description="api tasks to Postgres port 5432",
            allow_all_outbound=False,
        )
        db_sg.add_ingress_rule(api_sg, ec2.Port.tcp(5432), "from api tasks only")
        cache_sg = ec2.SecurityGroup(
            self,
            "CacheSg",
            vpc=vpc,
            description="api tasks to Valkey port 6379",
            allow_all_outbound=False,
        )
        cache_sg.add_ingress_rule(api_sg, ec2.Port.tcp(6379), "from api tasks only")

        # --- Secrets: generated by AWS, read by ECS at task start ------------------------------
        owner = generated_secret(self, "DbOwnerSecret", "triage_owner")  # RDS master: migrations
        app_user = generated_secret(self, "DbAppSecret", "triage_app")  # what the api connects as
        llm_key = generated_secret(self, "LlmKeySecret")
        oidc_secret = generated_secret(self, "OidcClientSecret")

        # --- Database: managed Postgres in subnets with no internet route ---------------------
        db = rds.DatabaseInstance(
            self,
            "Db",
            instance_identifier="triage-db",
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_17),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=db_subnets),
            availability_zone="ap-south-1c",  # where RDS had capacity (network.py)
            security_groups=[db_sg],
            credentials=rds.Credentials.from_secret(owner),
            database_name=DB_NAME,
            allocated_storage=20,
            storage_type=rds.StorageType.GP2,
            storage_encrypted=True,
            multi_az=False,  # production: True (a standby in the other AZ, ~2x the price)
            backup_retention=Duration.days(1),
            delete_automated_backups=True,
            deletion_protection=False,  # production: True
            removal_policy=RemovalPolicy.DESTROY,  # production: SNAPSHOT
        )
        db_env = {
            "DB_HOST": db.db_instance_endpoint_address,
            "DB_PORT": db.db_instance_endpoint_port,
            "DB_NAME": DB_NAME,
            "PGSSLMODE": "require",  # RDS Postgres 17 refuses unencrypted connections
        }

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, cluster_name="triage")
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=vpc,
            vpc_subnets=public,
            internet_facing=True,
            security_group=alb_sg,
            idle_timeout=Duration.seconds(130),  # > the api's 120 s chat stream
        )
        public_url = f"http://{alb.load_balancer_dns_name}"

        def log_group(name: str) -> logs.LogGroup:
            return logs.LogGroup(
                self,
                f"{name.title()}Logs",
                log_group_name=f"/triage/{name}",
                retention=logs.RetentionDays.THREE_DAYS,
                removal_policy=RemovalPolicy.DESTROY,
            )

        api_logs, web_logs = log_group("api"), log_group("web")

        def to(group: logs.ILogGroup, prefix: str) -> ecs.LogDriver:
            return ecs.LogDrivers.aws_logs(stream_prefix=prefix, log_group=group)

        # --- api task -------------------------------------------------------------------------
        api_def = ecs.FargateTaskDefinition(
            self,
            "ApiTask",
            family="triage-api",
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=X86,
        )
        api_image = ecs.ContainerImage.from_ecr_repository(repos["api"], tag)

        dbinit = api_def.add_container(
            "dbinit",
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/docker/library/postgres:17-alpine"
            ),
            essential=False,
            entry_point=["sh", "-c"],
            command=[script("dbinit.sh")],
            environment={
                "PGHOST": db.db_instance_endpoint_address,
                "PGDATABASE": DB_NAME,
                "PGSSLMODE": "require",
            },
            secrets={
                "PGUSER": secret(owner, "username"),
                "PGPASSWORD": secret(owner, "password"),
                "APP_DB_PASSWORD": secret(app_user, "password"),
            },
            memory_reservation_mib=64,
            logging=to(api_logs, "dbinit"),
        )
        migrate = api_def.add_container(
            "migrate",
            image=api_image,
            essential=False,
            entry_point=["sh", "-c"],
            command=[script("migrate.sh")],
            environment=db_env,
            secrets={
                "DB_OWNER_USER": secret(owner, "username"),
                "DB_OWNER_PASSWORD": secret(owner, "password"),
            },
            memory_reservation_mib=128,
            logging=to(api_logs, "migrate"),
        )
        migrate.add_container_dependencies(
            ecs.ContainerDependency(
                container=dbinit, condition=ecs.ContainerDependencyCondition.SUCCESS
            )
        )
        if cache == "sidecar":
            redis_url = "redis://localhost:6379/0"
            valkey = api_def.add_container(
                "valkey",
                image=ecs.ContainerImage.from_registry("public.ecr.aws/valkey/valkey:8.1-alpine"),
                command=[
                    "valkey-server",
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--maxmemory",
                    "128mb",
                    "--maxmemory-policy",
                    "allkeys-lru",
                ],
                health_check=ecs.HealthCheck(
                    command=["CMD", "valkey-cli", "ping"],
                    interval=Duration.seconds(5),
                    timeout=Duration.seconds(3),
                ),
                memory_reservation_mib=64,
                logging=to(api_logs, "valkey"),
            )
        else:
            # One Valkey for every task: rate limits and sessions are counted once, globally.
            # TLS in transit (rediss://), encrypted at rest, reachable only from the api's SG.
            # Production: a replica in the other AZ with automatic failover, and an AUTH user.
            subnets = elasticache.CfnSubnetGroup(
                self,
                "CacheSubnets",
                description="triage Valkey",
                subnet_ids=[sn.subnet_id for sn in db_subnets],
            )
            valkey_rg = elasticache.CfnReplicationGroup(
                self,
                "Cache",
                replication_group_description="triage rate limits and cache",
                engine="valkey",
                engine_version="8.1",  # the version compose runs (valkey:8.1-alpine)
                cache_node_type="cache.t4g.micro",
                num_cache_clusters=1,
                automatic_failover_enabled=False,
                transit_encryption_enabled=True,
                at_rest_encryption_enabled=True,
                cache_subnet_group_name=subnets.ref,
                security_group_ids=[cache_sg.security_group_id],
            )
            redis_url = (
                f"rediss://{valkey_rg.attr_primary_end_point_address}:"
                f"{valkey_rg.attr_primary_end_point_port}/0"
            )
        mock_llm = api_def.add_container(
            "mock-llm",
            image=ecs.ContainerImage.from_asset(
                str(REPO / "tools/mock-llm"), platform=assets.Platform.LINUX_AMD64
            ),
            health_check=ecs.HealthCheck(
                command=[
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen("
                    "'http://127.0.0.1:8020/health', timeout=2).status == 200 else 1)",
                ],
                interval=Duration.seconds(5),
                timeout=Duration.seconds(3),
            ),
            memory_reservation_mib=64,
            logging=to(api_logs, "mock-llm"),
        )
        # The production guard (ADR-0023) refuses plain HTTP and the mock model unless waived by
        # name. The drill drops one waiver: the new api exits on boot, like a bad config release.
        waived = "insecure_cookies" if drill == "bad-config" else "insecure_cookies,mock_model"
        no_caps = ecs.LinuxParameters(self, "ApiLinux")
        no_caps.drop_capabilities(ecs.Capability.ALL)
        api = api_def.add_container(
            "api",
            image=api_image,
            entry_point=["sh", "-c"],
            command=[script("api.sh")],
            port_mappings=[ecs.PortMapping(container_port=8010)],
            environment={
                **db_env,
                "APP_ENV": "prod",
                "APP_VERSION": tag,
                "ROOT_PATH": "/api",  # the ALB strips /api, as nginx does in compose
                "FORWARDED_ALLOW_IPS": "*",  # only the ALB can reach the task (security group)
                "WEB_CONCURRENCY": "2",
                "DB_POOL_SIZE": "5",  # x 2 workers: well inside a db.t4g.micro's ~80 connections
                "REDIS_URL": redis_url,
                "LLM_BASE_URL": "http://localhost:8020/v1",
                "LLM_MODEL": "mock-1",
                "PUBLIC_URL": public_url,
                # No identity provider on AWS in this exercise: sign-in is off, /ready says
                # "identity_provider: degraded" (a soft dependency), everything else works.
                "OIDC_ISSUER": "https://idp.invalid/realms/triage",
                "OIDC_CLIENT_ID": "triage-web",
                "PROD_CHECKS_WAIVED": waived,
            },
            secrets={
                "DB_USER": secret(app_user, "username"),
                "DB_PASSWORD": secret(app_user, "password"),
                "LLM_API_KEY": secret(llm_key, "password"),
                "OIDC_CLIENT_SECRET": secret(oidc_secret, "password"),
            },
            # Liveness, per container: is the process alive? ECS replaces the task if not.
            health_check=ecs.HealthCheck(
                command=[
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen("
                    "'http://127.0.0.1:8010/health', timeout=2).status == 200 else 1)",
                ],
                interval=Duration.seconds(10),
                timeout=Duration.seconds(3),
                start_period=Duration.seconds(20),
            ),
            linux_parameters=no_caps,
            stop_timeout=Duration.seconds(120),  # let chat streams finish (Fargate's maximum)
            logging=to(api_logs, "api"),
        )
        api.add_container_dependencies(
            ecs.ContainerDependency(
                container=migrate, condition=ecs.ContainerDependencyCondition.SUCCESS
            ),
            ecs.ContainerDependency(
                container=mock_llm, condition=ecs.ContainerDependencyCondition.HEALTHY
            ),
        )
        if cache == "sidecar":
            api.add_container_dependencies(
                ecs.ContainerDependency(
                    container=valkey, condition=ecs.ContainerDependencyCondition.HEALTHY
                )
            )

        # --- web task -------------------------------------------------------------------------
        web_def = ecs.FargateTaskDefinition(
            self,
            "WebTask",
            family="triage-web",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=X86,
        )
        web_caps = ecs.LinuxParameters(self, "WebLinux")
        web_caps.drop_capabilities(ecs.Capability.ALL)
        web_def.add_container(
            "web",
            image=ecs.ContainerImage.from_ecr_repository(repos["web"], tag),
            port_mappings=[ecs.PortMapping(container_port=8080)],
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    "wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1",
                ],
                interval=Duration.seconds(10),
                timeout=Duration.seconds(3),
            ),
            linux_parameters=web_caps,
            logging=to(web_logs, "web"),
        )

        # --- services: rolling deploys, rolled back automatically when tasks keep failing -------
        def service(
            name: str, task: ecs.FargateTaskDefinition, sg: ec2.SecurityGroup
        ) -> ecs.FargateService:
            # Two tasks, spread over both AZs: losing a task or a zone is not an outage.
            return ecs.FargateService(
                self,
                f"{name.title()}Service",
                service_name=name,
                cluster=cluster,
                task_definition=task,
                desired_count=2,
                # A public IP to pull images without a NAT gateway; the SG admits only the ALB.
                assign_public_ip=True,
                vpc_subnets=public,
                security_groups=[sg],
                min_healthy_percent=100,  # start a new task before stopping an old one
                # 150% of 2 = one extra task at a time: new api tasks start one after another,
                # so their migrate init containers never race each other.
                max_healthy_percent=150,
                circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
                health_check_grace_period=Duration.seconds(120),
                enable_execute_command=True,  # `aws ecs execute-command`: a shell without SSH
            )

        api_svc = service("api", api_def, api_sg)
        web_svc = service("web", web_def, web_sg)

        # --- the ALB: the edge's job (routing) moves here ---------------------------------------
        listener = alb.add_listener("Http", port=80, open=False)
        listener.add_targets(
            "Web",
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[web_svc],
            health_check=elbv2.HealthCheck(
                path="/healthz", interval=Duration.seconds(10), healthy_threshold_count=2
            ),
            deregistration_delay=Duration.seconds(10),
        )
        api_tg = elbv2.ApplicationTargetGroup(
            self,
            "ApiTg",
            vpc=vpc,
            port=8010,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            targets=[api_svc.load_balancer_target(container_name="api", container_port=8010)],
            # Liveness, not readiness. ECS replaces any task its load balancer calls unhealthy,
            # so a deep check (/ready: the database) turns a database outage into task churn:
            # measured, every api task marked unhealthy and replacements stuck in dbinit. Kubernetes
            # separates the two (readinessProbe /ready, livenessProbe /health); ECS has one signal.
            # Readiness at start is covered by the init containers: migrate succeeds only if the
            # database answers. A database outage still shows: /api/ready 503 and the 5xx alarm.
            health_check=elbv2.HealthCheck(
                path="/health", interval=Duration.seconds(10), healthy_threshold_count=2
            ),
            deregistration_delay=Duration.seconds(30),
        )
        elbv2.ApplicationListenerRule(
            self,
            "Internal",
            listener=listener,
            priority=5,
            conditions=[
                elbv2.ListenerCondition.path_patterns(["/api/metrics", "/api/alerts/alertmanager"])
            ],
            action=elbv2.ListenerAction.fixed_response(
                404, content_type="application/json", message_body='{"error":"not_found"}'
            ),
        )
        api_rule = elbv2.ApplicationListenerRule(
            self,
            "Api",
            listener=listener,
            priority=10,
            conditions=[elbv2.ListenerCondition.path_patterns(["/api/*"])],
            action=elbv2.ListenerAction.forward([api_tg]),
        )
        # URL rewrite (ALB, 2025): /api/ready -> /ready. Not in the CDK L2 yet, so set it on the
        # CloudFormation resource directly (an "escape hatch").
        api_rule.node.default_child.add_property_override(
            "Transforms",
            [
                {
                    "Type": "url-rewrite",
                    "UrlRewriteConfig": {"Rewrites": [{"Regex": "^/api/(.*)$", "Replace": "/$1"}]},
                }
            ],
        )

        # --- autoscaling: more api tasks while each serves over 1,000 requests a minute --------
        # Bounded by the database: 4 tasks x 2 workers x DB_POOL_SIZE 5 = 40 connections, inside
        # a db.t4g.micro's ~80. Scale-in waits longer than scale-out, so it does not flap.
        scaling = api_svc.auto_scale_task_count(min_capacity=2, max_capacity=4)
        scaling.scale_on_request_count(
            "Requests",
            requests_per_target=1000,
            target_group=api_tg,
            scale_out_cooldown=Duration.seconds(60),
            scale_in_cooldown=Duration.seconds(300),
        )

        # --- alarms (the first 10 are free); production: an SNS topic to the on-call ------------
        cw.Alarm(
            self,
            "ApiUnhealthy",
            alarm_name="triage-api-unhealthy-targets",
            metric=api_tg.metrics.unhealthy_host_count(period=Duration.minutes(1)),
            threshold=0,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=2,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        cw.Alarm(
            self,
            "Api5xx",
            alarm_name="triage-api-5xx",
            metric=api_tg.metrics.http_code_target(
                elbv2.HttpCodeTarget.TARGET_5XX_COUNT, period=Duration.minutes(1)
            ),
            threshold=5,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )

        CfnOutput(self, "Url", value=public_url)
        CfnOutput(self, "ImageTag", value=tag)
        CfnOutput(self, "DbEndpoint", value=db.db_instance_endpoint_address)
