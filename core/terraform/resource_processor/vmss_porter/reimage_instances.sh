#!/bin/bash
# Reimage the instances of the resource processor scale set, so that they use the new custom data.
#
# Azure can update an instance of this scale set at the same time as the deploy, for example for an
# automatic OS image upgrade or an automatic repair. Then the instance can stay in the state
# "Updating", and a plain "az vmss reimage" does not return before the Azure CLI login of the deploy
# expires. So this script uses a time limit for each step and for each Azure CLI call. If an instance
# stays busy or fails, the script replaces it: it deletes the instance and scales the scale set back
# to its capacity. A new instance uses the current model of the scale set, so it gets the new custom
# data. Before the reimage, the script replaces all instances, because no instance has the new custom
# data yet.
#
# Environment:
#   VMSS_NAME                     The name of the scale set. Required.
#   RESOURCE_GROUP                The resource group of the scale set. Required.
#   REIMAGE_IDLE_WAIT_SECONDS     Before the reimage, the maximum time to wait until no instance is busy. Default 300.
#   REIMAGE_WAIT_SECONDS          The time limit of the "az vmss reimage" command. Default 720.
#   REIMAGE_REPLACE_WAIT_SECONDS  The maximum time to replace instances. Default 600.
#   REIMAGE_POLL_SECONDS          The time between two checks of the instances, in the wait before the
#                                 reimage and in a replace. Default 20.
#   REIMAGE_CALL_SECONDS          The time limit of each Azure CLI call other than the reimage. Default 60.
#                                 In a wait, a read of the instances also stops at the end of the wait,
#                                 but it gets at least 10 seconds. If a read in a wait fails, the next
#                                 check reads the instances again.
#
# A wait ends at most 10 seconds after its time limit. With the default values, the worst case is
# 1770 seconds (29.5 minutes):
#   310 seconds for the wait before the reimage.
#   720 seconds for the reimage.
#    60 seconds for the read after the reimage.
#   680 seconds for the replace: the 600 seconds of the replace, 10 seconds for the last read of the
#       delete wait, 60 seconds for the scale call, and 10 seconds for the last read after the scale.
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
CALL_SECONDS=${REIMAGE_CALL_SECONDS:-60}
for value in "$IDLE_WAIT_SECONDS" "$REIMAGE_WAIT_SECONDS" "$REPLACE_WAIT_SECONDS" "$POLL_SECONDS" "$CALL_SECONDS"; do
  if [[ ! $value =~ ^[0-9]+$ ]]; then
    echo "Set each REIMAGE_*_SECONDS value to a whole number of seconds." >&2
    exit 1
  fi
done
# With the time limit 0, "timeout" runs the command without a time limit.
if [ "$REIMAGE_WAIT_SECONDS" -eq 0 ] || [ "$CALL_SECONDS" -eq 0 ]; then
  echo "Set REIMAGE_WAIT_SECONDS and REIMAGE_CALL_SECONDS to 1 or more, because \"timeout 0\" has no time limit." >&2
  exit 1
fi
# The shortest time limit of a read in a wait, so that the last read of a wait can end.
MIN_READ_SECONDS=10

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

# Print the number of seconds until the given time, or 0 if the time is in the past.
# Argument: the time, in seconds since the epoch.
seconds_until() {
  local remaining
  remaining=$(($1 - $(date +%s)))
  echo $((remaining < 0 ? 0 : remaining))
}

# Print why an Azure CLI call failed.
# Arguments: the exit code of "timeout", and the time limit of the call in seconds.
call_error() {
  if [ "$1" -eq 124 ]; then
    echo "The time limit of $2 seconds for the Azure CLI call ended."
  else
    echo "The Azure CLI call ended with exit code $1."
  fi
}

