-- Sample rows for reviewing the Semrush screens before credits are approved. Every sample row is flagged, shown
-- under a "Sample data" banner and removed in one click (Settings → Data). Real pulls never touch flagged rows.
ALTER TABLE clients ADD COLUMN sample_semrush boolean NOT NULL DEFAULT false;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['keyword_ideas','keyword_gaps','backlink_overview','backlink_refdomains','backlink_gap',
                           'tech_crawls','tech_issues','rank_positions','rank_keywords'] LOOP
    EXECUTE format('ALTER TABLE %I ADD COLUMN sample boolean NOT NULL DEFAULT false', t);
  END LOOP;
END $$;
ALTER TABLE semrush_keywords ADD COLUMN sample_features boolean NOT NULL DEFAULT false;
