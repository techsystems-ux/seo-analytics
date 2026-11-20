-- "Continue with ChatGPT": a brand's own ChatGPT plan pays for its AI answers and blog drafts instead of the API key.
-- One connection per brand, signed in by the client (or staff) with their own ChatGPT account. Tokens are
-- Fernet-encrypted with TOKEN_KEY; never stored in plain text. See collectors/chatgpt_plan.py.
CREATE TABLE chatgpt_connections (
  client_id           uuid PRIMARY KEY REFERENCES clients ON DELETE CASCADE,
  email               text,
  subject             text,                              -- validated ID-token subject: the ChatGPT account
  oauth_client_id     text NOT NULL,                     -- the client id OpenAI issued for this sign-in (oaiapp_...)
  host_id             text NOT NULL,                     -- ext_agent_host_id sent at sign-in
  id_token_enc        bytea,
  access_token_enc    bytea,
  refresh_token_enc   bytea,
  expires_at          timestamptz,
  earliest_refresh_at timestamptz,
  scopes              text,
  plan_usage          boolean NOT NULL DEFAULT false,    -- chatgpt.tokens.use.direct granted
  models              jsonb NOT NULL DEFAULT '[]',       -- [{slug, display_name}] this account may use
  model               text,                              -- chosen slug
  use_for_ai          boolean NOT NULL DEFAULT true,
  use_for_blogs       boolean NOT NULL DEFAULT true,
  status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active','reconnect','not_eligible')),
  last_error          text,
  limit_hit_at        timestamptz,                       -- last "plan limit reached"; the API key covers until the next day
  last_used_at        timestamptz,
  plan_calls          integer NOT NULL DEFAULT 0,        -- requests answered by the plan
  api_fallbacks       integer NOT NULL DEFAULT 0,        -- requests the API key covered instead
  connected_by        text,
  connected_at        timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chatgpt_oauth_pending (                     -- one sign-in attempt, kept 30 minutes
  state          text PRIMARY KEY,
  client_id      uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  nonce          text NOT NULL,
  verifier_enc   bytea NOT NULL,
  host_id        text NOT NULL,
  redirect_uri   text NOT NULL,
  oauth_client_id text NOT NULL,                         -- dynamic_agent_client, or a registered id
  created_by     text,
  created_at     timestamptz NOT NULL DEFAULT now()
);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['chatgpt_connections','chatgpt_oauth_pending'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
