"""Niche presets for "Sources AI trusts": the high-authority sites large language models lean on when they answer questions
in a category, as a starting list per niche (India-first, with global anchors). Every brand gets the generic four
(Wikipedia, Reddit, Quora, YouTube) plus its niche's list; the team can add or hide sources per brand in the screen.

These lists are a researched starting point, not a measurement. What Orbit measures is how often ChatGPT actually cites
each source in the brand's own prompts, and which brands those answers name (see analytics.source_presence)."""

GENERIC = [
    ("wikipedia.org", "Wikipedia", "encyclopedia", "Entity facts that models learn and cite; a brand or founder page anchors the knowledge graph"),
    ("reddit.com", "Reddit", "forum", "First-hand discussion; heavily cited by ChatGPT and Google AI Overviews for 'best' and 'vs' questions"),
    ("quora.com", "Quora", "q&a", "Question-led answers that map closely to how people prompt AI assistants"),
    ("youtube.com", "YouTube", "video", "Reviews and how-tos; transcripts are indexed and cited by AI answers"),
]

NICHES = {
    "edtech": ("Education and edtech", [
        ("collegedunia.com", "Collegedunia", "directory", "College profiles, fees, placements and reviews"),
        ("shiksha.com", "Shiksha", "directory", "Course and college comparisons, student Q&A"),
        ("careers360.com", "Careers360", "directory", "Rankings, exam and admission guides"),
        ("collegedekho.com", "CollegeDekho", "directory", "Admissions, cut-offs, college comparisons"),
        ("getmyuni.com", "GetMyUni", "directory", "College listings and reviews"),
        ("jagranjosh.com", "Jagran Josh", "media", "Exam, career and education news"),
    ]),
    "manufacturing": ("Manufacturing and B2B industrial", [
        ("indiamart.com", "IndiaMART", "marketplace", "B2B supplier listings and product catalogues"),
        ("tradeindia.com", "TradeIndia", "marketplace", "B2B directory of manufacturers and exporters"),
        ("exportersindia.com", "ExportersIndia", "marketplace", "Exporter and supplier profiles"),
        ("justdial.com", "Justdial", "directory", "Local business listings and ratings"),
        ("thomasnet.com", "Thomasnet", "directory", "Global industrial supplier directory"),
        ("alibaba.com", "Alibaba", "marketplace", "Global B2B sourcing"),
    ]),
    "jewellery": ("Jewellery and fashion", [
        ("amazon.in", "Amazon India", "marketplace", "Product listings, ratings and Q&A"),
        ("myntra.com", "Myntra", "marketplace", "Fashion catalogue and reviews"),
        ("nykaafashion.com", "Nykaa Fashion", "marketplace", "Curated fashion and jewellery"),
        ("flipkart.com", "Flipkart", "marketplace", "Product listings and reviews"),
        ("vogue.in", "Vogue India", "media", "Editorial trend and gift guides"),
        ("elle.in", "ELLE India", "media", "Editorial roundups and brand features"),
    ]),
    "beauty": ("Beauty and personal care", [
        ("nykaa.com", "Nykaa", "marketplace", "Beauty catalogue, ratings and reviews"),
        ("purplle.com", "Purplle", "marketplace", "Beauty listings and reviews"),
        ("amazon.in", "Amazon India", "marketplace", "Product listings, ratings and Q&A"),
        ("femina.in", "Femina", "media", "Beauty editorial and recommendations"),
        ("vogue.in", "Vogue India", "media", "Beauty editorial and product roundups"),
        ("makeupandbeauty.com", "MakeupAndBeauty", "review", "Long-form product reviews"),
    ]),
    "health": ("Health, wellness and pharma", [
        ("1mg.com", "Tata 1mg", "marketplace", "Product and medicine information"),
        ("pharmeasy.in", "PharmEasy", "marketplace", "Product listings and health content"),
        ("practo.com", "Practo", "directory", "Doctor and clinic listings, health articles"),
        ("healthline.com", "Healthline", "media", "Evidence-led health content, widely cited"),
        ("webmd.com", "WebMD", "media", "Medical reference content"),
        ("netmeds.com", "Netmeds", "marketplace", "Product listings and health content"),
    ]),
    "saas": ("SaaS and B2B software", [
        ("g2.com", "G2", "review", "Software reviews and category grids, cited for 'best X software'"),
        ("capterra.com", "Capterra", "review", "Software directory and reviews"),
        ("trustradius.com", "TrustRadius", "review", "In-depth B2B software reviews"),
        ("producthunt.com", "Product Hunt", "directory", "Launch pages and maker discussions"),
        ("getapp.com", "GetApp", "review", "Software comparisons"),
        ("softwaresuggest.com", "SoftwareSuggest", "review", "India-focused software reviews"),
    ]),
    "real_estate": ("Real estate", [
        ("99acres.com", "99acres", "marketplace", "Project and property listings"),
        ("magicbricks.com", "Magicbricks", "marketplace", "Listings, locality and price trends"),
        ("housing.com", "Housing.com", "marketplace", "Listings and locality guides"),
        ("nobroker.in", "NoBroker", "marketplace", "Owner listings and guides"),
        ("squareyards.com", "Square Yards", "marketplace", "Projects and market data"),
        ("makaan.com", "Makaan", "marketplace", "Listings and project reviews"),
    ]),
    "travel": ("Travel and hospitality", [
        ("tripadvisor.in", "Tripadvisor", "review", "Reviews and rankings for stays and experiences"),
        ("makemytrip.com", "MakeMyTrip", "marketplace", "Bookings and reviews"),
        ("booking.com", "Booking.com", "marketplace", "Stays and guest reviews"),
        ("goibibo.com", "Goibibo", "marketplace", "Bookings and reviews"),
        ("holidify.com", "Holidify", "media", "Destination guides"),
        ("lonelyplanet.com", "Lonely Planet", "media", "Destination guides, widely cited"),
    ]),
    "finance": ("Finance and fintech", [
        ("moneycontrol.com", "Moneycontrol", "media", "Markets and personal finance news"),
        ("economictimes.indiatimes.com", "Economic Times", "media", "Business and finance news"),
        ("paisabazaar.com", "Paisabazaar", "marketplace", "Loan and card comparisons"),
        ("bankbazaar.com", "BankBazaar", "marketplace", "Financial product comparisons"),
        ("groww.in", "Groww", "media", "Investing explainers"),
        ("investopedia.com", "Investopedia", "media", "Finance definitions, widely cited"),
    ]),
    "food": ("Food, FMCG and quick commerce", [
        ("blinkit.com", "Blinkit", "marketplace", "Quick commerce listings"),
        ("swiggy.com", "Swiggy Instamart", "marketplace", "Quick commerce listings"),
        ("zeptonow.com", "Zepto", "marketplace", "Quick commerce listings"),
        ("bigbasket.com", "BigBasket", "marketplace", "Grocery listings and ratings"),
        ("amazon.in", "Amazon India", "marketplace", "Product listings, ratings and Q&A"),
        ("zomato.com", "Zomato", "review", "Restaurant listings and reviews"),
    ]),
    "automotive": ("Automotive", [
        ("cardekho.com", "CarDekho", "review", "Specs, prices, reviews and comparisons"),
        ("carwale.com", "CarWale", "review", "Specs, prices and expert reviews"),
        ("autocarindia.com", "Autocar India", "media", "Road tests and news"),
        ("zigwheels.com", "ZigWheels", "review", "Car and bike reviews"),
        ("bikedekho.com", "BikeDekho", "review", "Two-wheeler specs and reviews"),
        ("team-bhp.com", "Team-BHP", "forum", "Owner reviews and long-term reports"),
    ]),
    "home": ("Home, furniture and appliances", [
        ("amazon.in", "Amazon India", "marketplace", "Product listings, ratings and Q&A"),
        ("flipkart.com", "Flipkart", "marketplace", "Product listings and reviews"),
        ("pepperfry.com", "Pepperfry", "marketplace", "Furniture catalogue and reviews"),
        ("urbanladder.com", "Urban Ladder", "marketplace", "Furniture catalogue"),
        ("houzz.in", "Houzz", "directory", "Design ideas and professionals"),
        ("smartprix.com", "Smartprix", "review", "Appliance and gadget comparisons"),
    ]),
}

