#!/usr/bin/env python3
"""Reddit for a brand, through an Apify Reddit scraper: where the brand and its competitors are discussed, the tone,
the questions and pain points in the category, and the threads ChatGPT cites in the brand's answers.

Apify charges per result, so every run is capped before it starts: APIFY_MAX_ITEMS per run (default 300) and
APIFY_TOTAL_ITEMS for the product's lifetime (default 1000), counted in apify_usage. Nothing runs on a schedule.
The token (APIFY_TOKEN) is sent in a header, never in a URL, and never stored.

  python reddit.py --client-id UUID --max-items 200
"""
import datetime as dt, os, re, sys
import requests
import community as cm
from ai_visibility import tracked_brands

ACTOR = os.environ.get("APIFY_REDDIT_ACTOR", "trudax~reddit-scraper-lite")
API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
TOTAL_ITEMS = int(os.environ.get("APIFY_TOTAL_ITEMS", "1000"))
RUN_ITEMS = int(os.environ.get("APIFY_MAX_ITEMS", "300"))

class LimitReached(Exception): pass

def items_used(conn):
    return conn.execute("SELECT coalesce(sum(items),0) FROM apify_usage").fetchone()[0]

def items_left(conn):
    return max(0, TOTAL_ITEMS - items_used(conn))

def thread_id(url):
    m = re.search(r"/comments/([a-z0-9]+)", url or "", re.I)
    return m.group(1).lower() if m else None

def call_actor(session, token, conn, client_id, run_input, max_items):
    if max_items > items_left(conn): raise LimitReached(f"needs up to {max_items} results; {items_left(conn)} left under APIFY_TOTAL_ITEMS")
    r = session.post(API.format(actor=ACTOR), params={"timeout": 300}, json={**run_input, "maxItems": max_items},
                     headers={"Authorization": f"Bearer {token}"}, timeout=330)
    if r.status_code >= 400: raise RuntimeError(f"Apify {r.status_code}: {r.text[:200]}")
    rows = r.json()[:max_items]
    conn.execute("INSERT INTO apify_usage (client_id, actor, items) VALUES (%s,%s,%s)", [client_id, ACTOR, len(rows)])
    return rows

def normalise(row):
    kind = "comment" if str(row.get("dataType", row.get("type", ""))).lower().startswith("comment") else "post"
    sub = row.get("parsedCommunityName") or (row.get("communityName") or row.get("subreddit") or "").replace("r/", "") or None
    rid = str(row.get("parsedId") or row.get("id") or thread_id(row.get("url")) or "")
    return {"item_id": rid.removeprefix("t3_").removeprefix("t1_").lower(), "kind": kind, "subreddit": sub,
            "title": row.get("title"), "body": (row.get("body") or row.get("text") or "")[:4000], "url": row.get("url"),
            "author": row.get("username") or row.get("author"), "score": row.get("upVotes") or row.get("score"),
            "comments": row.get("numberOfComments") or row.get("numComments"), "created_at": row.get("createdAt") or row.get("created")}

def save(conn, client_id, items, found_by, query, brands, today):
    n = 0
    for it in items:
        if not it["item_id"]: continue
        text = f"{it['title'] or ''}\n{it['body'] or ''}"
        conn.execute("""INSERT INTO reddit_items (client_id, item_id, kind, subreddit, title, body, url, author, score, comments, created_at,
                          found_by, query, brands, sentiment, is_question, pain_point, topic, pulled_on)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (client_id, item_id) DO UPDATE SET score=EXCLUDED.score, comments=EXCLUDED.comments, brands=EXCLUDED.brands,
                          sentiment=EXCLUDED.sentiment, pulled_on=EXCLUDED.pulled_on,
                          found_by=CASE WHEN EXCLUDED.found_by='cited' THEN 'cited' ELSE reddit_items.found_by END""",
                     [client_id, it["item_id"], it["kind"], it["subreddit"], it["title"], it["body"], it["url"], it["author"], it["score"],
                      it["comments"], it["created_at"], found_by, query, cm.brands_in(text, brands), cm.tone(text),
                      cm.is_question(it["title"], it["body"]), cm.pain_point(text), query if found_by == "topic" else None, today])
        n += 1
    return n

def run(conn, client_id, token, max_items=None, session=None, today=None):
    today = today or dt.date.today()
    s = session or requests.Session()
    budget = min(max_items or RUN_ITEMS, RUN_ITEMS, items_left(conn))
    if budget <= 0: return "stopped: the Apify result limit is used up (0 results)"
    brands = tracked_brands(conn, client_id)
    cited = [u for u in cm.cited_urls(conn, client_id, ["reddit.com"]) if thread_id(u)][:20]
    searches = cm.searches(conn, client_id)
    per = max(5, (budget - len(cited) * 5) // max(len(searches), 1))
    got, notes = 0, []
    try:
        if cited:            # the threads ChatGPT cites, with their top comments
            n = min(len(cited) * 5, budget)
            rows = call_actor(s, token, conn, client_id, {"startUrls": [{"url": u} for u in cited], "maxComments": 4, "skipComments": False}, n)
            got += save(conn, client_id, [normalise(r) for r in rows], "cited", None, brands, today)
        for found_by, q in searches:
            n = min(per, budget - got)
            if n <= 0: notes.append("result limit reached"); break
            rows = call_actor(s, token, conn, client_id, {"searches": [q], "searchPosts": True, "searchComments": True, "searchCommunities": False,
                                                          "sort": "relevance", "time": "year", "maxComments": 0, "skipComments": True,
                                                          "includeNSFW": False}, n)
            got += save(conn, client_id, [normalise(r) for r in rows], found_by, q, brands, today)
    except LimitReached as e:
        notes.append(f"stopped: {e}")
    return f"{got} Reddit posts and comments from {len(searches)} searches and {len(cited)} cited threads" + (f" ({'; '.join(notes)})" if notes else "")

if __name__ == "__main__":
    import argparse
    from common import connect
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id", required=True); ap.add_argument("--max-items", type=int, required=True)
    a = ap.parse_args()
    with connect() as conn:
        print(run(conn, a.client_id, os.environ["APIFY_TOKEN"], a.max_items))
