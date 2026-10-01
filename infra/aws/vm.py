"""Stage 3: the one-VM deployment, done the way a platform team would accept it.

Compared with a hand-built VM:
- no SSH key and no port 22: operators use SSM Session Manager / Run Command, which the
  instance role allows and CloudTrail records (who ran what, when);
- images come from ECR, authenticated by the instance role through the ECR credential helper,
  so no registry password exists anywhere;
- IMDSv2 only, encrypted disk, a security group that admits HTTP and nothing else;
- the host is built by cloud-init from code (adapted from infra/vm/cloud-init.yaml in the repo),
  so a replacement VM is one `cdk deploy` away.

Size: the repo's runbook asks for 2 vCPU / 4 GB minimum without monitoring. A Free Plan account
may only launch Free Tier-eligible types (t3.medium was refused), and c7i-flex.large (2 vCPU,
4 GB, x86) is one: `aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true`.
"""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from constructs import Construct

UBUNTU = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"

CLOUD_CONFIG = """#cloud-config
users:
  - default
  - name: deploy
    shell: /bin/bash
    lock_passwd: true
package_update: true
package_upgrade: true
apt:
  sources:
    docker.list:
      source: "deb [arch=amd64] https://download.docker.com/linux/ubuntu $RELEASE stable"
      keyid: 9DC858229FC7DD38854AE2D88D81803C0EBFCD88
packages: [docker-ce, docker-ce-cli, containerd.io, docker-buildx-plugin, docker-compose-plugin,
           amazon-ecr-credential-helper, make, git, curl, jq, unattended-upgrades]
write_files:
  - path: /etc/docker/daemon.json
    content: |
      {"log-driver": "json-file", "live-restore": true,
       "log-opts": {"max-size": "10m", "max-file": "3"}}
  # Docker asks the helper for ECR credentials; the helper uses the instance role.
  # defer: written at the end of boot, once the deploy user exists.
  - path: /home/deploy/.docker/config.json
    owner: "deploy:deploy"
    defer: true
    content: |
      {"credHelpers": {"__REGISTRY__": "ecr-login"}}
runcmd:
  - systemctl restart docker
  - usermod -aG docker deploy
  - install -d -o deploy -g deploy /srv/triage-assistant
  - sudo -u deploy git clone https://github.com/Parzon/triage-assistant.git /srv/triage-assistant
  - sudo -u deploy make -C /srv/triage-assistant .env
  - >-
    T=$(curl -sX PUT http://169.254.169.254/latest/api/token
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60');
    IP=$(curl -s -H "X-aws-ec2-metadata-token: $T" http://169.254.169.254/latest/meta-data/public-ipv4);
    WAIVE=insecure_cookies,mock_model,demo_identity_provider;
    cd /srv/triage-assistant &&
    sed -i -e "s|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=mock|"
    -e "s|^IMAGE_PREFIX=.*|IMAGE_PREFIX=__REGISTRY__/triage-assistant|"
    -e "s|^HTTP_BIND=.*|HTTP_BIND=0.0.0.0|" -e "s|^HTTP_PORT=.*|HTTP_PORT=80|"
    -e "s|^PUBLIC_URL=.*|PUBLIC_URL=http://$IP|"
    -e "s|^LLM_API_KEY=.*|LLM_API_KEY=$(openssl rand -hex 24)|"
    -e "s|^PROD_CHECKS_WAIVED=.*|PROD_CHECKS_WAIVED=$WAIVE|" .env
final_message: "triage-assistant host ready after $UPTIME s"
"""


class VmStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, vpc: ec2.IVpc, repos: dict, **kw) -> None:
        super().__init__(scope, cid, **kw)
        registry = f"{self.account}.dkr.ecr.{self.region}.amazonaws.com"

        role = iam.Role(
            self,
            "Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ],
        )
        for repo in repos.values():
            repo.grant_pull(role)

        sg = ec2.SecurityGroup(self, "Sg", vpc=vpc, description="HTTP in; no SSH")
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "the app")

        vm = ec2.Instance(
            self,
            "Vm",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            associate_public_ip_address=True,
            instance_type=ec2.InstanceType("c7i-flex.large"),
            machine_image=ec2.MachineImage.from_ssm_parameter(UBUNTU),
            role=role,
            security_group=sg,
            require_imdsv2=True,
            # cloud-init runs on first boot only: a changed host recipe means a new host.
            user_data_causes_replacement=True,
            user_data=ec2.UserData.custom(CLOUD_CONFIG.replace("__REGISTRY__", registry)),
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/sda1",
                    volume=ec2.BlockDeviceVolume.ebs(
                        20, encrypted=True, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )
        CfnOutput(self, "InstanceId", value=vm.instance_id)
        CfnOutput(self, "Url", value=f"http://{vm.instance_public_ip}")
