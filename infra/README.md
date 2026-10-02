# AWS Deployment

This CDK stack deploys the production API and storage. The existing Amplify app remains connected to GitHub and serves the static pages.

## Prerequisites

- Python 3.12 or newer
- Node.js and the AWS CDK CLI (`npm install -g aws-cdk`)
- AWS credentials configured for the account and region where the Amplify app runs
- The AWS CDK bootstrap stack deployed in that account and region (`cdk bootstrap`)
- A CDK deployer policy attached to the deployment identity, or an equivalent managed deployment role

## Deploy the backend

From PowerShell:

```powershell
cd infra
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cdk.cmd bootstrap
cdk.cmd synth
cdk.cmd deploy
```

The stack creates a private S3 bucket, a retained DynamoDB table, a Cognito user pool/client, an HTTP API backed by Python Lambda, and seeds the bucket from `../pictures/`. S3 and DynamoDB use retain policies so stack removal does not delete user photos or metadata.

The inline policy in `policies/cdk-deployer-inline-policy.json` is for deploying into an already bootstrapped account and updating/rebuilding this Amplify app's `main` branch. Replace `<AWS_ACCOUNT_ID>` and `<AWS_REGION>` before attaching it. Its app ID is taken from the Amplify build log; update it if you use another app. A privileged administrator must perform the one-time `cdk bootstrap` first.

IAM permissions do not limit AWS spend or guarantee Free Tier usage. Configure an AWS Budget and billing alerts separately; domain registration, Route 53 hosted zones, and usage beyond Free Tier allowances can incur charges.

Create the first administrator in the Cognito user pool from the AWS Console. Self-sign-up is disabled. Create username `nrobins` with a permanent password that meets the 14-character Cognito policy. Avoid a temporary password; this custom sign-in page does not implement Cognito's `NEW_PASSWORD_REQUIRED` challenge. The local development password is not reused in AWS.

## Connect Amplify

After deployment, copy the `ApiBaseUrl` stack output. In Amplify Hosting, add it as the `API_BASE_URL` environment variable, then redeploy the GitHub branch. [amplify.yml](../amplify.yml) builds the static HTML pages and writes this API URL into `runtime-config.js`.

The API allows all browser origins by default so the Amplify preview and custom-domain hosts can reach it. Restrict the CDK context value `siteOrigins` to comma-separated site origins before production, for example:

```powershell
cdk.cmd deploy -c "siteOrigins=https://www.example.com,https://main.example.amplifyapp.com"
```

## Optional custom domain

Only use this option when the domain is not already associated with the Amplify app. To let the stack create an Amplify association for your app, pass its Amplify app ID and root domain name:

```powershell
cdk.cmd deploy -c amplifyAppId=d123example -c domainName=example.com -c amplifyBranch=main -c domainPrefix=www
```

Do not add a second association for a domain that is already connected in the Amplify console. The hosted zone must be in the same AWS account for Amplify to configure its Route 53 records automatically. If DNS is hosted elsewhere, use the domain association's certificate validation record and Amplify's displayed CNAME instructions.

## Local development

The repository-root `python app.py` server remains available for local work. Its file-backed data is separate from the deployed S3/DynamoDB backend.
