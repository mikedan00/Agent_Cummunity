import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    body TEXT NOT NULL,
    author TEXT NOT NULL,
    is_agent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(os.environ.get("DB_PATH", "community.db"))
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def create_post(title: str, body: str, author: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO posts (title, body, author) VALUES (?, ?, ?)", (title, body, author)
        )
        return cur.lastrowid


def list_posts() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count "
            "FROM posts p ORDER BY p.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_post(post_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return dict(row) if row else None


def add_comment(post_id: int, body: str, author: str, is_agent: bool) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO comments (post_id, body, author, is_agent) VALUES (?, ?, ?, ?)",
            (post_id, body, author, int(is_agent)),
        )
        return cur.lastrowid


def list_comments(post_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY id", (post_id,)
        ).fetchall()
    return [dict(r) for r in rows]
