-- Monthly client report: new users and organic products from GA4, timestamps on work items so a month's work can be
-- listed, and the analyst's edits to a month's report (work done, plan of action).
ALTER TABLE ga4_daily ADD COLUMN new_users integer;
ALTER TABLE ga4_organic_landing ADD COLUMN new_users integer;
ALTER TABLE ga4_ai_referrals ADD COLUMN new_users integer;
ALTER TABLE tech_issues ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE keyword_ideas ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE ga4_organic_items (                 -- products bought in sessions from Google organic search, per month
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  month      date NOT NULL,                       -- first day of the month
  item_name  text NOT NULL,
  items_purchased integer, item_revenue numeric,
  PRIMARY KEY (client_id, month, item_name)
);
CREATE TABLE monthly_reports (
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  month      date NOT NULL,
  work_done  text,                                -- the analyst's version; empty = the automatic draft
  plan       text,
  updated_by text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, month)
);
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['ga4_organic_items','monthly_reports'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
