#!/usr/bin/env python3
"""Claude answers: asks each approved prompt in Claude through the Anthropic API with web search on, then records exactly
what the ChatGPT runner records (ai_visibility.py): the brands named and their order, sentiment and whether recommended,
the sites cited and the sites only read, and the Hovers Visibility Score per brand (engine 'claude').

These are Claude's own answers to a shopper's question, asked fresh with live web search. They are not a copy of what any
one person sees on claude.ai, which depends on that person's settings and history.

Runs only when the team clicks "Check Claude answers now" (never on a schedule or on page open), at most once a day per brand.

Spend limit, strict: every billed reply is written to api_spend the moment it returns (tokens + searches at Anthropic's
published rates). No request starts unless all-time Claude spend plus the worst case for one more prompt stays within
CLAUDE_MAX_USD, which can be lowered but never set above HARD_CAP_USD ($10). One click spends at most CLAUDE_RUN_USD ($2).

Env: ANTHROPIC_API_KEY (Vercel name 'Claude' also read; Sensitive, Production only), CLAUDE_MODEL (default claude-sonnet-5-5),
     CLAUDE_JUDGE_MODEL (default claude-haiku-5-5), CLAUDE_MAX_USD (default and ceiling 10), CLAUDE_RUN_USD (default 2),
     CLAUDE_MAX_SEARCHES (default 3)
Usage: python claude_answers.py --client-id UUID [--date 2026-11-20]
"""
import argparse, datetime as dt, json, os, sys, time
import requests
from common import connect
import ai_visibility as AV

API = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
JUDGE_MODEL = os.environ.get("CLAUDE_JUDGE_MODEL", "claude-haiku-5-5")
SEARCH_TOOL = "web_search_20260318"
SEARCH_USD = 0.01                                   # $10 per 1,000 searches
# USD per million tokens: input, output, cache read, cache write (5 minutes). Anthropic's published prices, Nov 2026.
PRICES = {"claude-fable-5-2": (8, 40, 0.20, 10), "claude-opus-5-5": (4, 20, 0.20, 5), "claude-sonnet-5-5": (2, 10, 0.10, 2.5),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125)}
HAIKU_LONG = (0.50, 2.50, 0.05, 0.625)              # Haiku 5.5 when a prompt is over 100K input tokens

HARD_CAP_USD = 10.0                                 # the user's limit: Claude never spends more than this in total
PROMPT_WORST_USD = 0.15                             # one prompt at most: 2,048 output + ~30K input tokens, 3 searches, the judge

def total_cap():
    try: v = float(os.environ.get("CLAUDE_MAX_USD", HARD_CAP_USD))
    except ValueError: v = HARD_CAP_USD
    return max(0.0, min(v, HARD_CAP_USD))

def run_cap():
    try: return max(0.0, float(os.environ.get("CLAUDE_RUN_USD", "2")))
    except ValueError: return 2.0

monthly_cap = total_cap                             # older name
def max_searches(): return max(1, int(os.environ.get("CLAUDE_MAX_SEARCHES", "3")))

class ClaudeError(RuntimeError):
    def __init__(self, message, fatal=False):
        super().__init__(message); self.fatal = fatal          # fatal: no point asking the next prompt (bad key, no credit)

_stop = {}                                                     # the run's fatal error, so the remaining prompts are not asked

# ------------------------------------------------------------------ cost
def price_for(model, usage):
    base = next((v for k, v in PRICES.items() if (model or "").startswith(k)), PRICES["claude-sonnet-5-5"])
    if (model or "").startswith("claude-haiku") and (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                                                    + usage.get("cache_creation_input_tokens", 0)) > 100_000:
        base = HAIKU_LONG
    return base

