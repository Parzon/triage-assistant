import os

import aws_cdk as cdk
from cicd import CicdStack
from network import NetworkStack
from registry import RegistryStack
from service import AppStack
from vm import VmStack

app = cdk.App()
env = cdk.Environment(account=os.environ["CDK_DEFAULT_ACCOUNT"], region="ap-south-1")
registry = RegistryStack(app, "triage-registry", env=env)  # Stage 1
CicdStack(app, "triage-cicd", repos=registry.repos, env=env)  # Stage 2
network = NetworkStack(app, "triage-network", env=env)  # shared by 3-5, free
VmStack(app, "triage-vm", vpc=network.vpc, repos=registry.repos, env=env)  # Stage 3
AppStack(
    app, "triage-app", vpc=network.vpc, db_subnets=network.db_subnets, repos=registry.repos, env=env
)  # Stages 4-5
app.synth()
