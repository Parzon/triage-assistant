#!/usr/bin/env bash
# Operator helpers for the AWS stages. Usage: ./ops.sh <command> [args]
#   vm-run '<shell>'      run a command on the VM through SSM (no SSH), print its output
#   deploy <tag> [drill]  deploy the ECS stack at an image tag (drill: bad-config)
#   status                ECS services: running task definition, desired/running, rollout state
#   probe [seconds]       hit the ALB every 0.5 s and count non-200 answers (zero-downtime check)
#   logs <container>      last 30 api-task log lines of one container (api, migrate, dbinit...)
#   cli <command> [args]  the operator CLI (app.cli) in a running api task: assistant, revoke...
#   cost                  month-to-date cost by service (Cost Explorer: $0.01 per request)
#   leftovers             anything still running that costs money
set -euo pipefail
# Credentials come from the environment: AWS_PROFILE, or `aws login` / `aws sso login`.
export AWS_REGION=${AWS_REGION:-ap-south-1} AWS_PAGER=""
# The same CDK CLI as the README and the Deploy workflow: a deploy must not change with npm's
# newest release.
CDK=aws-cdk@2.1143.0
export CDK_DEFAULT_ACCOUNT=${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}
cd "$(dirname "$0")"
out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }

case ${1:-} in
vm-run)
  id=$(out triage-vm InstanceId)
  # The output is also kept in /triage/vm-operations (vm.py): CloudTrail records only that
  # the command was sent.
  cmd=$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript \
        --parameters "$(jq -n --arg c "$2" '{commands: [$c], executionTimeout: ["1800"]}')" \
        --cloud-watch-output-config CloudWatchOutputEnabled=true,CloudWatchLogGroupName=/triage/vm-operations \
        --query Command.CommandId --output text)
  until s=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" --query Status --output text 2>/dev/null) \
        && [[ $s != Pending && $s != InProgress && $s != Delayed ]]; do sleep 3; done
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  echo "[ssm] $s"; [[ $s == Success ]] ;;
deploy)
  npx --yes "$CDK" deploy triage-app --exclusively --require-approval never --output cdk.out.app \
    -c tag="$2" ${3:+-c drill=$3} ;;
status)
  aws ecs describe-services --cluster triage --services api web --query \
    'services[].{svc:serviceName,taskdef:taskDefinition,desired:desiredCount,running:runningCount,rollout:deployments[0].rolloutState,reason:deployments[0].rolloutStateReason}' \
    --output table ;;
probe)
  url=$(out triage-app Url); end=$((SECONDS + ${2:-60})); ok=0; bad=0
  while [ $SECONDS -lt $end ]; do
    c=$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$url/api/ready" || true)
    if [ "$c" = 200 ]; then ok=$((ok+1)); else bad=$((bad+1)); echo "$(date -u +%T) HTTP $c"; fi
    sleep 0.5
  done
  echo "probe: $ok ok, $bad failed" ;;
cli)
  shift; (($#)) || { echo "usage: ops.sh cli <command> [args], e.g. cli assistant --off --reason '...'" >&2; exit 2; }
  task=$(aws ecs list-tasks --cluster triage --service-name api --desired-status RUNNING --query 'taskArns[0]' --output text)
  [[ $task == arn:* ]] || { echo "no running api task" >&2; exit 1; }
  # container/api.sh composes DATABASE_URL before it starts gunicorn, so a shell opened with ECS
  # Exec has the parts but not the URL: compose it the same way. The arguments travel base64,
  # NUL-separated, so spaces and quotes in them (a reason) arrive as typed.
  args=$(printf '%s\0' "$@" | base64 -w0)
  aws ecs execute-command --cluster triage --task "$task" --container api --interactive --command \
    "sh -c 'export DATABASE_URL=postgresql://\${DB_USER}:\${DB_PASSWORD}@\${DB_HOST}:\${DB_PORT}/\${DB_NAME}; echo $args | base64 -d | xargs -0 python -m app.cli'" ;;
logs)
  aws logs tail /triage/api --since 30m --log-stream-name-prefix "${2:-api}" --format short | tail -30 ;;
cost)
  aws ce get-cost-and-usage --region us-east-1 --granularity MONTHLY --metrics UnblendedCost \
    --time-period "Start=$(date -u +%Y-%m-01),End=$(date -u -d tomorrow +%F)" --group-by Type=DIMENSION,Key=SERVICE \
    --query 'ResultsByTime[].Groups[?Metrics.UnblendedCost.Amount>`0.001`].[Keys[0],Metrics.UnblendedCost.Amount]' --output text ;;
leftovers)
  echo "EC2:";  aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name]' --output text
  echo "RDS:";  aws rds describe-db-instances --query 'DBInstances[].[DBInstanceIdentifier,DBInstanceStatus]' --output text
  echo "ElastiCache:"; aws elasticache describe-replication-groups --query 'ReplicationGroups[].[ReplicationGroupId,Status]' --output text
  echo "ECS:";  aws ecs list-clusters --query clusterArns --output text
  echo "ALB:";  aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName' --output text
  echo "NAT:";  aws ec2 describe-nat-gateways --filter Name=state,Values=available,pending --query 'NatGateways[].NatGatewayId' --output text
  echo "EIP:";  aws ec2 describe-addresses --query 'Addresses[].PublicIp' --output text
  echo "EBS:";  aws ec2 describe-volumes --query 'Volumes[].[VolumeId,Size,State]' --output text
  echo "Secrets:"; aws secretsmanager list-secrets --query 'SecretList[].Name' --output text
  # Every state but deleted: a stack stuck in DELETE_FAILED still holds what it could not delete.
  echo "Stacks:"; aws cloudformation list-stacks --query 'StackSummaries[?StackStatus!=`DELETE_COMPLETE`].[StackName,StackStatus]' --output text ;;
*) sed -n '2,10p' "$0"; exit 2 ;;
esac
