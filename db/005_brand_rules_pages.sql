-- Module 1: brand rules card (SOP section "Brand rules") and the page checker (SOP META, COL, PDP, BLOG, writing rules).
-- Same tenant isolation as schema.sql.

CREATE TYPE page_type_t   AS ENUM ('home','collection','product','blog','static');
CREATE TYPE page_status_t AS ENUM ('not_started','in_progress','internal_review','client_review','approved','uploaded','live_checked','indexed');
CREATE TYPE check_result_t AS ENUM ('pass','warn','fail','na');

-- One card per brand (BR-01 to BR-10). Every writing check reads from it.
CREATE TABLE brand_rules (
  client_id          uuid PRIMARY KEY REFERENCES clients ON DELETE CASCADE,
  brand_spellings    text[] NOT NULL DEFAULT '{}',   -- BR-01 first = the correct spelling, rest = variants people search
  product_names      text[] NOT NULL DEFAULT '{}',   -- BR-02 exact names
  term_fixes         jsonb  NOT NULL DEFAULT '{}',   -- BR-02/05 wrong -> right, e.g. {"natural latex": "natural organic latex"}
  usps               text[] NOT NULL DEFAULT '{}',   -- BR-03 e.g. {"15-night trial","custom sizes","10-year warranty"}
  usp_markers        text[] NOT NULL DEFAULT '{}',   -- words that prove a USP is mentioned, e.g. {"trial","custom","warranty","oeko-tex"}
  banned_words       text[] NOT NULL DEFAULT '{}',   -- BR-05 never in any copy, e.g. {"elevate","game changer"}
  title_banned_words text[] NOT NULL DEFAULT '{}',   -- BR-05 never in titles or H1s, e.g. {"collection"}
  claims_to_avoid    text[] NOT NULL DEFAULT '{}',   -- BR-06 phrases that must not appear
  region_words       text[] NOT NULL DEFAULT '{}',   -- BR-07 e.g. {"UAE","Dubai"}
  online_only        boolean NOT NULL DEFAULT true,
  correct_domain     text,                           -- BR-08
  wrong_domains      text[] NOT NULL DEFAULT '{}',   -- BR-08 domains links must never point to, e.g. {"easeliving.in"}
  sister_domains     text[] NOT NULL DEFAULT '{}',   -- BR-09 copy must never be shared with these sites
  theme_appends_brand boolean NOT NULL DEFAULT false,-- META-02 true = the theme adds the brand to every title
  tone               text,
  reviewer           text,                           -- BR-10 client reviewer
  review_days        smallint,                       -- BR-10 agreed review turnaround (open question in the SOP)
  approved_by        text,
  approved_at        timestamptz,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Every URL we work on, with its keyword set (KW-01/02) and where it is in the workflow.
CREATE TABLE pages (
  page_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id          uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  url                text NOT NULL,
  page_type          page_type_t NOT NULL,
  language           text NOT NULL DEFAULT 'en',
  primary_keyword    text,
  secondary_keywords text[] NOT NULL DEFAULT '{}',
  search_volume      integer,
  intent             text,
  status             page_status_t NOT NULL DEFAULT 'not_started',
  batch              text,
  owner              text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (client_id, url)
);

-- What a page looks like at one moment: parsed from the live page, or a draft written in the tool.
CREATE TABLE page_snapshots (
  snapshot_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id          uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  page_id            uuid NOT NULL REFERENCES pages ON DELETE CASCADE,
  source             text NOT NULL CHECK (source IN ('live','draft')),
  http_status        integer,
  content            jsonb NOT NULL,                 -- title, description, h1s, headings, faqs, schema types, links, images, text
  taken_at           timestamptz NOT NULL DEFAULT now()
);

-- One row per check per snapshot. check_id = the SOP step id (META-02, PDP-04 ...).
CREATE TABLE page_checks (
  snapshot_id        uuid NOT NULL REFERENCES page_snapshots ON DELETE CASCADE,
  client_id          uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  page_id            uuid NOT NULL REFERENCES pages ON DELETE CASCADE,
  check_id           text NOT NULL,
  result             check_result_t NOT NULL,
  found              text,                           -- what we saw, e.g. "74 characters"
  message            text NOT NULL,                  -- what to do, in plain words
  PRIMARY KEY (snapshot_id, check_id)
);

CREATE INDEX ON pages (client_id, page_type);
CREATE INDEX ON page_snapshots (page_id, taken_at DESC);
CREATE INDEX ON page_checks (client_id, result);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['brand_rules','pages','page_snapshots','page_checks'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;

-- Check results of each page's most recent snapshot, for the Pages screen.
CREATE VIEW page_latest_checks WITH (security_invoker = true) AS
SELECT pc.*, s.source, s.taken_at
FROM page_checks pc JOIN page_snapshots s USING (snapshot_id)
WHERE s.snapshot_id = (SELECT s2.snapshot_id FROM page_snapshots s2 WHERE s2.page_id = pc.page_id
                       ORDER BY s2.taken_at DESC LIMIT 1);
GRANT SELECT ON page_latest_checks TO aeo_app;
