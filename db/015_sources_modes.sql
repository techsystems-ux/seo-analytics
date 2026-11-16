-- Niche-aware "sources AI trusts": each brand has a niche (edtech, manufacturing, ...) that brings a starting list of the
-- high-authority sites LLMs lean on in that category; the team can add their own and track the brand's listing on each.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS niche text;
CREATE TABLE client_sources (
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  domain      text NOT NULL CHECK (domain = lower(domain)),
  name        text,
  kind        text,                                   -- directory, review, q&a, forum, video, encyclopedia, marketplace, media
  status      text NOT NULL DEFAULT 'unknown' CHECK (status IN ('unknown','listed','in_progress','not_listed','hidden')),
  listing_url text,
  note        text,
  updated_by  text,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, domain)
);
ALTER TABLE client_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON client_sources USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON client_sources TO aeo_app;

-- "Talk to sales" requests from the public Orbit page. No brand yet, so staff-only (not tenant data).
CREATE TABLE demo_requests (
  request_id  bigserial PRIMARY KEY,
  name        text NOT NULL,
  email       text NOT NULL,
  company     text,
  website     text,
  role        text,
  message     text,
  source      text,                                   -- utm source / campaign, if the visitor came from an ad
  created_at  timestamptz NOT NULL DEFAULT now(),
  status      text NOT NULL DEFAULT 'new'
);
ALTER TABLE demo_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE demo_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY staff_only ON demo_requests USING (coalesce(current_setting('app.is_staff', true),'') = 'true');
GRANT SELECT, INSERT, UPDATE ON demo_requests TO aeo_app;
GRANT USAGE ON SEQUENCE demo_requests_request_id_seq TO aeo_app;
