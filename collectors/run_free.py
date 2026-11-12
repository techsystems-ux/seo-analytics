#!/usr/bin/env python3
"""One entry point for all free collectors, with a run log. Schedule this from cron (no n8n needed).
Usage: python run_free.py [--only gsc,ga4,pagespeed] [--days 7]"""
import argparse, os, subprocess, sys
from common import connect

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = {"gsc": ["gsc.py"], "ga4": ["ga4.py"], "pagespeed": ["pagespeed.py"]}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only", default="gsc,ga4,pagespeed"); ap.add_argument("--days", default="7")
    a = ap.parse_args(); worst = 0
    with connect() as conn:
        for name in a.only.split(","):
            script = JOBS[name]
            extra = ["--days", a.days] if name in ("gsc", "ga4") else []
            run_id = conn.execute("INSERT INTO collector_runs (collector,status) VALUES (%s,'running') RETURNING run_id", [name]).fetchone()[0]
            p = subprocess.run([sys.executable, os.path.join(HERE, script[0]), *extra], capture_output=True, text=True)
            ok = p.returncode == 0
            conn.execute("UPDATE collector_runs SET finished_at=now(), status=%s, error=%s WHERE run_id=%s",
                         ["ok" if ok else "error", None if ok else (p.stderr or p.stdout)[-500:], run_id])
            print(f"{name}: {'ok' if ok else 'FAILED'}"); worst = worst or (0 if ok else 1)
    return worst

if __name__ == "__main__":
    sys.exit(main())
