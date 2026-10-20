import sqlite3
import os
import hashlib
from typing import Optional, Dict, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "app.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                api_id TEXT DEFAULT '',
                api_hash TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        conn.commit()

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def create_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    username = username.strip()
    if not username or not password:
        return None
    hashed = hash_pw(password)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, hashed)
            )
            conn.commit()
            user_id = cur.lastrowid
            return {"id": user_id, "username": username, "api_id": "", "api_hash": ""}
    except sqlite3.IntegrityError:
        return None

def verify_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    username = username.strip()
    hashed = hash_pw(password)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, api_id, api_hash FROM users WHERE username = ? AND password = ?", (username, hashed))
        row = cur.fetchone()
        if row:
            return dict(row)
    return None

def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id, u.username, u.api_id, u.api_hash 
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ?
        """, (token,))
        row = cur.fetchone()
        if row:
            return dict(row)
    return None

def create_session(user_id: int, token: str):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
        conn.commit()

def delete_session(token: str):
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()

def update_user_api(user_id: int, api_id: str, api_hash: str):
    with get_db() as conn:
        conn.execute("UPDATE users SET api_id = ?, api_hash = ? WHERE id = ?", (api_id, api_hash, user_id))
        conn.commit()

init_db()