def cost(model, usage):
    """USD for one response's usage block, at Anthropic's published rates."""
    if not usage: return 0.0
    i, o, cr, cw = price_for(model, usage)
    searches = ((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
    return round((usage.get("input_tokens", 0) * i + usage.get("output_tokens", 0) * o + usage.get("cache_read_input_tokens", 0) * cr
                  + usage.get("cache_creation_input_tokens", 0) * cw) / 1e6 + searches * SEARCH_USD, 6)

def total_spend(conn):
    """All Claude spend ever, across all brands (one Anthropic account), from the ledger of billed replies."""
    return float(conn.execute("SELECT coalesce(sum(usd), 0) FROM api_spend WHERE provider='anthropic'").fetchone()[0] or 0)

month_spend = total_spend                           # older name

_ctx = {}                                            # the run in progress: conn, client_id, what this run spent

def record(model, usage):
    """Write one billed reply to the ledger at once; nothing is billed without being counted."""
    usd = cost(model, usage)
    _ctx["spent"] = _ctx.get("spent", 0.0) + usd
    if _ctx.get("conn"):
        _ctx["conn"].execute("INSERT INTO api_spend (provider, client_id, model, usd, searches) VALUES ('anthropic',%s,%s,%s,%s)",
                             [_ctx["client_id"], model, usd, ((usage or {}).get("server_tool_use") or {}).get("web_search_requests") or 0])
    return usd

def room(conn, need=PROMPT_WORST_USD):
    """None if one more request of up to `need` USD fits within both limits, else the reason it does not."""
    spent, cap = total_spend(conn), total_cap()
    if spent + need > cap:
        return f"the Claude spend limit is reached (${spent:.2f} of ${cap:.0f} used)"
    if _ctx.get("spent", 0.0) + need > run_cap():
        return f"this run reached its ${run_cap():g} limit; the rest of the prompts run next time"
    return None

# ------------------------------------------------------------------ the API
def headers(key):
    return {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"}

def call(session, key, body, timeout=110):
    """One Messages request with polite retries on rate limits and overload; plain-language errors otherwise."""
    for attempt in range(4):
        try:
            r = session.post(API, headers=headers(key), json=body, timeout=timeout)
        except requests.RequestException as e:
            if attempt < 2: time.sleep(3 * (attempt + 1)); continue
            raise ClaudeError(f"Could not reach the Claude API ({type(e).__name__}).")
        if r.status_code == 200:
            j = r.json(); record(j.get("model") or body.get("model"), j.get("usage") or {}); return j
        try: err = (r.json().get("error") or {})
        except ValueError: err = {}
        kind, msg = err.get("type") or "", err.get("message") or (r.text or "")[:200]
        if r.status_code in (429, 500, 502, 503, 504, 529) and attempt < 3:
            wait = float(r.headers.get("retry-after") or 0) if hasattr(r, "headers") and r.headers else 0
            time.sleep(min(30, wait or 4 * (attempt + 1))); continue
        if r.status_code == 401 or kind == "authentication_error":
            raise fatal("The Claude API key was refused. Check ANTHROPIC_API_KEY in Vercel.")
        if r.status_code == 403 or kind == "permission_error":
            raise fatal(f"The Claude API key is not allowed to do this: {msg}")
        if "credit balance" in msg.lower() or "billing" in kind:
            raise fatal("The Anthropic account has run out of credit. Add credit in the Claude Console.")
        raise ClaudeError(f"Claude API {r.status_code}: {msg}")
    raise ClaudeError("The Claude API stayed busy; try again later.")

def fatal(message):
    _stop["why"] = message
    return ClaudeError(message, fatal=True)

def ask(session, key, prompt, country="IN"):
    """The shopper's question, as is, with web search on. Long turns that pause are continued (up to three times).
    -> one merged response: {model, content: [...all blocks...], usage: {...summed...}, stop_reason}"""
    tool = {"type": SEARCH_TOOL, "name": "web_search", "max_uses": max_searches(), "allowed_callers": ["direct"]}
    if country and len(country) == 2: tool["user_location"] = {"type": "approximate", "country": country.upper()}
    messages = [{"role": "user", "content": prompt}]
    merged = {"content": [], "usage": {}, "model": MODEL}
    for _ in range(4):
        if _ctx.get("conn"):
            why = room(_ctx["conn"], PROMPT_WORST_USD / 2 if merged["content"] else PROMPT_WORST_USD)
            if why: raise fatal(why)
        j = call(session, key, {"model": MODEL, "max_tokens": 2048, "messages": messages, "tools": [tool]})
        merged["model"] = j.get("model") or MODEL; merged["id"] = j.get("id"); merged["stop_reason"] = j.get("stop_reason")
        merged["content"] += j.get("content") or []
        add_usage(merged["usage"], j.get("usage") or {})
        if j.get("stop_reason") != "pause_turn": break
        messages = messages + [{"role": "assistant", "content": j.get("content") or []}]
    return merged

def add_usage(total, u):
    for k, v in u.items():
        if isinstance(v, (int, float)): total[k] = total.get(k, 0) + v
        elif isinstance(v, dict):
            sub = total.setdefault(k, {})
            for k2, v2 in v.items():
                if isinstance(v2, (int, float)): sub[k2] = sub.get(k2, 0) + v2

def parse(j):
    """-> answer text, cited [(url, title)], consulted [url]. The answer is the text after the last search (the lead-in
    "I'll search for..." is not part of it)."""
    blocks = j.get("content") or []
    last = max([i for i, b in enumerate(blocks) if b.get("type") == "web_search_tool_result"], default=-1)
    text, cited, consulted = [], [], []
    for i, b in enumerate(blocks):
        if b.get("type") == "text" and i > last:
            text.append(b.get("text") or "")
            for c in b.get("citations") or []:
                if c.get("url"): cited.append((c["url"], c.get("title")))
        elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            consulted += [r["url"] for r in b["content"] if r.get("url")]
    seen, uniq = set(), []
    for u, t in cited:
        k = u.split("#")[0].rstrip("/")
        if k not in seen: seen.add(k); uniq.append((u, t))
    return "".join(text).strip(), uniq, [u for u in dict.fromkeys(consulted) if u.split("#")[0].rstrip("/") not in seen]

def judge(session, key, answer):
    """Every brand, store or marketplace the answer names, with sentiment and whether recommended (a cheap Haiku call
    that must answer through a tool, so the result is always well-formed). -> (brands, usage)"""
    j = call(session, key, {"model": JUDGE_MODEL, "max_tokens": 1024,
                            "system": "List every brand, store or marketplace named in the answer, in order of first appearance. "
                                      "Sentiment is how the answer presents that brand. Do not add brands that are not named.",
                            "messages": [{"role": "user", "content": answer}],
                            "tools": [{"name": "record_brands", "description": "Record the brands named in the answer.",
                                       "input_schema": AV.SENTIMENT_SCHEMA}],
                            "tool_choice": {"type": "tool", "name": "record_brands"}}, timeout=50)
    use = next((b for b in j.get("content") or [] if b.get("type") == "tool_use" and b.get("name") == "record_brands"), None)
    brands = (use or {}).get("input", {}).get("brands") or []
    clean = [{"name": str(b.get("name", ""))[:200], "sentiment": b.get("sentiment") if b.get("sentiment") in AV.SENT else "neutral",
              "recommended": bool(b.get("recommended"))} for b in brands if b.get("name")]
    return clean, {"model": j.get("model") or JUDGE_MODEL, "usage": j.get("usage") or {}}

class ClaudeEngine:
    key, default_model, prompt_seconds = "claude", MODEL, 200
    ask = staticmethod(ask)
    parse = staticmethod(parse)
    judge = staticmethod(judge)

    @staticmethod
    def meta(j, key, judge_meta=None):
        searches = ((j.get("usage") or {}).get("server_tool_use") or {}).get("web_search_requests") or 0
        total = cost(j.get("model"), j.get("usage")) + (cost(judge_meta["model"], judge_meta["usage"]) if judge_meta else 0)
        return {"usage": j.get("usage"), "id": j.get("id"), "via": "api", "searches": searches, "stop_reason": j.get("stop_reason"),
                "judge_usage": (judge_meta or {}).get("usage"), "cost_usd": round(total, 6)}

    @staticmethod
    def guard(conn, client_id):
        if _stop.get("why"): return _stop["why"]
        return room(conn)

def sync_client(conn, client_id, key, samples=1, cycle=None, session=None, deadline=None):
    _stop.clear(); _ctx.clear(); _ctx.update(conn=conn, client_id=client_id, spent=0.0)
    try:
        return AV.sync_client(conn, client_id, key, samples, cycle, session, True, deadline, eng=ClaudeEngine)
    finally:
        _ctx.clear()

def status(conn):
    """For screens: key set, this month's spend and limit, last run per brand is read separately."""
    spent = total_spend(conn)
    return {"key": bool(os.environ.get("ANTHROPIC_API_KEY")), "spent": spent, "cap": total_cap(), "model": MODEL,
            "room": spent + PROMPT_WORST_USD <= total_cap(), "run_cap": run_cap()}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id", required=True); ap.add_argument("--date")
    a = ap.parse_args()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key: print("ANTHROPIC_API_KEY is not set"); return 1
    with connect() as conn:
        tally, scores = sync_client(conn, a.client_id, key, cycle=dt.date.fromisoformat(a.date) if a.date else None)
        print("runs:", tally, f"Claude spend so far: ${total_spend(conn):.2f} of ${total_cap():.0f}")
        for region, s in scores.items():
            for brand, v in s.items(): print(f"{region} {brand}: {v['score']} ({AV.band(v['score'])})")
        return 1 if tally.get("error") else 0

if __name__ == "__main__":
    sys.exit(main())
