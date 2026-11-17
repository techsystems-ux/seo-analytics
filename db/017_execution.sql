-- Execution: blog drafts written from the brand's own search data, reviewed in Orbit, then published to the store.
-- One row per blog, from idea to published. Platform-neutral so WordPress can follow Shopify.
CREATE TABLE content_drafts (
  draft_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id         uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  kind              text NOT NULL DEFAULT 'blog',
  status            text NOT NULL DEFAULT 'idea' CHECK (status IN ('idea','writing','draft','approved','published','failed','discarded')),
  target_keyword    text NOT NULL,
  secondary_keywords text[] NOT NULL DEFAULT '{}',
  intent            text,                                -- informational, commercial, comparison
  angle             text,                                -- the brief: what the post answers and for whom
  evidence          jsonb NOT NULL DEFAULT '[]',         -- why this topic: [{source: gsc|prompt|reddit|suggest, text, impressions, position, ...}]
  priority          numeric,
  title             text,
  meta_title        text,
  meta_description  text,
  slug              text,
  excerpt           text,
  tags              text[] NOT NULL DEFAULT '{}',
  body_html         text,
  faqs              jsonb NOT NULL DEFAULT '[]',         -- [{q, a}]
  sources           jsonb NOT NULL DEFAULT '[]',         -- [{url, title}] cited in the body
  internal_links    text[] NOT NULL DEFAULT '{}',
  word_count        integer,
  checks            jsonb,                               -- on-page check results for the draft (same checks as live pages)
  score             integer,
  preview_token     text UNIQUE,
  model             text,
  error             text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  written_at        timestamptz,
  edited_by         text,
  reviewed_by       text,
  reviewed_at       timestamptz,
  published_by      text,
  published_at      timestamptz,
  platform          text,                                -- shopify, wordpress
  external_id       text,                                -- e.g. gid://shopify/Article/123
  published_url     text
);
CREATE INDEX ON content_drafts (client_id, status, priority DESC);
CREATE UNIQUE INDEX content_drafts_topic ON content_drafts (client_id, lower(target_keyword)) WHERE status <> 'discarded';
ALTER TABLE content_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_drafts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON content_drafts USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON content_drafts TO aeo_app;

-- The store each brand publishes to. Tokens are encrypted with TOKEN_KEY, like the Google tokens.
-- app_client_id / app_secret_enc: a per-store Shopify app (custom distribution), when the brand does not use the shared Orbit app.
CREATE TABLE cms_connections (
  client_id          uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  platform           text NOT NULL CHECK (platform IN ('shopify','wordpress')),
  shop               text,                               -- xxx.myshopify.com
  app_client_id      text,
  app_secret_enc     bytea,
  access_token_enc   bytea,
  refresh_token_enc  bytea,
  expires_at         timestamptz,
  refresh_expires_at timestamptz,
  scopes             text,
  blog_id            text,                               -- where articles go, e.g. gid://shopify/Blog/123
  blog_handle        text,
  blog_title         text,
  blogs              jsonb NOT NULL DEFAULT '[]',        -- [{id, title, handle}] to choose from
  author_name        text,
  primary_domain     text,                               -- https://www.brand.com, for live article links
  status             text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','reconnect')),
  last_error         text,
  connected_by       text,
  connected_at       timestamptz,
  updated_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, platform)
);
ALTER TABLE cms_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE cms_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cms_connections USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON cms_connections TO aeo_app;
