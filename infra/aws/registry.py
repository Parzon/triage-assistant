"""Stage 1: one ECR repository per image the release workflow publishes."""
from aws_cdk import Duration, RemovalPolicy, Stack, aws_ecr as ecr
from constructs import Construct

IMAGES = ["api", "web", "edge"]


class RegistryStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw) -> None:
        super().__init__(scope, cid, **kw)
        for name in IMAGES:
            ecr.Repository(
                self,
                name,
                repository_name=f"triage-assistant-{name}",
                image_scan_on_push=True,
                # Tags are immutable: a tag, once pushed, always means the same bytes.
                image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                # Throwaway account: `cdk destroy` must leave nothing behind.
                removal_policy=RemovalPolicy.DESTROY,
                empty_on_delete=True,
                lifecycle_rules=[
                    ecr.LifecycleRule(
                        description="expire untagged (dropped multi-arch children are kept by their index)",
                        tag_status=ecr.TagStatus.UNTAGGED,
                        max_image_age=Duration.days(7),
                    ),
                    ecr.LifecycleRule(description="keep the last 20 images", max_image_count=20),
                ],
            )
