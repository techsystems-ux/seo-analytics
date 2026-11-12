-- Sister brands: a brand's own other businesses, kept out of competitor tracking, keyword gap and backlinks, and
-- treated as the brand's family when labelling branded searches.
ALTER TABLE competitors ADD COLUMN own_brand boolean NOT NULL DEFAULT false;

-- Gargi, confirmed by the account lead on 12 Nov 2026: P N Gadgil & Sons is the group's own brand;
-- PNG Exclusive and PNG Brothers are competitors. Applies only where that brand exists.
SELECT set_config('app.is_staff', 'true', true);
UPDATE competitors c SET own_brand = true, tracked = false
  FROM clients k WHERE c.client_id = k.client_id AND k.domain ILIKE '%gargi.shop%' AND c.domain = 'pngadgilandsons.com';
UPDATE competitors c SET tracked = true,
       aliases = (SELECT array(SELECT DISTINCT unnest(c.aliases || CASE c.domain WHEN 'pngexclusive.com' THEN ARRAY['PNG Exclusive']
                                                                                 ELSE ARRAY['PNG Brothers'] END)))
  FROM clients k WHERE c.client_id = k.client_id AND k.domain ILIKE '%gargi.shop%' AND c.domain IN ('pngexclusive.com', 'pngbrothers.com');
UPDATE brand_rules b SET sister_domains = (SELECT array(SELECT DISTINCT unnest(coalesce(b.sister_domains, '{}') || ARRAY['pngadgilandsons.com'])))
  FROM clients k WHERE b.client_id = k.client_id AND k.domain ILIKE '%gargi.shop%';
