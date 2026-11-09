#!/usr/bin/env bash
# Read the outputs of a Finyl stack into environment variables. (WP9)
#   source deploy/aws/load_stack_outputs.sh staging
# Sets: STACK APP_URL CLUSTER SERVICE TASK_FAMILY TASK_SUBNETS API_SG LOG_GROUP
#       IMAGE_REPO WEB_BUCKET DISTRIBUTION_ID DB_ID DB_SUBNET_GROUP DB_SG
# and exports them (plus AWS_REGION, default af-south-1).

ENV_NAME="${1:-${ENV_NAME:-staging}}"
export AWS_REGION="${AWS_REGION:-af-south-1}"
export STACK="finyl-${ENV_NAME}"

_outputs=$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query 'Stacks[0].Outputs' --output json) || {
  echo "Could not read stack $STACK in $AWS_REGION. Is the name right, and is it created?" >&2
  return 1 2>/dev/null || exit 1
}
_out() { jq -r --arg k "$1" '.[] | select(.OutputKey == $k) | .OutputValue' <<<"$_outputs"; }

APP_URL=$(_out AppUrl)
CLUSTER=$(_out ClusterName)
SERVICE=$(_out ServiceName)
TASK_FAMILY=$(_out TaskFamily)
TASK_SUBNETS=$(_out TaskSubnets)
API_SG=$(_out ApiSecurityGroup)
LOG_GROUP=$(_out LogGroup)
IMAGE_REPO=$(_out ImageRepositoryUri)
WEB_BUCKET=$(_out WebBucket)
DISTRIBUTION_ID=$(_out DistributionId)
DB_ID=$(_out DatabaseId)
DB_SUBNET_GROUP=$(_out DatabaseSubnetGroup)
DB_SG=$(_out DatabaseSecurityGroup)
export APP_URL CLUSTER SERVICE TASK_FAMILY TASK_SUBNETS API_SG LOG_GROUP IMAGE_REPO WEB_BUCKET DISTRIBUTION_ID DB_ID DB_SUBNET_GROUP DB_SG
echo "Stack $STACK: $APP_URL"
