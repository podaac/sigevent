#!/usr/bin/env bash
set -eo pipefail

if [ ! $# -eq 1 ]
then
    echo "usage: $(caller | cut -d' ' -f2) venue"
    exit 1
fi

VENUE=$1

export TF_VENUE="$VENUE"

# Cleared before sourcing so a value left over from an earlier run in the same
# shell cannot leak into this one.
unset SIGEVENT_TF_ENVIRONMENT

source "$(dirname $BASH_SOURCE)/../environments/$VENUE.env"

export TF_IN_AUTOMATION=true  # https://www.terraform.io/cli/config/environment-variables#tf_in_automation
export TF_INPUT=false  # https://www.terraform.io/cli/config/environment-variables#tf_input

export TF_VAR_region="$REGION"
export TF_VAR_environment="${SIGEVENT_TF_ENVIRONMENT:-$VENUE}"

TF_VAR_FILES=(-var-file="tfvars/${TF_VENUE}.tfvars")

if [ -f "secrets/${TF_VENUE}.tfvars" ]; then
    TF_VAR_FILES+=(-var-file="secrets/${TF_VENUE}.tfvars")
else
    echo "warning: secrets/${TF_VENUE}.tfvars not found." >&2
    echo "         ses_sender_arn and notification_emails have no defaults and must" >&2
    echo "         be supplied another way. See secrets/example.tfvars." >&2
fi

# Print out what venue actually resolves to before touching anything.
ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "<unavailable>")

cat <<EOF

  venue:       ${TF_VENUE}
  environment: ${TF_VAR_environment}
  account:     ${ACCOUNT}
  region:      ${TF_VAR_region}
  backend:     ${BUCKET}

EOF

terraform init -reconfigure -backend-config="bucket=$BUCKET" -backend-config="region=$REGION"
