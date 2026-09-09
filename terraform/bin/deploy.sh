#!/usr/bin/env bash
set -eo pipefail

source "$(dirname $BASH_SOURCE)/config.sh"

if [ ! -f tfplan ]; then
    echo "error: no saved plan at $(pwd)/tfplan" >&2
    echo "       run ./bin/plan.sh ${TF_VENUE}, review the output, then re-run this." >&2
    exit 1
fi

PLANNED_VENUE=$(cat tfplan.venue 2>/dev/null || echo "")
if [ "$PLANNED_VENUE" != "$TF_VENUE" ]; then
    echo "error: tfplan was built for venue '${PLANNED_VENUE:-<unknown>}', not '${TF_VENUE}'." >&2
    echo "       applying it here would target the wrong deployment." >&2
    echo "       run ./bin/plan.sh ${TF_VENUE} to replace it." >&2
    exit 1
fi

terraform apply tfplan

rm -f tfplan tfplan.venue
