#!/bin/bash
# Reimage the instances of the resource processor scale set, so that they use the new custom data.
#
# Azure can update an instance of this scale set at the same time as the deploy, for example for an
# automatic OS image upgrade or an automatic repair. Then the instance can stay in the state
# "Updating", and a plain "az vmss reimage" does not return before the Azure CLI login of the deploy
# expires. So this script uses a time limit for each step. If an instance stays busy or fails, the
# script replaces it: it deletes the instance and scales the scale set back to its capacity. A new
# instance uses the current model of the scale set, so it gets the new custom data. Before the
# reimage, the script replaces all instances, because no instance has the new custom data yet.
#
# Environment:
#   VMSS_NAME                     The name of the scale set. Required.
#   RESOURCE_GROUP                The resource group of the scale set. Required.
#   REIMAGE_IDLE_WAIT_SECONDS     Before the reimage, the maximum time to wait until no instance is busy. Default 300.
#   REIMAGE_WAIT_SECONDS          The maximum time to wait for the reimage. Default 720.
#   REIMAGE_REPLACE_WAIT_SECONDS  The maximum time to replace instances. Default 600.
#   REIMAGE_POLL_SECONDS          The time between two checks of the instances. Default 20.
#
# With the default values, the worst case is 27 minutes, plus the time of the Azure CLI calls.
# This is less than the time that the Azure CLI login of a CI deploy stays valid.

set -o errexit
set -o pipefail
set -o nounset

: "${VMSS_NAME:?Set VMSS_NAME to the name of the scale set.}"
: "${RESOURCE_GROUP:?Set RESOURCE_GROUP to the resource group of the scale set.}"
IDLE_WAIT_SECONDS=${REIMAGE_IDLE_WAIT_SECONDS:-300}
REIMAGE_WAIT_SECONDS=${REIMAGE_WAIT_SECONDS:-720}
REPLACE_WAIT_SECONDS=${REIMAGE_REPLACE_WAIT_SECONDS:-600}
POLL_SECONDS=${REIMAGE_POLL_SECONDS:-20}
for value in "$IDLE_WAIT_SECONDS" "$REIMAGE_WAIT_SECONDS" "$REPLACE_WAIT_SECONDS" "$POLL_SECONDS"; do
  if [[ ! $value =~ ^[0-9]+$ ]]; then
    echo "Set each REIMAGE_*_SECONDS value to a whole number of seconds." >&2
    exit 1
  fi
done

START_TIME=$(date +%s)
# The ID and the provisioning state of each instance, from the last check. One instance on each line.
INSTANCES=""
# The capacity of the scale set before a replace, and the IDs of the instances that the replace deletes.
CAPACITY=""
DELETED_IDS=""

log() {
  echo "[$(($(date +%s) - START_TIME))s] $*"
}

fail() {
  echo "[$(($(date +%s) - START_TIME))s] ERROR: $*" >&2
  echo "Make sure that each instance of scale set ${VMSS_NAME} in resource group ${RESOURCE_GROUP} is in the state Succeeded." >&2
  echo "For the repair steps, see the section 'A scale set instance stays in the state Updating' in docs/troubleshooting-faq/troubleshooting-rp.md." >&2
  exit 1
}

read_instances() {
  if ! INSTANCES=$(az vmss list-instances --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --query "[].[instanceId, provisioningState]" --output tsv); then
    fail "Could not read the instances of the scale set."
  fi
}

# Print the IDs of all instances, separated by spaces.
all_ids() {
  awk -F '\t' 'NF { printf "%s%s", sep, $1; sep = " " }' <<<"$INSTANCES"
}

# Print the IDs of the instances that are not in the state Succeeded, separated by spaces.
not_succeeded_ids() {
  awk -F '\t' 'NF && $2 != "Succeeded" { printf "%s%s", sep, $1; sep = " " }' <<<"$INSTANCES"
}

# Print the states of the instances, for example "0=Updating 1=Succeeded".
summary() {
  awk -F '\t' 'NF { printf "%s%s=%s", sep, $1, $2; sep = " " } END { if (sep == "") printf "no instances" }' <<<"$INSTANCES"
}

