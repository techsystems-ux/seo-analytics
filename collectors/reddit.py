#!/usr/bin/env python3
"""Reddit for a brand, through an Apify Reddit scraper: where the brand and its competitors are discussed, the tone,
the questions and pain points in the category, and the threads ChatGPT cites in the brand's answers.

Apify charges per result, so every run is capped before it starts: APIFY_MAX_ITEMS per run (default 300) and
APIFY_TOTAL_ITEMS for the product's lifetime (default 1000), counted in apify_usage. It runs by itself weekly
(APIFY_AUTO_ITEMS a run, default 150) once the token is set; topics and communities are found automatically.
The token (APIFY_TOKEN) is sent in a header, never in a URL, and never stored.

  python reddit.py --client-id UUID --max-items 200
"""
import datetime as dt, math, os, re, sys
from urllib.parse import quote
import requests
import community as cm
from ai_visibility import tracked_brands

ACTOR = os.environ.get("APIFY_REDDIT_ACTOR", "trudax~reddit-scraper-lite")
API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
TOTAL_ITEMS = int(os.environ.get("APIFY_TOTAL_ITEMS", "1000"))
RUN_ITEMS = int(os.environ.get("APIFY_MAX_ITEMS", "300"))
AUTO_ITEMS = int(os.environ.get("APIFY_AUTO_ITEMS", "150"))     # the weekly automatic run

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

def community_relevance(c, topic_words):
    text = f"{c.get('name', '')} {c.get('title', '')} {c.get('description', '')}".lower()
    hits = sum(1 for w in topic_words if w in text)
    return round(hits * 2 + math.log10(max(c.get("members") or 1, 1)), 2)

def save_communities(conn, client_id, rows, found_for, topic_words, today):
    out = []
    for r in rows:
        if str(r.get("dataType", "")).lower() not in ("community", "subreddit") or r.get("over18"): continue
        name = (r.get("parsedName") or r.get("name") or r.get("communityName") or "").replace("r/", "").strip("/") or None
        if not name: continue
        c = {"name": name, "title": r.get("title"), "description": (r.get("description") or "")[:500],
             "members": r.get("numberOfMembers") or r.get("members") or r.get("subscribers")}
        c["relevance"] = community_relevance(c, topic_words)
        conn.execute("""INSERT INTO reddit_communities (client_id, name, title, description, members, found_for, relevance, pulled_on)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, name) DO UPDATE SET members=EXCLUDED.members,
                        title=EXCLUDED.title, description=EXCLUDED.description, relevance=greatest(reddit_communities.relevance, EXCLUDED.relevance),
                        pulled_on=EXCLUDED.pulled_on""",
                     [client_id, name, c["title"], c["description"], c["members"], found_for, c["relevance"], today])
        out.append(c)
    return out

def run(conn, client_id, token, max_items=None, session=None, today=None):
    """One pass, nothing typed by anyone: find the communities for the brand's topics, read the threads ChatGPT cites,
    search Reddit for the brand, each competitor and each topic, then search inside the most relevant communities."""
    today = today or dt.date.today()
    s = session or requests.Session()
    budget = min(max_items or RUN_ITEMS, RUN_ITEMS, items_left(conn))
    if budget <= 0: return "stopped: the Apify result limit is used up (0 results)"
    brands = tracked_brands(conn, client_id)
    tps = cm.topics(conn, client_id)[0]
    topic_words = sorted({w for t in tps for w in t.split() if len(w) > 3})
    cited = [u for u in cm.cited_urls(conn, client_id, ["reddit.com"]) if thread_id(u)][:10]
    searches = cm.searches(conn, client_id)
    got, notes, comms = 0, [], []
    left = lambda: budget - got
    try:
        disc = cm.discovery_queries(conn, client_id)
        if disc:                                                # 1. which communities discuss the category
            n = min(15, max(5, budget // 10))
            rows = call_actor(s, token, conn, client_id, {"searches": disc, "searchCommunities": True, "searchPosts": False,
                                                          "searchComments": False, "includeNSFW": False}, n)
            comms = save_communities(conn, client_id, rows, ", ".join(disc), topic_words, today)
            got += len(rows)
        if cited and left() > 0:                                # 2. the threads ChatGPT cites, with their top comments
            n = min(len(cited) * 4, max(4, budget // 5), left())
            rows = call_actor(s, token, conn, client_id, {"startUrls": [{"url": u} for u in cited], "maxComments": 3, "skipComments": False}, n)
            got += save(conn, client_id, [normalise(r) for r in rows], "cited", None, brands, today)
        top = sorted(comms, key=lambda c: -c["relevance"])[:3]
        per = max(3, int(left() * (0.7 if top else 1.0)) // max(len(searches), 1))
        for found_by, q in searches:                            # 3. brand, competitors, topics across Reddit
            n = min(per, left())
            if n <= 0: notes.append("result limit reached"); break
            rows = call_actor(s, token, conn, client_id, {"searches": [q], "searchPosts": True, "searchComments": True, "searchCommunities": False,
                                                          "sort": "relevance", "time": "year", "maxComments": 0, "skipComments": True,
                                                          "includeNSFW": False}, n)
            got += save(conn, client_id, [normalise(r) for r in rows], found_by, q, brands, today)
        for c in top:                                           # 4. inside the most relevant communities
            n = min(max(3, left() // max(len(top), 1)), left())
            if n <= 0 or not tps: break
            url = f"https://www.reddit.com/r/{c['name']}/search/?q={quote(tps[0])}&restrict_sr=1&sort=relevance&t=year"
            rows = call_actor(s, token, conn, client_id, {"startUrls": [{"url": url}], "maxComments": 0, "skipComments": True}, n)
            got += save(conn, client_id, [normalise(r) for r in rows], "topic", tps[0], brands, today)
    except LimitReached as e:
        notes.append(f"stopped: {e}")
    return (f"{got} Reddit results: {len(comms)} communities found, {len(searches)} searches, {len(cited)} cited threads"
            + (f" ({'; '.join(notes)})" if notes else ""))

if __name__ == "__main__":
    import argparse
    from common import connect
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id", required=True); ap.add_argument("--max-items", type=int, required=True)
    a = ap.parse_args()
    with connect() as conn:
        print(run(conn, a.client_id, os.environ["APIFY_TOKEN"], a.max_items))
