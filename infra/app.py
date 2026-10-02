import os

import aws_cdk as cdk

from favsite_stack import FavSiteStack

application = cdk.App()
FavSiteStack(
    application,
    "UrFavSiteStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION"),
    ),
)
application.synth()
