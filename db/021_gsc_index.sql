-- Search Console beyond search data: submitted sitemaps and per-URL index status (collectors/gsc_index.py).
CREATE TABLE gsc_sitemaps (
  client_id       uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  path            text NOT NULL,
  type            text,
  is_index        boolean NOT NULL DEFAULT false,
  is_pending      boolean NOT NULL DEFAULT false,
  last_submitted  timestamptz,
  last_downloaded timestamptz,
  errors          integer NOT NULL DEFAULT 0,
  warnings        integer NOT NULL DEFAULT 0,
  contents        jsonb NOT NULL DEFAULT '[]',      -- [{type: web|image|video..., submitted, indexed}]
  fetched_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, path)
);

CREATE TABLE gsc_url_inspection (
  client_id        uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  url              text NOT NULL,
  inspected_at     timestamptz NOT NULL DEFAULT now(),
  verdict          text,          -- PASS, PARTIAL, FAIL, NEUTRAL
  coverage_state   text,          -- e.g. 'Submitted and indexed', 'Crawled - currently not indexed'
  indexing_state   text,
  robots_state     text,
  fetch_state      text,
  last_crawl       timestamptz,
  crawled_as       text,
  google_canonical text,
  user_canonical   text,
  rich_verdict     text,
  rich_types       text,
  PRIMARY KEY (client_id, url)
);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['gsc_sitemaps','gsc_url_inspection'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