# A busy instance is not in the state Succeeded and not in the state Failed. For example, it is in
# the state Updating, Creating or Deleting.
no_instance_busy() {
  awk -F '\t' 'NF && $2 != "Succeeded" && $2 != "Failed" { busy = 1 } END { exit busy }' <<<"$INSTANCES"
}

deleted_instances_gone() {
  awk -F '\t' -v ids=" ${DELETED_IDS} " 'NF && index(ids, " " $1 " ") { found = 1 } END { exit found }' <<<"$INSTANCES"
}

replace_complete() {
  no_instance_busy && [ "$(awk -F '\t' 'NF { n++ } END { print n + 0 }' <<<"$INSTANCES")" -eq "$CAPACITY" ]
}

# Check the instances until the check function returns success, or until the time limit ends.
# Arguments: the time limit in seconds, the time before the first check in seconds, and the check function.
wait_until() {
  local limit=$1 delay=$2 check=$3 deadline
  deadline=$(($(date +%s) + limit))
  while true; do
    if [ "$delay" -gt 0 ]; then
      sleep "$delay"
    fi
    delay=$POLL_SECONDS
    read_instances
    log "Instances: $(summary)"
    if "$check"; then
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "The time limit of ${limit} seconds ended."
      return 1
    fi
  done
}

# Delete the given instances, then scale the scale set back to its capacity.
# Argument: the IDs of the instances, separated by spaces.
replace_instances() {
  local deadline remaining
  local -a ids
  read -r -a ids <<<"$1"
  DELETED_IDS=$1
  deadline=$(($(date +%s) + REPLACE_WAIT_SECONDS))
  if ! CAPACITY=$(az vmss show --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --query sku.capacity --output tsv) || [[ ! $CAPACITY =~ ^[0-9]+$ ]]; then
    fail "Could not read the capacity of the scale set."
  fi

  log "Delete instances ${DELETED_IDS} of scale set ${VMSS_NAME}."
  if ! az vmss delete-instances --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --instance-ids "${ids[@]}" --no-wait --output none; then
    fail "The delete of instances ${DELETED_IDS} did not start."
  fi
  if ! wait_until "$REPLACE_WAIT_SECONDS" 0 deleted_instances_gone; then
    fail "Azure did not delete instances ${DELETED_IDS} in time."
  fi

  log "Scale the scale set back to ${CAPACITY} instances."
  if ! az vmss scale --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --new-capacity "$CAPACITY" --no-wait --output none; then
    fail "The scale of the scale set did not start."
  fi
  remaining=$((deadline - $(date +%s)))
  if ! wait_until "$((remaining < 0 ? 0 : remaining))" 0 replace_complete || [ -n "$(not_succeeded_ids)" ]; then
    fail "After the replace, not all instances are in the state Succeeded."
  fi
  log "The replace is complete. All instances are in the state Succeeded."
}

log "Check the instances of scale set ${VMSS_NAME} before the reimage."
if ! wait_until "$IDLE_WAIT_SECONDS" 0 no_instance_busy || [ -n "$(not_succeeded_ids)" ]; then
  log "An instance is busy or failed. Replace all instances, because no instance has the new custom data yet."
  replace_instances "$(all_ids)"
  exit 0
fi

log "Reimage all instances of scale set ${VMSS_NAME}."
if ! az vmss reimage --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" --no-wait --output none; then
  fail "The reimage did not start."
fi
# Azure sets the instances to the state Updating when it accepts the reimage.
# The first check comes one poll interval after the request.
if wait_until "$REIMAGE_WAIT_SECONDS" "$POLL_SECONDS" no_instance_busy && [ -z "$(not_succeeded_ids)" ]; then
  log "The reimage is complete. All instances are in the state Succeeded."
  exit 0
fi
stuck_ids=$(not_succeeded_ids)
log "Instances ${stuck_ids} did not complete the reimage. Replace them."
replace_instances "$stuck_ids"
