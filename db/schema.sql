-- AEO/GEO Visibility Platform: core schema (PRD data model)
-- Multi-tenant: every table carries client_id, enforced by row-level security.
-- App sets per-connection:  SET app.client_id = '<uuid>';   (client login)
--                      or   SET app.is_staff  = 'true';     (Hovers staff / n8n workers)

-- gen_random_uuid() is built into Postgres 13+, so no extension is needed.

CREATE TYPE engine_t      AS ENUM ('chatgpt','gemini','perplexity','ai_overviews','bing_copilot');
CREATE TYPE funnel_t      AS ENUM ('awareness','consideration','decision');
CREATE TYPE sentiment_t   AS ENUM ('positive','neutral','negative');
CREATE TYPE source_type_t AS ENUM ('reddit','youtube','wikipedia','news','competitor','own_site','review_site','other');
CREATE TYPE action_type_t AS ENUM ('on_page','new_content','third_party','brand_fact');
CREATE TYPE action_status_t AS ENUM ('proposed','approved','published','rejected');
CREATE TYPE run_status_t  AS ENUM ('ok','error');

CREATE TABLE clients (
  client_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL,
  domain     text NOT NULL,
  aliases    text[] NOT NULL DEFAULT '{}',          -- brand name variants for the parser
  plan       text NOT NULL DEFAULT 'pilot',
  regions    text[] NOT NULL DEFAULT '{IN}',        -- ISO country codes
  status     text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE competitors (
  competitor_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  name      text NOT NULL,
  domain    text,
  aliases   text[] NOT NULL DEFAULT '{}',
  UNIQUE (client_id, name)
);

CREATE TABLE prompts (
  prompt_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id    uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  text         text NOT NULL,
  funnel_stage funnel_t NOT NULL DEFAULT 'consideration',
  topic        text,
  region       text NOT NULL DEFAULT 'IN',
  approved     boolean NOT NULL DEFAULT false,      -- a person signs off before it runs
  active       boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (client_id, text, region)
);

CREATE TABLE runs (
  run_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  prompt_id uuid NOT NULL REFERENCES prompts ON DELETE CASCADE,
  engine    engine_t NOT NULL,
  region    text NOT NULL,
  sample_no smallint NOT NULL CHECK (sample_no >= 1),
  cycle     date NOT NULL,                          -- the scheduled week/day this run belongs to
  run_at    timestamptz NOT NULL DEFAULT now(),
  status    run_status_t NOT NULL DEFAULT 'ok',
  error     text,
  cost_usd  numeric(10,6),
  UNIQUE (prompt_id, engine, region, cycle, sample_no)   -- makes retries idempotent
);

CREATE TABLE answers (
  answer_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id      uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  run_id         uuid NOT NULL UNIQUE REFERENCES runs ON DELETE CASCADE,
  full_text      text NOT NULL,
  engine_version text,
  raw_json       jsonb,
  parsed_at      timestamptz                         -- null = parser has not processed it yet
);

CREATE TABLE mentions (
  mention_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id        uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  answer_id        uuid NOT NULL REFERENCES answers ON DELETE CASCADE,
  brand            text NOT NULL,
  is_client        boolean NOT NULL,
  rank_in_answer   smallint,
  sentiment        sentiment_t,
  recommended      boolean
);

CREATE TABLE citations (
  citation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  answer_id   uuid NOT NULL REFERENCES answers ON DELETE CASCADE,
  url         text NOT NULL,
  domain      text NOT NULL,
  source_type source_type_t NOT NULL DEFAULT 'other'
);

CREATE TABLE scores_daily (
  client_id        uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  engine           engine_t NOT NULL,
  region           text NOT NULL,
  date             date NOT NULL,
  samples          integer NOT NULL,
  mention_rate     numeric(5,4),
  citation_rate    numeric(5,4),
  share_of_voice   numeric(5,4),
  avg_position     numeric(5,2),
  visibility_score numeric(5,2),
  PRIMARY KEY (client_id, engine, region, date)
);

CREATE TABLE actions (
  action_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id    uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  prompt_id    uuid REFERENCES prompts ON DELETE SET NULL,
  type         action_type_t NOT NULL,
  target_url   text,
  brief        text NOT NULL,
  status       action_status_t NOT NULL DEFAULT 'proposed',
  approved_by  text,
  published_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON competitors (client_id);
CREATE INDEX ON prompts (client_id, active, approved);
CREATE INDEX ON runs (client_id, cycle);
CREATE INDEX ON answers (client_id) WHERE parsed_at IS NULL;
CREATE INDEX ON mentions (answer_id);
CREATE INDEX ON citations (answer_id);
CREATE INDEX ON citations (client_id, domain);
CREATE INDEX ON actions (client_id, status);

-- ---------- Row-level security ----------
-- The app's role. It cannot log in by itself: the app connects as the database owner and switches to it with
-- SET ROLE (see collectors/common.py). A local trial gives it a login in start_local.py.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aeo_app') THEN CREATE ROLE aeo_app NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF;
END $$;
GRANT USAGE ON SCHEMA public TO aeo_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aeo_app;

CREATE FUNCTION app_can_see(row_client uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('app.is_staff', true), '') = 'true'
      OR row_client = nullif(current_setting('app.client_id', true), '')::uuid
$$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['clients','competitors','prompts','runs','answers','mentions','citations','scores_daily','actions']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
  END LOOP;
END $$;
