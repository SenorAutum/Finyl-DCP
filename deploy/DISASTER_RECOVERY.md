# Finyl-DCP — Database Disaster Recovery Runbook

This document explains how the Finyl-DCP PostgreSQL database is backed up and
how to recover it. Keep it current whenever the backup setup changes.

---

## 1. Overview

| Item | Value |
|------|-------|
| Database engine | PostgreSQL 17 (Abacus hosted) |
| Application schema | `finyl_dcp` (inside database `4b29d16f`) |
| Backup format | `pg_dump` custom format (`-Fc`, compressed) |
| Schedule | Daily at **02:00 UTC** via systemd timer |
| Backup script | `deploy/backup_db.sh` |
| Restore script | `deploy/restore_db.sh` |
| Local copies | `backend/storage/backups/` (gitignored), pruned after **7 days** |
| Off-site copies | Abacus S3 bucket, pruned after **30 days** |

Credentials (`DATABASE_URL`, including the DB password) are read at runtime
from `backend/.env`. They are **never** printed, logged, or committed.

### Recovery objectives

| Metric | Target | Rationale |
|--------|--------|-----------|
| **RPO** (max data loss) | **24 hours** | Backups run once daily at 02:00 UTC. A failure just before the next run loses at most ~1 day of writes. Run `deploy/backup_db.sh` manually before risky operations to shrink this. |
| **RTO** (time to restore) | **≤ 30 minutes** | Dumps are small (~0.5 MB) and restore in seconds; RTO is dominated by provisioning a target database and human coordination. |

---

## 2. Where backups live

**Local (on the VM):**
```
/home/ubuntu/finyl-dcp/backend/storage/backups/finyl_dcp_<UTC-timestamp>.dump
```
Log file: `backend/storage/backups/backup.log`

**Off-site (S3):**
```
s3://<bucket>/<vm-storage-path>backups/finyl-dcp/finyl_dcp_<UTC-timestamp>.dump
```
The bucket name and storage path are discovered automatically from the VM
metadata service, so you don't hardcode them. To print the current location:

```bash
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-abacus-vm-metadata-token-ttl-seconds: 300")
curl -s -H "X-abacus-vm-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/user-data \
  | python3 -c 'import sys,json;s=json.load(sys.stdin)["storage"];print(f"s3://{s[\"bucket_name\"]}/{s[\"path\"]}backups/finyl-dcp/")'
```

Filenames are UTC timestamps: `finyl_dcp_YYYYmmddTHHMMSSZ.dump`.

### Off-Abacus configuration (portable hosts)

`deploy/backup_db.sh` is **host-agnostic**. It resolves the off-site S3 target
in this order, so the same script runs unchanged on Abacus, a plain VPS, AWS,
etc.:

1. **Explicit environment variables (any host — recommended off Abacus):**

   | Variable | Required | Meaning |
   |----------|----------|---------|
   | `BACKUP_S3_BUCKET` | yes (to enable off-site) | S3 bucket name for off-site dumps |
   | `BACKUP_S3_PREFIX` | no | Key prefix inside the bucket (default `backups/finyl-dcp`) |

   AWS credentials come from the **standard AWS credential chain** — set
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (+ optional `AWS_SESSION_TOKEN`),
   attach an EC2/ECS instance role, or configure `~/.aws/credentials`. Also set
   `AWS_DEFAULT_REGION` (or `AWS_REGION`) if your bucket needs it. The script
   never prints these values. Example (systemd drop-in / cron env / `.env` for
   the timer):
   ```bash
   BACKUP_S3_BUCKET=my-company-finyl-backups
   BACKUP_S3_PREFIX=finyl-dcp/prod        # optional; defaults to backups/finyl-dcp
   AWS_ACCESS_KEY_ID=AKIA...              # or use an instance role instead
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=eu-west-1
   ```
   Resulting off-site path: `s3://my-company-finyl-backups/finyl-dcp/prod/finyl_dcp_<ts>.dump`

2. **Abacus VM metadata (IMDSv2)** — on Abacus, if `BACKUP_S3_BUCKET` is unset,
   the bucket and path are still auto-discovered from the metadata service
   (best-effort; the probe times out quickly and never aborts the run on a
   non-Abacus host).

3. **Neither available** → off-site upload is **skipped with a WARNING** and the
   backup still produces a verified **local** dump under
   `backend/storage/backups/` with local retention. The run does **not**
   hard-fail. Provide `BACKUP_S3_BUCKET` (or a mounted/rsynced off-site copy of
   the local dumps) to restore off-site protection.

Retention knobs are the same on every host: `LOCAL_RETENTION_DAYS` (default 7)
and `S3_RETENTION_DAYS` (default 30).

Off Abacus, install the backup timer from `deploy/finyl-dcp-backup.{service,timer}`
(section 4) or add a cron entry, and ensure the above env vars are present in the
timer/cron environment (e.g. a systemd `Environment=`/`EnvironmentFile=` drop-in).

---

## 3. Listing available backups

**Local:**
```bash
ls -lh /home/ubuntu/finyl-dcp/backend/storage/backups/
```

