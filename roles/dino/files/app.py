"""Dino runner scoreboard: static game + JSON score API + Prometheus metrics.

Every request opens its own DB connection, so when the database is dropped
the game page keeps loading but the scoreboard shows the failure, and when the
database is restored it recovers without a restart.
"""
import glob
import os
import re
import threading
import time

import pymysql
from flask import Flask, jsonify, request, send_from_directory
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 120  # lets HAProxy cache the game files

DB = dict(
    host="127.0.0.1",
    user=os.environ["DINO_DB_USER"],
    password=os.environ["DINO_DB_PASSWORD"],
    database=os.environ["DINO_DB_NAME"],
    connect_timeout=2,
    cursorclass=pymysql.cursors.DictCursor,
)
BACKUP_DIR = os.environ.get("DINO_BACKUP_DIR", "/var/backups/dino")

SUBMISSIONS = Counter("dino_submissions_total", "Scores submitted since start")
DB_UP = Gauge("dino_db_up", "1 if the scores table answers queries")
SCORES = Gauge("dino_scores_total", "Rows in the scores table")
TOP = Gauge("dino_top_score", "Highest score in the table")
BACKUPS = Gauge("dino_backups_total", "Backup dumps on disk")

SCHEMA = """CREATE TABLE IF NOT EXISTS scores (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(16) NOT NULL,
  score INT NOT NULL,
  played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""


def db():
    return pymysql.connect(**DB)


def ensure_schema():
    try:
        with pymysql.connect(**DB) as conn, conn.cursor() as c:
            c.execute(SCHEMA)
            conn.commit()
    except pymysql.MySQLError:
        pass  # database may be gone (that's the demo); the insert path retries the schema


ensure_schema()

# 300 phones poll the leaderboard every 5 s: serve one DB read per second, not sixty.
_cache = {"t": 0.0, "body": None, "status": 200}
_cache_lock = threading.Lock()
CACHE_TTL = 1.0


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/board")
def board():
    return send_from_directory("static", "board.html")


@app.get("/healthz")
def healthz():
    return "ok"


def read_scores():
    try:
        with db() as conn, conn.cursor() as c:
            c.execute("SELECT name, score, played_at FROM scores ORDER BY score DESC, id ASC LIMIT 10")
            rows = c.fetchall()
            c.execute("SELECT COUNT(*) AS n FROM scores")
            total = c.fetchone()["n"]
        for r in rows:
            r["played_at"] = r["played_at"].strftime("%H:%M:%S")
        return {"ok": True, "total": total, "scores": rows}, 200
    except pymysql.MySQLError as e:
        return {"ok": False, "error": str(e.args[1] if len(e.args) > 1 else e)}, 503


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
    resp.headers["Cache-Control"] = "public, max-age=1"  # HAProxy serves the polls; Python sees ~1 req/s
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
        with db() as conn, conn.cursor() as c:
            try:
                c.execute("INSERT INTO scores (name, score) VALUES (%s, %s)", (name, score))
            except pymysql.err.ProgrammingError:  # table missing (fresh DB after a drop): create it once
                c.execute(SCHEMA)
                c.execute("INSERT INTO scores (name, score) VALUES (%s, %s)", (name, score))
            conn.commit()
            c.execute("SELECT COUNT(*) + 1 AS pos FROM scores WHERE score > %s", (score,))
            rank = c.fetchone()["pos"]
        with _cache_lock:
            _cache["t"] = 0.0  # next read sees the new score
        return jsonify(ok=True, rank=rank)
    except pymysql.MySQLError as e:
        return jsonify(ok=False, error=str(e.args[1] if len(e.args) > 1 else e)), 503


@app.get("/metrics")
def metrics():
    try:
        with db() as conn, conn.cursor() as c:
            c.execute("SELECT COUNT(*) AS n, COALESCE(MAX(score), 0) AS top FROM scores")
            row = c.fetchone()
        DB_UP.set(1)
        SCORES.set(row["n"])
        TOP.set(row["top"])
    except pymysql.MySQLError:
        DB_UP.set(0)
        SCORES.set(0)
        TOP.set(0)
    BACKUPS.set(len(glob.glob(os.path.join(BACKUP_DIR, "*.sql"))))
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}
