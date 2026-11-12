"""Shared by the Reddit and YouTube collectors: what to search for, which brands a text mentions, and simple reads of
tone, questions and pain points. The tone read is a word list, not a model: it is a rough signal, labelled as such."""
import re
from ai_visibility import tracked_brands, find_mentions

POSITIVE = ["love", "loved", "great", "good", "beautiful", "recommend", "amazing", "best", "happy", "pretty",
            "durable", "elegant", "gorgeous", "satisfied", "value for money", "lovely", "perfect"]
NEGATIVE = ["bad", "worst", "fake", "tarnish", "tarnished", "turned black", "broke", "broken", "disappointed", "disappointing", "poor",
            "refund", "scam", "overpriced", "allergy", "rash", "itchy", "delay", "never again", "cheap quality", "not worth", "rude", "damaged"]
PAIN = {"Tarnish and care": ["tarnish", "turn black", "turned black", "turns black", "oxidis", "oxidiz", "clean silver", "polish", "darken"],
        "Real silver or not": ["fake", "real silver", "pure silver", "925", "92.5", "hallmark", "genuine", "purity", "silver plated"],
        "Skin reactions": ["allergy", "allergic", "rash", "itch", "green skin", "sensitive skin"],
        "Price and value": ["expensive", "overpriced", "price", "worth it", "cheap", "making charge", "value for money"],
        "Delivery and service": ["delivery", "refund", "return", "customer service", "customer care", "late", "damaged", "exchange"],
        "Durability": ["broke", "broken", "clasp", "snapped", "loose", "bent", "fell off"]}
QUESTION_START = re.compile(r"^\s*(how|what|which|where|why|when|is|are|can|could|should|does|do|did|any|anyone|has|have|would|will)\b", re.I)

def tone(text):
    t = (text or "").lower()
    score = sum(t.count(w) for w in POSITIVE) - sum(t.count(w) for w in NEGATIVE)
    return "positive" if score > 0 else "negative" if score < 0 else "neutral"

def is_question(title, body=""):
    first = (title or "").strip() or (body or "").strip().split("\n")[0]
    return first.rstrip().endswith("?") or bool(QUESTION_START.match(first)) and len(first.split()) >= 4

def pain_point(text):
    t = (text or "").lower()
    hits = [(sum(t.count(w) for w in words), theme) for theme, words in PAIN.items()]
    best = max(hits)
    return best[1] if best[0] else None

def brands_in(text, brands):
    """Brand names in the text, client first, using the same rules as ChatGPT answers ("PNG" inside "PNG Brothers" is not the client)."""
    found = find_mentions(text or "", brands)
    return sorted({m["name"] for m in found}, key=lambda n: (not any(b["is_client"] and b["name"] == n for b in brands), n))

def display_name(b):
    """'pngbrothers.com' with alias 'PNG Brothers' -> 'PNG Brothers' (searchable words, not a domain)."""
    words = [a for a in (b.get("aliases") or []) if " " in a or not a.islower()]
    if b["is_client"] or "." not in b["name"]: return b["name"]
    return words[0] if words else b["name"].split(".")[0]

GENERIC_SLUGS = {"all", "frontpage", "new", "new-arrivals", "latest", "sale", "offers", "best-sellers", "bestsellers", "bestseller", "shop-all",
                 "featured", "trending", "collections", "products", "home", "shop", "clearance", "deals", "gift-card", "gift-cards"}
STOP = {"for", "with", "and", "the", "of", "in", "to", "a", "near", "me", "online", "buy", "price", "best", "under", "below", "designs", "design",
        "new", "latest", "women", "men", "girls", "boys", "ladies"}

def _stem(w): return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w

def auto_topics(conn, client_id, n=6):
    """The brand's categories, found on their own: collection names from the sitemap, ranked by how often people reach
    the site searching for them (Search Console, non-branded), topped up from the most common non-branded search phrases.
    Brand and competitor names never become topics. A person can still override them (clients.community_topics)."""
    import gsc
    name, dom, aliases = conn.execute("SELECT name, domain, aliases FROM clients WHERE client_id=%s", [client_id]).fetchone()
    own = gsc.brand_terms(name, dom, aliases)
    rival = gsc.competitor_terms(conn, client_id, own)
    bad = lambda t: any(x in t.replace(" ", "") for x in list(own) + list(rival))
    queries = conn.execute("""SELECT lower(query), sum(impressions) + 20 * sum(clicks) FROM gsc_search_daily WHERE client_id=%s AND NOT is_branded
                              AND date > current_date - 90 GROUP BY 1 ORDER BY 2 DESC LIMIT 3000""", [client_id]).fetchall()
    queries = [(q, w) for q, w in queries if not bad(q)]
    qwords = [({_stem(x) for x in q.split()}, w) for q, w in queries]
    def score(phrase):
        ws = {_stem(x) for x in phrase.split() if x not in STOP}
        return sum(w for qs, w in qwords if ws and ws <= qs)
    cands = {}
    for (url,) in conn.execute("SELECT url FROM pages WHERE client_id=%s AND page_type='collection'", [client_id]):
        slug = url.rstrip("/").rsplit("/", 1)[-1].lower()
        if slug in GENERIC_SLUGS or re.search(r"under|below|rs-?\d|\d{3,}", slug): continue
        phrase = re.sub(r"[-_]+", " ", slug).strip()
        if len(phrase) >= 4 and not bad(phrase): cands[phrase] = score(phrase)
    if len([c for c in cands.values() if c]) < n:              # few collections: the commonest short searches, whole
        for q, w in queries[:300]:
            words = [x for x in q.split() if x not in STOP]
            if 2 <= len(words) <= 3 and not any(x.isdigit() for x in words):
                cands.setdefault(" ".join(words), w)
    chosen = []
    for p, w in sorted(cands.items(), key=lambda x: -x[1]):
        ws = {_stem(x) for x in p.split() if x not in STOP}
        if not ws or any(ws <= {_stem(x) for x in c.split()} or {_stem(x) for x in c.split()} <= ws for c in chosen): continue
        chosen.append(p)
        if len(chosen) == n: break
    return chosen

def topics(conn, client_id):
    """(topics, automatic?) — a person's override if set, otherwise the automatic list."""
    t = conn.execute("SELECT community_topics FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    return (list(t), False) if t else (auto_topics(conn, client_id), True)

def discovery_queries(conn, client_id, k=3):
    """What to look up to find the communities and channels that matter: the brand's top topics."""
    return topics(conn, client_id)[0][:k]

def searches(conn, client_id):
    """[(found_by, query)]: the brand's names, each tracked competitor, and the brand's topics (found automatically)."""
    brands = tracked_brands(conn, client_id)
    me = brands[0]
    out = [("brand", me["name"])] + [("brand", a) for a in me["aliases"] if " " in a and a.lower() != me["name"].lower()][:3]
    out += [("competitor", display_name(b)) for b in brands[1:]]
    out += [("topic", t) for t in topics(conn, client_id)[0]]
    seen, uniq = set(), []
    for k, q in out:
        if q.lower() not in seen: seen.add(q.lower()); uniq.append((k, q))
    return uniq

def cited_urls(conn, client_id, domains):
    """URLs ChatGPT cited in this brand's answers, for the given domains (e.g. reddit.com)."""
    return [r[0] for r in conn.execute("""SELECT DISTINCT url FROM citations WHERE client_id=%s AND (domain = ANY(%s) OR domain LIKE ANY(%s))""",
                                       [client_id, domains, ["%." + d for d in domains]])]
