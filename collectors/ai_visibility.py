#!/usr/bin/env python3
"""AI visibility runner (ChatGPT through the OpenAI API, web search on).

For every approved prompt it asks the engine, stores the answer, then records:
  mentions   which tracked brands the answer names, in what order, with what sentiment
  citations  which links the answer cites, and which it only read while searching
Then it writes the Hovers Visibility Score for the client and each tracked competitor (scores_daily).

Hovers Visibility Score, 0-100, per brand per engine per day:
  35% mention rate      share of answers that name the brand
  25% position          where the brand appears among the brands named (1st = 1.0, then 0.2 less per place, floor 0.2)
  25% citation rate     share of answers that cite the brand's own site
  15% sentiment         positive 1, neutral 0.5, negative 0, over the answers that name the brand
Bands: 0-39 Not shortlisted, 40-59 Emerging, 60-79 Competitive, 80+ Leader.

The API answer approximates what ChatGPT shows users; it is not a scrape of chatgpt.com.
Usage: python ai_visibility.py --client-id UUID [--samples 1] [--date 2026-11-12]
Env: OPENAI_API_KEY, OPENAI_MODEL (default below), AI_SENTIMENT=0 to skip the sentiment call
"""
import argparse, datetime as dt, json, os, re, sys
from urllib.parse import urlparse
import requests
from common import connect

API = "https://api.openai.com/v1/responses"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-astra")
WEIGHTS = {"mention": 0.35, "position": 0.25, "citation": 0.25, "sentiment": 0.15}
BANDS = [(80, "Leader"), (60, "Competitive"), (40, "Emerging"), (0, "Not shortlisted")]
SENT = {"positive": 1.0, "neutral": 0.5, "negative": 0.0}

def band(score):
    return next(name for floor, name in BANDS if (score or 0) >= floor)

def host(url):
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h

def same_site(h, domain):
    d = (domain or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    return bool(d) and (h == d or h.endswith("." + d))

# ------------------------------------------------------------------ talking to the engine
def ask(session, key, prompt, country="IN"):
    body = {"model": MODEL, "input": prompt, "include": ["web_search_call.action.sources"],
            "tools": [{"type": "web_search", "user_location": {"type": "approximate", "country": country}}]}
    r = session.post(API, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
    if r.status_code != 200: raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    return r.json()

def parse_response(j):
    """-> answer text, cited [(url, title)], consulted [url]"""
    text, cited, consulted = [], [], []
    for item in j.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text"):
                    text.append(part.get("text", ""))
                    for a in part.get("annotations", []):
                        if a.get("type") == "url_citation" and a.get("url"): cited.append((a["url"], a.get("title")))
        elif item.get("type") == "web_search_call":
            for s in ((item.get("action") or {}).get("sources") or []):
                if s.get("url"): consulted.append(s["url"])
    seen, uniq = set(), []
    for u, t in cited:
        k = u.split("#")[0].rstrip("/")
        if k not in seen: seen.add(k); uniq.append((u, t))
    return "\n".join(text).strip(), uniq, [u for u in dict.fromkeys(consulted) if u.split("#")[0].rstrip("/") not in seen]

SENTIMENT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["brands"], "properties": {"brands": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "sentiment", "recommended"],
    "properties": {"name": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                   "recommended": {"type": "boolean"}}}}}}

def judge_brands(session, key, answer):
    """Second, cheap call without web search: every brand or store the answer names, its sentiment, recommended or not."""
    body = {"model": MODEL, "input": [
        {"role": "system", "content": "List every brand, store or marketplace named in the answer, in order of first appearance. "
                                      "Sentiment is how the answer presents that brand. Do not add brands that are not named."},
        {"role": "user", "content": answer}],
        "text": {"format": {"type": "json_schema", "name": "brands", "schema": SENTIMENT_SCHEMA, "strict": True}}}
    r = session.post(API, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)
    if r.status_code != 200: raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    text, _, _ = parse_response(r.json())
    return json.loads(text).get("brands", [])

# ------------------------------------------------------------------ reading the answer
def brand_patterns(name, aliases, domain):
    terms = {name, *(aliases or [])}
    if domain: terms.add(domain.lower().replace("www.", "").split("/")[0])
    terms = sorted({t.strip() for t in terms if t and len(t.strip()) >= 3}, key=len, reverse=True)
    return [re.compile(r"(?<![\w])" + re.escape(t).replace(r"\ ", r"[\s-]?") + r"(?![\w])", re.I) for t in terms]

def find_mentions(answer, brands):
    """brands = [{name, aliases, domain, is_client}] -> [{name, is_client, rank}] in order of first appearance."""
    spans = [[(m.start(), m.end()) for p in brand_patterns(b["name"], b["aliases"], b["domain"]) for m in p.finditer(answer)] for b in brands]
    hits = []
    for i, b in enumerate(brands):
        # a match inside a longer match of another brand is that brand's: "PNG" in "PNG Brothers" is not Gargi
        own = [s for s in spans[i] if not any(o[0] <= s[0] and s[1] <= o[1] and o[1] - o[0] > s[1] - s[0]
                                             for j, other in enumerate(spans) if j != i for o in other)]
        if own: hits.append((min(s[0] for s in own), b))
    hits.sort(key=lambda x: x[0])
    return [{"name": b["name"], "is_client": b["is_client"], "rank": i + 1} for i, (_, b) in enumerate(hits)]

