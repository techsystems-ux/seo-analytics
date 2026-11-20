#!/usr/bin/env python3
"""Prompts from search demand: which questions Orbit asks the AI engines, chosen by monthly search volume.

Method (Orbit prompt selection, v1):
 1. Demand pool. Every keyword Orbit knows for the brand with a monthly search volume: the brand's own Semrush keywords and
    the competitor keyword gaps (volumes from Google Keyword Planner when that connection exists, else Semrush), plus Search
    Console queries, whose 28-day impressions stand in for volume when no volume is known.
 2. Clean. Branded searches are dropped: the brand, its sister brands and every competitor, so a prompt never names a brand
    and the engines are free to choose. Navigational and one-word searches go too.
 3. Topics. Keywords that share their content words are one topic ("silver anklets", "silver anklet for women"); a topic's
    demand is the sum of its volumes, and it is written from its biggest keyword.
 4. Rank and allocate. Topics in descending monthly volume. The prompt budget (default 40) is shared across buying stages,
    about 30% awareness, 40% consideration, 30% decision, and each topic gets prompts in proportion to its demand (largest
    remainder), at least one for every topic in the budget, at most three for any one.
 5. Write. Each prompt is how a shopper would ask an AI assistant at that stage, with the market (India) in it:
      awareness      "What should I know before buying {topic}?", a question search kept as asked
      consideration  "What are the best brands for {topic} in India?", "Is {topic} worth it?", "{a} vs {b}, which is better?"
      decision       "Where is the best place to buy {topic} online in India?", "... with easy returns", "... under ₹{price}"
 6. Keep the evidence. Each prompt stores its source keyword, topic and monthly volume; the score weights prompts by demand
    (ai_visibility.demand_weight), so a 20,000-search topic counts more than a 200-search one.
New prompts are saved as suggestions (not approved): someone approves them in Settings, Prompts, before they are asked.
Usage: python prompt_builder.py --client-id UUID [--n 40] [--save]
"""
import argparse, math, re, sys
from common import connect

STAGE_SHARE = {"awareness": 0.30, "consideration": 0.40, "decision": 0.30}
MAX_PER_TOPIC = 3
STOP = set("a an the of for to in on and or with by at from is are be how what why which best top vs your my you i do does can "
           "should near me online buy price prices shop store india indian latest new".split())
SPELL = {"jewelry": "jewellery", "jewellry": "jewellery", "color": "colour", "gray": "grey", "favorite": "favourite"}
BUY = re.compile(r"\b(buy|price|prices|cost|online|shop|store|sale|discount|offer|cod|near me|order|delivery)\b", re.I)
COMPARE = re.compile(r"\b(vs|versus|or|difference|compare|comparison|better|review|reviews|worth|quality)\b", re.I)
QUESTION = re.compile(r"^(how|what|why|which|when|where|who|can|does|do|is|are|should)\b", re.I)
NAVIGATIONAL = re.compile(r"\b(login|near me|customer care|contact|store locator|app|careers|jobs|franchise)\b", re.I)

def words(s):
    out = []
    for w in re.findall(r"[a-z0-9]+", (s or "").lower()):
        w = SPELL.get(w, w)
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"): w = w[:-1]
        out.append(w)
    return out

def content(s): return {w for w in words(s) if w not in STOP and len(w) > 1}

