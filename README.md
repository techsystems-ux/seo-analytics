# AEO platform: free-API collectors (no n8n needed)

Plain Python scripts. Schedule with cron; n8n can call them later if you want it, but doesn't have to.

## How clients get connected: Google sign-in
A Hovers staff member signs in once with a Google account that already has access to client properties.
The app lists every verified Search Console site and GA4 property that account can see, matches each site
to its GA4 property by website address, and imports the ones you tick as clients. No per-client service-account
invitations and no typing property IDs by hand. Sign in with more than one account if access is spread across accounts.

Access is read-only (`webmasters.readonly`, `analytics.readonly`). The refresh token is stored encrypted.
Clients never see connections or tokens (staff-only row-level security).

## One-time setup
1. Apply migrations in order: `db/schema.sql`, `002_pagespeed.sql`, `003_gsc_ga4.sql`, `004_google_oauth.sql`.
2. `pip install "psycopg[binary]" google-auth requests flask cryptography`
3. Google Cloud project (console.cloud.google.com):
   - Enable **Google Search Console API**, **Google Analytics Data API** and **Google Analytics Admin API**.
   - **OAuth consent screen**: user type **Internal** if the signing-in accounts are on the hovers.in Google Workspace.
     (External in "Testing" mode works for a trial, but Google expires the sign-in after 7 days.)
     Add the scopes `.../auth/webmasters.readonly` and `.../auth/analytics.readonly`.
   - **Credentials → Create credentials → OAuth client ID → Web application.** Authorised redirect URI:
     `http://localhost:8080/auth/google/callback` for a local trial, or `https://<your app domain>/auth/google/callback` on the server.
4. Env:
   - `DATABASE_URL` (the `aeo_app` role)
   - `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `OAUTH_REDIRECT_URI` (same as in step 3)
   - `TOKEN_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it safe: losing it means signing in again.
   - `FLASK_SECRET`: any long random string
   - `ALLOWED_EMAIL_DOMAINS=hovers.in` (only these Google accounts can sign in)
   - optional `PSI_API_KEY`
5. Start the connect page: `flask --app webapp/app.py run --port 8080`, open it, **Sign in with Google**, tick the sites to import, check the GA4 matches, **Import selected**.
6. Per client: add the pages to test in `client_urls` (PageSpeed) and brand variants in `clients.aliases`.

Service accounts still work as a fallback: a client with no Google sign-in linked uses `GOOGLE_APPLICATION_CREDENTIALS`.

## Schedule (cron)
    0 5 * * *   cd /opt/aeo/collectors && python run_free.py --only gsc,ga4 --days 7
    0 6 * * 1   cd /opt/aeo/collectors && python run_free.py --only pagespeed
First load of history: `python gsc.py --days 480` (Search Console keeps about 16 months).
New sites added to the Google account later: open the connect page and press **Refresh list**, then import.

## Notes
- If Google rejects a stored sign-in (password change, access removed, consent revoked), the connection shows **Reconnect** and its clients are skipped with a clear error until someone signs in again.
- Data is only as broad as the signed-in account's access. A "Restricted" Search Console user still gets search performance data.
- GSC and GA4 loads are upserts over an overlapping window, so reruns are safe and late data self-corrects.
- `is_branded` in `gsc_search_daily` uses the client's name, domain stem and `clients.aliases`. Fill aliases per client.
- `ga4_ai_referrals` only catches traffic whose source is visible (chatgpt.com, perplexity, ...). AI traffic with no referrer lands in Direct, so treat it as a floor.
- Run history and errors: `collector_runs` (staff only).

## Tests
    python tests/test_oauth.py      # sign-in, discovery, import, revocation, isolation (fake Google)
    python tests/test_gsc_ga4.py
    python tests/test_pagespeed.py

## Run it on a server (Render)

The repo carries a Render Blueprint (`render.yaml`): the web app, a nightly data pull and a Postgres database, in Singapore.

1. On render.com: New, Blueprint, pick this repository. Render creates the database, the web service and the nightly job.
2. In the Render dashboard, open the environment group **hovers-secrets** and add:
   `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `OPENAI_API_KEY`, `SEMRUSH_API_KEY`. Keys never go in this repo.
3. In Google Cloud, on the same OAuth client, add `https://<your-service>.onrender.com/auth/google/callback` as an authorised redirect URI.
4. Open the web address, sign in with a hovers.in Google account, connect Google, and import the brand. Gargi (gargi.shop)
   is already set up from `seeds/gargi.json` with draft prompts: approve them in Settings, Prompts.