SOURCE_TYPES = [("reddit", ("reddit.com",)), ("youtube", ("youtube.com", "youtu.be")), ("wikipedia", ("wikipedia.org",)),
                ("review_site", ("trustpilot.com", "mouthshut.com", "quora.com", "justdial.com")),
                ("news", ("timesofindia", "economictimes", "hindustantimes", "ndtv", "livemint", "vogue", "elle", "femina", "news"))]

def source_type(h, client_domain, competitor_domains):
    if same_site(h, client_domain): return "own_site"
    if any(same_site(h, d) for d in competitor_domains): return "competitor"
    for t, keys in SOURCE_TYPES:
        if any(k in h for k in keys): return t
    return "other"

# ------------------------------------------------------------------ the run
def tracked_brands(conn, client_id):
    name, domain, aliases = conn.execute("SELECT name, domain, aliases FROM clients WHERE client_id=%s", [client_id]).fetchone()
    br = conn.execute("SELECT brand_spellings FROM brand_rules WHERE client_id=%s", [client_id]).fetchone()
    out = [{"name": name, "aliases": list(aliases or []) + list((br or [[]])[0] or []), "domain": domain, "is_client": True}]
    for n, d, a in conn.execute("SELECT name, domain, aliases FROM competitors WHERE client_id=%s AND tracked", [client_id]):
        out.append({"name": n, "aliases": a or [], "domain": d, "is_client": False})
    return out

