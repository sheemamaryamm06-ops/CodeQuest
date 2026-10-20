import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("CODEQUEST_DB", BASE_DIR / "codequest.sqlite3"))
SESSION_COOKIE = "codequest_session"
SESSION_LIFETIME = 60 * 60 * 24 * 30
LANGUAGES = ("Python", "C", "HTML", "JavaScript", "Java")


@contextmanager
def connect_database():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                language TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS completed_levels (
                player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 6),
                completed_at INTEGER NOT NULL,
                PRIMARY KEY (player_id, level)
            );
            CREATE TABLE IF NOT EXISTS language_progress (
                player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 6),
                completed_at INTEGER NOT NULL,
                PRIMARY KEY (player_id, language, level)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            """
        )

        legacy_rows = connection.execute(
            "SELECT player_id, level FROM completed_levels ORDER BY player_id, level"
        ).fetchall()
        for row in legacy_rows:
            player = connection.execute(
                "SELECT language FROM players WHERE id = ?",
                (row["player_id"],),
            ).fetchone()
            language = player["language"] if player and player["language"] in LANGUAGES else "Python"
            connection.execute(
                "INSERT OR IGNORE INTO language_progress (player_id, language, level, completed_at) VALUES (?, ?, ?, ?)",
                (row["player_id"], language, row["level"], int(time.time())),
            )


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 310_000
    ).hex()
    return salt, password_hash


def player_progress_by_language(connection, player_id):
    progress = {language: [] for language in LANGUAGES}
    for row in connection.execute(
        "SELECT language, level FROM language_progress WHERE player_id = ? ORDER BY language, level",
        (player_id,),
    ):
        language = row["language"]
        if language in progress:
            progress[language].append(row["level"])
    return progress


def player_state(connection, player_id, language=None):
    player = connection.execute(
        "SELECT id, username, language FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    selected_language = language or player["language"] or "Python"
    progress = player_progress_by_language(connection, player_id)
    completed_levels = progress.get(selected_language, [])
    completed_languages = [
        lang for lang, levels in progress.items() if len(levels) >= 6
    ]
    return {
        "username": player["username"],
        "language": selected_language,
        "completed_levels": completed_levels,
        "language_progress": progress,
        "completed_languages": completed_languages,
        "xp": sum(len(levels) * 100 for levels in progress.values()),
        "current_level": min(len(completed_levels) + 1, 6),
    }


class CodeQuestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def respond_json(self, status, payload, extra_headers=()):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 16_384:
                return None
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None

    def session_player_id(self, connection):
        cookie = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        if not morsel:
            return None
        token_hash = hashlib.sha256(morsel.value.encode("ascii", "ignore")).hexdigest()
        row = connection.execute(
            "SELECT player_id FROM sessions WHERE token_hash = ? AND expires_at > ?",
            (token_hash, int(time.time())),
        ).fetchone()
        return row["player_id"] if row else None

    def issue_session(self, player_id, connection):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        connection.execute(
            "INSERT INTO sessions (token_hash, player_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, player_id, int(time.time()) + SESSION_LIFETIME),
        )
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_LIFETIME}",
        )

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/logout":
            with connect_database() as connection:
                player_id = self.session_player_id(connection)
                if player_id:
                    cookie = cookies.SimpleCookie(self.headers.get("Cookie", ""))
                    token = cookie[SESSION_COOKIE].value
                    token_hash = hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()
                    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            self.send_response(303)
            self.send_header("Location", "/login.html")
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
            self.end_headers()
            return
        if path == "/api/me":
            with connect_database() as connection:
                player_id = self.session_player_id(connection)
                if player_id is None:
                    self.respond_json(401, {"error": "Please log in to continue."})
                    return
                self.respond_json(200, player_state(connection, player_id))
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self.respond_json(404, {"error": "Endpoint not found."})
            return

        data = self.read_json()
        if data is None or not isinstance(data, dict):
            self.respond_json(400, {"error": "Send a valid JSON object."})
            return

        if path == "/api/register":
            username = str(data.get("username", "")).strip()
            password = data.get("password", "")
            if not 3 <= len(username) <= 24 or not username.isprintable():
                self.respond_json(400, {"error": "Username must be 3 to 24 printable characters."})
                return
            if not isinstance(password, str) or len(password) < 8:
                self.respond_json(400, {"error": "Password must be at least 8 characters."})
                return
            salt, password_hash = hash_password(password)
            try:
                with connect_database() as connection:
                    connection.execute(
                        "INSERT INTO players (username, password_hash, password_salt, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (username, password_hash, salt, int(time.time())),
                    )
            except sqlite3.IntegrityError:
                self.respond_json(409, {"error": "That username is already taken."})
                return
            self.respond_json(201, {"message": "Account created."})
            return

        if path == "/api/login":
            username = str(data.get("username", "")).strip()
            password = data.get("password", "")
            with connect_database() as connection:
                player = connection.execute(
                    "SELECT id, password_hash, password_salt FROM players WHERE username = ?",
                    (username,),
                ).fetchone()
                if not player or not isinstance(password, str):
                    self.respond_json(401, {"error": "Incorrect username or password."})
                    return
                _, password_hash = hash_password(password, player["password_salt"])
                if not secrets.compare_digest(password_hash, player["password_hash"]):
                    self.respond_json(401, {"error": "Incorrect username or password."})
                    return
                cookie = self.issue_session(player["id"], connection)
                state = player_state(connection, player["id"])
            self.respond_json(200, state, [cookie])
            return

        if path == "/api/language":
            with connect_database() as connection:
                player_id = self.session_player_id(connection)
                if player_id is None:
                    self.respond_json(401, {"error": "Please log in to continue."})
                    return
                language = data.get("language")
                if language not in LANGUAGES:
                    self.respond_json(400, {"error": "Choose a supported language."})
                    return
                connection.execute(
                    "UPDATE players SET language = ? WHERE id = ?", (language, player_id)
                )
                state = player_state(connection, player_id, language=language)
            self.respond_json(200, state)
            return

        if path == "/api/complete-level":
            with connect_database() as connection:
                player_id = self.session_player_id(connection)
                if player_id is None:
                    self.respond_json(401, {"error": "Please log in to continue."})
                    return
                player = connection.execute(
                    "SELECT language FROM players WHERE id = ?", (player_id,)
                ).fetchone()
                selected_language = data.get("language") or (player["language"] if player else None) or "Python"
                if selected_language not in LANGUAGES:
                    self.respond_json(400, {"error": "Choose a supported language."})
                    return
                level = data.get("level")
                if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 6:
                    self.respond_json(400, {"error": "Level must be between 1 and 6."})
                    return
                completed = {
                    row["level"]
                    for row in connection.execute(
                        "SELECT level FROM language_progress WHERE player_id = ? AND language = ? ORDER BY level",
                        (player_id, selected_language),
                    )
                }
                next_level = min(len(completed) + 1, 6)
                if level not in completed and level != next_level:
                    self.respond_json(403, {"error": "Complete the previous level first."})
                    return
                connection.execute(
                    "INSERT OR IGNORE INTO language_progress (player_id, language, level, completed_at) VALUES (?, ?, ?, ?)",
                    (player_id, selected_language, level, int(time.time())),
                )
                state = player_state(connection, player_id, language=selected_language)
            self.respond_json(200, state)
            return

        self.respond_json(404, {"error": "Endpoint not found."})

    def log_message(self, format, *args):
        if os.environ.get("CODEQUEST_ACCESS_LOG") == "1":
            super().log_message(format, *args)



def main():
    initialize_database()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), CodeQuestHandler)
    print("CodeQuest is running at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CodeQuest.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