Every deploy runs `scripts/migrate.py` first (new tables, then seed files). The nightly job (`collectors/daily.py`, 03:10 IST)
pulls Search Console, GA4 and ChatGPT for every brand and checks pages on Mondays.

**Semrush credit limit.** `SEMRUSH_TOTAL_CAP` (default 500) is a lifetime limit on Semrush units across all brands and pulls,
counted from `semrush_usage`, including the 400 units used to build Gargi's seed data. A call that could pass the limit is never made.
Semrush never runs on the schedule (unless `SEMRUSH_AUTO=1`); it runs only from Settings, Data.

## Tests

`ADMIN_DATABASE_URL=postgresql://postgres@/postgres?host=/tmp python tests/test_analytics_e2e.py` runs the whole analytics flow against
a throwaway database with fake Semrush and OpenAI responses; the other files in `tests/` cover page checks, collectors and sign-in.

## Technical audit, rankings, content ideas, backlinks (migration 007)

| Feature | Where | Data | Cost |
|---|---|---|---|
| Technical audit | Work → Technical | `seo/crawler.py`, every Monday and on the button | free |
| Semrush Site Audit import | Work → Technical | `collectors/semrush_projects.py audit` (info + issue names + pages for top errors) | up to 700 credits |
| Rankings | Search performance → Rankings | `collectors/rank.py` from stored Search Console rows, nightly | free |
| Semrush Position Tracking import | Search performance → Rankings | `collectors/semrush_projects.py rank` | 100 per keyword |
| Keyword gap | Competitors → Untapped keywords | `collectors/semrush_suite.py gap` | 80 per keyword |
| Content ideas | Work → Content ideas | `collectors/semrush_suite.py ideas` (questions + related) | 40 per idea |
| Backlinks and link prospects | Competitors → Backlinks | `collectors/semrush_suite.py backlinks` | 40 per line or request |
| SERP features (AI Overview, featured snippet, ...) | Competitors → Your ranking keywords, Rankings | Fk/Fp columns on the keyword pull | no extra cost |

Semrush features never run on the schedule. Each button shows its worst-case cost and is disabled when the key is missing,
the project ID is missing (Site Audit, Position Tracking) or the cost passes what `SEMRUSH_TOTAL_CAP` leaves. Optional
per-feature caps: `SEMRUSH_CAP_IDEAS`, `SEMRUSH_CAP_BACKLINKS`, `SEMRUSH_CAP_GAP`, `SEMRUSH_CAP_AUDIT`, `SEMRUSH_CAP_RANK`.
The 10,000-credit Site Audit snapshot report is never called.

Test: `DATABASE_URL=... python tests/test_semrush_suite.py` (fake site and fake Semrush, no credits).

## Research and Community (migration 010)

Side panel → **Research**: Keyword research, Domain research, Backlink analytics, Paid and shopping
(`collectors/semrush_research.py`). Each lookup is one button with its worst-case credits shown, capped like every
Semrush call (`SEMRUSH_TOTAL_CAP`, optional `SEMRUSH_CAP_RESEARCH_<TOOL>`), and stored so reopening it is free. Rows can
be sent to Rankings (Track) or Content ideas (Plan content).

Side panel → **Community**:
- **Reddit** (`collectors/reddit.py`, Apify, `APIFY_TOKEN`): brand vs competitor mentions and tone, subreddit map with
  each brand's mentions per community, questions and pain points, and the Reddit threads ChatGPT cites. Paid per
  result, capped by `APIFY_MAX_ITEMS` (per run, default 300) and `APIFY_TOTAL_ITEMS` (lifetime, default 1000).
  Actor: `APIFY_REDDIT_ACTOR` (default `trudax~reddit-scraper-lite`).
- **YouTube** (`collectors/youtube.py`, YouTube Data API, `YOUTUBE_API_KEY`): share of voice in topic searches, videos
  naming each brand, creators, questions and pain points from comments, videos ChatGPT cites. Free daily quota
  (`YOUTUBE_DAILY_QUOTA`, default 10,000; a run stops at 90%). Runs every Monday once the key is set.

Nothing is typed in: topics are found from the brand's collections, ranked by non-branded Search Console searches
(brand and competitor names, sale and price pages left out); Reddit communities are found for those topics; the
brand's own YouTube channel is found by name. Once a key is set, the first pull starts by itself (when the app starts
or the screen opens) and then refreshes weekly; Reddit's automatic run uses `APIFY_AUTO_ITEMS` (default 150) results.
`COMMUNITY_AUTO=0` turns this off. Topics can still be overridden under "Adjust topics".
Settings → Data → Show sample data fills all of these screens with marked sample rows.
