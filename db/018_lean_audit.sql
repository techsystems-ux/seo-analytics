-- Lean technical audit: store issues, not pages. One-off clean-up of what earlier crawls and page checks kept.
-- From now on the crawler keeps the affected-URL list of the latest crawl only (capped per issue: errors 500,
-- warnings 100, notices 25; the full count stays in tech_issues.pages), and page checks keep one small record per page.
SET LOCAL app.is_staff = 'true';                        -- these tables force row-level security, even for their owner

-- 1. Affected-URL lists: the latest crawl per brand and source only
DELETE FROM tech_issue_pages p
WHERE p.crawl_id <> (SELECT c.crawl_id FROM tech_crawls c
                     WHERE c.client_id = p.client_id
                       AND c.source = (SELECT source FROM tech_crawls WHERE crawl_id = p.crawl_id)
                     ORDER BY c.finished_at DESC NULLS LAST, c.started_at DESC LIMIT 1);

-- 2. ... and capped per issue
WITH ranked AS (
  SELECT p.ctid AS rid, row_number() OVER (PARTITION BY p.crawl_id, p.issue_code ORDER BY p.url) AS rn,
         CASE i.severity WHEN 'error' THEN 500 WHEN 'warning' THEN 100 WHEN 'notice' THEN 25 ELSE 100 END AS cap
  FROM tech_issue_pages p LEFT JOIN tech_issues i ON i.client_id = p.client_id AND i.issue_code = p.issue_code)
DELETE FROM tech_issue_pages WHERE ctid IN (SELECT rid FROM ranked WHERE rn > cap);

-- 3. Page checks: the latest live and the latest draft record per page (older ones and their check rows go)
DELETE FROM page_snapshots s
WHERE EXISTS (SELECT 1 FROM page_snapshots n WHERE n.page_id = s.page_id AND n.source = s.source
              AND (n.taken_at > s.taken_at OR (n.taken_at = s.taken_at AND n.snapshot_id > s.snapshot_id)));

-- 4. ... and only the fields the screens and cross-page checks read (no page text, link or image lists)
UPDATE page_snapshots SET content = coalesce((SELECT jsonb_object_agg(k, v) FROM jsonb_each(content) AS e(k, v)
                                              WHERE k IN ('url','title','description','h1s','word_count','canonical','robots','schema_types','faq_count')), '{}'::jsonb)
WHERE content ?| ARRAY['text','links','images','headings'];
