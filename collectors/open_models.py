#!/usr/bin/env python3
"""Testing stand-ins for Gemini and Copilot: Google's open model Gemma and Microsoft's open model Phi, hosted free by NVIDIA
(build.nvidia.com). Each is asked the same approved prompts and read exactly like ChatGPT (ai_visibility.py), and its answers
are stored under the real engine's key ('gemini', 'bing_copilot') so the screens keep one button per engine. When the real
Gemini and Copilot connections are added they write to the same keys and these stop.

What these answers are: what the open models say from their own training, with no live web search, so they cite no sources
(the score then leaves source authority out). They are not Gemini's or Copilot's own answers; the team sees that on screen.

Speed: the prompts are asked in parallel (5 at a time, NVIDIA allows ~40 requests a minute per key) and stored together,
so one worker run covers most of a brand's prompts; the rest carry on in the next run.

API: OpenAI-compatible, POST https://integrate.api.nvidia.com/v1/chat/completions, Authorization: Bearer nvapi-...
Models are picked from GET /v1/models: the newest instruction-tuned model of each family, unless set:
Env: NVIDIA_API_KEY (or NVIDIA_GOOGLE_KEY / NVIDIA_MICROSOFT_KEY, Vercel names 'Google' / 'Microsoft'),
     NVIDIA_GEMMA_MODEL, NVIDIA_PHI_MODEL, OPEN_MODEL_THREADS (default 5)
Usage: python open_models.py --client-id UUID [--engine gemma|phi]
"""
import argparse, json, os, re, sys, time
import requests
from common import connect
import ai_visibility as AV

BASE = "https://integrate.api.nvidia.com/v1"
FAMILIES = {   # engine: (vendor prefix in NVIDIA's catalog, env override, preferred words, avoided words)
    "gemma": ("google/gemma", "NVIDIA_GEMMA_MODEL", ("it", "instruct"), ("embed", "code", "vision", "shield", "guard")),
    "phi": ("microsoft/phi", "NVIDIA_PHI_MODEL", ("instruct",), ("vision", "embed", "multimodal", "audio")),
}
ENGINE_KEY = {"gemma": "gemini", "phi": "bing_copilot"}            # stored under the real engine's key while testing
LOCALE = "The person asking is a shopper in India. Answer the question directly and name specific brands or stores where helpful."

def key_for(engine=None):
    return (os.environ.get("NVIDIA_API_KEY") or (os.environ.get("NVIDIA_GOOGLE_KEY") if engine == "gemma" else None)
            or (os.environ.get("NVIDIA_MICROSOFT_KEY") if engine == "phi" else None)
            or os.environ.get("NVIDIA_GOOGLE_KEY") or os.environ.get("NVIDIA_MICROSOFT_KEY"))

def available(): return bool(key_for())

class OpenModelError(RuntimeError):
    pass

_models = {}

def version_key(mid):
    """Sort key: newest family version and largest size first ('gemma-3-27b-it' over 'gemma-2-9b-it')."""
    nums = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)", mid.split("/", 1)[-1].replace("b-", " "))]
    size = max([float(s) for s in re.findall(r"(\d+(?:\.\d+)?)b", mid.lower())] or [0])
    return (nums[0] if nums else 0, size)

def pick_model(session, key, engine):
    """The model id for an engine: the env override, else the best match in NVIDIA's catalog (cached for the run)."""
    prefix, env, prefer, avoid = FAMILIES[engine]
    if os.environ.get(env): return os.environ[env]
    if engine in _models: return _models[engine]
    if "_all" not in _models:
        r = session.get(BASE + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        if r.status_code == 401: raise OpenModelError("NVIDIA refused the key. Check the Google / Microsoft keys in Vercel.")
        if r.status_code != 200: raise OpenModelError(f"Could not list NVIDIA's models ({r.status_code}).")
        _models["_all"] = [m.get("id", "") for m in r.json().get("data", [])]
    ids = [m for m in _models["_all"] if m.lower().startswith(prefix) and not any(a in m.lower() for a in avoid)]
    if not ids: raise OpenModelError(f"NVIDIA lists no {engine} model for this key.")
    preferred = [m for m in ids if any(re.search(rf"(^|[-/_.]){p}($|[-/_.])", m.lower()) for p in prefer)] or ids
    _models[engine] = sorted(preferred, key=version_key, reverse=True)[0]
    return _models[engine]

def chat(session, key, model, messages, max_tokens=1400, timeout=70):
    for attempt in range(3):
        try:
            r = session.post(BASE + "/chat/completions", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                             json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3, "stream": False},
                             timeout=timeout)
        except requests.RequestException as e:
            if attempt < 1: time.sleep(3); continue
            raise OpenModelError(f"Could not reach NVIDIA ({type(e).__name__}).")
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            try: wait = float(r.headers.get("retry-after") or 0)
            except (TypeError, ValueError): wait = 0
            time.sleep(min(10, wait or 3 * (attempt + 1))); continue
        if r.status_code in (401, 403): raise OpenModelError("NVIDIA refused the key for this model.")
        raise OpenModelError(f"NVIDIA {r.status_code}: {(r.text or '')[:200]}")
    raise OpenModelError("NVIDIA stayed busy; the rest runs next time.")

