-- Pulls asked for on the online copy (Vercel cannot run them) wait here until the Hovers Mac picks them up. The Mac also
-- records its once-a-day pull here (label 'nightly', no brand), so it runs once a day however often the app restarts.
CREATE TABLE pull_queue (
  pull_id      bigserial PRIMARY KEY,
  client_id    uuid REFERENCES clients ON DELETE CASCADE,   -- empty for the daily pull of every brand
  label        text NOT NULL,                                -- what was asked for, e.g. 'youtube', 'crawl', 'nightly'
  args         jsonb NOT NULL,                               -- arguments for collectors/daily.py
  requested_by text,
  requested_at timestamptz NOT NULL DEFAULT now(),
  started_at   timestamptz,
  finished_at  timestamptz,
  exit_code    integer
);
CREATE INDEX ON pull_queue (started_at, pull_id);
ALTER TABLE pull_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE pull_queue FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pull_queue USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON pull_queue TO aeo_app;
GRANT USAGE ON SEQUENCE pull_queue_pull_id_seq TO aeo_app;
