-- The six Semrush-era features: technical audit (own crawler + optional Semrush Site Audit import), rank tracking,
-- keyword gap (already in 006), questions and related searches, backlinks, SERP features. Same tenant isolation.

ALTER TABLE clients ADD COLUMN semrush_project_id text;            -- Semrush Project (Site Audit, Position Tracking)
ALTER TABLE semrush_usage ADD COLUMN feature text;                 -- which feature spent the units (per-feature caps)
ALTER TABLE semrush_keywords
  ADD COLUMN serp_features text,                                    -- Fk: features Google shows for the keyword (codes, comma separated)
  ADD COLUMN serp_features_owned text;                              -- Fp: features where this domain appears

-- ---------------- technical audit ----------------
CREATE TABLE tech_crawls (
  crawl_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id     uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  source        text NOT NULL CHECK (source IN ('hovers','semrush')),
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  pages_crawled integer,
  health_score  numeric,                                            -- Semrush "quality", or ours: share of pages with no error
  errors integer, warnings integer, notices integer,
  external_id   text                                                -- Semrush snapshot_id
);
-- One row per issue type per brand: the tracker the team works from (SOP: severity, team, owner, due date, verified on re-crawl)
CREATE TABLE tech_issues (
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  issue_code  text NOT NULL,                                        -- e.g. 'broken_internal_link' or 'semrush:4'
  source      text NOT NULL,
  title       text NOT NULL,
  severity    text NOT NULL CHECK (severity IN ('error','warning','notice')),
  team        text NOT NULL DEFAULT 'tech' CHECK (team IN ('tech','seo')),
  how_to_fix  text,
  status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','fixed','verified')),
  owner       text,
  due_date    date,
  pages       integer NOT NULL DEFAULT 0,                           -- pages affected in the latest crawl
  first_seen  timestamptz NOT NULL DEFAULT now(),
  last_seen   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, issue_code)
);
CREATE TABLE tech_issue_pages (
  crawl_id   uuid NOT NULL REFERENCES tech_crawls ON DELETE CASCADE,
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  issue_code text NOT NULL,
  url        text NOT NULL,
  detail     text,
  PRIMARY KEY (crawl_id, issue_code, url)
);

-- ---------------- rank tracking ----------------
CREATE TABLE rank_keywords (
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  keyword   text NOT NULL,
  target_url text,
  tags      text[] NOT NULL DEFAULT '{}',
  added_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, keyword)
);
CREATE TABLE rank_positions (
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  keyword   text NOT NULL,
  date      date NOT NULL,
  source    text NOT NULL CHECK (source IN ('gsc','semrush')),      -- gsc = daily average position (free); semrush = tracked position
  position  numeric,
  url       text,
  clicks    integer,
  impressions integer,
  PRIMARY KEY (client_id, keyword, date, source)
);

-- ---------------- questions and related searches (content ideas) ----------------
CREATE TABLE keyword_ideas (
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  seed       text NOT NULL,
  kind       text NOT NULL CHECK (kind IN ('question','related')),
  keyword    text NOT NULL,
  search_volume integer, kd numeric, intent text, serp_features text,
  pulled_on  date NOT NULL,
  status     text NOT NULL DEFAULT 'new' CHECK (status IN ('new','planned','done','dismissed')),
  PRIMARY KEY (client_id, kind, keyword)
);

-- ---------------- backlinks ----------------
CREATE TABLE backlink_overview (
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  domain    text NOT NULL,
  pulled_on date NOT NULL,
  ascore integer, backlinks bigint, refdomains integer, follows bigint, nofollows bigint,
  PRIMARY KEY (client_id, domain, pulled_on)
);
CREATE TABLE backlink_refdomains (
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  pulled_on date NOT NULL,
  domain    text NOT NULL,                                          -- the site that links
  ascore integer, backlinks integer, first_seen date, last_seen date,
  PRIMARY KEY (client_id, pulled_on, domain)
);
CREATE TABLE backlink_gap (                                         -- sites linking to competitors but not the client: off-page prospects
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  pulled_on date NOT NULL,
  domain    text NOT NULL,
  ascore integer,
  links_to  jsonb NOT NULL,                                         -- {"competitor.com": backlinks, ...}
  status    text NOT NULL DEFAULT 'new' CHECK (status IN ('new','contacted','won','dismissed')),
  PRIMARY KEY (client_id, pulled_on, domain)
);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['tech_crawls','tech_issues','tech_issue_pages','rank_keywords','rank_positions','keyword_ideas',
                           'backlink_overview','backlink_refdomains','backlink_gap'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
CREATE INDEX ON rank_positions (client_id, date);
CREATE INDEX ON tech_issue_pages (client_id, issue_code);
