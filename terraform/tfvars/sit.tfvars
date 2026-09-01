# Legacy PO.DAAC SIT.
# Sensitive values are in secrets/sit.tfvars, which is not committed.

ses_region      = "us-east-1"
log_level       = "DEBUG"
max_daily_warns = 3

# The deployed stack runs UNMUTED and really does send daily email.
muted_mode = false
