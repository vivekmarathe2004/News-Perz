"""SQLite access layer.

Connections are per-request, created lazily, and `None` is returned when the
database is unusable so callers can degrade instead of raising. Callers must
check for `None` — silently 500ing on every page when the DB is unavailable was
the previous behaviour.
"""

import logging
import sqlite3
from pathlib import Path

import click
from flask import current_app, g

log = logging.getLogger(__name__)


class _DualRow(sqlite3.Row):
    """Row that supports both `row["col"]` (sqlite3.Row) and `row[col]` (dict)."""

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return super().__getitem__(key)
            except KeyError:
                raise IndexError(key) from None
        return super().__getitem__(key)


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = _DualRow
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA busy_timeout = 4000')
    return conn


def get_db():
    """Return the request's SQLite connection, or None if unavailable."""
    if 'db' in g:
        return g.db
    try:
        g.db = _connect(current_app.config['DATABASE_PATH'])
    except Exception:
        log.exception('database unavailable')
        g.db = None
    return g.db


def is_available() -> bool:
    return get_db() is not None


def close_db(_exception=None):
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except sqlite3.Error:
            pass


def query_all(sql, params=()):
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db.execute(sql, params)
        try:
            return cursor.fetchall()
        finally:
            cursor.close()
    except sqlite3.Error:
        log.exception('query_all failed: %s', sql)
        return []


def query_one(sql, params=()):
    rows = query_all(sql, params)
    return rows[0] if rows else None


def scalar(sql, params=(), default=None):
    row = query_one(sql, params)
    if row is None:
        return default
    try:
        return row[0]
    except (IndexError, KeyError):
        return default


def execute(sql, params=(), *, ignore_conflict=False):
    """Run a write statement.

    Returns the new rowid, or None when the statement failed. With
    `ignore_conflict=True` a UNIQUE/PRIMARY KEY clash returns the sentinel
    -1 instead of raising, so callers can tell "already there" from "broken".
    """
    db = get_db()
    if db is None:
        return None
    try:
        cursor = db.execute(sql, params)
        db.commit()
        try:
            return cursor.lastrowid
        finally:
            cursor.close()
    except sqlite3.IntegrityError as exc:
        if ignore_conflict:
            return -1
        log.info('integrity error on %s: %s', sql, exc)
        raise
    except sqlite3.Error:
        log.exception('execute failed: %s', sql)
        return None


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(_init_db_command)


@click.command('init-db')
@click.option('--force', is_flag=True, help='Re-apply every migration.')
def _init_db_command(force):
    """Create/upgrade the SQLite schema in instance/."""
    from newsperz.migrations import run_migrations

    app = current_app._get_current_object()
    with app.app_context():
        before, after = run_migrations(app.config['DATABASE_PATH'], force=force)
    click.echo(f"migrations {before} -> {after} on {app.config['DATABASE_PATH']}")
