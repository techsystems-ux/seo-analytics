-- Guardrail for paid sources (OpenAI: ChatGPT prompts and blog drafts; Semrush): one row each time a paid step starts
-- for a brand, whatever started it (schedule, button, sign-up). The worker allows at most PAID_RUNS_PER_DAY per brand and
-- kind in 24 hours, with a minimum gap, so opening, refreshing or signing in can never spend credits.
CREATE TABLE paid_runs (
  run_id      bigserial PRIMARY KEY,
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  kind        text NOT NULL,                     -- ai, blogs, semrush
  pull_id     bigint,
  started_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON paid_runs (client_id, kind, started_at DESC);
ALTER TABLE paid_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE paid_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON paid_runs USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON paid_runs TO aeo_app;
GRANT USAGE ON SEQUENCE paid_runs_run_id_seq TO aeo_app;