def run_prompt(conn, session, key, client_id, prompt_id, text, region, cycle, sample_no, brands, sentiment=True):
    row = conn.execute("""INSERT INTO runs (client_id, prompt_id, engine, region, sample_no, cycle, status)
                          VALUES (%s,%s,'chatgpt',%s,%s,%s,'error') ON CONFLICT (prompt_id, engine, region, cycle, sample_no) DO NOTHING
                          RETURNING run_id""", [client_id, prompt_id, region, sample_no, cycle]).fetchone()
    if not row:                                                    # already asked this cycle: retry only if it failed
        row = conn.execute("""SELECT run_id FROM runs WHERE prompt_id=%s AND engine='chatgpt' AND region=%s AND cycle=%s AND sample_no=%s
                              AND status='error'""", [prompt_id, region, cycle, sample_no]).fetchone()
        if not row: return "skipped"
        conn.execute("DELETE FROM answers WHERE run_id=%s", [row[0]])
    run_id = row[0]
    try:
        j = ask(session, key, text, region)
        answer, cited, consulted = parse_response(j)
        judged = judge_brands(session, key, answer) if sentiment and answer else []
    except Exception as e:
        conn.execute("UPDATE runs SET status='error', error=%s, run_at=now() WHERE run_id=%s", [str(e)[:500], run_id]); return "error"
    client = brands[0]; comp_domains = [b["domain"] for b in brands[1:] if b["domain"]]
    with conn.transaction():
        answer_id = conn.execute("""INSERT INTO answers (client_id, run_id, full_text, engine_version, raw_json, parsed_at)
                                    VALUES (%s,%s,%s,%s,%s,now()) RETURNING answer_id""",
                                 [client_id, run_id, answer, j.get("model", MODEL), json.dumps({"usage": j.get("usage"), "id": j.get("id")})]).fetchone()[0]
        verdict = {b["name"].lower(): b for b in judged}
        found = find_mentions(answer, brands)
        known = {m["name"].lower() for m in found}
        for m in found:
            v = next((verdict[k] for k in verdict if k == m["name"].lower() or m["name"].lower() in k or k in m["name"].lower()), None)
            conn.execute("""INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer, sentiment, recommended)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)""", [client_id, answer_id, m["name"], m["is_client"], m["rank"],
                                                               (v or {}).get("sentiment"), (v or {}).get("recommended")])
        for v in judged:                                          # brands we do not track yet: kept for "who else gets named"
            if not any(k in v["name"].lower() or v["name"].lower() in k for k in known):
                conn.execute("""INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer, sentiment, recommended)
                                VALUES (%s,%s,%s,false,NULL,%s,%s)""", [client_id, answer_id, v["name"][:200], v["sentiment"], v["recommended"]])
        for urls, is_cited in (([u for u, _ in cited], True), (consulted, False)):
            titles = dict(cited)
            for u in urls:
                h = host(u)
                conn.execute("""INSERT INTO citations (client_id, answer_id, url, domain, source_type, title, cited, is_client)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                             [client_id, answer_id, u[:2000], h, source_type(h, client["domain"], comp_domains), titles.get(u), is_cited,
                              same_site(h, client["domain"])])
        conn.execute("UPDATE runs SET status='ok', error=NULL, run_at=now() WHERE run_id=%s", [run_id])
    return "ok"

def position_score(rank):
    return max(0.2, 1 - 0.2 * (rank - 1)) if rank else 0.0

def score_brand(answers):
    """answers = [{mentioned, rank, cited, sentiment}] for one brand -> components and score."""
    n = len(answers)
    if not n: return None
    named = [a for a in answers if a["mentioned"]]
    mention = len(named) / n
    pos = sum(position_score(a["rank"]) for a in named) / len(named) if named else 0.0
    cite = sum(1 for a in answers if a["cited"]) / n
    sent = sum(SENT.get(a["sentiment"] or "neutral", 0.5) for a in named) / len(named) if named else 0.0
    score = 100 * (WEIGHTS["mention"] * mention + WEIGHTS["position"] * pos + WEIGHTS["citation"] * cite + WEIGHTS["sentiment"] * sent)
    ranks = [a["rank"] for a in named if a["rank"]]
    return {"samples": n, "mention_rate": mention, "position": pos, "citation_rate": cite, "sentiment": sent,
            "avg_position": sum(ranks) / len(ranks) if ranks else None, "score": round(score, 2)}

def compute_scores(conn, client_id, cycle, region="IN", engine="chatgpt"):
    brands = tracked_brands(conn, client_id)
    rows = conn.execute("""SELECT a.answer_id FROM runs r JOIN answers a USING (run_id)
                           WHERE r.client_id=%s AND r.cycle=%s AND r.region=%s AND r.engine=%s AND r.status='ok'""",
                        [client_id, cycle, region, engine]).fetchall()
    ids = [r[0] for r in rows]
    if not ids: return {}
    ment = {}
    for aid, brand, rank, sent in conn.execute("""SELECT answer_id, brand, rank_in_answer, sentiment FROM mentions
                                                  WHERE answer_id = ANY(%s) AND rank_in_answer IS NOT NULL""", [ids]):
        ment[(aid, brand)] = (rank, sent)
    cites = conn.execute("SELECT answer_id, domain FROM citations WHERE answer_id = ANY(%s) AND cited", [ids]).fetchall()
    total_mentions = sum(1 for _ in ment)
    out = {}
    for b in brands:
        ans = [{"mentioned": (aid, b["name"]) in ment, "rank": ment.get((aid, b["name"]), (None, None))[0],
                "sentiment": ment.get((aid, b["name"]), (None, None))[1],
                "cited": any(a == aid and same_site(d, b["domain"]) for a, d in cites)} for aid in ids]
        s = score_brand(ans)
        sov = sum(1 for k in ment if k[1] == b["name"]) / total_mentions if total_mentions else 0
        key = "" if b["is_client"] else b["name"]
        conn.execute("""INSERT INTO scores_daily (client_id, engine, region, date, brand, samples, mention_rate, citation_rate, share_of_voice,
                          avg_position, sentiment, visibility_score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (client_id, engine, region, date, brand) DO UPDATE SET samples=EXCLUDED.samples, mention_rate=EXCLUDED.mention_rate,
                          citation_rate=EXCLUDED.citation_rate, share_of_voice=EXCLUDED.share_of_voice, avg_position=EXCLUDED.avg_position,
                          sentiment=EXCLUDED.sentiment, visibility_score=EXCLUDED.visibility_score""",
                     [client_id, engine, region, cycle, key, s["samples"], s["mention_rate"], s["citation_rate"], sov,
                      s["avg_position"], s["sentiment"], s["score"]])
        out[b["name"]] = s
    return out

def sync_client(conn, client_id, key, samples=1, cycle=None, session=None, sentiment=True):
    cycle = cycle or dt.date.today(); session = session or requests.Session()
    brands = tracked_brands(conn, client_id)
    prompts = conn.execute("SELECT prompt_id, text, region FROM prompts WHERE client_id=%s AND approved AND active ORDER BY created_at",
                           [client_id]).fetchall()
    tally = {}
    for pid, text, region in prompts:
        for n in range(1, samples + 1):
            res = run_prompt(conn, session, key, client_id, pid, text, region, cycle, n, brands, sentiment)
            tally[res] = tally.get(res, 0) + 1
    regions = {r for _, _, r in prompts}
    scores = {r: compute_scores(conn, client_id, cycle, r) for r in regions}
    return tally, scores

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True); ap.add_argument("--samples", type=int, default=1); ap.add_argument("--date")
    a = ap.parse_args()
    with connect() as conn:
        tally, scores = sync_client(conn, a.client_id, os.environ["OPENAI_API_KEY"], a.samples,
                                    dt.date.fromisoformat(a.date) if a.date else None, sentiment=os.environ.get("AI_SENTIMENT", "1") != "0")
        print("runs:", tally)
        for region, s in scores.items():
            for brand, v in s.items(): print(f"{region} {brand}: {v['score']} ({band(v['score'])})")
        return 1 if tally.get("error") else 0

if __name__ == "__main__":
    sys.exit(main())
