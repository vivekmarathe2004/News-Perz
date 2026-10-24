"""Bookmarks for authenticated users (SQLite) and guests (session cookie).

Guest data is deliberately capped and stored once, not mirrored for logged-in
users: the previous code appended full article text to the signed session cookie
for everyone, and past ~4 KB browsers drop the cookie — which silently logged
users out.
"""

import json
import logging

from flask import session
from flask_login import current_user

from newsperz.db import execute, query_all
from newsperz.migrations import bookmark_url_hash

log = logging.getLogger(__name__)

_GUEST_ITEMS_KEY = 'guest_saved_items'
_LEGACY_GUEST_URLS_KEY = 'guest_bookmarks'
# The guest list lives inside the signed session cookie. Unbounded growth pushes
# the cookie past the ~4 KB browser limit, at which point the whole session —
# including the Flask-Login id — is dropped. Budget leaves room for the login
# id, guest preferences and the cookie signature.
MAX_GUEST_ITEMS = 12
GUEST_COOKIE_BUDGET = 2800
GUEST_DESCRIPTION_LIMIT = 140
FIELD_LIMITS = {
    'article_url': 2048,
    'article_title': 512,
    'article_source': 200,
    'article_description': 4000,
    'article_image': 1024,
    'article_published_at': 80,
}


_GUEST_FIELD_LIMITS = {
    'article_url': 512,
    'article_title': 180,
    'article_source': 80,
    'article_description': GUEST_DESCRIPTION_LIMIT,
    'article_image': 400,
    'article_published_at': 40,
}


def _clip(field, value, limits=FIELD_LIMITS):
    text = (value or '').strip()
    return text[:limits[field]]


def _clean_url(value):
    """Only web URLs become clickable; a javascript:/data: href would be a XSS sink."""
    url = str(value or '').strip()
    return url if url.lower().startswith(('http://', 'https://')) else ''


def _normalize_item(url, title, source, description, image, published_at):
    return {
        'article_url': _clip('article_url', url),
        'article_title': _clip('article_title', title),
        'article_source': _clip('article_source', source),
        'article_description': _clip('article_description', description),
        'article_image': _clip('article_image', image),
        'article_published_at': _clip('article_published_at', published_at),
    }


def get_bookmarked_urls():
    """URL set used to render the saved/un-saved state of each card."""
    if current_user.is_authenticated:
        rows = query_all('SELECT article_url FROM bookmarks WHERE user_id = ?', (current_user.id,))
        return {row['article_url'] for row in rows}
    return {item['article_url'] for item in session.get(_GUEST_ITEMS_KEY, [])}


def get_saved_items():
    """Full bookmark rows, newest first."""
    if current_user.is_authenticated:
        rows = query_all(
            """SELECT article_url, article_title, article_source, article_description,
                      article_image, article_published_at, saved_at
               FROM bookmarks WHERE user_id = ? ORDER BY saved_at DESC, rowid DESC""",
            (current_user.id,),
        )
        return [dict(row) for row in rows]
    return list(session.get(_GUEST_ITEMS_KEY, []))


def add_bookmark(url, title='', source='', description='', image='', published_at=''):
    url = _clean_url(url)
    if not url:
        return {'saved': False, 'reason': 'invalid_url'}

    if not current_user.is_authenticated:
        item = _normalize_item(url, title, source, description, image, published_at)
        item = {key: _clip(key, item[key], _GUEST_FIELD_LIMITS) for key in _GUEST_FIELD_LIMITS}
        stored = _store_guest_items([item] + [
            i for i in session.get(_GUEST_ITEMS_KEY, []) if i.get('article_url') != item['article_url']
        ])
        return {'saved': stored, 'storage': 'session', 'limit': MAX_GUEST_ITEMS}

    item = _normalize_item(url, title, source, description, image, published_at)
    try:
        result = execute(
            """INSERT OR IGNORE INTO bookmarks
               (url_hash, user_id, article_url, article_title, article_source,
                article_description, article_image, article_published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (bookmark_url_hash(item['article_url']), current_user.id, item['article_url'],
             item['article_title'], item['article_source'], item['article_description'],
             item['article_image'], item['article_published_at']),
            ignore_conflict=True,
        )
        if result is None:
            return {'saved': False, 'reason': 'db_error'}
        return {'saved': True, 'storage': 'db', 'existing': result == -1}
    except Exception:
        log.exception('bookmark insert failed for user %s', current_user.id)
        return {'saved': False, 'reason': 'db_error'}


def remove_bookmark(url):
    if not url:
        return {'saved': False, 'reason': 'missing_url'}
    url = url.strip()

    if not current_user.is_authenticated:
        items = [i for i in session.get(_GUEST_ITEMS_KEY, []) if i.get('article_url') != url]
        _store_guest_items(items)
        return {'saved': True, 'storage': 'session'}

    try:
        execute('DELETE FROM bookmarks WHERE user_id = ? AND article_url = ?', (current_user.id, url))
        return {'saved': True, 'storage': 'db'}
    except Exception:
        log.exception('bookmark delete failed for user %s', current_user.id)
        return {'saved': False, 'reason': 'db_error'}


def _store_guest_items(items):
    """Write the (capped) guest list back to the session; False if it was dropped."""
    trimmed = []
    seen = set()
    for item in items:
        url = (item.get('article_url') or '').strip()
        if not url or url in seen:
            continue
        seen.add(url)
        trimmed.append({key: _clip(key, item.get(key), _GUEST_FIELD_LIMITS) for key in _GUEST_FIELD_LIMITS})
    trimmed = trimmed[:MAX_GUEST_ITEMS]
    while trimmed and len(json.dumps(trimmed, separators=(',', ':'))) > GUEST_COOKIE_BUDGET:
        trimmed.pop()
    session[_GUEST_ITEMS_KEY] = trimmed
    session.permanent = True
    return bool(trimmed) or not items


def merge_guest_bookmarks(user_id):
    """Move cookie bookmarks into the account after login/registration."""
    items = session.get(_GUEST_ITEMS_KEY, [])
    if not items:
        return 0
    moved = 0
    try:
        for raw in items:
            item = _normalize_item(
                raw.get('article_url'), raw.get('article_title'), raw.get('article_source'),
                raw.get('article_description'), raw.get('article_image'), raw.get('article_published_at'),
            )
            if not _clean_url(item['article_url']):
                continue
            result = execute(
                """INSERT OR IGNORE INTO bookmarks
                   (url_hash, user_id, article_url, article_title, article_source,
                    article_description, article_image, article_published_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (bookmark_url_hash(item['article_url']), user_id, item['article_url'],
                 item['article_title'], item['article_source'], item['article_description'],
                 item['article_image'], item['article_published_at']),
                ignore_conflict=True,
            )
            if result and result != -1:
                moved += 1
    except Exception:
        log.exception('guest bookmark merge failed for user %s', user_id)
    session.pop(_GUEST_ITEMS_KEY, None)
    session.pop(_LEGACY_GUEST_URLS_KEY, None)
    return moved
