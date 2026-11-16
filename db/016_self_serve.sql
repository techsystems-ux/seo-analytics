-- Self-serve trials: a visitor signs up with Google, connects Search Console and GA4 in the same consent, picks their site,
-- and becomes the owner of a new brand with a 14-day trial. Owners see the client (read-only) view of their own brand.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS trial_ends date;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS signup_email text;
ALTER TABLE client_viewers ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer','owner'));
