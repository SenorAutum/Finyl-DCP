-- WP8: background jobs that run once across servers, and report e-mail status.
--
-- scheduled_job_runs: every background job claims a row for its time slot before
-- it runs (job_id + slot is unique), so when two or more copies of the backend
-- run on AWS, each job still runs only once per slot. The row also records when
-- it finished and whether it worked, for the Finyl admin page.
SET search_path TO finyl_dcp, public;

CREATE TABLE IF NOT EXISTS scheduled_job_runs (
    id          BIGSERIAL PRIMARY KEY,
    job_id      VARCHAR(60)  NOT NULL,
    slot        VARCHAR(40)  NOT NULL,
    host        VARCHAR(120),
    started_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      VARCHAR(20)  NOT NULL DEFAULT 'running',   -- running | ok | failed
    detail      TEXT,
    CONSTRAINT uq_scheduled_job_runs_slot UNIQUE (job_id, slot)
);
CREATE INDEX IF NOT EXISTS ix_scheduled_job_runs_started ON scheduled_job_runs (started_at DESC);

-- Report schedules: what happened the last time it was due.
ALTER TABLE report_schedules ADD COLUMN IF NOT EXISTS last_status VARCHAR(20);
ALTER TABLE report_schedules ADD COLUMN IF NOT EXISTS last_error  TEXT;
