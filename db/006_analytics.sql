-- Analytics v1: Semrush (keywords, competitors, keyword gap), GA4 organic landing pages, and the extra fields the
-- AI visibility runner needs. Same tenant isolation as schema.sql.

-- Competitors found by Semrush sit next to the ones a person adds.
ALTER TABLE competitors
  ADD COLUMN source           text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','semrush')),
  ADD COLUMN tracked          boolean NOT NULL DEFAULT true,     -- false = suggested, not yet followed in prompts and gaps
  ADD COLUMN relevance        numeric,                           -- Semrush competitor relevance 0-1
  ADD COLUMN common_keywords  integer,
  ADD COLUMN organic_keywords integer,
  ADD COLUMN organic_traffic  integer,
  ADD COLUMN pulled_on        date;

-- Keywords a domain ranks for in Google's top 100 (Semrush domain_organic). Client and competitors alike.
CREATE TABLE semrush_keywords (
  client_id      uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  domain         text NOT NULL,
  database       text NOT NULL,                    -- Semrush country database, e.g. 'in'
  pulled_on      date NOT NULL,
  keyword        text NOT NULL,
  position       smallint,
  prev_position  smallint,
  search_volume  integer,
  cpc            numeric,
  url            text,
  traffic_pct    numeric,
  competition    numeric,
  kd             numeric,
  intent         text,
  PRIMARY KEY (client_id, domain, database, pulled_on, keyword)
);
CREATE INDEX ON semrush_keywords (client_id, pulled_on);

-- Keywords where tracked competitors rank and the client does not (or ranks worse). Semrush domain_domains.
CREATE TABLE keyword_gaps (
  client_id      uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  database       text NOT NULL,
  pulled_on      date NOT NULL,
  keyword        text NOT NULL,
  search_volume  integer,
  cpc            numeric,
  competition    numeric,
  positions      jsonb NOT NULL,                   -- {"gargi.shop": null, "competitor.com": 4, ...}
  PRIMARY KEY (client_id, database, pulled_on, keyword)
);

-- Every Semrush call and the API units it cost, so the credit balance is never a surprise.
CREATE TABLE semrush_usage (
  usage_id   bigserial PRIMARY KEY,
  client_id  uuid REFERENCES clients ON DELETE CASCADE,
  report     text NOT NULL,
  lines      integer NOT NULL,
  units      integer NOT NULL,
  called_at  timestamptz NOT NULL DEFAULT now()
);

-- Organic Search sessions and revenue by landing page (GA4), for revenue by page and, joined to Search Console, by keyword.
CREATE TABLE ga4_organic_landing (
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  date date NOT NULL, landing_page text NOT NULL,
  sessions integer, engaged_sessions integer, key_events numeric, transactions integer, revenue numeric,
  PRIMARY KEY (client_id, date, landing_page)
);

-- AI answers: which links the engine cited in the text, and which it only read.
ALTER TABLE citations
  ADD COLUMN title      text,
  ADD COLUMN cited      boolean NOT NULL DEFAULT true,       -- false = consulted during search, not cited in the answer
  ADD COLUMN is_client  boolean NOT NULL DEFAULT false;
ALTER TABLE scores_daily
  ADD COLUMN brand      text NOT NULL DEFAULT '',              -- '' = the client; otherwise a competitor's name
  ADD COLUMN sentiment  numeric(5,4),
  DROP CONSTRAINT scores_daily_pkey,
  ADD PRIMARY KEY (client_id, engine, region, date, brand);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['semrush_keywords','keyword_gaps','semrush_usage','ga4_organic_landing'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
GRANT USAGE ON SEQUENCE semrush_usage_usage_id_seq TO aeo_app;
