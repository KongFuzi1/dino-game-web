"""Dino scoreboard: serves the game, a JSON score API, a /board page and Prometheus metrics.

Database engine comes from DINO_DB_URL:
  mysql://user:pass@127.0.0.1/dino      postgres://user:pass@127.0.0.1/dino      sqlite:////var/lib/dino/scores.db
Every request opens its own connection, so when the database is dropped the page keeps
loading, the scoreboard shows the failure, and a restore is picked up without a restart.
"""
import glob
import json
import os
import re
import threading
import time
from urllib.parse import urlparse, unquote

from flask import Flask, jsonify, request, send_from_directory
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 120  # lets HAProxy cache the game files

DB_URL = os.environ.get("DINO_DB_URL", "sqlite:///scores.db")
BACKUP_DIR = os.environ.get("DINO_BACKUP_DIR", "")
CONFIG_FILE = os.environ.get("DINO_CONFIG", "config.json")
LEADERBOARD_SIZE = int(os.environ.get("DINO_LEADERBOARD_SIZE", "10"))
CACHE_TTL = float(os.environ.get("DINO_CACHE_TTL", "1"))


# ---- tiny engine adapter --------------------------------------------------------------
class Engine:
    """One class per engine: connect, placeholder style, schema, and 'table missing' detection."""

    def __init__(self, url):
        u = urlparse(url)
        self.kind = {"mysql": "mysql", "postgres": "postgres", "postgresql": "postgres", "sqlite": "sqlite"}[u.scheme]
        self.u = u
        if self.kind == "mysql":
            import pymysql
            self.mod = pymysql
            self.errors = (pymysql.MySQLError,)
            self.ph = "%s"
            self.schema = ("CREATE TABLE IF NOT EXISTS scores (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(16) NOT NULL, "
                           "score INT NOT NULL, played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        elif self.kind == "postgres":
            import psycopg2
            self.mod = psycopg2
            self.errors = (psycopg2.Error,)
            self.ph = "%s"
            self.schema = ("CREATE TABLE IF NOT EXISTS scores (id SERIAL PRIMARY KEY, name VARCHAR(16) NOT NULL, "
                           "score INT NOT NULL, played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        else:
            import sqlite3
            self.mod = sqlite3
            self.errors = (sqlite3.Error,)
            self.ph = "?"
            self.schema = ("CREATE TABLE IF NOT EXISTS scores (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                           "score INTEGER NOT NULL, played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")

    def connect(self):
        u = self.u
        if self.kind == "mysql":
            return self.mod.connect(host=u.hostname or "127.0.0.1", port=u.port or 3306, user=unquote(u.username or ""),
                                    password=unquote(u.password or ""), database=u.path.lstrip("/"), connect_timeout=2)
        if self.kind == "postgres":
            return self.mod.connect(host=u.hostname or "127.0.0.1", port=u.port or 5432, user=unquote(u.username or ""),
                                    password=unquote(u.password or ""), dbname=u.path.lstrip("/"), connect_timeout=2)
        path = self.sqlite_path()
        if not os.path.exists(path):  # a deleted file is "the database is gone", don't silently recreate it on read
            raise self.mod.OperationalError(f"no such database file: {path}")
        return self.mod.connect(path, timeout=2)

    def sqlite_path(self):
        # sqlite:///relative.db  ->  relative.db        sqlite:////var/lib/dino/scores.db  ->  /var/lib/dino/scores.db
        p = unquote(self.u.path)
        return p[1:] if p.startswith("//") else p.lstrip("/")

    def connect_create(self):
        """Like connect(), but for SQLite also creates the file (used when inserting after a restore/drop)."""
        if self.kind == "sqlite":
            return self.mod.connect(self.sqlite_path(), timeout=2)
        return self.connect()

    def q(self, sql):
        return sql.replace("%s", self.ph)

    def table_missing(self, exc):
        msg = str(exc).lower()
        return "doesn't exist" in msg or "does not exist" in msg or "no such table" in msg

    def error_text(self, exc):
        if self.kind == "mysql" and len(exc.args) > 1:
            return str(exc.args[1])
        return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__


ENGINE = Engine(DB_URL)


def ensure_schema():
    try:
        conn = ENGINE.connect_create()
        try:
            cur = conn.cursor()
            cur.execute(ENGINE.schema)
            conn.commit()
        finally:
            conn.close()
    except ENGINE.errors:
        pass  # database may be gone (that's the demo); the insert path retries the schema


ensure_schema()

# ---- metrics -----------------------------------------------------------------------------
SUBMISSIONS = Counter("dino_submissions_total", "Scores submitted since start")
DB_UP = Gauge("dino_db_up", "1 if the scores table answers queries")
SCORES = Gauge("dino_scores_total", "Rows in the scores table")
TOP = Gauge("dino_top_score", "Highest score in the table")
BACKUPS = Gauge("dino_backups_total", "Backup dumps on disk")

# Many phones poll the leaderboard: serve one DB read per CACHE_TTL, not one per phone.
_cache = {"t": 0.0, "body": None, "status": 200}
_cache_lock = threading.Lock()


# ---- pages -------------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/board")
def board():
    return send_from_directory("static", "board.html")


@app.get("/config.json")
def config():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    resp = jsonify(cfg)
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp


@app.get("/healthz")
def healthz():
    return "ok"


# ---- API ---------------------------------------------------------------------------------
def read_scores():
    try:
        conn = ENGINE.connect()
        try:
            cur = conn.cursor()
            cur.execute(ENGINE.q("SELECT name, score, played_at FROM scores ORDER BY score DESC, id ASC LIMIT %s"), (LEADERBOARD_SIZE,))
            rows = [{"name": r[0], "score": r[1], "played_at": _hms(r[2])} for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM scores")
            total = cur.fetchone()[0]
        finally:
            conn.close()
        return {"ok": True, "total": total, "scores": rows}, 200
    except ENGINE.errors as e:
        return {"ok": False, "error": ENGINE.error_text(e)}, 503


def _hms(v):
    if hasattr(v, "strftime"):
        return v.strftime("%H:%M:%S")
    return str(v)[11:19] if v else ""


@app.get("/api/scores")
def scores():
    now = time.monotonic()
    with _cache_lock:
        if _cache["body"] is None or now - _cache["t"] > CACHE_TTL:
            _cache["body"], _cache["status"] = read_scores()
            _cache["t"] = now
        body, status = _cache["body"], _cache["status"]
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Cache-Control"] = f"public, max-age={max(int(CACHE_TTL), 1)}"  # HAProxy serves the polls
    return resp


@app.post("/api/scores")
def submit():
    data = request.get_json(silent=True) or {}
    name = re.sub(r"[^\w .-]", "", str(data.get("name", "")))[:16].strip() or "anon"
    try:
        score = max(0, min(int(data.get("score", 0)), 1_000_000))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="bad score"), 400
    SUBMISSIONS.inc()
    try:
        conn = ENGINE.connect_create()
        try:
            cur = conn.cursor()
            try:
                cur.execute(ENGINE.q("INSERT INTO scores (name, score) VALUES (%s, %s)"), (name, score))
            except ENGINE.errors as e:
                if not ENGINE.table_missing(e):
                    raise
                conn.rollback()
                cur.execute(ENGINE.schema)  # fresh database after a drop: create the table once
                cur.execute(ENGINE.q("INSERT INTO scores (name, score) VALUES (%s, %s)"), (name, score))
            conn.commit()
            cur.execute(ENGINE.q("SELECT COUNT(*) + 1 FROM scores WHERE score > %s"), (score,))
            rank = cur.fetchone()[0]
        finally:
            conn.close()
        with _cache_lock:
            _cache["t"] = 0.0  # next read sees the new score
        return jsonify(ok=True, rank=rank)
    except ENGINE.errors as e:
        return jsonify(ok=False, error=ENGINE.error_text(e)), 503


@app.get("/metrics")
def metrics():
    try:
        conn = ENGINE.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), COALESCE(MAX(score), 0) FROM scores")
            n, top = cur.fetchone()
        finally:
            conn.close()
        DB_UP.set(1); SCORES.set(n); TOP.set(top)
    except ENGINE.errors:
        DB_UP.set(0); SCORES.set(0); TOP.set(0)
    BACKUPS.set(len(glob.glob(os.path.join(BACKUP_DIR, "*"))) if BACKUP_DIR else 0)
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}
