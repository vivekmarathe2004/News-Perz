-- NewsPerz schema (reference copy).
--
-- The authoritative, versioned schema lives in newsperz/migrations.py and is
-- applied automatically at boot, or manually with:
--
--   flask --app app init-db
--
-- Database: SQLite at instance/newsperz.db (override with DATABASE_PATH).

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_login_at TEXT
);

-- Composite keys replace the old auto-increment + "DELETE then INSERT" pattern,
-- so duplicate topics/sources are impossible.
CREATE TABLE user_interests (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic TEXT NOT NULL,
  PRIMARY KEY (user_id, topic)
);

CREATE TABLE user_sources (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_name TEXT NOT NULL,
  PRIMARY KEY (user_id, source_name)
);

CREATE TABLE user_location (
  user_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  city TEXT NOT NULL DEFAULT 'Pune',
  area TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'Maharashtra',
  country TEXT NOT NULL DEFAULT 'India'
);

CREATE TABLE user_feed_prefs (
  user_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  sort_by TEXT NOT NULL DEFAULT 'relevance'
);

-- url_hash is sha1(article_url): Google News redirect URLs are far longer than
-- any sane index prefix and used to be silently truncated to 255 characters.
CREATE TABLE bookmarks (
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
CREATE INDEX idx_bookmarks_user_saved ON bookmarks (user_id, saved_at DESC);

-- Persisted fetch cache so restarts and multiple workers share one upstream call.
CREATE TABLE feed_cache (
  cache_key TEXT NOT NULL PRIMARY KEY,
  payload TEXT NOT NULL,
  expires_at REAL NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  last_used REAL NOT NULL DEFAULT 0
);
CREATE INDEX idx_feed_cache_expires ON feed_cache (expires_at);

-- Login throttling.
CREATE TABLE login_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_key TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX idx_login_attempts_key ON login_attempts (attempt_key, created_at);

CREATE TABLE schema_version (
  version INTEGER NOT NULL PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
