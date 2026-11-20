#!/usr/bin/env python3
"""Blog drafts written by Claude (Anthropic API, web search on), for the "Generate drafts" button only.

Same brief, brand rules and checks as the ChatGPT writer (blog_writer.py); only the model call differs. Claude researches
with web search, then hands the finished post back through a `submit_post` tool whose input is the draft schema, so the
post arrives as structured fields rather than text to be parsed.

Spend, strict:
  - every billed reply goes to api_spend at once, marked purpose='blogs' (claude_answers.record);
  - a draft only starts if blog spend so far + DRAFT_WORST_USD stays within the blog cap (CLAUDE_BLOG_MAX_USD, default and
    ceiling $2.50), and all Claude spend + DRAFT_WORST_USD stays within Claude's overall $10 cap;
  - each reply inside a draft is checked again before it is sent.
Nothing here runs on a schedule or when a page opens.

Env: ANTHROPIC_API_KEY, CLAUDE_BLOG_MODEL (default claude-sonnet-5-5), CLAUDE_BLOG_MAX_USD (≤ 2.5), CLAUDE_BLOG_SEARCHES (default 5)
"""
import os
import claude_answers as CA

BLOG_HARD_CAP_USD = 2.5                 # the user's limit for blog writing on Claude
DRAFT_WORST_USD = 0.50                  # one draft at most: ~60K input incl. search results, 8K output, 5 searches, a follow-up turn
MODEL = os.environ.get("CLAUDE_BLOG_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = 9000

def blog_cap():
    try: v = float(os.environ.get("CLAUDE_BLOG_MAX_USD", BLOG_HARD_CAP_USD))
    except ValueError: v = BLOG_HARD_CAP_USD
    return max(0.0, min(v, BLOG_HARD_CAP_USD))

def blog_spend(conn):
    return float(conn.execute("SELECT coalesce(sum(usd), 0) FROM api_spend WHERE provider='anthropic' AND purpose='blogs'").fetchone()[0] or 0)

def searches():
    try: return max(1, min(8, int(os.environ.get("CLAUDE_BLOG_SEARCHES", "5"))))
    except ValueError: return 5

def available(): return bool(os.environ.get("ANTHROPIC_API_KEY"))

def room(conn, need=DRAFT_WORST_USD):
    """None if one more draft fits within the blog cap and Claude's overall cap, else the reason it does not."""
    spent, cap = blog_spend(conn), blog_cap()
    if spent + need > cap:
        return f"the Claude blog limit is reached (${spent:.2f} of ${cap:.2f} used)"
    total, tcap = CA.total_spend(conn), CA.total_cap()
    if total + need > tcap:
        return f"the overall Claude limit is reached (${total:.2f} of ${tcap:.0f} used)"
    return None

def status(conn):
    spent, cap = blog_spend(conn), blog_cap()
    return {"key": available(), "spent": spent, "cap": cap, "room": room(conn) is None, "model": MODEL,
            "left_drafts": max(0, int((cap - spent) // DRAFT_WORST_USD))}

class ClaudeWriter:
    """Passed to blog_writer as its `key`: blog_writer.ask hands the call to draft()."""
    kind = "claude"

    def __init__(self, conn, client_id, api_key=None):
        self.conn, self.client_id, self.api_key = conn, client_id, api_key or os.environ.get("ANTHROPIC_API_KEY")

    def label(self, _model=None): return MODEL

    def _send(self, session, body):
        why = room(self.conn, 0.12)                       # each further reply inside a draft fits too
        if why: raise CA.ClaudeError(why, fatal=True)
        return CA.call(session, self.api_key, body, timeout=200)

    def draft(self, session, system, user, schema):
        why = room(self.conn)
        if why: raise CA.ClaudeError(why, fatal=True)
        if not self.api_key: raise CA.ClaudeError("No Claude key on the server (ANTHROPIC_API_KEY).", fatal=True)
        CA._ctx.clear(); CA._ctx.update(conn=self.conn, client_id=self.client_id, spent=0.0, purpose="blogs")
        try:
            tools = [{"type": CA.SEARCH_TOOL, "name": "web_search", "max_uses": searches(), "allowed_callers": ["direct"]},
                     {"name": "submit_post", "description": "Hand back the finished blog post. Call it exactly once, at the end.",
                      "input_schema": schema}]
            messages = [{"role": "user", "content": user + "\n\nResearch with web search where facts are needed, then call "
                                                          "submit_post once with the finished post. Do not write the post as plain text."}]
            body = {"model": MODEL, "max_tokens": MAX_TOKENS, "system": system, "tools": tools, "messages": messages}
            for turn in range(4):
                j = self._send(session, body)
                for b in j.get("content") or []:
                    if b.get("type") == "tool_use" and b.get("name") == "submit_post" and isinstance(b.get("input"), dict):
                        return b["input"]
                messages = messages + [{"role": "assistant", "content": j.get("content") or []}]
                if j.get("stop_reason") == "pause_turn":
                    body = dict(body, messages=messages); continue          # a long search turn: let it carry on
                # it answered in text or stopped early: ask for the tool, and only the tool
                messages = messages + [{"role": "user", "content": "Now call submit_post with the finished post."}]
                body = dict(body, messages=messages, tool_choice={"type": "tool", "name": "submit_post"})
            raise RuntimeError("Claude did not hand back a draft.")
        finally:
            CA._ctx.clear()
