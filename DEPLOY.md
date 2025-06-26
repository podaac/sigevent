# Deploying Sigevent

### Package code

You can build the AWS Lambda zip files by running the following command from the root of the repository:

```bash
./deploy/build_lambda_zips.sh 
```

This will generate local file dist/lambda.zip. The Terraform will pull this local zip and redeploy if the checksum has changed. 

## Terraform

Terraform 1.5.7 is required for this deployment. 

You can deploy sigevent to any NGAP IA venue by running the following commands:

```bash
cd terraform
./bin/deploy.sh --app-version 0.1.0 --tf-venue sndbx
```

Where `--app-version` should be the semver of the sigevent application, and `--tf-venue` is the venue being deployed to.
