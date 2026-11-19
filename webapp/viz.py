"""Small SVG charts drawn on the server for the Orbit dashboard: ring, half gauge, smooth multi-line, donut and grouped
bars. No JavaScript and no chart library; text stays HTML or SVG text at a fixed size. Every function returns Markup."""
from markupsafe import Markup, escape

# Entity colours: the brand is always Hovers Red; competitors take the next colours in order.
BRAND = "#B20F00"
PALETTE = ["#2F5BEA", "#8B5CF6", "#F59E0B", "#10B981", "#0EA5E9", "#EC4899", "#64748B"]
GRID = "#EEF0F4"
MUTED = "#8A8F98"

def color(i):
    return BRAND if i == 0 else PALETTE[(i - 1) % len(PALETTE)]

def ring(value, size=132, stroke=14, colour=BRAND, label=None):
    """0-100 ring with the number in the middle."""
    v = max(0.0, min(100.0, float(value or 0)))
    r = (size - stroke) / 2
    circ = 2 * 3.14159265 * r
    txt = "–" if value is None else f"{v:.0f}%"
    return Markup(
        f'<svg class="viz-ring" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="{escape(label or txt)}">'
        f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{GRID}" stroke-width="{stroke}"/>'
        f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{colour}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{circ * v / 100:.1f} {circ:.1f}" transform="rotate(-90 {size/2} {size/2})"/>'
        f'<text x="50%" y="50%" text-anchor="middle" dominant-baseline="central" font-size="{size*0.2:.0f}" font-weight="700" fill="#0F0F0F">{txt}</text></svg>')

