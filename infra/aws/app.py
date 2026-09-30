import aws_cdk as cdk

from registry import RegistryStack

app = cdk.App()
RegistryStack(app, "triage-registry", env=cdk.Environment(region="ap-south-1"))
app.synth()
