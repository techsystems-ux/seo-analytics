-- Every billed API response, written the moment it comes back (before the answer is even parsed), so spend limits count
-- everything that was charged. Claude (provider 'anthropic') has a hard lifetime limit checked against this table.
CREATE TABLE api_spend (
  spend_id   bigserial PRIMARY KEY,
  provider   text NOT NULL,
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  model      text,
  usd        numeric(12,6) NOT NULL,
  searches   integer NOT NULL DEFAULT 0,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_spend_provider ON api_spend (provider, at);
ALTER TABLE api_spend ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_spend FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON api_spend USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT ON api_spend TO aeo_app;
GRANT USAGE ON SEQUENCE api_spend_spend_id_seq TO aeo_app;
