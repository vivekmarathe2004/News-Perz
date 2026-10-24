"""Preference storage shared by the feed routes, the preferences page and auth.

Authenticated users live in SQLite; guests live in the signed session cookie and
are capped so the cookie cannot outgrow the browser limit (which would silently
drop the session).
"""

import logging

from flask import session
from flask_login import current_user

from newsperz.config import CATEGORIES, CITIES_WITH_AREAS, CITY_STATES, PUBLISHERS, SORT_OPTIONS
from newsperz.db import execute, query_all, query_one

log = logging.getLogger(__name__)

DEFAULT_LOCATION = {'city': 'Pune', 'area': 'Hinjewadi', 'state': 'Maharashtra', 'country': 'India'}
DEFAULT_TOPICS = ['Technology', 'Cybersecurity', 'Cricket', 'Education']
DEFAULT_PUBLISHERS = ['The Indian Express', 'BBC News', 'The Hindu', 'Hindustan Times']
DEFAULT_SORT = 'relevance'
VALID_SORTS = {key for key, _label in SORT_OPTIONS}

_GUEST_PREFS_KEY = 'guest_prefs'
MAX_TOPICS = len(CATEGORIES)
MAX_PUBLISHERS = len(PUBLISHERS)
MAX_TEXT_LEN = 80


def _clean_text(value, limit=MAX_TEXT_LEN):
    return (value or '').strip()[:limit]


def _normalize_location(raw):
    raw = raw or {}
    city = _clean_text(raw.get('city'))
    if not city or city == '__other__':
        city = DEFAULT_LOCATION['city']
    state = _clean_text(raw.get('state')) or CITY_STATES.get(city, DEFAULT_LOCATION['state'])
    return {
        'city': city,
        'area': _clean_text(raw.get('area')),
        'state': state,
        'country': _clean_text(raw.get('country'), 60) or 'India',
    }


def _filter_choices(values, allowed, limit, fallback):
    """`None` means "not supplied" -> fallback; `[]` means "user deselected all"."""
    if values is None:
        return list(fallback)
    allowed_set = set(allowed)
    kept = [v for v in dict.fromkeys(_clean_text(v) for v in values) if v in allowed_set]
    return kept[:limit]


def get_guest_preferences():
    return session.get(_GUEST_PREFS_KEY) or {}


def get_user_preferences():
    """Return (location, topics, publishers, sort_by) for the current visitor."""
    location = dict(DEFAULT_LOCATION)
    topics = list(DEFAULT_TOPICS)
    publishers = list(DEFAULT_PUBLISHERS)
    sort_by = DEFAULT_SORT

    if current_user.is_authenticated:
        row = query_one('SELECT city, area, state, country FROM user_location WHERE user_id = ?', (current_user.id,))
        if row and _clean_text(row['city']):
            location = _normalize_location(dict(row))

        topics = [r['topic'] for r in query_all('SELECT topic FROM user_interests WHERE user_id = ?', (current_user.id,))]
        publishers = [r['source_name'] for r in query_all('SELECT source_name FROM user_sources WHERE user_id = ?', (current_user.id,))]

        sort_row = query_one('SELECT sort_by FROM user_feed_prefs WHERE user_id = ?', (current_user.id,))
        sort_by = (sort_row and sort_row['sort_by']) or DEFAULT_SORT
        if sort_by not in VALID_SORTS:
            sort_by = DEFAULT_SORT
        return location, topics, publishers, sort_by

    prefs = get_guest_preferences()
    if prefs:
        location = _normalize_location(prefs.get('location'))
        topics = prefs.get('topics') if prefs.get('topics') is not None else topics
        publishers = prefs.get('publishers') if prefs.get('publishers') is not None else publishers
        sort_by = prefs.get('sort_by') or sort_by
        if sort_by not in VALID_SORTS:
            sort_by = DEFAULT_SORT

    return location, topics, publishers, sort_by


def save_preferences(city=None, area=None, state=None, country=None,
                     topics=None, publishers=None, sort_by=None):
    """Persist preferences for the visitor. Returns True if anything was stored."""
    location = _normalize_location({'city': city, 'area': area, 'state': state, 'country': country})
    topics = _filter_choices(topics, CATEGORIES, MAX_TOPICS, DEFAULT_TOPICS)
    publishers = _filter_choices(publishers, PUBLISHERS, MAX_PUBLISHERS, DEFAULT_PUBLISHERS)
    if sort_by not in VALID_SORTS:
        sort_by = DEFAULT_SORT

    prefs = {
        'location': location,
        'topics': topics,
        'publishers': publishers,
        'sort_by': sort_by,
    }

    if not current_user.is_authenticated:
        session[_GUEST_PREFS_KEY] = prefs
        return True

    # Guests' cached prefs must not shadow the saved account rows later on.
    session.pop(_GUEST_PREFS_KEY, None)

    try:
        execute(
            """INSERT INTO user_location (user_id, city, area, state, country) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 city = excluded.city, area = excluded.area,
                 state = excluded.state, country = excluded.country""",
            (current_user.id, location['city'], location['area'], location['state'], location['country']),
        )
        execute('DELETE FROM user_interests WHERE user_id = ?', (current_user.id,))
        for topic in topics:
            execute('INSERT OR IGNORE INTO user_interests (user_id, topic) VALUES (?, ?)', (current_user.id, topic))
        execute('DELETE FROM user_sources WHERE user_id = ?', (current_user.id,))
        for publisher in publishers:
            execute('INSERT OR IGNORE INTO user_sources (user_id, source_name) VALUES (?, ?)',
                    (current_user.id, publisher))
        execute(
            """INSERT INTO user_feed_prefs (user_id, sort_by) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET sort_by = excluded.sort_by""",
            (current_user.id, sort_by),
        )
        return True
    except Exception:
        log.exception('could not save preferences for user %s', current_user.id)
        return False


def merge_guest_preferences(user_id):
    """Copy a guest's cookie preferences onto a freshly authenticated account."""
    prefs = get_guest_preferences()
    if not prefs:
        return False
    saved = False
    try:
        location = _normalize_location(prefs.get('location'))
        execute(
            """INSERT INTO user_location (user_id, city, area, state, country) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 city = excluded.city, area = excluded.area,
                 state = excluded.state, country = excluded.country""",
            (user_id, location['city'], location['area'], location['state'], location['country']),
        )
        topics = _filter_choices(prefs.get('topics'), CATEGORIES, MAX_TOPICS, [])
        if topics:
            execute('DELETE FROM user_interests WHERE user_id = ?', (user_id,))
            for topic in topics:
                execute('INSERT OR IGNORE INTO user_interests (user_id, topic) VALUES (?, ?)', (user_id, topic))
        publishers = _filter_choices(prefs.get('publishers'), PUBLISHERS, MAX_PUBLISHERS, [])
        if publishers:
            execute('DELETE FROM user_sources WHERE user_id = ?', (user_id,))
            for publisher in publishers:
                execute('INSERT OR IGNORE INTO user_sources (user_id, source_name) VALUES (?, ?)', (user_id, publisher))
        sort_by = prefs.get('sort_by')
        if sort_by in VALID_SORTS:
            execute(
                """INSERT INTO user_feed_prefs (user_id, sort_by) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET sort_by = excluded.sort_by""",
                (user_id, sort_by),
            )
        saved = True
    except Exception:
        log.exception('guest preference merge failed for user %s', user_id)
    session.pop(_GUEST_PREFS_KEY, None)
    return saved


def known_areas(city):
    return list(CITIES_WITH_AREAS.get(city, []))
