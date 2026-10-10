"""Name Match: swipe on baby names, match with your partner, rank favourites by Elo."""
import collections
import json
import os
import random
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get("NAME_MATCH_DB", ROOT / "name_match.db"))
NAMES_JSON = ROOT / "data" / "names.json"
ELO_START = 1500.0
ELO_K = 32.0
USERNAME = re.compile(r"^[a-z0-9_.-]{1,32}$")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL,
    matches_seen_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS names (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    sex TEXT NOT NULL,
    countries TEXT NOT NULL,          -- JSON {cc: {rank, per10k}}, countries the name is tagged with
    ranks TEXT NOT NULL DEFAULT '{}', -- JSON {cc: {rank, count, per10k}}, every country it appears in
    UNIQUE (name, sex)
);
CREATE TABLE IF NOT EXISTS swipes (
    user_id INTEGER NOT NULL REFERENCES users(id),
    name_id INTEGER NOT NULL REFERENCES names(id),
    liked INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (user_id, name_id)
);
CREATE TABLE IF NOT EXISTS ratings (
    user_id INTEGER NOT NULL REFERENCES users(id),
    name_id INTEGER NOT NULL REFERENCES names(id),
    elo REAL NOT NULL,
    games INTEGER NOT NULL,
    PRIMARY KEY (user_id, name_id)
);
CREATE TABLE IF NOT EXISTS name_info (
    name_id INTEGER PRIMARY KEY REFERENCES names(id),
    info TEXT NOT NULL,               -- JSON from Gemini + sources
    model TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS duels (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    winner_id INTEGER NOT NULL,
    loser_id INTEGER NOT NULL,
    ts REAL NOT NULL
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        if "ranks" not in {r["name"] for r in conn.execute("PRAGMA table_info(names)")}:
            conn.execute("ALTER TABLE names ADD COLUMN ranks TEXT NOT NULL DEFAULT '{}'")
        data = json.loads(NAMES_JSON.read_text())
        conn.executemany(
            "INSERT INTO names (name, sex, countries, ranks) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (name, sex) DO UPDATE SET countries = excluded.countries, ranks = excluded.ranks",
            [(n["name"], n["sex"], json.dumps(n["countries"]), json.dumps(n.get("ranks", {}))) for n in data["names"]],
        )
    return data["countries"], data.get("meta", {}).get("top_only", {})


COUNTRIES, TOP_ONLY = init_db()


def load_names():
    with db() as conn:
        rows = conn.execute("SELECT id, name, sex, countries, ranks FROM names").fetchall()
    names = {r["id"]: {"id": r["id"], "name": r["name"], "sex": r["sex"],
                       "countries": json.loads(r["countries"])} for r in rows}
    return names, {r["id"]: json.loads(r["ranks"]) for r in rows}


NAMES, RANKS = load_names()  # RANKS kept apart so deck cards stay small

app = FastAPI(title="Name Match")


# ---------- helpers ----------

def current_user(conn, username):
    if not username:
        raise HTTPException(401, "not logged in")
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        raise HTTPException(401, "unknown user")
    return row


def best_rank(name, countries=None):
    ranks = [c["rank"] for cc, c in name["countries"].items() if not countries or cc in countries]
    return min(ranks) if ranks else None


def card(name, countries=None):
    return {**name, "best_rank": best_rank(name, countries)}


def matched_with(conn, user_id, name_id):
    rows = conn.execute(
        "SELECT u.username FROM swipes s JOIN users u ON u.id = s.user_id "
        "WHERE s.name_id = ? AND s.liked = 1 AND s.user_id != ? ORDER BY u.username",
        (name_id, user_id),
    ).fetchall()
    return [r["username"] for r in rows]


# ---------- auth ----------

class Login(BaseModel):
    username: str


@app.post("/api/login")
def login(body: Login, response: Response):
    username = body.username.strip().lower()
    if not USERNAME.match(username):
        raise HTTPException(400, "Use 1-32 letters, digits, dot, dash or underscore")
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO users (username, created_at) VALUES (?, ?)", (username, time.time()))
    response.set_cookie("user", username, max_age=10 * 365 * 86400, samesite="lax")
    return {"username": username}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("user")
    return {"ok": True}


@app.get("/api/me")
def me(user: str | None = Cookie(None)):
    with db() as conn:
        u = current_user(conn, user)
        stats = conn.execute(
            "SELECT COUNT(*) AS swiped, COALESCE(SUM(liked), 0) AS liked FROM swipes WHERE user_id = ?",
            (u["id"],),
        ).fetchone()
        others = [r["username"] for r in conn.execute(
            "SELECT username FROM users WHERE id != ? ORDER BY username", (u["id"],))]
        new_matches = conn.execute(
            "SELECT COUNT(*) FROM swipes mine JOIN swipes theirs "
            "ON theirs.name_id = mine.name_id AND theirs.user_id != mine.user_id AND theirs.liked = 1 "
            "WHERE mine.user_id = ? AND mine.liked = 1 AND MAX(mine.ts, theirs.ts) > ?",
            (u["id"], u["matches_seen_at"]),
        ).fetchone()[0]
    return {"username": u["username"], "swiped": stats["swiped"], "liked": stats["liked"],
            "others": others, "new_matches": new_matches, "total_names": len(NAMES)}


@app.get("/api/meta")
def meta():
    return {"countries": COUNTRIES,
            "counts": {s: sum(1 for n in NAMES.values() if n["sex"] == s) for s in "mf"}}


# ---------- swiping ----------

def matches_filters(n, sex, countries, min_len, max_len, pop):
    if sex and n["sex"] != sex:
        return False
    if not min_len <= len(n["name"]) <= max_len:
        return False
    rank = best_rank(n, countries)
    if rank is None:
        return False
    if pop == "top100":
        return rank <= 100
    if pop == "top500":
        return 100 < rank <= 500
    if pop == "rare":
        return rank > 500
    return True


@app.get("/api/deck")
def deck(sex: str = "m", countries: str = "", min_len: int = 1, max_len: int = 99,
         pop: str = "any", limit: int = 30, user: str | None = Cookie(None)):
    ccs = {c for c in countries.split(",") if c in COUNTRIES}
    with db() as conn:
        u = current_user(conn, user)
        seen = {r[0] for r in conn.execute("SELECT name_id FROM swipes WHERE user_id = ?", (u["id"],))}
    pool = [n for n in NAMES.values() if n["id"] not in seen
            and matches_filters(n, sex if sex in ("m", "f") else None, ccs, min_len, max_len, pop)]
    # Weighted shuffle: popular names tend to come first, but the order is not strictly sorted
    keyed = sorted(pool, key=lambda n: -random.random() ** (best_rank(n, ccs) ** 0.75))
    return {"remaining": len(pool), "cards": [card(n, ccs) for n in keyed[:limit]]}


class Swipe(BaseModel):
    name_id: int
    liked: bool


@app.post("/api/swipe")
def swipe(body: Swipe, user: str | None = Cookie(None)):
    if body.name_id not in NAMES:
        raise HTTPException(404, "unknown name")
    with db() as conn:
        u = current_user(conn, user)
        conn.execute(
            "INSERT INTO swipes (user_id, name_id, liked, ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id, name_id) DO UPDATE SET liked = excluded.liked, ts = excluded.ts",
            (u["id"], body.name_id, int(body.liked), time.time()),
        )
        if body.liked:
            conn.execute("INSERT OR IGNORE INTO ratings (user_id, name_id, elo, games) VALUES (?, ?, ?, 0)",
                         (u["id"], body.name_id, ELO_START))
        match = matched_with(conn, u["id"], body.name_id) if body.liked else []
    return {"match": match}


@app.post("/api/undo")
def undo(user: str | None = Cookie(None)):
    with db() as conn:
        u = current_user(conn, user)
        last = conn.execute("SELECT name_id FROM swipes WHERE user_id = ? ORDER BY ts DESC LIMIT 1",
                            (u["id"],)).fetchone()
        if not last:
            raise HTTPException(404, "nothing to undo")
        conn.execute("DELETE FROM swipes WHERE user_id = ? AND name_id = ?", (u["id"], last["name_id"]))
    return {"card": card(NAMES[last["name_id"]])}


@app.post("/api/reset-passed")
def reset_passed(user: str | None = Cookie(None)):
    """Put every name you swiped left on back into the deck."""
    with db() as conn:
        u = current_user(conn, user)
        n = conn.execute("DELETE FROM swipes WHERE user_id = ? AND liked = 0", (u["id"],)).rowcount
    return {"restored": n}


@app.post("/api/reset-me")
def reset_me(user: str | None = Cookie(None)):
    """Delete all of your swipes, Elo scores and duels. Other users' data is untouched."""
    with db() as conn:
        u = current_user(conn, user)
        swipes = conn.execute("DELETE FROM swipes WHERE user_id = ?", (u["id"],)).rowcount
        conn.execute("DELETE FROM ratings WHERE user_id = ?", (u["id"],))
        duels = conn.execute("DELETE FROM duels WHERE user_id = ?", (u["id"],)).rowcount
        conn.execute("UPDATE users SET matches_seen_at = 0 WHERE id = ?", (u["id"],))
    return {"swipes": swipes, "duels": duels}


# ---------- likes, matches, rankings ----------

def liked_rows(conn, user_id):
    return conn.execute(
        "SELECT s.name_id, s.ts, COALESCE(r.elo, ?) AS elo, COALESCE(r.games, 0) AS games "
        "FROM swipes s LEFT JOIN ratings r ON r.user_id = s.user_id AND r.name_id = s.name_id "
        "WHERE s.user_id = ? AND s.liked = 1",
        (ELO_START, user_id),
    ).fetchall()


@app.get("/api/likes")
def likes(user: str | None = Cookie(None)):
    with db() as conn:
        u = current_user(conn, user)
        rows = liked_rows(conn, u["id"])
        out = [{**card(NAMES[r["name_id"]]), "elo": round(r["elo"]), "games": r["games"],
                "matched_with": matched_with(conn, u["id"], r["name_id"])} for r in rows]
    out.sort(key=lambda n: -n["elo"])
    return {"likes": out}


@app.delete("/api/likes/{name_id}")
def unlike(name_id: int, user: str | None = Cookie(None)):
    with db() as conn:
        u = current_user(conn, user)
        # ts untouched so undo keeps targeting the last swipe, not this edit
        conn.execute("UPDATE swipes SET liked = 0 WHERE user_id = ? AND name_id = ?", (u["id"], name_id))
    return {"ok": True}


@app.get("/api/matches")
def matches(user: str | None = Cookie(None)):
    with db() as conn:
        u = current_user(conn, user)
        rows = conn.execute(
            "SELECT mine.name_id, ou.username, ou.id AS other_id, MAX(mine.ts, theirs.ts) AS ts, "
            "COALESCE(rm.elo, ?) AS my_elo, COALESCE(rt.elo, ?) AS their_elo "
            "FROM swipes mine JOIN swipes theirs ON theirs.name_id = mine.name_id "
            "  AND theirs.user_id != mine.user_id AND theirs.liked = 1 "
            "JOIN users ou ON ou.id = theirs.user_id "
            "LEFT JOIN ratings rm ON rm.user_id = mine.user_id AND rm.name_id = mine.name_id "
            "LEFT JOIN ratings rt ON rt.user_id = theirs.user_id AND rt.name_id = theirs.name_id "
            "WHERE mine.user_id = ? AND mine.liked = 1",
            (ELO_START, ELO_START, u["id"]),
        ).fetchall()
        seen_at = u["matches_seen_at"]
        conn.execute("UPDATE users SET matches_seen_at = ? WHERE id = ?", (time.time(), u["id"]))
    out = [{**card(NAMES[r["name_id"]]), "with": r["username"], "ts": r["ts"], "new": r["ts"] > seen_at,
            "my_elo": round(r["my_elo"]), "their_elo": round(r["their_elo"]),
            "joint_elo": round((r["my_elo"] + r["their_elo"]) / 2)} for r in rows]
    out.sort(key=lambda m: -m["joint_elo"])
    return {"matches": out}


@app.get("/api/duel")
def duel(user: str | None = Cookie(None)):
    with db() as conn:
        u = current_user(conn, user)
        rows = liked_rows(conn, u["id"])
    if len(rows) < 2:
        return {"pair": None, "liked": len(rows)}
    rows = list(rows)
    random.shuffle(rows)
    # First pick: one of the least-played names; second: a close rating, so each duel is informative
    a = min(rows, key=lambda r: r["games"])
    others = sorted((r for r in rows if r["name_id"] != a["name_id"]),
                    key=lambda r: abs(r["elo"] - a["elo"]) + 40 * r["games"] ** 0.5)
    b = random.choice(others[:3])
    pair = [a, b] if random.random() < 0.5 else [b, a]
    return {"pair": [{**card(NAMES[r["name_id"]]), "elo": round(r["elo"]), "games": r["games"]} for r in pair],
            "liked": len(rows)}


class DuelResult(BaseModel):
    winner_id: int
    loser_id: int


@app.post("/api/duel")
def duel_result(body: DuelResult, user: str | None = Cookie(None)):
    with db() as conn:
        u = current_user(conn, user)
        r = {row["name_id"]: row for row in liked_rows(conn, u["id"])}
        if body.winner_id not in r or body.loser_id not in r or body.winner_id == body.loser_id:
            raise HTTPException(400, "both names must be in your likes")
        w, l = r[body.winner_id]["elo"], r[body.loser_id]["elo"]
        expected_w = 1 / (1 + 10 ** ((l - w) / 400))
        delta = ELO_K * (1 - expected_w)
        for name_id, elo in ((body.winner_id, w + delta), (body.loser_id, l - delta)):
            conn.execute(
                "INSERT INTO ratings (user_id, name_id, elo, games) VALUES (?, ?, ?, 1) "
                "ON CONFLICT (user_id, name_id) DO UPDATE SET elo = excluded.elo, games = games + 1",
                (u["id"], name_id, elo),
            )
        conn.execute("INSERT INTO duels (user_id, winner_id, loser_id, ts) VALUES (?, ?, ?, ?)",
                     (u["id"], body.winner_id, body.loser_id, time.time()))
    return {"delta": round(delta, 1)}


@app.get("/api/popularity/{name_id}")
def popularity(name_id: int, user: str | None = Cookie(None)):
    """Rank and share in every country of the dataset, most popular first."""
    if name_id not in NAMES:
        raise HTTPException(404, "unknown name")
    with db() as conn:
        current_user(conn, user)
    ranks = RANKS[name_id]
    rows = [{"cc": cc, "country": label, **ranks[cc]} for cc, label in COUNTRIES.items() if cc in ranks]
    rows.sort(key=lambda r: -r["per10k"])
    missing = [{"cc": cc, "country": label, "top_only": TOP_ONLY.get(cc)}
               for cc, label in COUNTRIES.items() if cc not in ranks]
    return {"sex": NAMES[name_id]["sex"], "countries": rows, "missing": missing}


# ---------- name info (Gemini, fetched only on request, cached forever) ----------

INFO_PROMPT = """You are an expert in the etymology of first names, helping expecting parents.
Describe the {sex} first name "{name}", as used in: {countries}.
Always run a web search first and base the answer on what you find. If sources disagree, or the
name is a modern coinage with no established meaning, say so. Never invent a meaning.
Keep every field short; this is shown on a phone.
Reply with ONLY a JSON object, no markdown fences, with these keys:
{{"origin": "language/culture of origin and the path it took, e.g. 'Hebrew, via Greek and Latin Matthaeus'",
 "meaning": "literal meaning, e.g. 'gift of God'",
 "pronunciation": "simple English-style respelling with the stressed syllable in capitals, max 12 words, e.g. 'mat-TEH-oh'",
 "variants": ["forms of the name in other languages"],
 "namesakes": ["up to 3 well-known people with this first name"],
 "note": "one or two sentences of interesting context (history, saints, popularity trend)",
 "confidence": "high, medium or low"}}"""

_info_locks = collections.defaultdict(threading.Lock)


def gemini_generate(prompt, search=True):
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


def fetch_info(name):
    countries = ", ".join(COUNTRIES[c] for c in sorted(name["countries"], key=lambda c: name["countries"][c]["rank"]))
    prompt = INFO_PROMPT.format(sex="boy's" if name["sex"] == "m" else "girl's", name=name["name"], countries=countries)
    try:
        data, searched = gemini_generate(prompt), True
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        data, searched = gemini_generate(prompt, search=False), False  # model without search tool support
    cand = data["candidates"][0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    info = json.loads(text[text.index("{") : text.rindex("}") + 1])
    chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
    info["sources"] = [c["web"]["title"] for c in chunks if c.get("web", {}).get("title")][:5]
    info["searched"] = searched
    return info


@app.get("/api/info/{name_id}")
def name_info(name_id: int, user: str | None = Cookie(None)):
    if name_id not in NAMES:
        raise HTTPException(404, "unknown name")
    with db() as conn:
        current_user(conn, user)
    # One Gemini call per name, even if both of you tap at the same moment
    with _info_locks[name_id]:
        with db() as conn:
            row = conn.execute("SELECT info FROM name_info WHERE name_id = ?", (name_id,)).fetchone()
        if row:
            return {"info": json.loads(row["info"]), "cached": True}
        if not GEMINI_KEY:
            raise HTTPException(503, "Name info is not set up yet (GEMINI_API_KEY missing on the server)")
        try:
            info = fetch_info(NAMES[name_id])
        except urllib.error.HTTPError as e:
            raise HTTPException(502, f"Gemini error {e.code}: {e.read().decode(errors='replace')[:200]}")
        except (urllib.error.URLError, TimeoutError) as e:
            raise HTTPException(502, f"Could not reach Gemini: {e}")
        except (KeyError, IndexError, ValueError):
            raise HTTPException(502, "Gemini returned an unexpected answer, try again")
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO name_info (name_id, info, model, created_at) VALUES (?, ?, ?, ?)",
                         (name_id, json.dumps(info, ensure_ascii=False), GEMINI_MODEL, time.time()))
    return {"info": info, "cached": False}


# ---------- static ----------

app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