def stem(t):
    t = (t or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
    return re.sub(r"[^a-z0-9]", "", t.split(".")[0] if "." in t else t)

def brand_terms(conn, cid):
    """Every way a brand in this market can be written: the brand, aliases, sister brands, competitors (tracked or not)."""
    name, domain, aliases = conn.execute("SELECT name, domain, aliases FROM clients WHERE client_id=%s", [cid]).fetchone()
    terms = {name, domain, *(aliases or [])}
    r = conn.execute("SELECT brand_spellings, sister_domains FROM brand_rules WHERE client_id=%s", [cid]).fetchone()
    if r: terms |= set(r[0] or []) | set(r[1] or [])
    for n, d, a in conn.execute("SELECT name, domain, aliases FROM competitors WHERE client_id=%s", [cid]):
        terms |= {n, d, *(a or [])}
    out = set()
    for t in terms:
        if not t: continue
        out.add(t.lower().strip())
        s = stem(t)
        if len(s) >= 3: out.add(s)
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            if len(w) >= 4 and w not in STOP and w not in {"jewel", "jewellery", "jewellers", "silver", "gold", "shop", "store", "sons", "brothers", "exclusive"}:
                out.add(w)
    return out

def is_branded(kw, terms):
    k = kw.lower(); flat = re.sub(r"[^a-z0-9]", "", k)
    toks = set(re.findall(r"[a-z0-9]+", k))
    return any((t in toks) or (len(t) >= 5 and t in flat) or (" " in t and t in k) for t in terms)

def pool(conn, cid, volumes=None):
    """[{keyword, volume, source}] from everything Orbit has, newest pull of each. volumes: an optional {keyword: volume}
    from Keyword Planner that overrides Semrush's figures."""
    dom = stem(conn.execute("SELECT domain FROM clients WHERE client_id=%s", [cid]).fetchone()[0] or "")
    rows = {}
    def add(k, v, src):
        k = re.sub(r"\s+", " ", (k or "").strip().lower())
        if not k: return
        cur = rows.get(k)
        if cur is None or (v or 0) > (cur["volume"] or 0): rows[k] = {"keyword": k, "volume": v, "source": src}
    for k, v in conn.execute("""SELECT keyword, search_volume FROM semrush_keywords s WHERE client_id=%s
                                AND pulled_on = (SELECT max(pulled_on) FROM semrush_keywords WHERE client_id=s.client_id AND domain=s.domain)""", [cid]):
        add(k, v, "semrush")
    for k, v in conn.execute("""SELECT keyword, search_volume FROM keyword_gaps g WHERE client_id=%s
                                AND pulled_on = (SELECT max(pulled_on) FROM keyword_gaps WHERE client_id=g.client_id)""", [cid]):
        add(k, v, "semrush_gap")
    for k, imp in conn.execute("""SELECT query, sum(impressions) FROM gsc_search_daily WHERE client_id=%s AND date > current_date - 28
                                  GROUP BY 1 HAVING sum(impressions) >= 30 ORDER BY 2 DESC LIMIT 300""", [cid]):
        if k.lower() not in rows: add(k, None, "gsc"); rows[k.lower()]["impressions"] = int(imp)
    if volumes:
        for k, v in volumes.items():
            k = k.lower()
            if k in rows and v is not None: rows[k].update(volume=int(v), source="keyword_planner")
    return list(rows.values())

def demand(r):
    """Monthly searches; a Search Console query without a volume counts its 28-day impressions (a floor on demand)."""
    return r["volume"] if r.get("volume") is not None else r.get("impressions", 0)

def topics(rows):
    """Merge keywords sharing their content words; biggest keyword names the topic; demand adds up."""
    out = []
    for r in sorted(rows, key=lambda r: -demand(r)):
        c = content(r["keyword"])
        if not c: continue
        hit = next((t for t in out if len(c & t["words"]) / len(c | t["words"]) >= 0.6), None)
        if hit:
            hit["demand"] += demand(r); hit["keywords"].append(r)
        else:
            out.append({"name": r["keyword"], "words": c, "demand": demand(r), "keywords": [r]})
    return sorted(out, key=lambda t: -t["demand"])

def market(region):
    return {"IN": "India", "AE": "the UAE", "US": "the US", "GB": "the UK"}.get(region or "IN", region)

def tidy(topic):
    """'silver anklets for women online' -> 'silver anklets for women' (shopping words go; the stage adds its own)."""
    t = re.sub(r"\b(buy|online|price|prices|shop|store|in india|india|near me|best|top|latest|new)\b", " ", topic, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" ,-") or topic

PHRASES = {
    "awareness": ["What should I know before buying {t}?", "How do I choose good quality {t}?",
                  "What are the popular styles of {t} in {m} right now?", "What makes {t} good quality, and what should I avoid?"],
    "consideration": ["What are the best brands for {t} in {m}?", "Which brands make the best quality {t}?",
                      "Which brands of {t} are most trusted in {m}?", "Is it worth buying {t} online, and which brands are good?"],
    "decision": ["Where is the best place to buy {t} online in {m}?", "Which online store has the best {t} with easy returns in {m}?",
                 "Where can I buy {t} with free delivery in {m}?", "Which website should I order {t} from in {m}?"]}

def write(topic, stage, n, region="IN", others=(), rot=0):
    """Up to n prompts for one topic at one stage, as a shopper would ask an AI assistant. rot varies the wording by topic,
    so a list of prompts reads like different shoppers rather than one template."""
    t, m = tidy(topic["name"]), market(region)
    q = next((r["keyword"] for r in topic["keywords"] if QUESTION.search(r["keyword"])), None)
    vs = next((r["keyword"] for r in topic["keywords"] if re.search(r"\b(vs|versus)\b", r["keyword"])), None)
    base = PHRASES[stage][rot % 4:] + PHRASES[stage][:rot % 4]
    lead = ([q[0].upper() + q[1:].rstrip("?") + "?"] if q and stage == "awareness" else []) + \
           ([vs[0].upper() + vs[1:] + ", which is better?"] if vs and stage == "consideration" else [])
    seen, out = set(), []
    for o in lead + [x.format(t=t, m=m) for x in base]:
        o = re.sub(r"\s+", " ", o).strip()
        key = " ".join(sorted(content(o)))
        if key in seen or key in others: continue
        seen.add(key); out.append(o)
        if len(out) >= n: break
    return out

def stage_of(topic):
    """The stage the searches themselves lean to: buying words, comparing words, or questions."""
    ks = [r["keyword"] for r in topic["keywords"]]
    if sum(1 for k in ks if BUY.search(k)) * 2 >= len(ks): return "decision"
    if any(COMPARE.search(k) for k in ks): return "consideration"
    if any(QUESTION.search(k) for k in ks): return "awareness"
    return None

def allocate(tops, n):
    """Prompts per topic in proportion to demand (largest remainder), min 1 for topics in the budget, max MAX_PER_TOPIC."""
    tops = [t for t in tops if t["demand"] > 0][:n]
    total = sum(t["demand"] for t in tops) or 1
    raw = [n * t["demand"] / total for t in tops]
    got = [min(MAX_PER_TOPIC, max(1, math.floor(x))) for x in raw]
    left = n - sum(got)
    for i in sorted(range(len(tops)), key=lambda i: raw[i] - math.floor(raw[i]), reverse=True):
        if left <= 0: break
        if got[i] < MAX_PER_TOPIC: got[i] += 1; left -= 1
    while sum(got) > n: got[got.index(max(got))] -= 1
    return list(zip(tops, got))

def build(conn, cid, n=40, volumes=None):
    """-> [{text, stage, keyword, topic, volume, source}] best first, never naming a brand."""
    region = (conn.execute("SELECT region FROM prompts WHERE client_id=%s LIMIT 1", [cid]).fetchone() or ["IN"])[0]
    terms = brand_terms(conn, cid)
    rows = [r for r in pool(conn, cid, volumes) if not is_branded(r["keyword"], terms) and not NAVIGATIONAL.search(r["keyword"])
            and len(content(r["keyword"])) >= 1 and len(r["keyword"].split()) >= 2]
    plan = allocate(topics(rows), n)
    quota = {s: round(n * f) for s, f in STAGE_SHARE.items()}
    out, used = [], set()
    order = ["consideration", "decision", "awareness"]
    for i, (t, k) in enumerate(plan):
        lean = stage_of(t)
        stages = ([lean] if lean else []) + [s for s in order if s != lean]
        for s in stages:
            if k <= 0: break
            if quota[s] <= 0: continue
            for text in write(t, s, 1, region, used, rot=i + order.index(s)):
                used.add(" ".join(sorted(content(text))))
                vol = t["demand"] if t["keywords"][0].get("volume") is not None else None
                out.append({"text": text, "stage": s, "keyword": t["keywords"][0]["keyword"], "topic": t["name"], "volume": vol,
                            "source": t["keywords"][0]["source"]})
                quota[s] -= 1; k -= 1
    return out

def save(conn, cid, prompts):
    """Saves new prompts as suggestions (not approved). Existing prompts are untouched; returns how many were added."""
    n = 0
    region = (conn.execute("SELECT region FROM prompts WHERE client_id=%s LIMIT 1", [cid]).fetchone() or ["IN"])[0]
    have = {" ".join(sorted(content(r[0]))) for r in conn.execute("SELECT text FROM prompts WHERE client_id=%s", [cid])}
    for p in prompts:
        if " ".join(sorted(content(p["text"]))) in have: continue
        n += conn.execute("""INSERT INTO prompts (client_id, text, funnel_stage, region, approved, source, source_keyword, topic, search_volume)
                             VALUES (%s,%s,%s,%s,false,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                          [cid, p["text"][:500], p["stage"], region, p["source"], p["keyword"], p["topic"], p["volume"]]).rowcount
    return n

def refresh_volumes(conn, cid):
    """Gives existing prompts the monthly volume of their topic when they have none (the hand-written starting prompts)."""
    rows = topics(pool(conn, cid))
    n = 0
    for pid, text in conn.execute("SELECT prompt_id, text FROM prompts WHERE client_id=%s AND search_volume IS NULL", [cid]).fetchall():
        c = content(text)
        best = max(rows, key=lambda t: len(c & t["words"]) / (len(c | t["words"]) or 1), default=None)
        if best and c & best["words"] and len(c & best["words"]) / len(best["words"]) >= 0.5 and best["keywords"][0].get("volume") is not None:
            n += conn.execute("UPDATE prompts SET search_volume=%s, topic=coalesce(topic,%s) WHERE prompt_id=%s",
                              [best["demand"], best["name"], pid]).rowcount
    return n

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id", required=True); ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    with connect() as conn:
        ps = build(conn, a.client_id, a.n)
        for p in ps: print(f"{p['stage']:<13} {p['volume'] or '-':>7}  {p['text']}   [{p['keyword']}]")
        if a.save: print("saved:", save(conn, a.client_id, ps))
    return 0

if __name__ == "__main__":
    sys.exit(main())
