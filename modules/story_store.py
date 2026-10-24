"""Persistent story previews and in-app reading.

Cards link to ``/story/<story_id>`` instead of jumping straight to the
publisher. Every article returned by a provider is remembered here so that
detail page can be rendered without another provider call. The readable text
is extracted lazily from the publisher page (never fabricated) with a strict
timeout, an SSRF guard, and a small cached excerpt.

Everything degrades silently: a missing table or an unreachable database means
"no story cache", not a 500.
"""
import hashlib
import html as html_lib
import ipaddress
import logging
import re
import socket
import time
import urllib.parse
from html.parser import HTMLParser

import requests

from database.db import get_db, query_all, query_one

log = logging.getLogger(__name__)

STORY_RETENTION_DAYS = 45
STORY_MAX_ROWS = 6000
CONTENT_RETRY_SECONDS = 3600
MAX_HTML_BYTES = 1_500_000
MAX_BODY_CHARS = 2600
MAX_REDIRECTS = 4
MIN_PARAGRAPH_CHARS = 70
_REQUEST_TIMEOUT = (3.0, 7.0)

_BOILERPLATE = (
    'subscribe', 'sign in', 'sign up', 'log in', 'create account', 'cookie',
    'privacy policy', 'terms of use', 'all rights reserved', 'advertisement',
    'follow us', 'download the app', 'newsletter', 'enable javascript',
    'share this', 'whatsapp', 'telegram', 'facebook', 'twitter', 'copyright',
    'read more', 'related articles', 'recommended for you',
)

_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NewsPerz/1.0'
)
_HEADERS = {
    'User-Agent': _USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.5',
    'Accept-Language': 'en-IN,en;q=0.9',
}


class StoryFetchError(Exception):
    """Raised when a publisher page cannot be fetched or read safely."""


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def story_id_for_url(url: str) -> str:
    """Stable, fixed-width id for a story URL (same URL -> same page)."""
    raw = str(url or '').strip()
    if not raw:
        return ''
    return hashlib.sha1(raw.encode('utf-8', 'ignore')).hexdigest()[:16]


def _clean_url(value) -> str:
    url = str(value or '').strip()
    return url if url.lower().startswith(('http://', 'https://')) else ''


def _source_name(article) -> str:
    source = (article or {}).get('source')
    if isinstance(source, dict):
        return str(source.get('name') or '').strip()
    return str(source or '').strip()


def _row_to_story(row) -> dict:
    if row is None:
        return {}
    source = str(row['source'] or '').strip()
    return {
        'story_id': row['story_id'],
        'url': row['article_url'],
        'title': row['title'] or 'Untitled story',
        'description': row['description'] or '',
        'urlToImage': row['image'] or '',
        'publishedAt': row['published_at'] or '',
        'source': {'name': source} if source else {},
        'source_name': source or 'News Desk',
        'category': row['category'] or '',
    }