# Read the instances into INSTANCES. If the read fails, INSTANCES does not change.
# Argument: the time limit of the read in seconds. The exit code is the exit code of "timeout".
read_instances() {
  local output status=0
  output=$(timeout "$1" az vmss list-instances --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --query "[].[instanceId, provisioningState]" --output tsv) || status=$?
  if [ "$status" -eq 0 ]; then
    INSTANCES=$output
  fi
  return "$status"
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
# Each read stops at the end of the wait, but it gets at least MIN_READ_SECONDS. If a read fails,
# the next check reads the instances again. If the last read fails, the script stops, because the
# state of the instances is not known.
# Arguments: the time limit in seconds, and the check function.
wait_until() {
  local limit=$1 check=$2 deadline remaining read_limit status
  deadline=$(($(date +%s) + limit))
  while true; do
    remaining=$(seconds_until "$deadline")
    read_limit=$((remaining > MIN_READ_SECONDS ? remaining : MIN_READ_SECONDS))
    read_limit=$((read_limit < CALL_SECONDS ? read_limit : CALL_SECONDS))
    status=0
    read_instances "$read_limit" || status=$?
    if [ "$status" -eq 0 ]; then
      log "Instances: $(summary)"
      if "$check"; then
        return 0
      fi
    else
      log "Could not read the instances of the scale set. $(call_error "$status" "$read_limit")"
    fi
    remaining=$(seconds_until "$deadline")
    if [ "$remaining" -eq 0 ]; then
      log "The time limit of ${limit} seconds ended."
      if [ "$status" -ne 0 ]; then
        fail "The last read of the instances failed, so the state of the instances is not known."
      fi
      return 1
    fi
    sleep $((remaining < POLL_SECONDS ? remaining : POLL_SECONDS))
  done
}

# Delete the given instances, then scale the scale set back to its capacity.
# Argument: the IDs of the instances, separated by spaces.
replace_instances() {
  local deadline status=0
  local -a ids
  read -r -a ids <<<"$1"
  DELETED_IDS=$1
  deadline=$(($(date +%s) + REPLACE_WAIT_SECONDS))
  CAPACITY=$(timeout "$CALL_SECONDS" az vmss show --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --query sku.capacity --output tsv) || status=$?
  if [ "$status" -ne 0 ]; then
    fail "Could not read the capacity of the scale set. $(call_error "$status" "$CALL_SECONDS")"
  fi
  if [[ ! $CAPACITY =~ ^[0-9]+$ ]]; then
    fail "Could not read the capacity of the scale set."
  fi

  log "Delete instances ${DELETED_IDS} of scale set ${VMSS_NAME}."
  timeout "$CALL_SECONDS" az vmss delete-instances --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --instance-ids "${ids[@]}" --no-wait --output none || status=$?
  if [ "$status" -ne 0 ]; then
    fail "The delete of instances ${DELETED_IDS} did not start. $(call_error "$status" "$CALL_SECONDS")"
  fi
  if ! wait_until "$(seconds_until "$deadline")" deleted_instances_gone; then
    fail "Azure did not delete instances ${DELETED_IDS} in time."
  fi

  log "Scale the scale set back to ${CAPACITY} instances."
  timeout "$CALL_SECONDS" az vmss scale --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
    --new-capacity "$CAPACITY" --no-wait --output none || status=$?
  if [ "$status" -ne 0 ]; then
    fail "The scale of the scale set did not start. $(call_error "$status" "$CALL_SECONDS")"
  fi
  if ! wait_until "$(seconds_until "$deadline")" replace_complete || [ -n "$(not_succeeded_ids)" ]; then
    fail "After the replace, not all instances are in the state Succeeded."
  fi
  log "The replace is complete. All instances are in the state Succeeded."
}

log "Check the instances of scale set ${VMSS_NAME} before the reimage."
if ! wait_until "$IDLE_WAIT_SECONDS" no_instance_busy || [ -n "$(not_succeeded_ids)" ]; then
  log "An instance is busy or failed. Replace all instances, because no instance has the new custom data yet."
  replace_instances "$(all_ids)"
  exit 0
fi

log "Reimage all instances of scale set ${VMSS_NAME}. The time limit is ${REIMAGE_WAIT_SECONDS} seconds."
# The Azure CLI waits until Azure ends the reimage operation. If the time limit ends first, "timeout"
# stops the Azure CLI and returns exit code 124. The reimage operation can then continue in Azure.
reimage_status=0
timeout "$REIMAGE_WAIT_SECONDS" az vmss reimage --name "$VMSS_NAME" --resource-group "$RESOURCE_GROUP" \
  --output none || reimage_status=$?
if [ "$reimage_status" -eq 124 ]; then
  reimage_result="The reimage did not end in the time limit of ${REIMAGE_WAIT_SECONDS} seconds."
else
  reimage_result="The reimage ended with exit code ${reimage_status}."
fi
log "$reimage_result"

read_status=0
read_instances "$CALL_SECONDS" || read_status=$?
if [ "$read_status" -ne 0 ]; then
  fail "Could not read the instances of the scale set. $(call_error "$read_status" "$CALL_SECONDS")"
fi
log "Instances: $(summary)"
stuck_ids=$(not_succeeded_ids)
if [ -n "$stuck_ids" ]; then
  log "Instances ${stuck_ids} are not in the state Succeeded after the reimage. Replace them."
  replace_instances "$stuck_ids"
  exit 0
fi
if [ "$reimage_status" -ne 0 ]; then
  fail "${reimage_result} All instances are in the state Succeeded, so the script does not replace them. Look for the Azure CLI error above."
fi
log "The reimage is complete. All instances are in the state Succeeded."
