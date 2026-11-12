"""The SOP as code. Every check is named by its SOP step id, so a result on screen points to the rule in the SOP doc.

run_page_checks(content, page, brand) -> list of {check_id, result, found, message}
  content = page_parser.parse_html(...) or page_parser.draft_content(...)
  page    = {"page_type": "product"|"collection"|"blog"|"home"|"static", "primary_keyword": str, "secondary_keywords": [...]}
  brand   = a brand_rules row as a dict (missing keys are treated as empty)
run_site_checks(pages) -> {page_id: [results]}  for rules that need every page at once (META-08, KW-03)
score(results) -> 0-100, share of applicable checks that pass (a warning counts half)

result is one of: pass, warn, fail, na (not applicable, e.g. intro length on a live page we cannot isolate).
"""
import datetime as dt, re
from urllib.parse import urlparse
from page_parser import host_of

# Limits from the SOP. Change them here and every check follows.
RULES = {
    "url_max": 75, "title_max": 60, "desc_min": 150, "desc_max": 160, "h1_max": 55,
    "keyword_start_within": 15,                       # META-02 primary keyword should start within the first 15 characters
    "intro_chars": (250, 400), "read_more_words": (300, 500),
    "faqs": {"collection": (4, 6), "product": (4, 6), "blog": (5, None)},
    "pdp_words": (250, 350), "blog_words": (1000, 1200),
    "density_max": 1.5,                               # PDP-04 primary keyword uses per 100 words
    "question_headings_min": 3,                       # BLOG-05
}
STOPWORDS = {"a", "an", "the", "of", "in", "for", "to", "and", "with", "on", "at", "by", "or", "from"}
QUESTION_START = re.compile(r"^(what|why|how|which|when|where|who|can|is|are|does|do|should|will)\b", re.I)
WORST = {"fail": 3, "warn": 2, "pass": 1, "na": 0}

def norm(s):
    return " ".join(re.sub(r"[-_/|–—:,.!?()]+", " ", (s or "").lower()).split())

def has_phrase(text, phrase):
    return bool(phrase) and f" {norm(phrase)} " in f" {norm(text)} "

def count_phrase(text, phrase):
    t, p = f" {norm(text)} ", f" {norm(phrase)} "
    return t.count(p) if p.strip() else 0

