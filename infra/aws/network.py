"""Stages 3-5: one VPC shared by the VM and the ECS stack. A VPC itself costs nothing.

Two availability zones (an ALB and an RDS subnet group both require two). Public subnets hold
the load balancer, the VM and the Fargate tasks; isolated subnets (no route to the internet in
either direction) hold the database. No NAT gateway: nothing in a private subnet needs to reach
out, and one would cost ~$0.06/hour plus data. The trade-off: tasks get public IPs (each
$0.005/hour) and rely on security groups, not subnets, to stay unreachable.
"""

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

# A constant, not read from the VPC: the app stack trusts proxies in this range
# (FORWARDED_ALLOW_IPS), and a token would make it import from this stack.
VPC_CIDR = "10.20.0.0/16"


class NetworkStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw) -> None:
        super().__init__(scope, cid, **kw)
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(VPC_CIDR),
            # Named, not looked up: synth works without credentials.
            availability_zones=["ap-south-1a", "ap-south-1b"],
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="data", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24
                ),
            ],
        )
        # Added after RDS refused 1a and 1b: "no subnets exist in Availability Zones with
        # sufficient capacity ... for db.t4g.micro; choose from ap-south-1c". Capacity is per AZ
        # and changes; an extra isolated subnet (main route table: local routes only) gives the
        # database a third zone without touching the subnets the VM already uses.
        self.data_1c = ec2.Subnet(
            self,
            "Data1c",
            vpc_id=self.vpc.vpc_id,
            availability_zone="ap-south-1c",
            cidr_block="10.20.10.0/24",
        )
        self.db_subnets = [*self.vpc.isolated_subnets, self.data_1c]
