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

def searches(conn, client_id):
    """[(found_by, query)]: the brand's names, each tracked competitor, and the brand's topics."""
    import semrush_suite
    brands = tracked_brands(conn, client_id)
    me = brands[0]
    out = [("brand", me["name"])] + [("brand", a) for a in me["aliases"] if " " in a and a.lower() != me["name"].lower()][:3]
    out += [("competitor", display_name(b)) for b in brands[1:]]
    topics = conn.execute("SELECT community_topics FROM clients WHERE client_id=%s", [client_id]).fetchone()[0] or semrush_suite.default_seeds(conn, client_id)
    out += [("topic", t) for t in topics]
    seen, uniq = set(), []
    for k, q in out:
        if q.lower() not in seen: seen.add(q.lower()); uniq.append((k, q))
    return uniq

def cited_urls(conn, client_id, domains):
    """URLs ChatGPT cited in this brand's answers, for the given domains (e.g. reddit.com)."""
    return [r[0] for r in conn.execute("""SELECT DISTINCT url FROM citations WHERE client_id=%s AND (domain = ANY(%s) OR domain LIKE ANY(%s))""",
                                       [client_id, domains, ["%." + d for d in domains]])]
