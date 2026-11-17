-- Pulls run on the Orbit server (Vercel), not on a Mac. A pull (pull_queue) is split into short tasks, one step for one
-- brand each (a Search Console window, a batch of ChatGPT prompts, a crawl), so each fits inside one function run.
-- Tasks that have more to do add the next task themselves; a run that is cut off is retried (three tries).
CREATE TABLE pull_tasks (
  task_id      bigserial PRIMARY KEY,
  pull_id      bigint REFERENCES pull_queue ON DELETE CASCADE,
  client_id    uuid REFERENCES clients ON DELETE CASCADE,
  step         text NOT NULL,                        -- gsc, gsc_history, ga4, rank, ai, pages, blogs, crawl, reddit, youtube, semrush...
  args         jsonb NOT NULL DEFAULT '{}',          -- e.g. a date window, Semrush budget and seeds
  seq          integer NOT NULL DEFAULT 0,           -- order within a pull
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','error')),
  attempts     integer NOT NULL DEFAULT 0,
  note         text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  started_at   timestamptz,
  finished_at  timestamptz
);
CREATE INDEX ON pull_tasks (status, pull_id, seq, task_id);
ALTER TABLE pull_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE pull_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pull_tasks USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON pull_tasks TO aeo_app;
GRANT USAGE ON SEQUENCE pull_tasks_task_id_seq TO aeo_app;
