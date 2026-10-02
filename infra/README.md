# AWS Deployment

This CDK stack deploys the production API and storage. The existing Amplify app remains connected to GitHub and serves the static pages.

## Prerequisites

- Python 3.12 or newer
- Node.js and the AWS CDK CLI (`npm install -g aws-cdk`)
- AWS credentials configured for the account and region where the Amplify app runs
- The AWS CDK bootstrap stack deployed in that account and region (`cdk bootstrap`)

## Deploy the backend

From PowerShell:

```powershell
cd infra
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cdk synth
cdk deploy
```

The stack creates a private S3 bucket, a retained DynamoDB table, a Cognito user pool/client, an HTTP API backed by Python Lambda, and seeds the bucket from `../pictures/`. S3 and DynamoDB use retain policies so stack removal does not delete user photos or metadata.

Create the first administrator in the Cognito user pool from the AWS Console. Self-sign-up is disabled. Use username `nrobins` and set a new strong password there; the old local development password is not reused in AWS.

## Connect Amplify

After deployment, copy the `ApiBaseUrl` stack output. In Amplify Hosting, add it as the `API_BASE_URL` environment variable, then redeploy the GitHub branch. [amplify.yml](../amplify.yml) builds the static HTML pages and writes this API URL into `runtime-config.js`.

The API allows all browser origins by default so the Amplify preview and custom-domain hosts can reach it. Restrict the CDK context value `siteOrigins` to comma-separated site origins before production, for example:

```powershell
cdk deploy -c siteOrigins=https://www.example.com,https://main.example.amplifyapp.com
```

## Optional custom domain

To let the stack associate your existing Amplify app with its Route 53 domain, pass its Amplify app ID and root domain name:

```powershell
cdk deploy -c amplifyAppId=d123example -c domainName=example.com -c amplifyBranch=main -c domainPrefix=www
```

The hosted zone must be in the same AWS account for Amplify to configure its Route 53 records automatically. If DNS is hosted elsewhere, use the domain association's certificate validation record and Amplify's displayed CNAME instructions.

## Local development

The repository-root `python app.py` server remains available for local work. Its file-backed data is separate from the deployed S3/DynamoDB backend.
