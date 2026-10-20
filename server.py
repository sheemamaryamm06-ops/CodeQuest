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
