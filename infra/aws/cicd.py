"""GitHub Actions gets AWS credentials by OIDC, with no stored keys: two roles, two jobs.

- triage-gha-ecr-push: the Release workflow's `ecr` job, on a version tag, copies the images to ECR.
- triage-gha-deploy: the Deploy workflow, only inside the GitHub environment "aws-demo", runs
  `cdk deploy`. It may only assume the roles `cdk bootstrap` created; CloudFormation does the
  rest with its own execution role. The environment is where approvals are enforced: it has a
  required reviewer, and accepts deployments from protected branches only.
"""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

# This repo issues GitHub's "immutable subject" claim, which embeds the numeric owner and repo IDs
# (survives renames, blocks a re-created repo of the same name). Read it with:
#   gh api repos/Parzon/triage-assistant/actions/oidc/customization/sub
# A copy of this template has other ids: put its own prefix here (the same command, on its repo),
# or no role can be assumed. scripts/new-project.sh renames the repo, not these numbers.
SUB_PREFIX = "repo:Parzon@124113141/triage-assistant@1381921470"
ISSUER = "token.actions.githubusercontent.com"


class CicdStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, repos: dict, **kw) -> None:
        super().__init__(scope, cid, **kw)
        # AWS trusts GitHub's signed tokens (one provider per account).
        provider = iam.OpenIdConnectProvider(
            self, "GitHub", url=f"https://{ISSUER}", client_ids=["sts.amazonaws.com"]
        )
        role = iam.Role(
            self,
            "EcrPush",
            role_name="triage-gha-ecr-push",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {f"{ISSUER}:aud": "sts.amazonaws.com"},
                    # Only a release tag of this repo: not a PR, not a branch, not a fork. A tag
                    # pushed on any branch runs that branch's release.yml, which could drop its
                    # "tag is on main" check: the repository's tag ruleset, which lets only admins
                    # create v* tags, is what makes this a release (using-this-template.md).
                    "StringLike": {f"{ISSUER}:sub": f"{SUB_PREFIX}:ref:refs/tags/v*"},
                },
            ),
        )
        for repo in repos.values():
            repo.grant_pull_push(role)  # scoped to these repos, not ecr:*
        # GetAuthorizationToken cannot be scoped to a repository.
        role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        CfnOutput(self, "RoleArn", value=role.role_arn)

        deploy = iam.Role(
            self,
            "Deploy",
            role_name="triage-gha-deploy",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        f"{ISSUER}:aud": "sts.amazonaws.com",
                        # A job that names an environment gets this subject instead of its ref.
                        f"{ISSUER}:sub": f"{SUB_PREFIX}:environment:aws-demo",
                    },
                },
            ),
        )
        deploy.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole", "sts:TagSession"],
                resources=[
                    f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-{self.account}-{self.region}"
                ],
            )
        )
        CfnOutput(self, "DeployRoleArn", value=deploy.role_arn)
