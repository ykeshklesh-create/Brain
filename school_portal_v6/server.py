#!/usr/bin/env python3
"""School Portal server.

Serves the portal and stores every record in a database:
  - SQLite file school.db (default, nothing to set up), or
  - Neon, Supabase or any PostgreSQL, when a connection string is given (see README.txt).

API used by the portal:
  GET  /api/state    read all records
  PUT  /api/state    save all records (needs a sign-in token once the database has data)
  POST /api/login    check a login and return a token
  GET  /api/health   which database is in use

The Developer account is protected on the server: only a signed-in developer can
add, change or remove a developer account, whatever the browser sends.
"""
import json
import os
import secrets
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = os.environ.get("SCHOOL_DB", os.path.join(HERE, "school.db"))
MAX_BYTES = int(os.environ.get("MAX_MB", "60")) * 1024 * 1024
CORS = os.environ.get("CORS_ORIGIN", "")  # only needed if the portal page is hosted on another site
HISTORY_EVERY = float(os.environ.get("HISTORY_HOURS", "6")) * 3600
HISTORY_KEEP = int(os.environ.get("HISTORY_KEEP", "20"))
TOKEN_TTL = 12 * 3600
LOCK = threading.Lock()
TOKENS = {}  # token -> (user id, role, expiry)


def pg_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        # Windows hides the .url ending, so database.txt is accepted as well
        for name in ("database.url", "database.txt", "database.url.txt"):
            f = os.path.join(HERE, name)
            if os.path.exists(f):
                url = open(f, encoding="utf-8-sig").read().strip().strip('"').strip("'")
                if url:
                    print("Using the database address in", name)
                    break
    return url


PG_URL = pg_url()
if PG_URL:
    try:
        import psycopg
    except ImportError:
        raise SystemExit('A Neon/PostgreSQL address is set but the driver is missing.\nRun:  pip install "psycopg[binary]"')
    DB_ERRORS = (psycopg.Error,)
else:
    DB_ERRORS = (sqlite3.Error,)


