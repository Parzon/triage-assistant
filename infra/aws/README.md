# AWS (CDK, Python)

The stack on AWS, as code. A demo/staging shape, exercised end to end in a throwaway account;
the production differences are marked in the code (`production:` comments).

| Stack | What | Cost while up (ap-south-1) |
|---|---|---|
| `triage-registry` | ECR repositories for api, web, edge: immutable tags, scan on push | storage only |
| `triage-cicd` | GitHub OIDC provider; roles for the Release `ecr` job and the Deploy workflow | free |
| `triage-network` | VPC, 2 AZs, public + isolated subnets, no NAT gateway | free |
| `triage-vm` | one VM running compose (`docs/runbooks/demo-vm.md`), no SSH: SSM only | ~$0.10/h |
| `triage-app` | ECS Fargate (api, web) + RDS Postgres + ALB | ~$0.11/h |

```
internet -> ALB :80 -+- /api/metrics, /api/alerts/alertmanager -> 404
                     +- /api/*  -> rewritten to /* -> api task (target health: /ready)
                     +- else    -> web task, nginx serving the built app (target health: /healthz)

api task (containers share localhost):
  dbinit, migrate   init containers: the app's least-privileged DB role, then migrations as owner
  valkey, mock-llm  sidecars
  api               starts after both init containers succeed and the sidecars are healthy
```

Why the ALB routes `/api`: the web image's nginx resolves `api:8010` through Docker's DNS
(`127.0.0.11`), which Fargate does not have. On ECS, nginx only serves files.

## Use

Needs AWS credentials (`aws login` or `aws sso login`), Node 22 and uv.

```
cd infra/aws
uv sync
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
npx aws-cdk@2.1143.0 bootstrap                      # once per account and region
npx aws-cdk@2.1143.0 deploy triage-registry triage-cicd triage-network
./ops.sh deploy 0.8.1                               # triage-app at a released version
./ops.sh status                                     # what runs, rollout state
./ops.sh logs migrate                               # one container's log (CloudWatch)
npx aws-cdk@2.1143.0 destroy triage-app --exclusively --force
./ops.sh leftovers                                  # anything still costing money
```

Deploys after setup go through `.github/workflows/deploy.yml`: automatically after a release,
or by hand with any released version (that is the rollback). It needs the repository variable
`AWS_DEPLOY_ROLE_ARN` (the `triage-cicd.DeployRoleArn` output) and a GitHub environment named
`aws-demo`.

Without a domain there is no TLS and no identity provider: the api runs with
`PROD_CHECKS_WAIVED=insecure_cookies,mock_model` and reports `identity_provider: degraded`.
For real use: an ACM certificate on an HTTPS listener, the organisation's OIDC provider, a model
provider, Multi-AZ RDS with deletion protection.
