-- What a billed reply was for, so blog writing has its own limit inside Claude's overall one.
ALTER TABLE api_spend ADD COLUMN purpose text NOT NULL DEFAULT 'answers';
CREATE INDEX api_spend_purpose ON api_spend (provider, purpose);
