"""Page checker tests. Fixtures are SYNTHETIC pages built to repeat the mistakes the client corrected in the
Easeliving workbook (brand twice in the title, long descriptions, no USP, wrong domain, "natural latex", em dashes)."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "seo"))
from page_parser import parse_html, draft_content
from checks import run_page_checks, run_site_checks, score

BRAND = {
    "brand_spellings": ["Easeliving", "ease living"], "usps": ["15-night trial", "custom sizes", "up to 10-year warranty"],
    "usp_markers": ["trial", "custom", "warranty"], "title_banned_words": ["collection"], "banned_words": ["game changer"],
    "term_fixes": {"natural latex": "natural organic latex", "organic latex": "natural organic latex"},
    "claims_to_avoid": ["90% of orthopedic specialists"], "region_words": ["UAE", "Dubai"],
    "correct_domain": "easelivinguae.com", "wrong_domains": ["easeliving.in"], "theme_appends_brand": False,
}
by = lambda rs: {r["check_id"]: r for r in rs}

BAD_PDP = """<html><head><title>Cooling Gel Memory Foam Mattress | Plush Comfort – Easeliving – EaseLiving</title>
<meta name="description" content="Discover the Easeliving Cooling Gel Memory Foam Mattress. Stay cool with gel-infused foam, plush comfort and adaptive support for every sleeper, night after night, all year long.">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Gel","offers":{"@type":"Offer","price":"1999"}}</script>
</head><body><header><nav><a href="/collections/all">Shop</a></nav></header>
<h1>Cooling Gel Memory Foam Mattress | Plush Comfort – Easeliving – EaseLiving</h1>
<p>The cool gel memory foam mattress keeps you cool. Our cool gel memory foam mattress is soft — and our cool gel memory foam mattress is firm.
It pairs well with our natural latex pillow. This is a game changer for sleep, say 90% of orthopedic specialists.
""" + "Sleep better every night with pressure relief and support. " * 12 + """</p>
<a href="https://easeliving.in/products/pillow">Pillow</a><img src="/a.jpg"><img src="/b.jpg" alt="Gel mattress layers">
<h2>Is it cool?</h2><h2>Does it sag?</h2></body></html>"""

c = parse_html("https://easelivinguae.com/products/Gel_Memory_Foam_Mattress-for-the-bed", BAD_PDP)
rs = by(run_page_checks(c, {"page_type": "product", "primary_keyword": "cool gel memory foam mattress"}, BRAND))
assert rs["META-01"]["result"] == "fail" and "capital" in rs["META-01"]["message"] and "filler" in rs["META-01"]["message"], rs["META-01"]
assert rs["META-02"]["result"] == "fail" and "2 times" in rs["META-02"]["message"] and "cut it to 60" in rs["META-02"]["message"], rs["META-02"]
assert rs["META-03"]["result"] == "fail" and "USP" in rs["META-03"]["message"] and "Add the region" in rs["META-03"]["message"], rs["META-03"]
assert rs["META-04"]["result"] == "fail" and "same as the title" in rs["META-04"]["message"], rs["META-04"]
assert rs["META-05"]["result"] == "warn"
assert rs["META-06"]["result"] == "fail" and rs["META-06"]["found"] == "1 of 2 images"
assert rs["PDP-04"]["result"] == "fail" and "3 times" in rs["PDP-04"]["message"], rs["PDP-04"]
assert rs["PDP-05"]["result"] == "fail" and rs["PDP-05"]["found"] == "2 FAQs"
assert rs["PDP-06"]["result"] == "fail" and "availability" in rs["PDP-06"]["message"] and "rating" in rs["PDP-06"]["message"]
assert rs["PDP-03"]["result"] == "na"                                         # live page: description length is judged on drafts
assert rs["WRITE-01"]["result"] == "warn"                                     # em dash on a live page
assert rs["WRITE-02"]["result"] == "fail" and "game changer" in rs["WRITE-02"]["message"]
assert rs["BR-02"]["result"] == "fail" and "natural organic latex" in rs["BR-02"]["message"]
assert rs["BR-06"]["result"] == "fail"
assert rs["BR-01"]["result"] == "warn" and "EaseLiving" in rs["BR-01"]["message"], rs["BR-01"]
assert rs["BR-08"]["result"] == "fail" and "easeliving.in" in rs["BR-08"]["message"]
assert c["word_count"] > 50 and "Shop" not in c["text"]                       # header and nav are not page copy
print("bad product page: every planted mistake caught, score", score(list(rs.values())))

# A draft that follows the SOP passes. "natural organic latex" must not trip the "organic latex" fix.
body = ("Why choose the Easeliving cool gel memory foam mattress? If you wake up warm or stiff, this mattress is built for you. "
        "A gel-infused top layer draws heat away while memory foam follows your shape and eases pressure on shoulders and hips. "
        "The dense base keeps your spine aligned, and it pairs well with our natural organic latex pillow. "
        + "Every layer is tested for comfort and support so you can sleep well through warm UAE nights. " * 12
        + "Order it in custom sizes with a 15-night trial and free delivery across the UAE.")
d = draft_content("https://easelivinguae.com/products/cool-gel-memory-foam-mattress",
                  title="Cool Gel Memory Foam Mattress | Cooling Comfort | Easeliving",
                  description="Shop a cool gel memory foam mattress in the UAE with pressure relief, custom sizes and a 15-night trial. Free delivery across Dubai and the UAE, order today.",
                  h1="Cool Gel Memory Foam Mattress for Hot Sleepers", body=body,
                  faqs=["Is a cool gel mattress good for hot sleepers?", "Can I order a custom size?", "How long is the trial?", "Does it need a box spring?"],
                  og_title="Cool Gel Memory Foam Mattress", og_description="Cooling gel memory foam, custom sizes, 15-night trial.")
ok = by(run_page_checks(d, {"page_type": "product", "primary_keyword": "cool gel memory foam mattress"}, BRAND))
fails = {k: v for k, v in ok.items() if v["result"] == "fail"}
assert not fails, fails
assert ok["BR-02"]["result"] == "pass" and ok["META-03"]["result"] == "pass", (ok["BR-02"], ok["META-03"], len(d["description"]))
assert ok["PDP-03"]["result"] in ("pass", "warn") and ok["PDP-06"]["result"] == "na"
print("good product draft: no failures, score", score(list(ok.values())))

# Collection draft: intro length, read-more length and question headings, banned word in title.
coll = draft_content("https://easelivinguae.com/collections/euro-top-mattress",
                     title="Euro Top Mattress Collection | Plush Support | Easeliving", h1="Euro Top Mattress in UAE",
                     description="x" * 155, intro="A euro top mattress has a plush layer sewn flush into the cover.",
                     read_more="## What is a euro top mattress?\n" + "word " * 120, faqs=["Q1?", "Q2?", "Q3?", "Q4?", "Q5?"])
cr = by(run_page_checks(coll, {"page_type": "collection", "primary_keyword": "euro top mattress"}, BRAND))
assert cr["META-02"]["result"] == "fail" and "collection" in cr["META-02"]["message"].lower()
assert cr["COL-04"]["result"] == "fail" and cr["COL-06"]["result"] == "fail" and cr["COL-05"]["result"] == "pass"
assert "question-style" not in cr["COL-06"]["message"]                         # has a question subheading
print("collection draft: intro, read-more and banned title word caught")

# Blog: duplicate internal links, source link without nofollow, skipped heading level, no author or Article schema.
BLOG = """<html><head><title>How to Choose the Right Mattress in UAE (2024)</title><meta name="description" content="d"></head><body>
<h1>How to choose the right mattress</h1><h2>What firmness do I need?</h2><h4>Side sleepers</h4><h2>How long does a mattress last?</h2>
<p>""" + "Mattress advice for sleepers in the UAE. " * 60 + """</p>
<a href="https://easelivinguae.com/collections/soft-mattress">soft</a> <a href="https://easelivinguae.com/collections/soft-mattress/">soft again</a>
<a href="https://www.thelancet.com/study">study</a></body></html>"""
b = by(run_page_checks(parse_html("https://easelivinguae.com/blogs/post/how-to-choose", BLOG), {"page_type": "blog", "primary_keyword": "choose the right mattress"}, BRAND))
assert b["BLOG-03"]["result"] == "fail" and b["BLOG-04"]["result"] == "fail" and b["BLOG-05"]["result"] == "warn"
assert b["BLOG-07"]["result"] == "fail" and "soft-mattress" in b["BLOG-07"]["message"]
assert b["BLOG-08"]["result"] == "fail" and "thelancet.com" in b["BLOG-08"]["message"]
assert b["BLOG-09"]["result"] == "fail" and b["BLOG-11"]["result"] == "fail" and b["BLOG-13"]["result"] == "fail"
assert b["META-07"]["result"] == "warn"
print("blog: links, sources, headings, author, schema and old year caught")

# Site-wide: duplicate titles and keyword cannibalisation.
site = run_site_checks([
    {"page_id": 1, "url": "/a", "primary_keyword": "memory foam mattress", "content": {"title": "Memory Foam Mattress | Easeliving", "description": "one"}},
    {"page_id": 2, "url": "/b", "primary_keyword": "Memory-Foam Mattress", "content": {"title": "Memory foam mattress | Easeliving", "description": "two"}},
    {"page_id": 3, "url": "/c", "primary_keyword": "latex mattress", "content": {"title": "Latex", "description": "three"}},
    {"page_id": 4, "url": "/d", "primary_keyword": None, "content": None}])
assert [r["check_id"] for r in site[1]] == ["META-08", "KW-03"] and site[1][0]["result"] == "fail" and site[3][0]["result"] == "pass"
assert site[4] == [] and "/b" in site[1][1]["message"]
print("site checks: duplicate titles and cannibalisation caught")
