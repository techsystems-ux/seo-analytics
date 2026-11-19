"""Local preview data for designing screens: 12 weeks of ChatGPT results for a brand and three competitors, AI and
organic sessions from GA4, Semrush keywords with AI Overview flags and tracked Google positions. Run after
test_analytics_e2e.py (it fills the 'Gargi' brand in aeo_e2e). Never runs on the server; numbers are invented for layout."""
import os, random, datetime as dt, uuid, psycopg

DB = os.environ.get("PREVIEW_DB", "postgresql://postgres@/aeo_e2e?host=/tmp&port=5433")
random.seed(7)
today = dt.date.today()
with psycopg.connect(DB, autocommit=True) as c:
    cid = c.execute("SELECT client_id FROM clients WHERE name='Gargi'").fetchone()[0]
    for t in ("scores_daily", "ga4_ai_referrals", "rank_positions"):
        c.execute(f"DELETE FROM {t} WHERE client_id=%s", [cid])
    c.execute("DELETE FROM runs WHERE client_id=%s", [cid])
    brands = {"": (52, 0.55), "GIVA": (61, 0.66), "Palmonas": (44, 0.48), "Zaveri Pearls": (31, 0.33)}
    # 1. weekly visibility scores
    for w in range(12, -1, -1):
        d = today - dt.timedelta(days=7 * w)
        for b, (base, mr) in brands.items():
            drift = (12 - w) * (1.1 if b == "" else 0.4)
            score = max(5, min(95, base - 8 + drift + random.uniform(-4, 4)))
            c.execute("""INSERT INTO scores_daily (client_id, engine, region, date, samples, mention_rate, citation_rate, share_of_voice,
                           avg_position, visibility_score, brand, sentiment) VALUES (%s,'chatgpt','IN',%s,20,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT DO NOTHING""",
                      [cid, d, min(1, mr + random.uniform(-.05, .05) + drift / 200), round(random.uniform(.1, .4), 3),
                       round(score / 220, 3), round(random.uniform(1.5, 4), 2), round(score, 2), b, round(random.uniform(.55, .9), 3)])
    # 2. prompts and two runs of answers
    texts = ["best silver jewellery brands in india", "where to buy real silver anklets online", "gold plated earrings that do not tarnish",
             "pune jewellery stores for bridal sets", "affordable silver rings for daily wear", "is 925 silver worth it",
             "best gifts for wife under 5000 jewellery", "temple jewellery online shopping", "oxidised silver jhumkas brands",
             "how to clean silver jewellery at home", "lab grown diamond pendants india", "trusted online jewellery stores india"]
    c.execute("UPDATE prompts SET approved=false WHERE client_id=%s", [cid])
    pids = []
    for i, t in enumerate(texts):
        r = c.execute("SELECT prompt_id FROM prompts WHERE client_id=%s AND text=%s", [cid, t]).fetchone()
        pid = r[0] if r else c.execute("""INSERT INTO prompts (client_id, text, funnel_stage, approved, active) VALUES (%s,%s,%s,true,true)
                                          RETURNING prompt_id""", [cid, t, ["awareness", "consideration", "decision"][i % 3]]).fetchone()[0]
        c.execute("UPDATE prompts SET approved=true, active=true WHERE prompt_id=%s", [pid]); pids.append(pid)
    for k, cycle in enumerate((today - dt.timedelta(days=7), today)):
        for i, pid in enumerate(pids):
            run = c.execute("""INSERT INTO runs (client_id, prompt_id, engine, region, sample_no, cycle, status, run_at)
                               VALUES (%s,%s,'chatgpt','IN',1,%s,'ok',%s) RETURNING run_id""", [cid, pid, cycle, cycle]).fetchone()[0]
            ans = c.execute("INSERT INTO answers (client_id, run_id, full_text, engine_version) VALUES (%s,%s,%s,'gpt-preview') RETURNING answer_id",
                            [cid, run, "Preview answer text for layout."]).fetchone()[0]
            named = [b for b in brands if random.random() < brands[b][1] + (0.1 if k else 0)]
            for rank, b in enumerate(named, 1):
                c.execute("""INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer, sentiment, recommended)
                             VALUES (%s,%s,%s,%s,%s,%s,%s)""", [cid, ans, b or "Gargi", b == "", rank,
                                                                random.choice(["positive", "positive", "neutral", "negative"]), rank <= 2])
            for dom in random.sample(["reddit.com", "youtube.com", "gargi.shop", "giva.co", "vogue.in", "myntra.com", "quora.com", "palmonas.com"], 3):
                c.execute("""INSERT INTO citations (client_id, answer_id, url, domain, cited, is_client) VALUES (%s,%s,%s,%s,true,%s)""",
                          [cid, ans, f"https://{dom}/x{i}", dom, dom == "gargi.shop"])
    # 3. GA4: AI referrals and organic sessions
    for n in range(90):
        d = today - dt.timedelta(days=n)
        for src, base in (("chatgpt.com", 22), ("perplexity.ai", 6), ("gemini.google.com", 4), ("copilot.microsoft.com", 1)):
            s = max(0, int(base * (1 + (90 - n) / 90) + random.uniform(-3, 3)))
            c.execute("""INSERT INTO ga4_ai_referrals (client_id, date, source, sessions, engaged_sessions, key_events, transactions, revenue, new_users)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                      [cid, d, src, s, int(s * .72), round(s * .08, 1), int(s * .03), round(s * 41.5, 2), int(s * .8)])
        c.execute("""INSERT INTO ga4_daily (client_id, date, channel_group, sessions, engaged_sessions, key_events, transactions, revenue, new_users)
                     VALUES (%s,%s,'Organic Search',%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, date, channel_group) DO UPDATE SET sessions=EXCLUDED.sessions""",
                  [cid, d, s2 := 900 + random.randint(-80, 80), int(s2 * .61), round(s2 * .05), int(s2 * .02), round(s2 * 28.0, 2), int(s2 * .7)])
    # 4. Semrush keywords with AI Overview (feature 52) and tracked Google positions
    dom = "gargi.shop"
    c.execute("DELETE FROM semrush_keywords WHERE client_id=%s AND domain=%s", [cid, dom])
    kws = [("silver anklets", 4, 14800), ("925 silver rings", 7, 9900), ("oxidised jhumkas", 3, 22200), ("silver payal design", 12, 6600),
           ("gold plated earrings", 18, 12100), ("silver chain for men", 9, 8100), ("temple jewellery", 25, 5400), ("silver toe rings", 2, 4400)]
    for i, (k, pos, vol) in enumerate(kws):
        c.execute("""INSERT INTO semrush_keywords (client_id, domain, database, pulled_on, keyword, position, prev_position, search_volume, url,
                       traffic_pct, kd, intent, serp_features, serp_features_owned) VALUES (%s,%s,'in',%s,%s,%s,%s,%s,%s,%s,%s,'1',%s,%s)""",
                  [cid, dom, today, k, pos, pos + random.randint(-3, 4), vol, f"https://gargi.shop/{k.replace(' ', '-')}", round(10 - i, 2),
                   random.randint(20, 60), "52,11,21" if i % 2 == 0 else "5,21", "52" if i in (0, 2) else ""])
    c.execute("DELETE FROM rank_keywords WHERE client_id=%s", [cid])
    for k, pos, _ in kws:
        c.execute("INSERT INTO rank_keywords (client_id, keyword) VALUES (%s,%s) ON CONFLICT DO NOTHING", [cid, k])
        for n in range(0, 60):
            c.execute("""INSERT INTO rank_positions (client_id, keyword, date, source, position, url, clicks, impressions)
                         VALUES (%s,%s,%s,'gsc',%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                      [cid, k, today - dt.timedelta(days=n), max(1, pos + n / 12 + random.uniform(-1, 1)), f"https://gargi.shop/{k}",
                       random.randint(2, 30), random.randint(80, 600)])
    print("preview data ready for", cid)