@contextmanager
def tx(path=None):
    """One database transaction. Yields a cursor; commits on success."""
    if PG_URL and path is None:
        c = psycopg.connect(PG_URL, connect_timeout=20)
    else:
        c = sqlite3.connect(path or DB_PATH, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
    try:
        cur = c.cursor()
        yield cur
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def run(cur, sql, args=(), pg=None):
    pg = PG_URL if pg is None else pg
    cur.execute(sql.replace("?", "%s") if pg else sql, args)


def run_many(cur, sql, rows):
    if not rows:
        return
    if PG_URL:
        cur.executemany(sql.replace("?", "%s"), rows)
    else:
        cur.executemany(sql, rows)


SCHEMA = [
    "CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, json TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL)",
    "CREATE TABLE IF NOT EXISTS history(ts DOUBLE PRECISION PRIMARY KEY, json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS pupils(id TEXT PRIMARY KEY, name TEXT, class_id TEXT, dob TEXT, email TEXT, fees_paid DOUBLE PRECISION, marks_disabled INTEGER)",
    "CREATE TABLE IF NOT EXISTS staff(id TEXT PRIMARY KEY, name TEXT, role TEXT, email TEXT, phone TEXT, subject TEXT, classes TEXT)",
]


def init():
    with tx() as cur:
        for s in SCHEMA:
            cur.execute(s)
    if PG_URL:
        # Supabase publishes tables in the public schema through its own web API.
        # Row Level Security with no policies blocks that API; this server still works.
        try:
            with tx() as cur:
                for t in ("state", "history", "pupils", "staff"):
                    cur.execute("ALTER TABLE %s ENABLE ROW LEVEL SECURITY" % t)
        except DB_ERRORS as e:
            print("Warning: could not turn on Row Level Security:", e)
    # moving from SQLite to Neon: copy the existing school.db across once
    if PG_URL and read_state() is None and os.path.exists(DB_PATH):
        try:
            with tx(DB_PATH) as cur:
                cur.execute("SELECT json FROM state WHERE id=1")
                row = cur.fetchone()
            if row:
                save_state(row[0].encode("utf-8"), force=True)
                print("Copied your existing school.db records into the new database.")
        except sqlite3.Error:
            pass


def read_state():
    with tx() as cur:
        run(cur, "SELECT json FROM state WHERE id=1")
        row = cur.fetchone()
    return row[0] if row else None


def dev_records(data):
    return sorted((s for s in data.get("staff", []) if isinstance(s, dict) and s.get("role") == "dev"),
                  key=lambda s: str(s.get("id")))


def save_state(raw, role=None, force=False):
    """Save everything. Raises PermissionError if a non-developer changes a developer account."""
    data = json.loads(raw)
    if not (isinstance(data, dict) and isinstance(data.get("pupils"), list)
            and isinstance(data.get("staff"), list) and isinstance(data.get("cfg"), dict)):
        raise ValueError("not a school state")
    if not force and role != "dev":
        old = read_state()
        if old and dev_records(json.loads(old)) != dev_records(data):
            raise PermissionError("developer account is protected")
    text = json.dumps(data, separators=(",", ":"))
    now = time.time()
    pupils = {}
    for p in data["pupils"]:
        if isinstance(p, dict):
            pupils[str(p.get("id"))] = (str(p.get("id")), p.get("name"), p.get("cls"), p.get("dob"), p.get("email"),
                                        p.get("paid") or 0, 1 if p.get("blocked") else 0)
    staff = {}
    for s in data["staff"]:
        if isinstance(s, dict):
            staff[str(s.get("id"))] = (str(s.get("id")), s.get("name"), s.get("role"), s.get("email"), s.get("phone"),
                                       ", ".join(s.get("subjects") or ([s["subject"]] if s.get("subject") else [])), ",".join(s.get("classes") or []))
    with LOCK, tx() as cur:
        run(cur, "UPDATE state SET json=?, updated=? WHERE id=1", (text, now))
        if cur.rowcount == 0:
            run(cur, "INSERT INTO state(id,json,updated) VALUES(1,?,?)", (text, now))
        run(cur, "SELECT MAX(ts) FROM history")
        last = cur.fetchone()[0]
        if not last or now - last > HISTORY_EVERY:  # safety snapshots so a bad overwrite can be undone
            run(cur, "INSERT INTO history(ts,json) VALUES(?,?)", (now, text))
            run(cur, "DELETE FROM history WHERE ts NOT IN (SELECT ts FROM history ORDER BY ts DESC LIMIT ?)", (HISTORY_KEEP,))
        cur.execute("DELETE FROM pupils")
        run_many(cur, "INSERT INTO pupils VALUES(?,?,?,?,?,?,?)", list(pupils.values()))
        cur.execute("DELETE FROM staff")
        run_many(cur, "INSERT INTO staff VALUES(?,?,?,?,?,?,?)", list(staff.values()))


def check_login(body):
    """Verify id/password/role against the saved records; return (id, role) or None."""
    text = read_state()
    if not text:
        return None
    data = json.loads(text)
    uid, pw, role = str(body.get("id", "")).lower(), body.get("pw"), body.get("role")
    pool = data["pupils"] if role == "pupil" else [s for s in data["staff"] if s.get("role") == role]
    for a in pool:
        ids = [str(a.get("id", "")).lower(), str(a.get("email") or "").lower()]
        if uid and uid in ids and a.get("pw") and a.get("pw") == pw:
            return str(a["id"]), role
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "SchoolPortal"

    def _send(self, code, body=b"", ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if CORS:
            self.send_header("Access-Control-Allow-Origin", CORS)
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
        self.end_headers()
        self.wfile.write(body)

    def _path(self):
        return self.path.split("?")[0]

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BYTES:
            return None
        return self.rfile.read(n)

    def _session(self):
        t = TOKENS.get(self.headers.get("X-Token", ""))
        return t if t and t[2] > time.time() else None

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        p = self._path()
        try:
            if p in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif p == "/api/state":
                text = read_state()
                self._send(200, text if text else '{"empty":true}')
            elif p == "/api/health":
                self._send(200, json.dumps({"ok": True, "db": "postgres" if PG_URL else "sqlite"}))
            else:
                self._send(404, '{"error":"not found"}')
        except DB_ERRORS as e:
            print("Database error:", e)
            self._send(500, '{"error":"database error"}')

    def do_POST(self):
        if self._path() != "/api/login":
            return self._send(404, '{"error":"not found"}')
        raw = self._body()
        try:
            who = check_login(json.loads(raw)) if raw else None
        except (ValueError, DB_ERRORS):
            who = None
        if not who:
            return self._send(401, '{"error":"wrong login"}')
        now = time.time()
        for k in [k for k, v in TOKENS.items() if v[2] < now]:
            TOKENS.pop(k, None)
        tok = secrets.token_hex(24)
        TOKENS[tok] = (who[0], who[1], now + TOKEN_TTL)
        self._send(200, json.dumps({"token": tok}))

    def do_PUT(self):
        if self._path() != "/api/state":
            return self._send(404, '{"error":"not found"}')
        raw = self._body()
        if raw is None:
            return self._send(413, '{"error":"empty or too large"}')
        try:
            ses = self._session()
            if ses is None and read_state() is not None:
                return self._send(401, '{"error":"sign in again"}')
            if ses and ses[1] == "pupil":
                return self._send(403, '{"error":"pupils cannot change records"}')
            save_state(raw, role=ses[1] if ses else None)
        except PermissionError:
            return self._send(403, '{"error":"only the developer can change the developer account"}')
        except (ValueError, json.JSONDecodeError):
            return self._send(400, '{"error":"invalid data"}')
        except DB_ERRORS as e:
            print("Database error:", e)
            return self._send(500, '{"error":"database error"}')
        self._send(200, '{"ok":true}')

    def log_message(self, fmt, *args):
        if "/api/" not in (args[0] if args else ""):
            super().log_message(fmt, *args)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "YOUR-COMPUTER-IP"
    finally:
        s.close()


if __name__ == "__main__":
    try:
        init()
    except DB_ERRORS as e:
        raise SystemExit("Could not open the database: %s" % e)
    print("\nSchool Portal Server")
    print("--------------------")
    print(f"Computer: http://localhost:{PORT}")
    print(f"Phone:    http://{local_ip()}:{PORT}")
    print("Database: PostgreSQL (Neon, Supabase or similar)" if PG_URL else f"Database: {DB_PATH}")
    print("\nKeep this window open. Press Ctrl+C to stop.\n")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
