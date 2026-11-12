-- Google sign-in (OAuth) instead of per-client service-account access.
-- A Hovers staff member signs in once with a Google account that already has access to client
-- Search Console sites and GA4 properties. We store that account's refresh token (encrypted),
-- discover every site and property it can see, and staff import them as clients in one step.

CREATE TABLE google_connections (
  connection_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  google_email       text NOT NULL UNIQUE,
  refresh_token_enc  bytea NOT NULL,                    -- Fernet-encrypted with TOKEN_KEY; never stored in plain text
  scopes             text NOT NULL,
  status             text NOT NULL DEFAULT 'active',    -- 'active' | 'revoked' (token rejected by Google)
  last_error         text,
  connected_at       timestamptz NOT NULL DEFAULT now(),
  last_discovered_at timestamptz
);

CREATE TABLE google_gsc_sites (              -- every Search Console property the connected account can see
  connection_id    uuid NOT NULL REFERENCES google_connections ON DELETE CASCADE,
  site_url         text NOT NULL,            -- 'sc-domain:example.com' or 'https://www.example.com/'
  permission_level text NOT NULL,            -- siteOwner | siteFullUser | siteRestrictedUser
  discovered_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (connection_id, site_url)
);

CREATE TABLE google_ga4_properties (         -- every GA4 property the connected account can see
  connection_id uuid NOT NULL REFERENCES google_connections ON DELETE CASCADE,
  property_id   text NOT NULL,               -- numeric id, e.g. '123456789'
  display_name  text NOT NULL,
  account_name  text,
  web_uris      text[] NOT NULL DEFAULT '{}', -- default URIs of the property's web data streams, used for matching
  discovered_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (connection_id, property_id)
);

ALTER TABLE client_integrations ADD COLUMN google_connection_id uuid REFERENCES google_connections ON DELETE SET NULL;
CREATE UNIQUE INDEX client_integrations_gsc_site_uq ON client_integrations (gsc_site_url) WHERE gsc_site_url IS NOT NULL;

-- Connections and discovery results hold staff-level access, so clients never see them.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['google_connections','google_gsc_sites','google_ga4_properties'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY staff_only ON %I USING (coalesce(current_setting(''app.is_staff'', true),'''') = ''true'')', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
