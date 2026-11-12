\set ON_ERROR_STOP on
-- seed two clients as staff
SET app.is_staff = 'true';
INSERT INTO clients (client_id, name, domain) VALUES
  ('11111111-1111-1111-1111-111111111111','Client A','a.example'),
  ('22222222-2222-2222-2222-222222222222','Client B','b.example');
INSERT INTO prompts (client_id, text, approved) VALUES
  ('11111111-1111-1111-1111-111111111111','best protein bar in India', true),
  ('22222222-2222-2222-2222-222222222222','best lab-grown diamond ring', true);
SELECT 'staff sees prompts' AS check, count(*) FROM prompts;      -- expect 2
RESET app.is_staff;

SET app.client_id = '11111111-1111-1111-1111-111111111111';
SELECT 'client A sees prompts' AS check, count(*) FROM prompts;  -- expect 1
SELECT 'client A sees clients' AS check, count(*) FROM clients;  -- expect 1
-- cross-tenant write must fail
DO $$ BEGIN
  INSERT INTO prompts (client_id, text) VALUES ('22222222-2222-2222-2222-222222222222','sneaky');
  RAISE EXCEPTION 'LEAK: cross-tenant insert succeeded';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK: cross-tenant insert blocked';
END $$;
RESET app.client_id;

-- no context at all => nothing visible
SELECT 'no context sees prompts' AS check, count(*) FROM prompts; -- expect 0