def gauge(value, size=64):
    """Half gauge for sentiment: green share over red. value 0-1 (share positive) or None."""
    w, h, s = size, size * 0.6, size * 0.13
    r = (w - s) / 2
    import math
    def arc(a0, a1, col):
        x0, y0 = w / 2 + r * math.cos(math.pi * (1 - a0)), h - 2 - r * math.sin(math.pi * (1 - a0))
        x1, y1 = w / 2 + r * math.cos(math.pi * (1 - a1)), h - 2 - r * math.sin(math.pi * (1 - a1))
        return f'<path d="M{x0:.1f},{y0:.1f} A{r:.1f},{r:.1f} 0 0 1 {x1:.1f},{y1:.1f}" fill="none" stroke="{col}" stroke-width="{s:.1f}" stroke-linecap="butt"/>'
    if value is None:
        return Markup(f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">{arc(0, 1, GRID)}</svg>')
    p = max(0.0, min(1.0, float(value)))
    parts = (arc(0, p, "#16A34A") if p > 0 else "") + (arc(p, 1, "#DC2626") if p < 1 else "")
    return Markup(f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{p*100:.0f}% positive">{parts}'
                  f'<text x="50%" y="{h-4:.0f}" text-anchor="middle" font-size="{size*0.2:.0f}" font-weight="700" fill="#0F0F0F">{p*100:.0f}%</text></svg>')

def _smooth(pts):
    """Monotone cubic (Fritsch-Carlson) through the points: soft curves that never overshoot, so a chart never shows a
    peak or dip the data does not have."""
    n = len(pts)
    if n < 3: return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    d = [(ys[i + 1] - ys[i]) / ((xs[i + 1] - xs[i]) or 1) for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0: m[i] = m[i + 1] = 0.0; continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        h = a * a + b * b
        if h > 9:
            t = 3 / h ** 0.5; m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    out = [f"M{xs[0]:.1f},{ys[0]:.1f}"]
    for i in range(n - 1):
        dx = (xs[i + 1] - xs[i]) / 3
        out.append(f"C{xs[i] + dx:.1f},{ys[i] + m[i] * dx:.1f} {xs[i + 1] - dx:.1f},{ys[i + 1] - m[i + 1] * dx:.1f} {xs[i + 1]:.1f},{ys[i + 1]:.1f}")
    return " ".join(out)

def lines(labels, series, height=230, ymax=None, area_first=True, fmt="{:,.0f}"):
    """labels: x labels; series: [{name, colour, values}] (None = gap skipped). Smooth lines, dots, light grid, legend outside."""
    if len(labels) < 2 or not series:
        return Markup('<p class="empty">Not enough history yet for a trend. It fills in as the nightly runs add points.</p>')
    W, H, L, B, T = 1000, height, 44, 26, 10
    vals = [v for s in series for v in s["values"] if v is not None]
    top = ymax or (max(vals) if vals else 1) * 1.15 or 1
    x = lambda i: L + i * (W - L - 8) / (len(labels) - 1)
    y = lambda v: T + (H - T - B) * (1 - float(v) / top)
    out = [f'<svg class="viz-lines" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="Trend" style="height:{H}px">']
    for k in range(5):
        yy = T + (H - T - B) * k / 4
        out.append(f'<line x1="{L}" x2="{W}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{GRID}" vector-effect="non-scaling-stroke"/>')
    for n, s in enumerate(series):
        pts = [(x(i), y(v)) for i, v in enumerate(s["values"]) if v is not None]
        if len(pts) < 2: continue
        path = _smooth(pts)
        if area_first and n == 0:
            gid = f"g{abs(hash(s['name'])) % 99999}"
            out.append(f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{s["colour"]}" stop-opacity=".16"/>'
                       f'<stop offset="1" stop-color="{s["colour"]}" stop-opacity="0"/></linearGradient></defs>'
                       f'<path d="{path} L{pts[-1][0]:.1f},{H-B} L{pts[0][0]:.1f},{H-B} Z" fill="url(#{gid})"/>')
        out.append(f'<path d="{path}" fill="none" stroke="{s["colour"]}" stroke-width="2.4" vector-effect="non-scaling-stroke" stroke-linecap="round"/>')
    out.append("</svg>")
    # dots and labels in HTML so they stay round and readable at any width
    dots = []
    for s in (series if len(labels) <= 40 else []):
        for i, v in enumerate(s["values"]):
            if v is None: continue
            dots.append(f'<i style="left:{x(i)/W*100:.2f}%;top:{y(v):.1f}px;background:{s["colour"]}" title="{escape(s["name"])} · {escape(labels[i])}: {fmt.format(v)}"></i>')
    ylab = "".join(f'<span style="top:{T + (H - T - B) * k / 4:.1f}px">{fmt.format(top * (1 - k / 4))}</span>' for k in range(5))
    step = max(1, len(labels) // 7)
    xlab = "".join(f'<span style="left:{x(i)/W*100:.2f}%">{escape(labels[i])}</span>' for i in range(0, len(labels), step))
    return Markup(f'<div class="viz-lc" style="height:{H+18}px">{"".join(out)}<div class="viz-dots">{"".join(dots)}</div>'
                  f'<div class="viz-y">{ylab}</div><div class="viz-x" style="top:{H-B+8}px">{xlab}</div></div>')

def donut(parts, size=170, stroke=26, centre="", sub=""):
    """parts: [{name, value, colour}] -> donut with a total in the middle."""
    total = sum(float(p["value"] or 0) for p in parts)
    r = (size - stroke) / 2
    circ = 2 * 3.14159265 * r
    out = [f'<svg class="viz-donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="{escape(sub)}">',
           f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{GRID}" stroke-width="{stroke}"/>']
    off = 0.0
    for p in parts:
        v = float(p["value"] or 0)
        if not total or v <= 0: continue
        seg = circ * v / total
        out.append(f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{p["colour"]}" stroke-width="{stroke}" '
                   f'stroke-dasharray="{max(0, seg - 3):.1f} {circ:.1f}" stroke-dashoffset="{-off:.1f}" transform="rotate(-90 {size/2} {size/2})"/>')
        off += seg
    out.append(f'<text x="50%" y="46%" text-anchor="middle" dominant-baseline="central" font-size="{size*0.15:.0f}" font-weight="700" fill="#0F0F0F">{escape(centre)}</text>'
               f'<text x="50%" y="62%" text-anchor="middle" font-size="{size*0.06:.0f}" fill="{MUTED}" letter-spacing=".06em">{escape(sub.upper())}</text></svg>')
    return Markup("".join(out))

def columns(groups, series, height=210, ymax=None):
    """Grouped vertical bars. groups: x labels; series: [{name, colour, values}] (one value per group)."""
    vals = [v for s in series for v in s["values"] if v is not None]
    if not groups or not vals:
        return Markup('<p class="empty">No data yet.</p>')
    top = ymax or max(vals) * 1.15 or 1
    W, H, B, L = 1000, height, 24, 40
    gw = (W - L) / len(groups)
    bw = min(46, gw * 0.7 / max(len(series), 1))
    out = [f'<svg class="viz-cols" viewBox="0 0 {W} {H}" preserveAspectRatio="none" style="height:{H}px" role="img" aria-label="Comparison">']
    for k in range(5):
        yy = 6 + (H - B - 6) * k / 4
        out.append(f'<line x1="{L}" x2="{W}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{GRID}" vector-effect="non-scaling-stroke"/>')
    labels = []
    for g, name in enumerate(groups):
        start = L + g * gw + (gw - bw * len(series)) / 2
        for k, s in enumerate(series):
            v = s["values"][g]
            if v is None: continue
            h = (H - B - 6) * float(v) / top
            out.append(f'<rect x="{start + k*bw + 2:.1f}" y="{H-B-h:.1f}" width="{bw-4:.1f}" height="{h:.1f}" rx="5" fill="{s["colour"]}"><title>{escape(s["name"])} · {escape(name)}: {v:,.0f}</title></rect>')
            if len(series) == 1:
                labels.append(f'<b style="left:{(start + bw/2)/W*100:.2f}%;top:{H-B-h-20:.1f}px">{v:,.0f}</b>')
        labels.append(f'<span style="left:{(L + g*gw + gw/2)/W*100:.2f}%;top:{H-B+6}px">{escape(name)}</span>')
    out.append("</svg>")
    ylab = "".join(f'<em style="top:{6 + (H - B - 6) * k / 4:.1f}px">{top * (1 - k / 4):,.0f}</em>' for k in range(5))
    return Markup(f'<div class="viz-cc" style="height:{H+16}px">{"".join(out)}<div class="viz-cl">{"".join(labels)}{ylab}</div></div>')
