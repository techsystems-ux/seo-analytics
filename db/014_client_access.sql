-- Client logins: people at a brand who may view that brand's dashboards (read-only), and one-time sign-in links sent by
-- email. A viewer's database session only ever sees its own brand's rows (app.client_id, not staff), as the policies require.
CREATE TABLE client_viewers (
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  email       text NOT NULL CHECK (email = lower(email)),
  added_by    text,
  added_at    timestamptz NOT NULL DEFAULT now(),
  last_login  timestamptz,
  PRIMARY KEY (client_id, email)
);
CREATE INDEX ON client_viewers (email);
ALTER TABLE client_viewers ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_viewers FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON client_viewers USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON client_viewers TO aeo_app;

CREATE TABLE login_links (                       -- only a hash of the link is stored; each works once, for 15 minutes
  token_hash  text PRIMARY KEY,
  email       text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz
);
CREATE INDEX ON login_links (email, created_at);
GRANT SELECT, INSERT, UPDATE, DELETE ON login_links TO aeo_app;
