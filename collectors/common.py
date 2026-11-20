"""Shared helpers for the free-API collectors. Plain scripts: run from cron, no n8n needed."""
import os, datetime as dt, psycopg

# Key names as saved in Vercel -> the names the code reads. The first name set wins; nothing is overwritten.
KEY_NAMES = {"ANTHROPIC_API_KEY": ("Claude", "CLAUDE_API_KEY"),
             "OPENAI_API_KEY_2": ("OPENAI_API_KEY_3",),
             "NVIDIA_GOOGLE_KEY": ("Google",),            # NVIDIA-hosted Google open models (not Gemini)
             "NVIDIA_MICROSOFT_KEY": ("Microsoft",)}      # NVIDIA-hosted Microsoft open models (not Copilot)

def alias_keys(env=os.environ):
    for name, others in KEY_NAMES.items():
        if not (env.get(name) or "").strip():
            v = next((env[o].strip() for o in others if (env.get(o) or "").strip()), None)
            if v: env[name] = v

alias_keys()

def connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    if os.environ.get("DB_SET_ROLE"):                                      # server: owner login, then act as the app role
        conn.execute(f'SET ROLE "{os.environ["DB_SET_ROLE"]}"')            # so row-level security always applies
    conn.execute("SET app.is_staff = 'true'")                              # collectors act as staff across clients
    return conn

def google_session(scope):
    """Service-account auth. GOOGLE_APPLICATION_CREDENTIALS = path to the key JSON.
    The service account's email must be added as a user on each client's GSC property / GA4 property."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_file(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=[scope])
    return AuthorizedSession(creds)

def window(days, lag):
    """Inclusive date window ending `lag` days ago (GSC and GA4 data settle after a short delay)."""
    end = dt.date.today() - dt.timedelta(days=lag)
    return end - dt.timedelta(days=days - 1), end
