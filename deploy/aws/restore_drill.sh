#!/usr/bin/env bash
# Backup restore drill (WP9, review step 6 and 7.12): prove a backup can really
# be restored, and how long it takes. Safe: it never touches the live database.
#
#   bash deploy/aws/restore_drill.sh staging
#
# Run it in AWS CloudShell (it has the AWS command line and jq). It:
#   1. takes the newest automatic backup (snapshot) of finyl-<env>;
#   2. restores it into a NEW temporary database next to the live one;
#   3. runs scripts/verify_restore.py against that copy (migrations complete,
#      record counts, newest repayment time);
#   4. deletes the temporary database again, even if a step failed;
#   5. prints PASSED or FAILED and the minutes it took (the recovery time).
# Expect 20-40 minutes. Costs a few US cents for the temporary database.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=load_stack_outputs.sh
source "$here/load_stack_outputs.sh" "${1:-staging}"

started=$(date +%s)
snapshot=$(aws rds describe-db-snapshots --db-instance-identifier "$DB_ID" --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots[?Status==`available`], &SnapshotCreateTime))[0].[DBSnapshotIdentifier,SnapshotCreateTime]' \
  --output text)
snap_id=$(cut -f1 <<<"$snapshot")
snap_time=$(cut -f2 <<<"$snapshot")
if [ -z "$snap_id" ] || [ "$snap_id" = "None" ]; then
  echo "No automatic backup found yet for $DB_ID. Backups start the first night after creation." >&2
  exit 1
fi
copy_id="${DB_ID}-restore-test-$(date +%m%d%H%M)"
echo "Restoring backup $snap_id (taken $snap_time) into temporary database $copy_id ..."

cleanup() {
  echo "Deleting the temporary database $copy_id ..."
  aws rds delete-db-instance --db-instance-identifier "$copy_id" \
    --skip-final-snapshot --delete-automated-backups > /dev/null 2>&1 || true
}
trap cleanup EXIT

aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "$copy_id" \
  --db-snapshot-identifier "$snap_id" \
  --db-instance-class db.t3.micro \
  --db-subnet-group-name "$DB_SUBNET_GROUP" \
  --vpc-security-group-ids "$DB_SG" \
  --no-publicly-accessible --no-multi-az --no-deletion-protection \
  --tags Key=purpose,Value=restore-drill > /dev/null

echo "Waiting for the copy to start (usually 10-25 minutes) ..."
for _ in 1 2 3; do
  if aws rds wait db-instance-available --db-instance-identifier "$copy_id" 2>/dev/null; then break; fi
done
endpoint=$(aws rds describe-db-instances --db-instance-identifier "$copy_id" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
echo "Copy is up at $endpoint. Checking it ..."

result=PASSED
bash "$here/one_off_task.sh" '["python","scripts/verify_restore.py"]' \
  "[{\"name\":\"DB_HOST\",\"value\":\"$endpoint\"}]" || result=FAILED

minutes=$(( ($(date +%s) - started) / 60 ))
echo
echo "RESTORE DRILL $result"
echo "  backup used:   $snap_id (taken $snap_time)"
echo "  time to a checked copy: about $minutes minutes"
echo "Write these in the change report / DR log (deploy/DISASTER_RECOVERY.md)."
[ "$result" = PASSED ]
