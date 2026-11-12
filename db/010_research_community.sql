-- Research lookups (Semrush keyword, domain, backlink, paid and shopping research) and community sources
-- (Reddit through Apify, YouTube through the YouTube Data API). Same tenant isolation; sample rows are flagged.

-- One stored Semrush lookup: the tool, what was looked up, and every section of the result
CREATE TABLE research_lookups (
  lookup_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  tool       text NOT NULL CHECK (tool IN ('keyword','domain','backlinks','paid')),
  query      text NOT NULL,                       -- keyword or domain
  database   text NOT NULL DEFAULT 'in',
  pulled_at  timestamptz NOT NULL DEFAULT now(),
  units      integer NOT NULL DEFAULT 0,
  result     jsonb NOT NULL,                      -- {"overview": {...}, "related": [...], ...}
  sample     boolean NOT NULL DEFAULT false
);
CREATE INDEX ON research_lookups (client_id, tool, pulled_at DESC);

-- ---------------- Reddit ----------------
CREATE TABLE reddit_items (
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  item_id    text NOT NULL,
  kind       text NOT NULL CHECK (kind IN ('post','comment')),
  subreddit  text,
  title      text,
  body       text,
  url        text,
  author     text,
  score      integer,
  comments   integer,
  created_at timestamptz,
  found_by   text NOT NULL,                       -- 'brand', 'competitor', 'topic' or 'cited'
  query      text,
  brands     text[] NOT NULL DEFAULT '{}',        -- brands mentioned, client first
  sentiment  text CHECK (sentiment IN ('positive','neutral','negative')),
  is_question boolean NOT NULL DEFAULT false,
  pain_point  text,                               -- matched pain-point theme, if any
  topic      text,
  pulled_on  date NOT NULL,
  sample     boolean NOT NULL DEFAULT false,
  PRIMARY KEY (client_id, item_id)
);
CREATE TABLE apify_usage (
  usage_id  bigserial PRIMARY KEY,
  client_id uuid REFERENCES clients ON DELETE SET NULL,
  actor     text NOT NULL,
  items     integer NOT NULL,
  run_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------- YouTube ----------------
CREATE TABLE youtube_videos (
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  video_id    text NOT NULL,
  title       text,
  description text,
  channel_id  text,
  channel     text,
  published_at timestamptz,
  views bigint, likes bigint, comments bigint,
  duration_s  integer,
  found_by    text NOT NULL,                      -- 'brand', 'competitor', 'topic' or 'cited'
  query       text,
  search_rank integer,                            -- position in the YouTube search for `query`
  brands      text[] NOT NULL DEFAULT '{}',
  own_channel boolean NOT NULL DEFAULT false,
  pulled_on   date NOT NULL,
  sample      boolean NOT NULL DEFAULT false,
  PRIMARY KEY (client_id, video_id)
);
CREATE TABLE youtube_channels (
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  channel_id  text NOT NULL,
  title       text,
  subscribers bigint,
  videos      integer,
  views       bigint,
  pulled_on   date NOT NULL,
  sample      boolean NOT NULL DEFAULT false,
  PRIMARY KEY (client_id, channel_id)
);
CREATE TABLE youtube_comments (
  client_id  uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  comment_id text NOT NULL,
  video_id   text NOT NULL,
  text       text,
  likes      integer,
  published_at timestamptz,
  is_question boolean NOT NULL DEFAULT false,
  pain_point text,
  pulled_on  date NOT NULL,
  sample     boolean NOT NULL DEFAULT false,
  PRIMARY KEY (client_id, comment_id)
);
CREATE TABLE youtube_quota (
  day   date PRIMARY KEY,
  units integer NOT NULL DEFAULT 0
);
ALTER TABLE clients ADD COLUMN youtube_channel_id text,           -- the brand's own channel, if it has one
  ADD COLUMN community_topics text[] NOT NULL DEFAULT '{}';      -- topics searched on Reddit and YouTube (empty: top collection keywords)

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['research_lookups','reddit_items','youtube_videos','youtube_channels','youtube_comments'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO aeo_app', t);
  END LOOP;
END $$;
-- usage logs are product-wide (like semrush_usage): staff only
GRANT SELECT, INSERT ON apify_usage, youtube_quota TO aeo_app;
GRANT UPDATE ON youtube_quota TO aeo_app;
GRANT USAGE ON SEQUENCE apify_usage_usage_id_seq TO aeo_app;
