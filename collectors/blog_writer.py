#!/usr/bin/env python3
"""Execution: blog topics from the brand's own data, then drafts written for review.

1. research(): free keyword research. Candidates come from
     Search Console   non-branded questions and "best / how / vs" searches the site already shows for, ranked by impressions
                      and by how close they are to page 1 (positions 4-30 move most with a dedicated post)
     ChatGPT prompts  approved prompts where the latest answer does not name the brand (a post that answers it is GEO work)
     Reddit           questions people ask in the category
     Google suggest   question-style completions of the brand's main topics (free; used most for new sites with little data)
   Near-duplicates are merged, topics the site already covers (existing blog URLs, other drafts) are dropped, and the rest
   are saved as ideas with the evidence for each.
2. write(): the top ideas are written as drafts by the OpenAI API (web search on, for sources), in the brand's rules,
   linking only to real pages of the site. Each draft is scored with the same on-page checks as live pages.
Nothing is published here: a person reviews and publishes from the Execution screen.

Usage: python blog_writer.py --client-id UUID [--write 3]
Env: OPENAI_API_KEY_2; BLOG_MODEL (default OPENAI_MODEL); BLOGS_PER_RUN (3); BLOG_QUEUE (6 drafts waiting at most); BLOG_SUGGEST=0 to skip Google suggest
"""
import argparse, datetime as dt, json, math, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "seo"), os.path.join(HERE, "..", "webapp")]
import requests
import blogs

API = "https://api.openai.com/v1/responses"
MODEL = os.environ.get("BLOG_MODEL") or os.environ.get("OPENAI_MODEL", "gpt-6-astra")
PER_RUN = int(os.environ.get("BLOGS_PER_RUN", "3"))
QUEUE = int(os.environ.get("BLOG_QUEUE", "6"))
IDEAS_KEEP = 30

INFO = re.compile(r"^(how|what|why|which|when|where|who|can|does|do|is|are|should|will)\b|\b(how to|guide|tips|ideas|vs|versus|"
                  r"difference|benefits?|meaning|types? of|best|top \d+|for (men|women|kids|gifting)|care|clean|maintain|"
                  r"choose|choosing|worth|pros and cons|alternatives?|explained|checklist|mistakes)\b", re.I)
BUY = re.compile(r"\b(buy|price|prices|cost|online|shop|store|sale|discount|coupon|offer|cod|near me|order|delivery)\b", re.I)
STOP = set("a an the of for to in on and or with by at from is are be how what why which best top vs your my you i do does can should near".split())

SPELL = {"jewelry": "jewellery", "jewellry": "jewellery", "color": "colour", "colors": "colours", "gray": "grey", "organize": "organise",
         "favorite": "favourite", "center": "centre", "mom": "mum"}

