from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_amplify as amplify,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_authorizers as apigw_authorizers,
    aws_apigatewayv2_integrations as integrations,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
)
from constructs import Construct

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FavSiteStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        configured_origins = self.node.try_get_context("siteOrigins") or "*"
        allowed_origins = [
            origin.strip()
            for origin in configured_origins.split(",")
            if origin.strip()
        ]
        pictures_bucket = s3.Bucket(
            self,
            "PicturesBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.POST,
                        s3.HttpMethods.GET,
                        s3.HttpMethods.HEAD,
                    ],
                    allowed_origins=allowed_origins,
                    allowed_headers=["*"],
                    exposed_headers=["ETag"],
                    max_age=3600,
                )
            ],
            lifecycle_rules=[
                s3.LifecycleRule(
                    abort_incomplete_multipart_upload_after=Duration.days(2)
                ),
                s3.LifecycleRule(
                    id="ExpirePendingUploads",
                    prefix="pending/",
                    expiration=Duration.days(1),
                ),
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )
        configured_origins = self.node.try_get_context("siteOrigins") or "*"
        allowed_origins = [
            origin.strip()
            for origin in configured_origins.split(",")
            if origin.strip()
        ]
        metadata_table = dynamodb.Table(
            self,
            "PhotoMetadata",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="expiresAt",
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )
        s3_deployment.BucketDeployment(
            self,
            "SeedPictureLibrary",
            sources=[s3_deployment.Source.asset(str(PROJECT_ROOT / "pictures"))],
            destination_bucket=pictures_bucket,
            destination_key_prefix="library",
            prune=True,
            retain_on_delete=True,
        )

        admin_pool = cognito.UserPool(
            self,
            "AdminUserPool",
            sign_in_aliases=cognito.SignInAliases(username=True),
            self_sign_up_enabled=False,
            password_policy=cognito.PasswordPolicy(
                min_length=14,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        admin_client = admin_pool.add_client(
            "AdminWebClient",
            auth_flows=cognito.AuthFlow(user_password=True),
            generate_secret=False,
            prevent_user_existence_errors=True,
            access_token_validity=Duration.minutes(60),
            id_token_validity=Duration.minutes(60),
            refresh_token_validity=Duration.days(1),
        )

        handler = lambda_.Function(
            self,
            "PhotoApiHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(str(Path(__file__).parent / "lambda")),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                "PICTURES_BUCKET": pictures_bucket.bucket_name,
                "METADATA_TABLE": metadata_table.table_name,
                "USER_POOL_CLIENT_ID": admin_client.user_pool_client_id,
                "DEFAULT_PICTURE_KEY": "library/portrait-01.jpg",
            },
        )
        pictures_bucket.grant_read_write(handler)
        metadata_table.grant_read_write_data(handler)

        api = apigwv2.HttpApi(
            self,
            "PhotoHttpApi",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=allowed_origins,
                allow_methods=[
                    apigwv2.CorsHttpMethod.GET,
                    apigwv2.CorsHttpMethod.POST,
                    apigwv2.CorsHttpMethod.OPTIONS,
                ],
                allow_headers=["authorization", "content-type"],
                max_age=Duration.hours(1),
            ),
        )
        integration = integrations.HttpLambdaIntegration("PhotoApiIntegration", handler)
        admin_authorizer = apigw_authorizers.HttpJwtAuthorizer(
            "AdminJwtAuthorizer",
            f"https://cognito-idp.{self.region}.{self.url_suffix}/{admin_pool.user_pool_id}",
            jwt_audience=[admin_client.user_pool_client_id],
        )
        for path, methods in (
            ("/api/admin/pictures", [apigwv2.HttpMethod.GET]),
            ("/api/admin/select", [apigwv2.HttpMethod.POST]),
            ("/api/admin/upload-url", [apigwv2.HttpMethod.POST]),
            ("/api/admin/upload-complete", [apigwv2.HttpMethod.POST]),
            ("/api/admin/logout", [apigwv2.HttpMethod.POST]),
        ):
            api.add_routes(
                path=path,
                methods=methods,
                integration=integration,
                authorizer=admin_authorizer,
            )
        api.add_routes(
            path="/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integration,
        )
        api.add_routes(
            path="/",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integration,
        )
        if api.default_stage is not None:
            default_stage = api.default_stage.node.default_child
            if isinstance(default_stage, apigwv2.CfnStage):
                default_stage.add_property_override(
                    "DefaultRouteSettings.ThrottlingBurstLimit", 30
                )
                default_stage.add_property_override(
                    "DefaultRouteSettings.ThrottlingRateLimit", 15
                )

        CfnOutput(self, "ApiBaseUrl", value=api.api_endpoint)
        CfnOutput(self, "PicturesBucketName", value=pictures_bucket.bucket_name)
        CfnOutput(self, "MetadataTableName", value=metadata_table.table_name)
        CfnOutput(self, "AdminUserPoolId", value=admin_pool.user_pool_id)
        CfnOutput(self, "AdminUserPoolClientId", value=admin_client.user_pool_client_id)
        CfnOutput(
            self,
            "AmplifyBuildSetting",
            value="Set Amplify environment variable API_BASE_URL to the ApiBaseUrl output.",
        )

        amplify_app_id = self.node.try_get_context("amplifyAppId")
        domain_name = self.node.try_get_context("domainName")
        if bool(amplify_app_id) != bool(domain_name):
            raise ValueError(
                "Set both amplifyAppId and domainName context values to manage a custom domain."
            )
        if amplify_app_id and domain_name:
            branch_name = self.node.try_get_context("amplifyBranch") or "main"
            domain_prefix = self.node.try_get_context("domainPrefix") or "www"
            domain = amplify.CfnDomain(
                self,
                "AmplifyDomainAssociation",
                app_id=amplify_app_id,
                domain_name=domain_name,
                sub_domain_settings=[
                    amplify.CfnDomain.SubDomainSettingProperty(
                        prefix=domain_prefix,
                        branch_name=branch_name,
                    )
                ],
            )
            CfnOutput(self, "AmplifyDomainArn", value=domain.attr_arn)
            CfnOutput(
                self,
                "AmplifyCertificateRecord",
                value=domain.attr_certificate_record,
                description="DNS validation record if Route 53 is not in the Amplify app account.",
            )
            CfnOutput(
                self,
                "AmplifyDomainNote",
                value="Route 53 hosted in this AWS account is configured by Amplify during domain association.",
            )
