"""Shared helpers for the free-API collectors. Plain scripts: run from cron, no n8n needed."""
import os, datetime as dt, psycopg

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
