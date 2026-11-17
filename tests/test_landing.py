"""Orbit page: renders with both calls to action and the sales form, and the client-logo route fetches each logo once from
the brand's own site (Tupperware's animated GIF reduced to its first frame), refuses non-images, and backs off after a failure."""
import os, sys, io, time
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "webapp")]
os.environ.setdefault("FLASK_SECRET", "t"); os.environ.setdefault("DATABASE_URL", "postgresql://x@/none")
import requests
from PIL import Image
import app as A

f0 = Image.new("RGB", (60, 20), (0, 128, 0)); f0.paste((255, 255, 255), (50, 0, 60, 20)); f1 = Image.new("RGB", (60, 20), (255, 0, 0)); b = io.BytesIO()
f0.save(b, "GIF", save_all=True, append_images=[f1]); GIF = b.getvalue()
class R:
    def __init__(s, c, t): s.content, s.headers = c, {"Content-Type": t}
    def raise_for_status(s): pass
calls, down = [], set()
def fake(url, **k):
    calls.append(url)
    if url in down: raise requests.ConnectionError("down")
    if url.endswith(".gif"): return R(GIF, "image/gif")
    if url.endswith(".svg"): return R(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")
    return R(b"<html>", "text/html")
requests.get = fake
c = A.app.test_client()

html = c.get("/").get_data(as_text=True)
for needle in ("Start free trial", "Talk to sales", 'name="company_url"', 'name="csrf"', "/brand-logo/tupperware", "/brand-logo/cradlewise", "/brand-logo/pack8"):
    assert needle in html, needle
r = c.get("/brand-logo/tupperware")
assert r.status_code == 200 and r.headers["Content-Type"] == "image/png" and "max-age" in r.headers["Cache-Control"]
im = Image.open(io.BytesIO(r.data)).convert("RGBA")
assert im.getpixel((5, 5)) == (0, 128, 0, 255) and im.getpixel((55, 5))[3] == 0     # first frame, white made transparent
r = c.get("/brand-logo/cradlewise"); assert r.status_code == 200 and "default-src 'none'" in r.headers["Content-Security-Policy"]
assert c.get("/brand-logo/pack8").status_code == 404                                    # an HTML page is not a logo
n = len(calls); c.get("/brand-logo/tupperware"); assert len(calls) == n                 # cached
assert c.get("/brand-logo/unknown").status_code == 404 and len(calls) == n              # only the fixed list
A._LOGO_CACHE.clear(); down.add(A.BRAND_LOGOS["cradlewise"][1]); n = len(calls)
assert c.get("/brand-logo/cradlewise").status_code == 404 and c.get("/brand-logo/cradlewise").status_code == 404
assert len(calls) == n + 1                                                              # one try per hour after a failure
A._LOGO_CACHE["cradlewise"] = (None, None, time.time() - 4000); down.clear()
assert c.get("/brand-logo/cradlewise").status_code == 200
print("landing: all checks passed")
