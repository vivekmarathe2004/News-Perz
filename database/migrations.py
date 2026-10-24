"""SQLite schema migrations.

Migrations are append-only: bump SCHEMA_VERSION and add a `(version, sql)` pair.
`run_migrations()` is called at boot and by `flask init-db`.
"""

import hashlib
import logging
import sqlite3

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

V1 = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS user_interests (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic TEXT NOT NULL,
  PRIMARY KEY (user_id, topic)
);

CREATE TABLE IF NOT EXISTS user_sources (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_name TEXT NOT NULL,
  PRIMARY KEY (user_id, source_name)
);

CREATE TABLE IF NOT EXISTS user_location (
  user_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  city TEXT NOT NULL DEFAULT 'Pune',
  area TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'Maharashtra',
  country TEXT NOT NULL DEFAULT 'India'
);

CREATE TABLE IF NOT EXISTS user_feed_prefs (
  user_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  sort_by TEXT NOT NULL DEFAULT 'relevance'
);

CREATE TABLE IF NOT EXISTS bookmarks (
  url_hash TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  article_url TEXT NOT NULL,
  article_title TEXT NOT NULL DEFAULT '',
  article_source TEXT NOT NULL DEFAULT '',
  article_description TEXT NOT NULL DEFAULT '',
  article_image TEXT NOT NULL DEFAULT '',
  article_published_at TEXT NOT NULL DEFAULT '',
  saved_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user_saved
  ON bookmarks (user_id, saved_at DESC);

-- Persisted news fetch cache: survives restarts and is shared across workers.
CREATE TABLE IF NOT EXISTS feed_cache (
  cache_key TEXT NOT NULL PRIMARY KEY,
  payload TEXT NOT NULL,
  expires_at REAL NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  last_used REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_feed_cache_expires ON feed_cache (expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_key TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_key
  ON login_attempts (attempt_key, created_at);

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

V2 = """
-- Story previews opened inside NewsPerz. Fetched articles are persisted so a
-- card can link to an in-app detail page without re-querying the provider.
CREATE TABLE IF NOT EXISTS articles (
  story_id TEXT NOT NULL PRIMARY KEY,
  article_url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  image TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  published_at TEXT NOT NULL DEFAULT '',
  fetched_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_url ON articles (article_url);
CREATE INDEX IF NOT EXISTS idx_articles_category ON articles (category, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles (fetched_at DESC);

-- Lazily extracted readable text, cached per story. status is one of
-- ok / blocked / empty / error; failed attempts retry after a cool-off.
CREATE TABLE IF NOT EXISTS article_content (
  story_id TEXT NOT NULL PRIMARY KEY,
  body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  fetched_at REAL NOT NULL DEFAULT 0
);
"""

MIGRATIONS = [
    (1, V1),
    (2, V2),
]


def bookmark_url_hash(article_url: str) -> str:
    """Google News redirect URLs are long and vary by locale; hash the full URL
    so the uniqueness key is fixed-width and never truncates."""
    return hashlib.sha1(article_url.strip().encode('utf-8', 'ignore')).hexdigest()


def current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute('SELECT MAX(version) FROM schema_version').fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def run_migrations(db_path: str, force: bool = False) -> tuple:
    """Apply pending migrations. Returns (before, after) version numbers."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA foreign_keys = ON')
        before = current_version(conn)
        applied = before
        for version, sql in MIGRATIONS:
            if not force and version <= before:
                continue
            conn.executescript(sql)
            conn.execute('INSERT OR IGNORE INTO schema_version (version) VALUES (?)', (version,))
            conn.commit()
            applied = max(applied, version)
            log.info('applied schema migration %s', version)
        return (before, applied)
    finally:
        conn.close()