def toks(s):
    """Content words, with US/UK spellings and plurals folded, so 'jewelry' and 'jewellery' count as one topic."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", (s or "").lower()):
        if w in STOP or len(w) < 2: continue
        w = SPELL.get(w, w)
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"): w = w[:-1]
        out.add(w)
    return out

def similar(a, b):
    A, B = toks(a), toks(b)
    return bool(A and B) and len(A & B) / len(A | B) >= 0.6

def branded(q, names):
    ql = (q or "").lower()
    return any(n and n.lower() in ql for n in names)

# ------------------------------------------------------------------ research
def from_gsc(conn, cid, names):
    rows = conn.execute("""SELECT query, sum(impressions), sum(clicks), sum(position*impressions)/nullif(sum(impressions),0)
                           FROM gsc_search_daily WHERE client_id=%s AND date > current_date - 31 AND NOT is_branded
                           GROUP BY query HAVING sum(impressions) >= 10 ORDER BY 2 DESC LIMIT 2000""", [cid]).fetchall()
    out = []
    for q, imp, clicks, pos in rows:
        if branded(q, names) or not INFO.search(q) or (BUY.search(q) and not re.match(r"^(how|what|which|why|is|are|can)\b", q)): continue
        pos = float(pos or 0)
        near = 20 if 4 <= pos <= 20 else 12 if pos <= 30 else 6 if pos > 30 else 2
        out.append({"keyword": q, "priority": round(math.log10(imp + 1) * 30 + near + (10 if q.endswith("?") or re.match(r"^(how|what|why|which)\b", q) else 0), 1),
                    "evidence": {"source": "gsc", "text": q, "impressions": int(imp), "clicks": int(clicks), "position": round(pos, 1)}})
    return out

def from_prompts(conn, cid):
    rows = conn.execute("""WITH last AS (SELECT DISTINCT ON (r.prompt_id) r.prompt_id, a.answer_id FROM runs r JOIN answers a USING (run_id)
                                         WHERE r.client_id=%s AND r.status='ok' ORDER BY r.prompt_id, r.cycle DESC, r.run_at DESC)
                           SELECT p.text, bool_or(m.is_client), array_remove(array_agg(DISTINCT m.brand) FILTER (WHERE NOT m.is_client), NULL)
                           FROM prompts p JOIN last USING (prompt_id) LEFT JOIN mentions m ON m.answer_id = last.answer_id
                           WHERE p.client_id=%s AND p.approved AND p.active GROUP BY p.text""", [cid, cid]).fetchall()
    return [{"keyword": t, "priority": 45 + (10 if rivals else 0),
             "evidence": {"source": "prompt", "text": t, "named": False, "rivals": (rivals or [])[:4]}}
            for t, named, rivals in rows if not named]

def from_reddit(conn, cid, names):
    rows = conn.execute("""SELECT title, score, comments, subreddit FROM reddit_items WHERE client_id=%s AND is_question AND kind='post'
                           AND title IS NOT NULL AND NOT sample ORDER BY coalesce(score,0)+coalesce(comments,0) DESC LIMIT 40""", [cid]).fetchall()
    return [{"keyword": re.sub(r"\s+", " ", t).strip()[:140], "priority": 25 + min(15, math.log10((s or 0) + (c or 0) + 1) * 6),
             "evidence": {"source": "reddit", "text": t, "subreddit": sub, "score": s, "comments": c}}
            for t, s, c, sub in rows if not branded(t, names)]

def seeds_for(conn, cid, niche):
    """Main topics for suggest: the site's biggest non-branded searches, its collection keywords, then the niche."""
    s = [q for (q,) in conn.execute("""SELECT query FROM gsc_search_daily WHERE client_id=%s AND date > current_date - 31 AND NOT is_branded
                                       AND array_length(regexp_split_to_array(query, '\\s+'), 1) BETWEEN 1 AND 3
                                       GROUP BY query ORDER BY sum(impressions) DESC LIMIT 4""", [cid])]
    s += [k for (k,) in conn.execute("""SELECT primary_keyword FROM pages WHERE client_id=%s AND page_type='collection'
                                        AND primary_keyword IS NOT NULL ORDER BY created_at LIMIT 4""", [cid])]
    import niches
    if niche and niche in niches.NICHES and len(s) < 3: s.append(niches.NICHES[niche][0].split(" and ")[0].lower())
    out = []
    for x in s:
        if x and not any(similar(x, y) for y in out): out.append(x)
    return out[:5]

def from_suggest(seeds, names, session=None, gl="in", pause=0.3):
    """Google's public autocomplete (no key, no cost). A handful of calls per run."""
    session = session or requests.Session()
    out = []
    for seed in seeds:
        for q in (f"how to {seed}", f"best {seed}", f"{seed} vs", f"what is {seed}", f"{seed} for"):
            try:
                r = session.get("https://suggestqueries.google.com/complete/search", params={"client": "firefox", "hl": "en", "gl": gl, "q": q}, timeout=10)
                sugg = r.json()[1] if r.status_code == 200 else []
            except Exception:
                sugg = []
            for s in sugg[:6]:
                if INFO.search(s) and not branded(s, names) and len(toks(s)) >= 2:
                    out.append({"keyword": s, "priority": 20, "evidence": {"source": "suggest", "text": s, "seed": seed}})
            time.sleep(pause)
    return out

