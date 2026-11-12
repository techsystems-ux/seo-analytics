-- Free-API layer 1: PageSpeed Insights (Core Web Vitals). Same tenant isolation as schema.sql.
CREATE TABLE client_urls (
  url_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  url       text NOT NULL,
  page_type text,                        -- home, collection, product, blog ...
  active    boolean NOT NULL DEFAULT true,
  UNIQUE (client_id, url)
);

CREATE TABLE pagespeed_results (
  result_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  url         text NOT NULL,
  strategy    text NOT NULL CHECK (strategy IN ('mobile','desktop')),
  fetched_at  timestamptz NOT NULL DEFAULT now(),
  perf_score  smallint,                  -- 0-100 (Lighthouse lab)
  lcp_ms      numeric, cls numeric, tbt_ms numeric, fcp_ms numeric, ttfb_ms numeric,   -- lab
  field_lcp_ms numeric, field_cls numeric, field_inp_ms numeric, field_category text,   -- real-user (CrUX), null if too little traffic
  error       text
);
CREATE INDEX ON pagespeed_results (client_id, url, strategy, fetched_at DESC);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['client_urls','pagespeed_results'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
