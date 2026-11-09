#!/usr/bin/env bash
# Run one command inside a temporary copy of the Finyl API on AWS, wait for it to
# finish, print what it printed, and fail if it failed. (WP9)
#
# Used by the "Deploy to AWS" workflow (database migrations, demo data) and by
# restore_drill.sh (checking a restored backup).
#
#   one_off_task.sh '<command as JSON list>' ['<extra settings as JSON list>']
#   e.g. one_off_task.sh '["python","scripts/run_migrations.py"]'
#        one_off_task.sh '["python","scripts/verify_restore.py"]' '[{"name":"DB_HOST","value":"x"}]'
#
# Needs these environment variables (load_stack_outputs.sh sets them):
#   CLUSTER TASK_FAMILY TASK_SUBNETS API_SG LOG_GROUP AWS_REGION
set -euo pipefail

command_json="${1:?command (JSON list) required}"
env_json="${2:-[]}"
: "${CLUSTER:?}" "${TASK_FAMILY:?}" "${TASK_SUBNETS:?}" "${API_SG:?}" "${LOG_GROUP:?}"

overrides=$(jq -cn --argjson c "$command_json" --argjson e "$env_json" \
  '{containerOverrides: [{name: "api", command: $c, environment: $e}]}')
network=$(jq -cn --arg s "$TASK_SUBNETS" --arg g "$API_SG" \
  '{awsvpcConfiguration: {subnets: ($s | split(",")), securityGroups: [$g], assignPublicIp: "ENABLED"}}')

task_arn=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TASK_FAMILY" \
  --launch-type FARGATE --network-configuration "$network" --overrides "$overrides" \
  --query 'tasks[0].taskArn' --output text)
if [ -z "$task_arn" ] || [ "$task_arn" = "None" ]; then
  echo "Could not start the task." >&2
  exit 1
fi
task_id="${task_arn##*/}"
echo "Started task $task_id: $command_json"

# Wait up to about 30 minutes (each wait is 10 minutes).
for _ in 1 2 3; do
  if aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$task_arn" 2>/dev/null; then
    break
  fi
done

echo "----- output -----"
aws logs get-log-events --log-group-name "$LOG_GROUP" \
  --log-stream-name "api/api/$task_id" --start-from-head --output json 2>/dev/null \
  | jq -r '.events[].message' || echo "(no output recorded)"
echo "------------------"

read -r status exit_code reason < <(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task_arn" \
  --output json | jq -r '.tasks[0] | [.lastStatus, (.containers[0].exitCode // "none"), ((.stoppedReason // "-") | gsub("\\s"; "_"))] | @tsv')
if [ "$status" != "STOPPED" ]; then
  echo "The task is still running after 30 minutes; check it in the ECS console." >&2
  exit 1
fi
if [ "$exit_code" != "0" ]; then
  echo "The task failed (exit code $exit_code, reason: ${reason//_/ })." >&2
  exit 1
fi
echo "Task finished successfully."