def find_words(text, words):
    return [w for w in words if w and re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", text or "", re.I)]

def R(check_id, result, found, message):
    return {"check_id": check_id, "result": result, "found": found, "message": message}

def combine(check_id, problems, found, ok_message):
    """problems = [(result, message)] -> one row with the worst result and every message."""
    if not problems: return R(check_id, "pass", found, ok_message)
    worst = max(problems, key=lambda p: WORST[p[0]])[0]
    return R(check_id, worst, found, " ".join(m for _, m in problems))

def body_links(c):
    return [l for l in c["links"] if not l.get("chrome")]

def clean_href(h):
    u = urlparse(h); return f"{host_of(h)}{u.path.rstrip('/')}"

# ------------------------------------------------------------------ META: every page
def check_url(c, page, brand):
    url = c["url"]; path = urlparse(url).path; slug = path.rstrip("/").rsplit("/", 1)[-1]
    p = []
    if len(url) > RULES["url_max"]: p.append(("fail", f"URL is {len(url)} characters; keep it to {RULES['url_max']} or fewer."))
    if path != path.lower(): p.append(("fail", "URL has capital letters; use lowercase only."))
    if "_" in slug: p.append(("warn", "Use hyphens, not underscores."))
    stops = [w for w in slug.split("-") if w in STOPWORDS]
    if stops: p.append(("warn", f"Drop filler words from the URL: {', '.join(stops)}."))
    kw = page.get("primary_keyword")
    if kw and page["page_type"] not in ("home", "static"):
        missing = [w for w in norm(kw).split() if w not in STOPWORDS and w not in slug.lower().split("-")]
        if missing: p.append(("warn", f"URL is missing keyword words: {', '.join(missing)}. Change a live URL only with a 301."))
    return combine("META-01", p, f"{len(url)} characters", "URL is short, lowercase and keyword-led.")

def check_title(c, page, brand):
    t = c["title"]; kw = page.get("primary_keyword")
    if not t: return R("META-02", "fail", "missing", "Add a meta title.")
    p = []
    if len(t) > RULES["title_max"]: p.append(("fail", f"Title is {len(t)} characters; cut it to {RULES['title_max']}."))
    if kw:
        i = norm(t).find(norm(kw))
        if i < 0: p.append(("fail", f"Title does not contain the primary keyword \"{kw}\"."))
        elif i > RULES["keyword_start_within"]: p.append(("warn", "Move the primary keyword to the start of the title."))
    spellings = brand.get("brand_spellings") or []
    if spellings:
        n = len(re.findall(rf"(?<!\w){re.escape(spellings[0])}(?!\w)", t, re.I))
        if n > 1: p.append(("fail", f"Brand name appears {n} times in the title; keep it once."))
        if n and brand.get("theme_appends_brand"): p.append(("fail", "The theme already adds the brand to titles, so remove it here."))
    bad = find_words(t, (brand.get("title_banned_words") or []) + (brand.get("banned_words") or []))
    if bad: p.append(("fail", f"Remove from the title: {', '.join(bad)}."))
    return combine("META-02", p, f"{len(t)} characters", "Title meets the standard.")

def check_description(c, page, brand):
    d = c["description"]; kw = page.get("primary_keyword")
    if not d: return R("META-03", "fail", "missing", "Add a meta description of 150 to 160 characters.")
    p = []
    if len(d) > RULES["desc_max"]: p.append(("fail", f"Description is {len(d)} characters; Google cuts it after about {RULES['desc_max']}."))
    elif len(d) < RULES["desc_min"]: p.append(("warn", f"Description is {len(d)} characters; use 150 to 160."))
    if kw and not has_phrase(d, kw): p.append(("fail", f"Description does not contain \"{kw}\"."))
    regions = brand.get("region_words") or []
    if regions and not find_words(d, regions): p.append(("warn", f"Add the region ({', '.join(regions)})."))
    markers = brand.get("usp_markers") or []
    if page["page_type"] == "product" and markers and not find_words(d, markers):
        p.append(("fail", f"Add at least one USP: {', '.join(brand.get('usps') or markers)}."))
    return combine("META-03", p, f"{len(d)} characters", "Description meets the standard.")

def check_h1(c, page, brand):
    h1s, kw = c["h1s"], page.get("primary_keyword")
    if not h1s: return R("META-04", "fail", "none", "Add one H1 with the primary keyword.")
    p = []
    if len(h1s) > 1: p.append(("fail", f"Page has {len(h1s)} H1s; keep one."))
    h = h1s[0]
    if kw and page["page_type"] not in ("home", "static") and not has_phrase(h, kw): p.append(("fail", f"H1 does not contain \"{kw}\"."))
    if len(h) > RULES["h1_max"]: p.append(("warn", f"H1 is {len(h)} characters; keep it to {RULES['h1_max']}."))
    if c["title"] and norm(h) == norm(c["title"]): p.append(("fail", "H1 is the same as the title; make them different."))
    bad = find_words(h, brand.get("title_banned_words") or [])
    if bad: p.append(("fail", f"Remove from the H1: {', '.join(bad)}."))
    return combine("META-04", p, f"\"{h}\" ({len(h)} characters)", "H1 meets the standard.")

def check_og(c, page, brand):
    miss = [k for k, v in (("OG title", c["og_title"]), ("OG description", c["og_description"])) if not v]
    return R("META-05", "warn" if miss else "pass", ", ".join(miss) or "both present",
             f"Fill in: {', '.join(miss)}." if miss else "OG tags present.")

def check_alt(c, page, brand):
    imgs = c["images"]
    if not imgs: return R("META-06", "na", "no images", "No images to check.")
    missing = [i for i in imgs if i["alt"] is None]; empty = [i for i in imgs if i["alt"] == ""]
    if missing: return R("META-06", "fail", f"{len(missing)} of {len(imgs)} images", "Add alt text that describes each image.")
    if empty: return R("META-06", "warn", f"{len(empty)} of {len(imgs)} images", "Empty alt text: fine for decoration, otherwise describe the image.")
    return R("META-06", "pass", f"{len(imgs)} images", "Every image has alt text.")

def check_year(c, page, brand):
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", c["title"] or "")]
    old = [y for y in years if y < dt.date.today().year]
    if old: return R("META-07", "warn", str(old[0]), f"Title says {old[0]}. Update the title and the content together, or drop the year.")
    return R("META-07", "pass" if years else "na", str(years[0]) if years else "no year", "Year is current." if years else "No year in the title.")

# ------------------------------------------------------------------ content: by page type
def check_intro(c, page, brand):
    if page["page_type"] != "collection": return None
    intro = c.get("intro")
    if intro is None: return R("COL-04", "na", "live page", "Intro text is checked on drafts.")
    lo, hi = RULES["intro_chars"]; n = len(intro); p = []
    if not lo <= n <= hi: p.append(("fail", f"Intro is {n} characters; write {lo} to {hi}."))
    kw = page.get("primary_keyword")
    first = re.split(r"(?<=[.!?])\s", intro.strip(), maxsplit=1)[0] if intro.strip() else ""
    if kw and not has_phrase(first, kw): p.append(("warn", "Put the primary keyword in the first sentence."))
    return combine("COL-04", p, f"{n} characters", "Intro meets the standard.")

def check_faqs(c, page, brand):
    t = page["page_type"]
    if t not in RULES["faqs"]: return None
    cid = {"collection": "COL-05", "product": "PDP-05", "blog": "BLOG-09"}[t]
    lo, hi = RULES["faqs"][t]; n = len(c["faqs"]); p = []
    if n < lo: p.append(("fail", f"{n} FAQs; add at least {lo}."))
    elif hi and n > hi: p.append(("warn", f"{n} FAQs; {lo} to {hi} is the standard."))
    if c["html_bytes"] is not None and n and "FAQPage" not in c["schema_types"]: p.append(("warn", "Add FAQ schema so the answers can show in search."))
    return combine(cid, p, f"{n} FAQs", "FAQs meet the standard.")

def check_read_more(c, page, brand):
    if page["page_type"] != "collection": return None
    rm = c.get("read_more")
    if rm is None: return R("COL-06", "na", "live page", "Read-more length is checked on drafts.")
    lo, hi = RULES["read_more_words"]; n = len(re.findall(r"[\w'’-]+", rm)); p = []
    if not lo <= n <= hi: p.append(("fail", f"Read-more is {n} words; write {lo} to {hi}."))
    qs = [h for l, h in c["headings"] if l >= 2 and (h.endswith("?") or QUESTION_START.match(h))]
    if not qs: p.append(("warn", "Use question-style subheadings (what, which, how)."))
    return combine("COL-06", p, f"{n} words", "Read-more meets the standard.")

def check_pdp_length(c, page, brand):
    if page["page_type"] != "product": return None
    if c.get("body") is None: return R("PDP-03", "na", "live page", "Description length is checked on drafts.")
    lo, hi = RULES["pdp_words"]; n = len(re.findall(r"[\w'’-]+", c["body"]))
    ok = lo <= n <= hi
    return R("PDP-03", "pass" if ok else "warn", f"{n} words", "Length is right." if ok else f"Product description is {n} words; aim for {lo} to {hi}.")

def check_density(c, page, brand):
    kw = page.get("primary_keyword")
    if not kw or page["page_type"] in ("home", "static"): return None
    text = c.get("body") or c["text"]
    words = len(re.findall(r"[\w'’-]+", text))
    if words < 50: return R("PDP-04", "na", f"{words} words", "Too little copy to judge keyword use.")
    n = count_phrase(text, kw); per100 = round(100 * n / words, 2); p = []
    if per100 > RULES["density_max"]: p.append(("fail", f"\"{kw}\" is used {n} times ({per100} per 100 words); bring it to about 1."))
    sents = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if any(has_phrase(a, kw) and has_phrase(b, kw) for a, b in zip(sents, sents[1:])):
        p.append(("warn", "The keyword appears in two sentences in a row; vary the wording."))
    return combine("PDP-04", p, f"{n} uses, {per100} per 100 words", "Keyword use reads naturally.")

def check_product_schema(c, page, brand):
    if page["page_type"] != "product": return None
    if c["html_bytes"] is None: return R("PDP-06", "na", "draft", "Schema is checked on the live page.")
    pr = c["product"]
    if not pr: return R("PDP-06", "fail", "no Product schema", "Add Product schema with price, availability and rating.")
    p = []
    if not pr["has_price"]: p.append(("fail", "Product schema has no price."))
    if not pr["has_availability"]: p.append(("fail", "Product schema has no availability."))
    if not pr["has_rating"]: p.append(("warn", "No review rating in the schema; add it once reviews exist."))
    return combine("PDP-06", p, "Product schema", "Product schema is complete.")

def check_blog(c, page, brand):
    if page["page_type"] != "blog": return []
    out = []
    lo, hi = RULES["blog_words"]; n = c["word_count"]
    out.append(R("BLOG-03", "fail" if n < lo else "warn" if n > hi else "pass", f"{n} words",
                 f"Blog is {n} words; write {lo} to {hi}." if not lo <= n <= hi else "Length is right."))
    levels = [l for l, _ in c["headings"]]; skips = [(a, b) for a, b in zip(levels, levels[1:]) if b > a + 1]
    out.append(R("BLOG-04", "fail" if skips else "pass", f"{len(levels)} headings",
                 f"Heading level skipped (H{skips[0][0]} to H{skips[0][1]}); go in order." if skips else "Headings are in order."))
    qs = [h for l, h in c["headings"] if l in (2, 3) and (h.endswith("?") or QUESTION_START.match(h))]
    m = RULES["question_headings_min"]
    out.append(R("BLOG-05", "pass" if len(qs) >= m else "warn", f"{len(qs)} question headings",
                 "Answer format is in place." if len(qs) >= m else f"Turn at least {m} H2s or H3s into questions readers ask."))
    links = body_links(c); internal = [clean_href(l["href"]) for l in links if l["internal"]]
    dup = sorted({h for h in internal if internal.count(h) > 1})
    commercial = [h for h in internal if "/products/" in h or "/collections/" in h]
    p = []
    if dup: p.append(("fail", f"Linked more than once: {', '.join(dup[:3])}. Link each page once."))
    if not commercial: p.append(("warn", "Link to the products and collections this blog supports."))
    out.append(combine("BLOG-07", p, f"{len(internal)} internal links", "Internal links meet the standard."))
    ext = [l for l in links if not l["internal"] and "nofollow" not in l["rel"].split()]
    out.append(R("BLOG-08", "fail" if ext else "pass", f"{len(ext)} without nofollow",
                 "Add rel=\"nofollow\" to source links: " + ", ".join(host_of(l["href"]) for l in ext[:3]) + "." if ext else "Source links are nofollow."))
    if c["html_bytes"] is not None:
        out.append(R("BLOG-11", "pass" if c["article_author"] else "fail", c["article_author"] or "no author",
                     "Author is named." if c["article_author"] else "Name the author in the byline and the Article schema."))
        art = set(c["schema_types"]) & {"Article", "BlogPosting", "NewsArticle"}
        out.append(R("BLOG-13", "pass" if art else "fail", ", ".join(sorted(art)) or "none",
                     "Article schema present." if art else "Add Article (or BlogPosting) schema."))
    return out

# ------------------------------------------------------------------ brand rules and writing rules
def copy_of(c):
    return " ".join([c["title"], c["description"], " ".join(c["h1s"]), c["text"]])

def check_writing(c, page, brand):
    out, copy = [], copy_of(c)
    dashes = copy.count("—")
    out.append(R("WRITE-01", ("fail" if c["html_bytes"] is None else "warn") if dashes else "pass", f"{dashes} em dashes",
                 "Replace em dashes with a comma, colon or full stop." if dashes else "No em dashes."))
    bad = find_words(copy, brand.get("banned_words") or [])
    out.append(R("WRITE-02", "fail" if bad else "pass", ", ".join(bad) or "none", f"Remove: {', '.join(bad)}." if bad else "No banned words."))
    wrong = []
    for w, right in (brand.get("term_fixes") or {}).items():
        masked = re.sub(re.escape(right), " ", copy, flags=re.I)
        if find_words(masked, [w]): wrong.append(f"\"{w}\" should be \"{right}\"")
    out.append(R("BR-02", "fail" if wrong else "pass", f"{len(wrong)} wrong terms", "; ".join(wrong) + "." if wrong else "Product terms are correct."))
    claims = [x for x in (brand.get("claims_to_avoid") or []) if has_phrase(copy, x)]
    out.append(R("BR-06", "fail" if claims else "pass", ", ".join(claims) or "none",
                 f"Remove or source these claims: {', '.join(claims)}." if claims else "No claims to avoid."))
    sp = brand.get("brand_spellings") or []
    if sp:
        found = {m.group(0) for m in re.finditer(rf"(?<!\w){re.escape(sp[0]).replace(' ', '[ -]?')}(?!\w)", copy, re.I)}
        found |= {v for v in sp[1:] if find_words(copy, [v])}
        wrong_sp = sorted(f for f in found if f != sp[0])
        out.append(R("BR-01", "warn" if wrong_sp else "pass", ", ".join(wrong_sp) or sp[0],
                     f"Write the brand as \"{sp[0]}\", not {', '.join(wrong_sp)}." if wrong_sp else "Brand name is spelled right."))
    bad_hosts = brand.get("wrong_domains") or []
    if bad_hosts:
        hits = sorted({l["href"] for l in c["links"] if any(host_of(l["href"]) == d or host_of(l["href"]).endswith("." + d) for d in bad_hosts)})
        out.append(R("BR-08", "fail" if hits else "pass", f"{len(hits)} links",
                     f"Links point to the wrong site: {', '.join(hits[:3])}. Point them to {brand.get('correct_domain') or 'the live domain'}." if hits else "All links point to the right domain."))
    return out

PAGE_CHECKS = [check_url, check_title, check_description, check_h1, check_og, check_alt, check_year,
               check_intro, check_faqs, check_read_more, check_pdp_length, check_density, check_product_schema]

def run_page_checks(content, page, brand=None):
    brand = brand or {}
    out = []
    for f in PAGE_CHECKS:
        r = f(content, page, brand)
        if r: out.append(r)
    out += check_blog(content, page, brand)
    out += check_writing(content, page, brand)
    return out

# ------------------------------------------------------------------ across all of a brand's pages
def run_site_checks(pages):
    """pages = [{page_id, primary_keyword, content}] (content may be None if never fetched)."""
    out = {p["page_id"]: [] for p in pages}
    shared = {p["page_id"]: [] for p in pages}
    for key, label in (("title", "title"), ("description", "meta description")):
        seen = {}
        for p in pages:
            v = norm((p.get("content") or {}).get(key, ""))
            if v: seen.setdefault(v, []).append(p["page_id"])
        for ids in seen.values():
            if len(ids) > 1:
                for i in ids: shared[i].append(f"{label} ({len(ids) - 1} other pages)")
    for p in pages:
        if p.get("content") is None: continue
        s = shared[p["page_id"]]
        out[p["page_id"]].append(R("META-08", "fail" if s else "pass", "; ".join(s) or "unique",
                                   f"Same {' and '.join(x.split(' (')[0] for x in s)} as other pages; make it unique." if s else "Title and description are unique."))
    kws = {}
    for p in pages:
        if p.get("primary_keyword"): kws.setdefault(norm(p["primary_keyword"]), []).append(p)
    for k, ps in kws.items():
        if len(ps) > 1:
            for p in ps:
                others = [q["url"] for q in ps if q is not p]
                out[p["page_id"]].append(R("KW-03", "fail", f"\"{k}\" on {len(ps)} pages",
                                           f"Primary keyword is also targeted by {', '.join(others[:2])}. Give each page its own."))
    return out

def score(results):
    s = [r for r in results if r["result"] != "na"]
    if not s: return None
    return round(100 * sum(1 if r["result"] == "pass" else 0.5 if r["result"] == "warn" else 0 for r in s) / len(s))