def clean(text):
    """Drop a reasoning model's <think>...</think> block; keep the answer."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return t.split("</think>", 1)[-1].strip()

def first_json(text):
    m = re.search(r"\{.*\}", clean(text), re.S)
    if not m: return None
    try: return json.loads(m.group(0))
    except ValueError: return None

def make_engine(engine):
    class OpenModelEngine:
        key, default_model, prompt_seconds = ENGINE_KEY[engine], None, 0

        @staticmethod
        def ask(session, api_key, text, region):
            model = pick_model(session, api_key, engine)
            j = chat(session, api_key, model, [{"role": "system", "content": LOCALE}, {"role": "user", "content": text}])
            j["_model"] = model
            return j

        @staticmethod
        def parse(j):
            msg = ((j.get("choices") or [{}])[0].get("message") or {})
            return clean(msg.get("content") or ""), [], []                     # no web search: nothing cited or read

        @staticmethod
        def judge(session, api_key, answer):
            """The same model lists the brands the answer names, with sentiment; anything unreadable leaves just the name matching."""
            model = pick_model(session, api_key, engine)
            try:
                j = chat(session, api_key, model, [
                    {"role": "system", "content": "List every brand, store or marketplace named in the text, in order of first appearance. "
                     'Reply with JSON only: {"brands":[{"name":"...","sentiment":"positive|neutral|negative","recommended":true|false}]}. '
                     "Do not add brands that are not named."},
                    {"role": "user", "content": answer[:6000]}], max_tokens=700, timeout=40)
            except OpenModelError:
                return []
            data = first_json(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "") or {}
            out = []
            for b in data.get("brands") or []:
                if isinstance(b, dict) and b.get("name"):
                    out.append({"name": str(b["name"])[:200], "sentiment": b.get("sentiment") if b.get("sentiment") in AV.SENT else "neutral",
                                "recommended": bool(b.get("recommended"))})
            return out

        @staticmethod
        def meta(j, api_key, judge_meta=None):
            return {"usage": j.get("usage"), "id": j.get("id"), "via": "nvidia", "model": j.get("_model")}

        @staticmethod
        def guard(conn, client_id): return None
    OpenModelEngine.__name__ = f"{engine.capitalize()}Engine"
    return OpenModelEngine

ENGINES = {e: make_engine(e) for e in FAMILIES}

def fetch(api_key, engine, text, region, session=None):
    """One prompt, network only (runs in a thread): the answer and the brands judged in it."""
    eng, session = ENGINES[engine], session or requests.Session()
    j = eng.ask(session, api_key, text, region)
    answer = eng.parse(j)[0]
    return j, (eng.judge(session, api_key, answer) if answer else [])

def sync_client(conn, client_id, engine, samples=1, cycle=None, session=None, deadline=None):
    """Asks the prompts not yet answered this cycle, in parallel, until `deadline`; stores what came back."""
    import datetime as dt, time
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    key = key_for(engine)
    if not key: raise OpenModelError("No NVIDIA key on the server.")
    cycle = cycle or dt.date.today()
    eng = ENGINES[engine]
    pick_model(session or requests.Session(), key, engine)                # once, before the threads
    done = {r[0] for r in conn.execute("""SELECT prompt_id FROM runs WHERE client_id=%s AND engine=%s AND cycle=%s AND status='ok'""",
                                       [client_id, eng.key, cycle])}
    todo = [(pid, text, region) for pid, text, region in conn.execute(
        "SELECT prompt_id, text, region FROM prompts WHERE client_id=%s AND approved AND active ORDER BY created_at", [client_id])
        if pid not in done]
    got, failed = {}, {}
    if todo:
        stop = (deadline - 25) if deadline else time.time() + 600
        pool = ThreadPoolExecutor(max_workers=max(1, int(os.environ.get("OPEN_MODEL_THREADS", "5"))))
        futs = {pool.submit(fetch, key, engine, text, region, session): pid for pid, text, region in todo}
        pending = set(futs)
        while pending and time.time() < stop:
            fin, pending = wait(pending, timeout=max(0.5, stop - time.time()), return_when=FIRST_COMPLETED)
            for f in fin:
                try: got[futs[f]] = f.result()
                except Exception as e: failed[futs[f]] = e
        pool.shutdown(wait=False, cancel_futures=True)
    by_text, current = {text: pid for pid, text, _ in todo}, {}
    def ask(s, k, text, region):                                           # serves what the threads fetched
        pid = current["pid"] = by_text[text]
        if pid in failed: raise failed[pid]
        return got[pid][0]
    Fetched = type(eng.__name__ + "Fetched", (eng,), {"ask": staticmethod(ask), "judge": staticmethod(lambda s, k, answer: got[current["pid"]][1])})
    tally, scores = AV.sync_client(conn, client_id, key, samples, cycle, session or requests.Session(), True, None, eng=Fetched,
                                   only=set(got) | set(failed))
    if len(got) + len(failed) < len(todo): tally["more"] = 1
    return tally, scores

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id", required=True); ap.add_argument("--engine", choices=list(FAMILIES))
    a = ap.parse_args()
    with connect() as conn:
        for e in ([a.engine] if a.engine else list(FAMILIES)):
            try:
                tally, _ = sync_client(conn, a.client_id, e)
                print(e, tally)
            except OpenModelError as err:
                print(e, "skipped:", err)
    return 0

if __name__ == "__main__":
    sys.exit(main())