# ---------------------------------------------------------------------------
# Remembering provider results
# ---------------------------------------------------------------------------
def remember_articles(articles):
    """Assign story ids and persist new articles. Never raises.

    Returns the same list so it can be used inline in the fetch path. Existing
    rows are left untouched (one existence query, then a single commit).
    """
    items = list(articles or [])
    if not items:
        return articles

    prepared = []
    for article in items:
        if not isinstance(article, dict):
            continue
        url = _clean_url(article.get('url'))
        if not url:
            continue
        story_id = story_id_for_url(url)
        article['story_id'] = story_id
        article['story_url'] = f'/story/{story_id}'
        prepared.append({
            'story_id': story_id,
            'article_url': url,
            'title': str(article.get('title') or '').strip()[:400],
            'description': str(article.get('description') or '').strip()[:4000],
            'image': _clean_url(article.get('urlToImage'))[:1200],
            'source': _source_name(article)[:200],
            'category': str(article.get('category') or '').strip()[:120],
            'published_at': str(article.get('publishedAt') or '').strip()[:80],
            'fetched_at': time.time(),
        })
    if not prepared:
        return articles

    db = get_db()
    if db is None:
        return articles

    try:
        existing = _existing_ids(db, [item['story_id'] for item in prepared])
        new_items = [item for item in prepared if item['story_id'] not in existing]
        if not new_items:
            return articles

        db.executemany(
            """INSERT OR IGNORE INTO articles
               (story_id, article_url, title, description, image, source,
                category, published_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (item['story_id'], item['article_url'], item['title'], item['description'],
                 item['image'], item['source'], item['category'], item['published_at'],
                 item['fetched_at'])
                for item in new_items
            ],
        )
        _prune(db)
        db.commit()
    except Exception:
        log.debug('story cache write skipped', exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
    return articles


def _existing_ids(db, ids):
    found = set()
    unique = list(dict.fromkeys(ids))
    for start in range(0, len(unique), 400):
        chunk = unique[start:start + 400]
        placeholders = ','.join('?' for _ in chunk)
        try:
            rows = db.execute(
                f'SELECT story_id FROM articles WHERE story_id IN ({placeholders})', chunk
            ).fetchall()
        except Exception:
            return found
        found.update(row[0] for row in rows)
    return found


def _prune(db):
    """Drop old/unbookmarked stories and orphaned body extracts."""
    cutoff = time.time() - STORY_RETENTION_DAYS * 86400
    db.execute(
        """DELETE FROM articles
           WHERE fetched_at < ?
             AND article_url NOT IN (SELECT article_url FROM bookmarks)""",
        (cutoff,),
    )
    db.execute(
        """DELETE FROM articles
           WHERE story_id NOT IN (
             SELECT story_id FROM articles ORDER BY fetched_at DESC LIMIT ?
           )
             AND article_url NOT IN (SELECT article_url FROM bookmarks)""",
        (STORY_MAX_ROWS,),
    )
    db.execute(
        'DELETE FROM article_content WHERE story_id NOT IN (SELECT story_id FROM articles)'
    )


def get_story(story_id):
    if not story_id:
        return {}
    row = query_one(
        """SELECT story_id, article_url, title, description, image, source,
                  category, published_at
           FROM articles WHERE story_id = ?""",
        (str(story_id),),
    )
    return _row_to_story(row)


def get_story_by_url(url):
    url = _clean_url(url)
    if not url:
        return {}
    return get_story(story_id_for_url(url))


def get_related_articles(story, limit=4):
    """Recent stories from the same topic (or source), never the same one."""
    if not story:
        return []
    story_id = story.get('story_id')
    category = story.get('category') or ''
    source = story.get('source_name') or ''
    rows = []
    if category:
        rows = query_all(
            """SELECT story_id, article_url, title, description, image, source,
                      category, published_at
               FROM articles
               WHERE story_id != ? AND category = ?
               ORDER BY fetched_at DESC LIMIT ?""",
            (story_id, category, limit),
        )
    if len(rows) < limit and source:
        rows += query_all(
            """SELECT story_id, article_url, title, description, image, source,
                      category, published_at
               FROM articles
               WHERE story_id != ? AND source = ?
               ORDER BY fetched_at DESC LIMIT ?""",
            (story_id, source, limit - len(rows)),
        )
    related, seen = [], {story_id}
    for row in rows:
        if row['story_id'] in seen:
            continue
        seen.add(row['story_id'])
        related.append(_row_to_story(row))
        if len(related) >= limit:
            break
    return related


def available_story_ids(story_ids):
    ids = [str(i) for i in (story_ids or []) if i]
    if not ids:
        return set()
    db = get_db()
    if db is None:
        return set()
    try:
        return _existing_ids(db, ids)
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Readable body: cache + on-demand extraction
# ---------------------------------------------------------------------------
def get_story_content(story_id):
    if not story_id:
        return {}
    try:
        row = query_one(
            'SELECT story_id, body, status, fetched_at FROM article_content WHERE story_id = ?',
            (str(story_id),),
        )
    except Exception:
        return {}
    return dict(row) if row else {}


def parse_body_paragraphs(body):
    return [part.strip() for part in str(body or '').split('\n\n') if part.strip()]


def read_story_body(story):
    """Return ``{'status': ..., 'body': ...}`` for a story.

    Cached reads are instant. Failed attempts are retried after a cool-off so a
    paywalled publisher is not hammered on every page view.
    """
    from flask import current_app

    story_id = (story or {}).get('story_id')
    if not story_id:
        return {'status': 'missing', 'body': ''}

    cached = get_story_content(story_id)
    if cached:
        age = time.time() - float(cached.get('fetched_at') or 0)
        if cached.get('status') == 'ok' and cached.get('body'):
            return {'status': 'ok', 'body': cached['body']}
        if cached.get('status') and age < CONTENT_RETRY_SECONDS:
            return {'status': cached['status'], 'body': ''}

    if not current_app.config.get('STORY_BODY_ENABLED', True):
        return {'status': 'disabled', 'body': ''}

    timeout = float(current_app.config.get('STORY_BODY_TIMEOUT', 7))
    try:
        final_url, page = _safe_get_html(_reader_target(story.get('url')), timeout)
    except StoryFetchError as exc:
        log.info('story body unavailable for %s: %s', story_id, exc)
        _store_content(story_id, 'blocked' if 'http 4' in str(exc) else 'error', '')
        return {'status': 'error', 'body': ''}
    except Exception:
        log.info('story body fetch failed for %s', story_id, exc_info=True)
        _store_content(story_id, 'error', '')
        return {'status': 'error', 'body': ''}

    paragraphs = _extract_paragraphs(page)
    if not paragraphs:
        _store_content(story_id, 'empty', '')
        return {'status': 'empty', 'body': ''}

    body = '\n\n'.join(paragraphs)[:MAX_BODY_CHARS].strip()
    if not body:
        _store_content(story_id, 'empty', '')
        return {'status': 'empty', 'body': ''}
    _store_content(story_id, 'ok', body)
    return {'status': 'ok', 'body': body}


def _store_content(story_id, status, body):
    db = get_db()
    if db is None:
        return
    try:
        db.execute(
            """INSERT INTO article_content (story_id, body, status, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(story_id) DO UPDATE SET
                 body = excluded.body,
                 status = excluded.status,
                 fetched_at = excluded.fetched_at""",
            (story_id, body, status, time.time()),
        )
        db.commit()
    except Exception:
        log.debug('story content write skipped', exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


def _reader_target(url):
    """Prefer the publisher URL hidden inside a Google News redirect."""
    url = _clean_url(url)
    if not url:
        raise StoryFetchError('missing url')
    if 'news.google.' in url:
        try:
            from modules.news_fetcher import _canonical_link
            decoded = _canonical_link(url)
            if _clean_url(decoded):
                return decoded
        except Exception:
            pass
    return url


def _assert_public_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise StoryFetchError('invalid url')
    host = parts.hostname
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as exc:
        raise StoryFetchError('dns failed') from exc
    if not addresses:
        raise StoryFetchError('dns empty')
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise StoryFetchError('bad address') from exc
        if not ip.is_global:
            raise StoryFetchError('private address')


def _safe_get_html(url, timeout):
    """GET a publisher page with manual redirects and a private-network guard."""
    session = requests.Session()
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _assert_public_url(current)
            response = session.get(
                current,
                headers=_HEADERS,
                timeout=(_REQUEST_TIMEOUT[0], min(timeout, _REQUEST_TIMEOUT[1])),
                allow_redirects=False,
                stream=True,
            )
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        raise StoryFetchError('redirect without location')
                    current = urllib.parse.urljoin(current, location)
                    continue
                if response.status_code != 200:
                    raise StoryFetchError(f'http {response.status_code}')
                content_type = (response.headers.get('Content-Type') or '').lower()
                if 'html' not in content_type and 'xml' not in content_type:
                    raise StoryFetchError('not html')
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_HTML_BYTES:
                        break
                encoding = response.encoding or 'utf-8'
                try:
                    return current, b''.join(chunks).decode(encoding, 'replace')
                except LookupError:
                    return current, b''.join(chunks).decode('utf-8', 'replace')
            finally:
                response.close()
        raise StoryFetchError('too many redirects')
    finally:
        session.close()


class _ParagraphParser(HTMLParser):
    _SKIP = {
        'script', 'style', 'noscript', 'template', 'nav', 'header', 'footer',
        'aside', 'form', 'svg', 'button', 'select', 'option', 'iframe',
    }
    _BLOCK = {'p', 'h2', 'h3', 'blockquote', 'li'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs = []
        self._skip_depth = 0
        self._capturing = False
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == 'br' and self._capturing:
            self._buffer.append(' ')
        elif tag in self._BLOCK:
            self._flush()
            self._capturing = True

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self._BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth or not self._capturing:
            return
        if len(self._buffer) < 4000:
            self._buffer.append(data)

    def _flush(self):
        if self._buffer:
            text = re.sub(r'\s+', ' ', html_lib.unescape(''.join(self._buffer))).strip()
            if text:
                self.paragraphs.append(text)
        self._buffer = []
        self._capturing = False

    def close(self):
        super().close()
        self._flush()


def _extract_paragraphs(page):
    parser = _ParagraphParser()
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        log.debug('html parse failed', exc_info=True)
        return []
    paragraphs, seen = [], set()
    for text in parser.paragraphs:
        low = text.lower()
        if len(text) < MIN_PARAGRAPH_CHARS:
            continue
        if any(marker in low for marker in _BOILERPLATE):
            continue
        key = low[:90]
        if key in seen:
            continue
        seen.add(key)
        paragraphs.append(text[:1000])
    return paragraphs
