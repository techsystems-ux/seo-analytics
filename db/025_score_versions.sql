-- Which brand-detection rules a brand's saved AI answers were last read with. When the rules change (or the tracked
-- competitors do), the answers are re-read for free and the scores recomputed (ai_visibility.rescore).
CREATE TABLE score_versions (
  client_id   uuid PRIMARY KEY REFERENCES clients ON DELETE CASCADE,
  version     integer NOT NULL,
  rescored_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE score_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE score_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON score_versions USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON score_versions TO aeo_app;
