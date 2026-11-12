-- Subreddits found automatically for a brand's topics (Reddit community search), with size, so the subreddit map
-- can show communities that matter even before the brand is mentioned in them.
CREATE TABLE reddit_communities (
  client_id   uuid NOT NULL REFERENCES clients ON DELETE CASCADE,
  name        text NOT NULL,                      -- without r/
  title       text,
  description text,
  members     integer,
  found_for   text,                               -- the topic that found it
  relevance   numeric,
  pulled_on   date NOT NULL,
  sample      boolean NOT NULL DEFAULT false,
  PRIMARY KEY (client_id, name)
);
ALTER TABLE reddit_communities ENABLE ROW LEVEL SECURITY;
ALTER TABLE reddit_communities FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON reddit_communities USING (app_can_see(client_id)) WITH CHECK (app_can_see(client_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON reddit_communities TO aeo_app;
