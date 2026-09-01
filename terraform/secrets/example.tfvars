# Sensitive per-venue values.
#
# This template is committed. Filled-in copies at secrets/<venue>.tfvars are NOT.
# see .gitignore. Never commit one: these values identify AWS accounts and name
# real people.
#
# Copy to secrets/<venue>.tfvars and fill in. bin/deploy.sh loads it alongside
# tfvars/<venue>.tfvars.

# ARN of the verified SES identity used as the sender. 
# the ARN's region must match ses_region.
ses_sender_arn = "arn:aws:ses:<ses-region>:<identity-owner-account>:identity/<domain>"

# List of who receives notifications. 
# These must be a verified identity; an unverified one fails the send,
# which is not caught, so the message is retried and eventually dead lettered.
notification_emails = ["first.last@example.gov"]

# AWS accounts permitted to publish to this deployment's SNS topic. Leave empty when
# the publishers run in the same account as the deployment, that account is added
# to the topic policy automatically and must not be listed here.
authorized_accounts = []