**S3** (resolve `<bucket>` / `<path>` with the snippet in section 2):
```bash
aws s3 ls "s3://<bucket>/<path>backups/finyl-dcp/"
```

---

## 4. Scheduling (systemd timer)

Unit files live in `deploy/` and are symlinked into `/etc/systemd/system/`:

- `deploy/finyl-dcp-backup.service` — oneshot that runs `deploy/backup_db.sh`
- `deploy/finyl-dcp-backup.timer` — fires daily at `02:00:00 UTC`, `Persistent=true`

Common operations:
```bash
# Status / next run
systemctl list-timers finyl-dcp-backup.timer --all

# Run a backup right now (out of schedule)
sudo systemctl start finyl-dcp-backup.service

# Inspect the last run
journalctl -u finyl-dcp-backup.service -n 50 --no-pager
tail -n 50 /home/ubuntu/finyl-dcp/backend/storage/backups/backup.log

# (Re)install the timer after editing unit files
sudo ln -sf /home/ubuntu/finyl-dcp/deploy/finyl-dcp-backup.service /etc/systemd/system/
sudo ln -sf /home/ubuntu/finyl-dcp/deploy/finyl-dcp-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now finyl-dcp-backup.timer
```

> The `pg_dump`/`pg_restore` client must match the server major version (17).
> They are provided by the `postgresql-client-17` package. If a run fails with
> `server version mismatch`, install/upgrade the matching client.

---

## 5. Test restore (safe — never touches live data)

Always rehearse restores into a **scratch** target, never the live database.
`restore_db.sh` defaults its target to `finyl_dcp_restore_test` and refuses to
write to the live database name unless `FORCE_LIVE=1` is explicitly set.

The Abacus hosted role cannot `CREATE DATABASE`, so the simplest isolated
rehearsal uses a throwaway PostgreSQL 17 container:

```bash
# 1. Start an ephemeral PG 17 instance
docker run -d --name finyl-restore-test \
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=finyl_dcp_restore_test \
  -p 55432:5432 postgres:17
sleep 8   # wait for readiness (docker exec finyl-restore-test pg_isready -U postgres)

# 2. Restore the latest local dump into it
DUMP=$(ls -t backend/storage/backups/finyl_dcp_*.dump | head -1)
./deploy/restore_db.sh "$DUMP" \
  "postgresql://postgres:test@127.0.0.1:55432/finyl_dcp_restore_test"

# 3. Verify (compare against live counts)
psql "postgresql://postgres:test@127.0.0.1:55432/finyl_dcp_restore_test" \
  -c "SELECT count(*) FROM finyl_dcp.borrowers;"

# 4. Tear down
docker rm -f finyl-restore-test
```

You can also restore straight from S3 by passing an `s3://…` URL as the first
argument — the script downloads it to a temp dir first.

### Integrity verification

A restore is considered good when key table counts match the live database
(read-only on live):

```bash
# Live counts
cd /home/ubuntu/finyl-dcp/backend
set -a; source <(grep '^DATABASE_URL=' .env); set +a
psql "$DATABASE_URL" -c "
  SELECT 'borrowers' t, count(*) FROM finyl_dcp.borrowers
  UNION ALL SELECT 'loans', count(*) FROM finyl_dcp.loans
  UNION ALL SELECT 'payment_transactions', count(*) FROM finyl_dcp.payment_transactions
  UNION ALL SELECT 'repayments', count(*) FROM finyl_dcp.repayments
  ORDER BY 1;"
```
Run the same query against the restored target and confirm the numbers match.

---

## 6. Real recovery (restoring production)

> **DANGER:** This overwrites live data. Do this only during a genuine
> disaster, after confirming the live database is actually lost/corrupt.

Preferred approach — restore into a **new** database and repoint the app,
rather than overwriting in place:

1. **Pick the dump** (usually the most recent good one) from S3 or local
   (sections 2–3). Confirm it is valid:
   ```bash
   pg_restore --list <dump> | head
   ```
2. **Provision a fresh target database** (ask the platform for a new hosted DB
   if the role can't `CREATE DATABASE`), and get its connection URL.
3. **Restore** into it:
   ```bash
   ./deploy/restore_db.sh <dump-path-or-s3-url> "<new-target-database-url>"
   ```
4. **Verify** integrity (section 5).
5. **Repoint the app**: update `DATABASE_URL` in `backend/.env`, then
   `sudo systemctl restart finyl-dcp` and smoke-test.

**Overwriting the existing live database in place** (last resort) requires the
explicit override, because the safety guard blocks the live name otherwise:
```bash
FORCE_LIVE=1 ./deploy/restore_db.sh <dump> 4b29d16f
```
`pg_restore` runs with `--clean --if-exists`, dropping and recreating objects
in the `finyl_dcp` schema. Take a fresh backup first if the database is still
reachable.

---

## 7. Routine checks

- **Weekly:** confirm the timer is active and recent runs succeeded
  (`systemctl list-timers`, `tail backup.log`).
- **Monthly:** perform a full test restore (section 5) and confirm counts.
- **After schema migrations:** run `deploy/backup_db.sh` manually so a
  post-migration snapshot exists.