KIND_LABEL = {"encyclopedia": "Encyclopedia", "forum": "Forum", "q&a": "Q&A", "video": "Video", "directory": "Directory",
              "marketplace": "Marketplace", "review": "Reviews", "media": "Media", "custom": "Added"}

def choices():
    return [(k, v[0]) for k, v in NICHES.items()]

def label(niche):
    return NICHES[niche][0] if niche in NICHES else None

def preset(niche):
    """Generic four first, then the niche's list, de-duplicated by domain."""
    seen, out = set(), []
    for d, n, k, why in GENERIC + (NICHES[niche][1] if niche in NICHES else []):
        if d not in seen:
            seen.add(d); out.append({"domain": d, "name": n, "kind": k, "why": why, "generic": (d, n, k, why) in GENERIC})
    return out

def guess(text):
    """A first guess at a brand's niche from its name, domain or seed notes; staff confirm it on the screen."""
    t = (text or "").lower()
    rules = [("edtech", ("college", "school", "university", "edu", "academy", "learn", "course", "institute")),
             ("jewellery", ("jewel", "gold", "silver", "diamond", "gadgil", "earring", "ring")),
             ("beauty", ("beauty", "cosmetic", "skin", "hair", "makeup")),
             ("health", ("health", "pharma", "clinic", "wellness", "care")),
             ("saas", ("saas", "software", "app", "cloud", ".io")),
             ("real_estate", ("realty", "estate", "homes", "infra", "builders", "properties")),
             ("travel", ("travel", "tour", "hotel", "resort", "holiday")),
             ("finance", ("finance", "capital", "invest", "bank", "loan", "insurance")),
             ("food", ("food", "foods", "tea", "snack", "dairy", "milk", "kitchen")),
             ("automotive", ("auto", "motor", "car", "bike")),
             ("manufacturing", ("industr", "manufactur", "engineering", "steel", "machin", "pipes"))]
    for k, words in rules:
        if any(w in t for w in words): return k
    return None
