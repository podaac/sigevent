#!/usr/bin/env bash
set -eo pipefail

source "$(dirname $BASH_SOURCE)/config.sh"

terraform plan -out=tfplan "${TF_VAR_FILES[@]}"

# deploy.sh can refuse a plan built for a different venue
echo "$TF_VENUE" > tfplan.venue

cat <<EOF

Plan saved to tfplan.

Review the output above, then apply it with:

    ./bin/deploy.sh ${TF_VENUE}

EOF