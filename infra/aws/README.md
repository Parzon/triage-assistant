# AWS (CDK, Python)

The stack on AWS, as code. A demo/staging shape, exercised end to end in a throwaway account;
the production differences are marked in the code (`production:` comments).

| Stack | What | Cost while up (ap-south-1) |
|---|---|---|
| `triage-registry` | ECR repositories for api, web, edge: immutable tags, scan on push | storage only |
| `triage-cicd` | GitHub OIDC provider; roles for the Release `ecr` job and the Deploy workflow | free |
| `triage-network` | VPC, 2 AZs, public + isolated subnets, no NAT gateway | free |
| `triage-vm` | one VM running compose (`docs/runbooks/demo-vm.md`), no SSH: SSM only | ~$0.10/h |
| `triage-app` | ECS Fargate (2 api, 2 web) + RDS Postgres + ElastiCache Valkey + ALB | ~$0.17/h |

```
internet -> ALB :80 -+- /api/metrics, /api/alerts/alertmanager -> 404
                     +- /api/*  -> rewritten to /* -> api task (target health: /health)
                     +- else    -> web task, nginx serving the built app (target health: /healthz)

api task (containers share localhost):
  dbinit, migrate   init containers: the app's least-privileged DB role, then migrations as owner
  mock-llm          sidecar
  api               starts after both init containers succeed and the sidecar is healthy
Valkey: one ElastiCache node shared by every api task (rate limits must be counted once).
2 tasks per service, one per AZ; the api scales to 4 on requests per target.
```

Why the ALB routes `/api`: the web image's nginx resolves `api:8010` through Docker's DNS
(`127.0.0.11`), which Fargate does not have. On ECS, nginx only serves files.

Why the ALB checks `/health`, not `/ready`: ECS replaces every task its load balancer calls
unhealthy. With `/ready` (which needs the database), stopping RDS made ECS replace api tasks
whose replacements could not start; with `/health`, a database outage leaves the tasks alone and
the api answers 503 until the database is back.

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

Before destroying `triage-app`, delete the secret `AWS_DEPLOY_ROLE_ARN`: otherwise the next
release tag re-creates the whole stack. If a delete ends in `DELETE_FAILED` on resources that are already
gone, finish it with `aws cloudformation delete-stack --stack-name triage-app --retain-resources
<their logical IDs>`.

Deploys after setup go through `.github/workflows/deploy.yml`: automatically after a release,
or by hand with any released version (that is the rollback). It needs the repository secret
`AWS_DEPLOY_ROLE_ARN` (the `triage-cicd.DeployRoleArn` output) and a GitHub environment named
`aws-demo` with a required reviewer. The Release workflow's copy to ECR needs the secret
`AWS_ECR_ROLE_ARN` (`triage-cicd.RoleArn`). Secrets, not variables: an ARN names the account, and
GitHub masks only secrets in public logs; `mask-aws-account-id` covers the lines that print the
registry or CDK's output.

Timeouts, from the outside in (each must outlast the one before it, or a reused or drained
connection fails): the ALB keeps idle connections 130 s, the api 140 s (`GUNICORN_KEEPALIVE`) and
nginx 140 s. On a deploy, ECS takes a task out of the ALB and waits up to 130 s
(deregistration delay, the longest chat stream) before SIGTERM; gunicorn then has 110 s, inside
the 120 s stop timeout. `FORWARDED_ALLOW_IPS` is the VPC's range, the ALB's addresses: the ALB
appends the client to `X-Forwarded-For`, so `*` would let a client choose its own address.

Without a domain there is no TLS and no identity provider: the api runs with
`PROD_CHECKS_WAIVED=insecure_cookies,mock_model` and reports `identity_provider: degraded`.
For real use: an ACM certificate on an HTTPS listener, the organisation's OIDC provider, a model
provider, and every `production:` comment in `service.py` (Multi-AZ RDS with deletion protection
and longer backups, private subnets, a WAF, a read-only root filesystem, ECS Exec limited, log
retention, secrets retained).
