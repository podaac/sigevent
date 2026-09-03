# Legacy PO.DAAC UAT.
# Sensitive values are in secrets/uat.tfvars, which is not committed.
ses_region      = "us-east-1"
log_level       = "INFO"
max_daily_warns = 3

# MUTED since 2026-08-31, after a looping CC UAT workflow produced 12,399 ERROR
# events in ~25 minutes and sigevent attempted to email every one of them.
#
# Keep this `true` until ERROR capping and storm detection ship AND the operator
# distribution group is confirmed fixed. Un-muting is a deliberate, separate step.
muted_mode      = true
