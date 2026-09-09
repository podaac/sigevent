# Non-sensitive per-venue settings. This file is committed, and so are the
# filled-in copies made from it.
#
# Sensitive values (i.e.,AWS account identifiers and notification recipients) are
# in secrets/<venue>.tfvars and are not committed. See secrets/example.tfvars.
# Copy to tfvars/<venue>.tfvars and adjust. bin/deploy.sh loads both files.

# Region the SES sender identity lives in. Independent of the region the rest of
# the service deploys to, which comes from the venue's .env file.
ses_region = "us-east-1"

# DEBUG, INFO, WARN, ERROR.
log_level = "INFO"

# When true, messages are still written to CloudWatch but no email is sent, and
# the daily report generator and its schedule are not created. Start new venues
# muted and flip only once the send path has been verified.
muted_mode = true

# Maximum WARN notifications per collection, per day. ERROR is never rate limited.
max_daily_warns = 3
