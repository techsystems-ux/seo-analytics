# Orbit by Hovers

Search intelligence across Google and AI answers. For each brand Orbit reads Search Console, GA4, ChatGPT answers,
Semrush, Reddit and YouTube, and turns them into one view (Overview, Search performance, AI visibility, Competitors,
Reddit, YouTube, Pages, Technical, Content ideas) and a monthly PowerPoint report. Hovers staff sign in with hovers.in
Google accounts; clients can be given a read-only view of their own brand.

## Quick map of the code

| Folder / file | What it is |
|---|---|
| `webapp/` | Flask app: `app.py` (sign-in, Google connections), `dashboard.py` (all screens), `portal.py` (client sign-in), `analytics.py` (queries), `report.py` (monthly deck), `templates/` (Jinja screens; `login.html` is the sign-in page, `base.html` the app shell) |
| `collectors/` | Data pulls: `gsc.py`, `ga4.py`, `rank.py`, `ai_visibility.py` (ChatGPT), `semrush*.py`, `reddit.py` (Apify), `youtube.py`, `community.py`; `daily.py` runs them all with credit caps |
| `seo/` | Site crawler, page checks, sitemap and keyword mapping |
| `db/` | Postgres schema and numbered migrations (row-level security keeps each brand's data separate) |
| `scripts/` | Brand setup from `seeds/`, sample data, `copy_to_cloud.py` |
| `api/index.py`, `vercel.json`, `pyproject.toml` | The online copy on Vercel (Singapore), database on Neon |
| `start_local.py` | Runs everything on a Mac: private Postgres or the online database, the web app, and the daily pull |
| `tests/` | End-to-end and unit tests (`python tests/<file>.py`, need a Postgres) |

Keys are never in this repository. Locally they live in `.env`; online in Vercel's environment variables.

## Original notes: free-API collectors

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

## Run it online (Vercel + Neon, pulls from the Mac)

The online copy is the Vercel project **orbit** at https://orbit-hovers.vercel.app (Singapore), deployed from `main` on every push. It reads and
writes a Neon Postgres database. Vercel cannot run long jobs, so data pulls run on the Hovers Mac:

1. **Database.** Vercel, project, Storage, Neon (Singapore), connected to the project: this sets `DATABASE_URL`.
   Tables are created on the first visit (`api/index.py`).
2. **Settings.** In Vercel add `TOKEN_KEY` (the Mac's value), `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`. Until
   they are in place the site lists what is missing. Add `<Vercel address>/auth/google/callback` on the Google OAuth client.
3. **Copy the Mac's data.** Put the Neon string in the Mac's `.env` as `CLOUD_DATABASE_URL=...` and double-click
   *Copy data to cloud.command*. It copies everything (Semrush usage included, so the cap keeps counting) and adds
   `USE_CLOUD_DB=1` to `.env`.
4. **From then on** the Mac app (Start SEO Analytics) uses the online database, so both show the same data. While it is
   open it runs the daily pull once a day and any pull asked for on the online copy (`pull_queue`), one at a time, within
   a minute. Pulls only happen while the Mac app is open. Set `USE_CLOUD_DB=0` to go back to the private database.

The Render files (`render.yaml`, `Dockerfile`, `.github/workflows/nightly.yml`) are an alternative host and are not used.

**Semrush credit limit.** `SEMRUSH_TOTAL_CAP` (600 online) is a lifetime limit on Semrush units across all brands and pulls,
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

## Monthly report (migration 012)

Side panel → Reports → **Monthly report**: pick the month (default: last complete month) and press Download PowerPoint.
The 10-slide deck (cover, contents, keyword rankings, search performance, organic traffic, organic revenue, branded vs
non-branded, top URLs, work done, plan of action) is built from stored Search Console and GA4 data by `webapp/report.py`
with python-pptx; no API calls. Work done is drafted from what was marked done in the tool that month (fixed and
verified technical issues, content ideas marked written, pages audited, crawls); the plan from keyword gaps, content
ideas, slipping pages and open technical issues. Analysts can edit both on the screen; edits are saved per month.
GA4 now also stores new users and organic product sales; the first pull after this update fetches 14 months of GA4
(same-month-last-year comparisons) and 7 months of Search Console (the branded chart).

## Client sign-in (migration 014)

Staff list a brand's people in Settings, Client access. They sign in at `<site>/portal` with Google or an emailed link and
see that brand only, read-only: Overview, Search performance, AI visibility, Competitors, Reddit, YouTube and the monthly
report download. Every form, pull, credit figure and staff screen is refused to them by the server, and their database
session is narrowed to their brand (not staff), so another brand's rows cannot appear even through a query mistake.

- Google: a second OAuth client (Google Cloud, OAuth consent screen External, scopes openid/email/profile only, so no
  Google review). Env `CLIENT_OAUTH_CLIENT_ID`, `CLIENT_OAUTH_CLIENT_SECRET`; redirect URI `<site>/portal/auth/google/callback`.
- Email link: a Resend account with the sending domain verified. Env `RESEND_API_KEY`, `MAIL_FROM` (e.g. `Hovers <reports@hovers.in>`).
  Links work once, for 15 minutes, at most 5 an hour per email; the page answers the same whether or not the email has access.
- Removing an email in Settings ends that person's access on their next click.
