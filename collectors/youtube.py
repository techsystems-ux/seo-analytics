#!/usr/bin/env python3
"""YouTube for a brand, through the YouTube Data API (free daily quota, key in YOUTUBE_API_KEY): videos that mention
the brand and its competitors, who shows up for the brand's topics (share of voice), the creators in the category,
questions and pain points in comments, and the videos ChatGPT cites in the brand's answers.

Quota (Google's prices): a search costs 100 units; video, channel and comment lookups cost 1 each. Use is counted in
youtube_quota per day and a run stops at YOUTUBE_DAILY_QUOTA x 0.9 (default quota 10,000).

  python youtube.py --client-id UUID
"""
import datetime as dt, os, re
import requests
import community as cm
from ai_visibility import tracked_brands

API = "https://www.googleapis.com/youtube/v3/"
DAILY = int(os.environ.get("YOUTUBE_DAILY_QUOTA", "10000"))
COST = {"search": 100, "videos": 1, "channels": 1, "commentThreads": 1}

class QuotaReached(Exception): pass

def used_today(conn):
    r = conn.execute("SELECT units FROM youtube_quota WHERE day = current_date").fetchone()
    return r[0] if r else 0

def video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None

def duration_s(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m: return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s

class YouTube:
    def __init__(self, key, conn, session=None):
        self.key, self.conn, self.s, self.spent = key, conn, session or requests.Session(), 0
    def get(self, endpoint, **params):
        cost = COST[endpoint]
        if used_today(self.conn) + cost > DAILY * 0.9: raise QuotaReached(f"today's YouTube quota is nearly used ({used_today(self.conn)} of {DAILY})")
        r = self.s.get(API + endpoint, params={**params, "key": self.key}, timeout=60)
        self.conn.execute("""INSERT INTO youtube_quota (day, units) VALUES (current_date, %s)
                             ON CONFLICT (day) DO UPDATE SET units = youtube_quota.units + EXCLUDED.units""", [cost])
        self.spent += cost
        if r.status_code == 403 and endpoint == "commentThreads": return {"items": []}       # comments turned off on that video
        if r.status_code >= 400: raise RuntimeError(f"YouTube {endpoint} {r.status_code}: {r.text[:200]}")
        return r.json()

def run(conn, client_id, key, per_search=25, comment_videos=10, session=None, today=None):
    today = today or dt.date.today()
    yt = YouTube(key, conn, session)
    brands = tracked_brands(conn, client_id)
    own_id = conn.execute("SELECT youtube_channel_id FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    found, notes = {}, []                                  # video_id -> (found_by, query, rank)
    try:
        for found_by, q in cm.searches(conn, client_id):
            res = yt.get("search", part="snippet", q=q, type="video", maxResults=per_search, regionCode="IN", relevanceLanguage="en")
            for i, it in enumerate(res.get("items", []), 1):
                vid = (it.get("id") or {}).get("videoId")
                if vid and vid not in found: found[vid] = (found_by, q, i)
        for u in cm.cited_urls(conn, client_id, ["youtube.com", "youtu.be"]):
            vid = video_id(u)
            if vid: found[vid] = ("cited", None, None)
    except QuotaReached as e:
        notes.append(str(e))
    ids, videos = list(found), []
    try:
        for i in range(0, len(ids), 50):
            videos += yt.get("videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).get("items", [])
        chans = sorted({v["snippet"]["channelId"] for v in videos})
        for i in range(0, len(chans), 50):
            for c in yt.get("channels", part="snippet,statistics", id=",".join(chans[i:i + 50])).get("items", []):
                st = c.get("statistics", {})
                conn.execute("""INSERT INTO youtube_channels (client_id, channel_id, title, subscribers, videos, views, pulled_on)
                                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, channel_id) DO UPDATE SET title=EXCLUDED.title,
                                subscribers=EXCLUDED.subscribers, videos=EXCLUDED.videos, views=EXCLUDED.views, pulled_on=EXCLUDED.pulled_on""",
                             [client_id, c["id"], c["snippet"].get("title"), st.get("subscriberCount"), st.get("videoCount"), st.get("viewCount"), today])
    except QuotaReached as e:
        notes.append(str(e))
    me = brands[0]
    for v in videos:
        sn, st = v["snippet"], v.get("statistics", {})
        text = " ".join([sn.get("title", ""), sn.get("description", ""), " ".join(sn.get("tags", []))])
        found_by, q, rank = found[v["id"]]
        own = bool(own_id and sn.get("channelId") == own_id) or me["name"] in cm.brands_in(sn.get("channelTitle", ""), brands)
        conn.execute("""INSERT INTO youtube_videos (client_id, video_id, title, description, channel_id, channel, published_at, views, likes, comments,
                          duration_s, found_by, query, search_rank, brands, own_channel, pulled_on)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (client_id, video_id) DO UPDATE SET views=EXCLUDED.views, likes=EXCLUDED.likes, comments=EXCLUDED.comments,
                          brands=EXCLUDED.brands, own_channel=EXCLUDED.own_channel, pulled_on=EXCLUDED.pulled_on, search_rank=EXCLUDED.search_rank,
                          query=coalesce(EXCLUDED.query, youtube_videos.query),
                          found_by=CASE WHEN EXCLUDED.found_by='cited' THEN 'cited' ELSE EXCLUDED.found_by END""",
                     [client_id, v["id"], sn.get("title"), (sn.get("description") or "")[:3000], sn.get("channelId"), sn.get("channelTitle"),
                      sn.get("publishedAt"), st.get("viewCount"), st.get("likeCount"), st.get("commentCount"),
                      duration_s(v.get("contentDetails", {}).get("duration")), found_by, q, rank, cm.brands_in(text, brands), own, today])
    if not own_id:                                       # the brand's own channel, found on its own: the channel named after the brand
        hit = conn.execute("""SELECT channel_id FROM youtube_videos WHERE client_id=%s AND own_channel AND channel_id IS NOT NULL
                              GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""", [client_id]).fetchone()
        if hit: conn.execute("UPDATE clients SET youtube_channel_id=%s WHERE client_id=%s AND youtube_channel_id IS NULL", [hit[0], client_id])
    # comments on the most watched topic and brand videos: what viewers ask and complain about
    top = conn.execute("""SELECT video_id FROM youtube_videos WHERE client_id=%s AND found_by IN ('topic','brand') AND pulled_on=%s
                          AND coalesce(comments,0) > 0 ORDER BY views DESC NULLS LAST LIMIT %s""", [client_id, today, comment_videos]).fetchall()
    nc = 0
    try:
        for (vid,) in top:
            for t in yt.get("commentThreads", part="snippet", videoId=vid, maxResults=50, order="relevance", textFormat="plainText").get("items", []):
                c = t["snippet"]["topLevelComment"]["snippet"]
                txt = (c.get("textDisplay") or "")[:2000]
                conn.execute("""INSERT INTO youtube_comments (client_id, comment_id, video_id, text, likes, published_at, is_question, pain_point, pulled_on)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, comment_id) DO UPDATE SET likes=EXCLUDED.likes""",
                             [client_id, t["id"], vid, txt, c.get("likeCount"), c.get("publishedAt"), cm.is_question(txt), cm.pain_point(txt), today])
                nc += 1
    except QuotaReached as e:
        notes.append(str(e))
    return f"{len(videos)} videos, {nc} comments ({yt.spent} quota units)" + (f"; {notes[0]}" if notes else "")

if __name__ == "__main__":
    import argparse
    from common import connect
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id", required=True)
    a = ap.parse_args()
    with connect() as conn:
        print(run(conn, a.client_id, os.environ["YOUTUBE_API_KEY"]))
