-- Free-API layer 2: Search Console + GA4 + run log. Same tenant isolation as schema.sql.
CREATE TABLE client_integrations (
  client_id       uuid PRIMARY KEY REFERENCES clients ON DELETE CASCADE,
  gsc_site_url    text,        -- exactly as in Search Console: 'sc-domain:example.com' or 'https://example.com/'
  ga4_property_id text         -- numeric property id, e.g. '123456789'
);

CREATE TABLE gsc_search_daily (
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  date date NOT NULL, query text NOT NULL, page text NOT NULL, country text NOT NULL, device text NOT NULL,
  clicks integer NOT NULL, impressions integer NOT NULL, ctr numeric, position numeric,
  is_branded boolean NOT NULL DEFAULT false,
  PRIMARY KEY (client_id, date, query, page, country, device)
);
CREATE INDEX ON gsc_search_daily (client_id, date);

CREATE TABLE gsc_totals_daily (          -- from a query with no query dimension, so totals include anonymised queries
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  date date NOT NULL, clicks integer NOT NULL, impressions integer NOT NULL, ctr numeric, position numeric,
  PRIMARY KEY (client_id, date)
);

CREATE TABLE ga4_daily (
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  date date NOT NULL, channel_group text NOT NULL,
  sessions integer, engaged_sessions integer, key_events numeric, transactions integer, revenue numeric,
  PRIMARY KEY (client_id, date, channel_group)
);

CREATE TABLE ga4_ai_referrals (          -- sessions whose source looks like an AI engine
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  date date NOT NULL, source text NOT NULL,
  sessions integer, engaged_sessions integer, key_events numeric, transactions integer, revenue numeric,
  PRIMARY KEY (client_id, date, source)
);

CREATE TABLE collector_runs (             -- ops log, staff only
  run_id bigserial PRIMARY KEY,
  client_id uuid REFERENCES clients ON DELETE CASCADE,
  collector text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  status text NOT NULL, rows_written integer, error text
);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['client_integrations','gsc_search_daily','gsc_totals_daily','ga4_daily','ga4_ai_referrals'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
  ALTER TABLE collector_runs ENABLE ROW LEVEL SECURITY;
  ALTER TABLE collector_runs FORCE ROW LEVEL SECURITY;
  CREATE POLICY staff_only ON collector_runs USING (coalesce(current_setting('app.is_staff', true),'') = 'true');
  GRANT SELECT, INSERT, UPDATE, DELETE ON collector_runs TO aeo_app;
  GRANT USAGE ON SEQUENCE collector_runs_run_id_seq TO aeo_app;
END $$;
