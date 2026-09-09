# Legacy PO.DAAC SIT.
# Sensitive values are in secrets/sit.tfvars, which is not committed.

ses_region      = "us-east-1"
log_level       = "DEBUG"
max_daily_warns = 3

# The deployed stack runs UNMUTED and really does send daily email.
muted_mode = false

# SigEvent Storm Detection error notifiction volume
max_daily_errors                   = 20
storm_threshold                    = 10
storm_window_minutes               = 5
storm_summary_max_interval_minutes = 60