def research(conn, cid, use_suggest=None, session=None):
    """Saves new blog ideas for the brand; returns how many were added."""
    name, domain, niche, aliases = conn.execute("SELECT name, domain, niche, aliases FROM clients WHERE client_id=%s", [cid]).fetchone()
    names = [name] + list(aliases or []) + [blogs.bare(domain).split(".")[0]]
    rows = from_gsc(conn, cid, names) + from_prompts(conn, cid) + from_reddit(conn, cid, names)
    if use_suggest is None: use_suggest = os.environ.get("BLOG_SUGGEST", "1") != "0"
    if use_suggest and len(rows) < 15:
        rows += from_suggest(seeds_for(conn, cid, niche), names, session)
    # merge near-duplicates: keep the strongest wording, add up the evidence
    merged = []
    for r in sorted(rows, key=lambda r: -r["priority"]):
        hit = next((m for m in merged if similar(m["keyword"], r["keyword"])), None)
        if hit:
            hit["evidence"].append(r["evidence"]); hit["priority"] += r["priority"] * 0.3
            if r["keyword"] not in hit["secondary"] and len(hit["secondary"]) < 6: hit["secondary"].append(r["keyword"])
        else:
            merged.append({"keyword": r["keyword"], "priority": r["priority"], "evidence": [r["evidence"]], "secondary": []})
    # drop what the site already covers: other drafts and posts, and existing blog URLs
    taken = [k for (k,) in conn.execute("SELECT target_keyword FROM content_drafts WHERE client_id=%s", [cid])]
    taken += [(u.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")) + " " + (k or "") for u, k in
              conn.execute("SELECT url, primary_keyword FROM pages WHERE client_id=%s AND page_type='blog'", [cid])]
    fresh = [m for m in merged if not any(similar(m["keyword"], t) for t in taken)]
    have = conn.execute("SELECT count(*) FROM content_drafts WHERE client_id=%s AND status='idea'", [cid]).fetchone()[0]
    added = 0
    for m in fresh[:max(0, IDEAS_KEEP - have)]:
        intent = "comparison" if re.search(r"\bvs\b|versus|difference|alternative", m["keyword"], re.I) else \
                 "commercial" if re.search(r"\bbest\b|\btop \d+|worth", m["keyword"], re.I) else "informational"
        cur = conn.execute("""INSERT INTO content_drafts (client_id, target_keyword, secondary_keywords, intent, evidence, priority)
                              VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                           [cid, m["keyword"], m["secondary"], intent, json.dumps(m["evidence"]), round(m["priority"], 1)])
        added += cur.rowcount
    return added

# ------------------------------------------------------------------ writing
DRAFT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["title", "meta_title", "meta_description", "slug", "excerpt", "tags", "body_html", "faqs", "sources"],
    "properties": {
        "title": {"type": "string"}, "meta_title": {"type": "string"}, "meta_description": {"type": "string"},
        "slug": {"type": "string"}, "excerpt": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
        "body_html": {"type": "string"},
        "faqs": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["q", "a"],
                 "properties": {"q": {"type": "string"}, "a": {"type": "string"}}}},
        "sources": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["url", "title"],
                    "properties": {"url": {"type": "string"}, "title": {"type": "string"}}}}}}

def link_targets(conn, cid, keyword, domain, limit=40):
    """Real pages of the site the post may link to: the most relevant collections, products and posts."""
    rows = conn.execute("""SELECT url, page_type::text, coalesce(primary_keyword,'') FROM pages WHERE client_id=%s
                           AND page_type IN ('collection','product','blog','home') LIMIT 3000""", [cid]).fetchall()
    rows += [(u, "blog", k) for u, k in conn.execute("""SELECT published_url, target_keyword FROM content_drafts
                                                        WHERE client_id=%s AND status='published' AND published_url IS NOT NULL""", [cid])]
    kt = toks(keyword)
    def rel(r):
        return len(kt & (toks(r[2]) | toks(r[0].rsplit("/", 1)[-1].replace("-", " ")))) + (0.5 if r[1] == "collection" else 0)
    rows = [r for r in rows if blogs.bare(blogs.host(r[0])) == blogs.bare(domain) or blogs.host(r[0]).endswith("." + blogs.bare(domain))]
    return sorted(rows, key=rel, reverse=True)[:limit]

def instructions(client, rules, targets, region):
    lo, hi = 1000, 1200
    r = rules or {}
    lines = [
        f"You write blog posts for {client['name']} ({client['domain']}), published on the brand's own store blog.",
        f"Audience: shoppers in {region or 'India'} researching the topic. Voice: {r.get('tone') or 'clear, expert and measured; useful before it is promotional'}.",
        f"Length: {lo} to {hi} words in body_html, not counting the FAQs.",
        "Open with a two-sentence direct answer to the search, so the post can be quoted by Google and AI assistants.",
        "Structure: body_html uses <h2> and <h3> only for headings (never <h1>), with <p>, <ul>/<ol>, <table> where a comparison helps, <strong>. "
        "At least three H2 or H3 headings are questions readers ask. Headings go in order (no H2 to H4 jumps).",
        "FAQs: give 5 to 6 short questions and answers in the faqs field (not in body_html).",
        "Facts: use web search for anything factual. Do not invent statistics, prices, dates, studies, awards, quotes or customer stories. "
        "If a fact cannot be sourced, leave it out. Cite outside sources with a normal link in the text and list them in sources.",
        "Internal links: link 3 to 5 of the brand's pages listed below, each once, with natural anchor text, including at least one collection or product. "
        "Use only these exact URLs; never invent a URL on the brand's site.",
        "Do not name or criticise competitor brands. Do not use em dashes. Avoid filler such as 'in today's world', 'in conclusion', 'game changer', 'elevate', 'unlock'.",
        "meta_title: under 60 characters, primary keyword near the start. meta_description: 140 to 155 characters, plain and specific. "
        "slug: lowercase words joined by hyphens, under 60 characters, contains the keyword. excerpt: one or two sentences. tags: 3 to 5.",
    ]
    if r.get("brand_spellings"): lines.append(f"Write the brand name exactly as: {r['brand_spellings'][0]}.")
    if r.get("product_names"): lines.append("Product names, exactly as written: " + ", ".join(r["product_names"][:20]) + ".")
    if r.get("usps"): lines.append("Brand facts you may use where relevant: " + "; ".join(r["usps"][:10]) + ".")
    if r.get("term_fixes"): lines.append("Terms: " + "; ".join(f"say '{v}', not '{k}'" for k, v in list(r["term_fixes"].items())[:15]) + ".")
    if r.get("banned_words"): lines.append("Never use these words: " + ", ".join(r["banned_words"][:30]) + ".")
    if r.get("claims_to_avoid"): lines.append("Never make these claims: " + "; ".join(r["claims_to_avoid"][:15]) + ".")
    lines.append("Brand pages you may link to (url | type | topic):\n" + "\n".join(f"{u} | {t} | {k}" for u, t, k in targets))
    return "\n".join(lines)

def brief(d):
    ev = d["evidence"] or []
    why = []
    for e in ev[:6]:
        if e["source"] == "gsc": why.append(f"Google search '{e['text']}': {e['impressions']} impressions in 30 days, average position {e['position']}")
        elif e["source"] == "prompt": why.append(f"Question asked to ChatGPT that does not name the brand yet: '{e['text']}'")
        elif e["source"] == "reddit": why.append(f"Reddit question in r/{e.get('subreddit')}: '{e['text']}'")
        else: why.append(f"Google suggests: '{e['text']}'")
    return (f"Primary keyword: {d['target_keyword']}\nSecondary keywords: {', '.join(d['secondary_keywords'] or []) or 'none'}\n"
            f"Search intent: {d['intent']}\nWhy this post: " + "; ".join(why))

def ask(session, key, system, user):
    body = {"model": MODEL, "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "tools": [{"type": "web_search"}],
            "text": {"format": {"type": "json_schema", "name": "blog_post", "schema": DRAFT_SCHEMA, "strict": True}}}
    r = session.post(API, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=300)
    if r.status_code != 200: raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    j = r.json()
    for item in j.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text"): return json.loads(part["text"])
    raise RuntimeError("OpenAI returned no draft")

def write_one(conn, cid, draft_id, key, session=None):
    import run_checks
    session = session or requests.Session()
    d = dict(zip(["target_keyword", "secondary_keywords", "intent", "evidence"],
                 conn.execute("SELECT target_keyword, secondary_keywords, intent, evidence FROM content_drafts WHERE draft_id=%s", [draft_id]).fetchone()))
    name, domain = conn.execute("SELECT name, domain FROM clients WHERE client_id=%s", [cid]).fetchone()
    region = (conn.execute("SELECT region FROM prompts WHERE client_id=%s LIMIT 1", [cid]).fetchone() or ["IN"])[0]
    rules = run_checks.load_brand(conn, cid)
    shop = conn.execute("SELECT blog_handle, author_name FROM cms_connections WHERE client_id=%s AND platform='shopify'", [cid]).fetchone() or (None, None)
    targets = link_targets(conn, cid, d["target_keyword"], domain)
    conn.execute("UPDATE content_drafts SET status='writing', error=NULL, updated_at=now() WHERE draft_id=%s", [draft_id])
    try:
        out = ask(session, key, instructions({"name": name, "domain": domain}, rules, targets, {"IN": "India"}.get(region, region)), brief(d))
    except Exception as e:
        conn.execute("UPDATE content_drafts SET status='failed', error=%s, updated_at=now() WHERE draft_id=%s", [str(e)[:500], draft_id]); raise
    body, internal, external = blogs.clean_body(out["body_html"], domain, [t[0] for t in targets])
    post = {"title": blogs.dashes(out["title"]).strip(), "meta_title": blogs.dashes(out["meta_title"]).strip()[:70],
            "meta_description": blogs.dashes(out["meta_description"]).strip()[:170], "slug": blogs.slugify(out["slug"] or out["title"]),
            "excerpt": blogs.dashes(out["excerpt"]).strip(), "tags": [t.strip() for t in out["tags"] if t.strip()][:6],
            "body_html": body, "faqs": [f for f in out["faqs"] if f["q"].strip() and f["a"].strip()][:8],
            "target_keyword": d["target_keyword"], "secondary_keywords": d["secondary_keywords"]}
    used = {blogs.norm_url(u) for u in external}
    sources = [s for s in out["sources"] if blogs.norm_url(s["url"]) in used] or [{"url": u, "title": blogs.host(u)} for u in external]
    results, sc = blogs.check(post, domain, rules, shop[0], shop[1] or name, name)
    conn.execute("""UPDATE content_drafts SET status='draft', title=%s, meta_title=%s, meta_description=%s, slug=%s, excerpt=%s, tags=%s,
                    body_html=%s, faqs=%s, sources=%s, internal_links=%s, word_count=%s, checks=%s, score=%s, model=%s,
                    preview_token=coalesce(preview_token, %s), written_at=now(), updated_at=now() WHERE draft_id=%s""",
                 [post["title"], post["meta_title"], post["meta_description"], post["slug"], post["excerpt"], post["tags"],
                  post["body_html"], json.dumps(post["faqs"]), json.dumps(sources), internal, blogs.words(body), json.dumps(results), sc,
                  MODEL, blogs.new_token(), draft_id])
    return post["title"]

def write(conn, cid, key, n=None, session=None):
    """Writes up to n drafts from the top ideas, keeping at most BLOG_QUEUE drafts waiting for review."""
    waiting = conn.execute("SELECT count(*) FROM content_drafts WHERE client_id=%s AND status IN ('draft','writing')", [cid]).fetchone()[0]
    n = max(0, min(PER_RUN if n is None else n, QUEUE - waiting))
    ids = [r[0] for r in conn.execute("""SELECT draft_id FROM content_drafts WHERE client_id=%s AND status IN ('idea','failed')
                                         ORDER BY (status='failed'), priority DESC NULLS LAST LIMIT %s""", [cid, n])]
    done = []
    for i in ids:
        try: done.append(write_one(conn, cid, i, key, session))
        except Exception as e: print(f"  draft failed: {e}", file=sys.stderr)
    return done

def run(conn, cid, key=None, n=None):
    conn.execute("""UPDATE content_drafts SET status='idea' WHERE client_id=%s AND status='writing'
                    AND updated_at < now() - interval '1 hour'""", [cid])           # a run cut off half way
    added = research(conn, cid)
    written = write(conn, cid, key, n) if key else []
    return f"{added} new ideas, {len(written)} drafts written" + ("" if key else " (no OpenAI key: ideas only)")

if __name__ == "__main__":
    from common import connect
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id", required=True); ap.add_argument("--write", type=int)
    a = ap.parse_args()
    with connect() as conn: print(run(conn, a.client_id, os.environ.get("OPENAI_API_KEY_2"), a.write))
