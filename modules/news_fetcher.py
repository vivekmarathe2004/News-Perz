"""
NewsPerz — Live News Fetcher Service
------------------------------------
Providers: Google News RSS (primary) and optional NewsAPI.org
(``/v2/everything`` only — the ``/top-headlines?country=`` endpoint is
enterprise-only and used to 4xx silently).

Public API (kept compatible with ``backend/news.py``)
    get_real_news(query=, category=, city=, area=, publisher=, limit=) -> NewsResult
        ``NewsResult`` is a *subclass of* ``list`` holding the article dicts, so
        ``len()``, iteration, slicing, ``or []`` truthiness and
        ``rank_articles(raw_articles, ...)`` all keep working unchanged.
        Extra attributes carried alongside the list:
            .ok        bool          -- False only when every provider failed
            .providers dict[str,str] -- provider name -> short status string
            .partial   bool          -- >=1 provider ok AND >=1 provider failed
    fetch_from_rss(feed_url, default_category='General', timeout=None) -> list[dict]
    fetch_news_api(query=None, category=None, country='in', page_size=30) -> list[dict]
    CATEGORIES_IMAGES_FOR(title, desc, fallback_cat) -> str   (deprecated shim)
    get_sample_articles() -> []                               (deprecated shim)
    _strip_html(text) -> str, CACHE_TTL (module fallback constant)

Nothing fabricated is ever returned: on total failure you get
``NewsResult([], ok=False, ...)``.

Configuration is read from ``flask.current_app.config`` with ``getattr``/``.get``
style defaults, so a missing key (or no app context at all) never raises.
Honoured keys (all optional, all read with getattr/.get defaults): NEWS_PROVIDER,
NEWS_API_KEY, NEWS_API_BASE, NEWS_LOCALE, NEWS_HL / NEWS_LOCALE_HL,
NEWS_GL / NEWS_LOCALE_GL, NEWS_CEID / NEWS_LOCALE_CEID, NEWS_TIMEOUT,
NEWS_CACHE_TTL, MAX_ARTICLES, PUBLISHERS.

The cache is two-layer: a tiny in-process LRU (<= 64 entries) in front of a
SQLite-backed ``feed_cache`` table accessed through ``database.db.get_db()``.
The stored payload is the *full deduped* list and the cache key deliberately
excludes ``limit``; slicing happens after the read. Every cache touch is
wrapped so a missing table, a missing/broken ``database.db`` or a ``None``
connection silently degrades to "no caching" instead of failing the request.
"""

import base64
import collections
import html as html_lib
import json
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:  # Flask is optional at import time (pure-function use / tests)
    from flask import current_app as _current_app
except Exception:  # pragma: no cover
    _current_app = None

logger = logging.getLogger(__name__)

# --- Module fallback constants (used when config is unavailable) -------------
PROVIDER_AUTO = 'auto'
DEFAULT_TIMEOUT = 4              # seconds, per HTTP request
DEFAULT_CACHE_TTL = 300          # seconds
DEFAULT_HL = 'en-IN'
DEFAULT_GL = 'IN'
DEFAULT_CEID = 'IN:en'
DEFAULT_NEWS_API_BASE = 'https://newsapi.org/v2'
DEFAULT_USER_AGENT = 'NewsPerz/1.0 (+https://localhost; news aggregator)'

MAX_RSS_WORKERS = 3              # concurrency cap for provider fetches
MIN_LIMIT = 1
MAX_LIMIT = 100                  # hard cap on input `limit`
CACHE_MAX_ROWS = 200             # SQLite rows kept (LRU-ish eviction)
MEM_CACHE_MAX = 64               # in-process entries
FEED_QUERY_MAX = 3               # max concurrent RSS queries per request

# Kept as a module attribute for backward compatibility (backend and modules never used it
# directly, but the name is documented as part of the old surface).
CACHE_TTL = DEFAULT_CACHE_TTL

HEADERS = {
    'User-Agent': DEFAULT_USER_AGENT,
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
}

# Editorial placeholder imagery (legitimate stock photos, keyed by topic).
CATEGORY_IMAGES = {
    'Technology': 'https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&auto=format&fit=crop&q=80',
    'Cybersecurity': 'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=800&auto=format&fit=crop&q=80',
    'Cricket': 'https://images.unsplash.com/photo-1531415074868-036b1c575351?w=800&auto=format&fit=crop&q=80',
    'Education': 'https://images.unsplash.com/photo-1523050854058-8df90110c9f1?w=800&auto=format&fit=crop&q=80',
    'Business': 'https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=800&auto=format&fit=crop&q=80',
    'Government schemes': 'https://images.unsplash.com/photo-1541872703-74c5e44368f9?w=800&auto=format&fit=crop&q=80',
    'Jobs': 'https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?w=800&auto=format&fit=crop&q=80',
    'Health': 'https://images.unsplash.com/photo-1505751172876-fa1923c5c528?w=800&auto=format&fit=crop&q=80',
    'Science': 'https://images.unsplash.com/photo-1507668077129-56e32842fceb?w=800&auto=format&fit=crop&q=80',
    'Entertainment': 'https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=800&auto=format&fit=crop&q=80',
    'Sports': 'https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=800&auto=format&fit=crop&q=80',
    'Politics': 'https://images.unsplash.com/photo-1540910419892-4a36d2c3266c?w=800&auto=format&fit=crop&q=80',
    'Traffic & transport': 'https://images.unsplash.com/photo-1506521781263-d8422e82f27a?w=800&auto=format&fit=crop&q=80',
    'Local': 'https://images.unsplash.com/photo-1570168007204-dfb528c6958f?w=800&auto=format&fit=crop&q=80',
    'Default': 'https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800&auto=format&fit=crop&q=80',
}

# Extra deterministic keyword -> category hints for the placeholder picker.
CATEGORY_KEYWORD_HINTS = {
    'Cybersecurity': ('cybersecurity', 'cyber attack', 'hackers', 'ransomware', 'data breach', 'malware'),
    'Cricket': ('cricket', 'bcci', 'ipl', 'test series', 'odai'),
    'Education': ('student', 'college', 'university', 'syllabus', 'curriculum', 'exam'),
    'Business': ('market', 'economy', 'stock', 'repo rate', 'startup', 'banking'),
    'Government schemes': ('scheme', 'government', 'policy', 'cabinet', 'subsidy'),
    'Jobs': ('recruit', 'vacancy', 'jobs', 'hiring', 'career'),
    'Health': ('hospital', 'health', 'disease', 'vaccine', 'medical'),
    'Science': ('science', 'research', 'isro', 'space'),
    'Entertainment': ('movie', 'film', 'bollywood', 'ott', 'music'),
    'Sports': ('tournament', 'match', 'team', 'sports'),
    'Politics': ('election', 'political', 'part', 'minister'),
    'Traffic & transport': ('metro', 'traffic', 'bus', 'railway', 'airport', 'highway'),
    'Technology': ('ai', 'tech', 'software', 'app', 'gadget', 'data center'),
}

_REMOVED_ARTIFACT = re.compile(r'\[\s*(?:Removed|~?[+\-]?\d+\s*(?:chars?|characters?)\s*)\]', re.I)
_TRAILING_ELLIPSE = re.compile(r'(?:\s*(?:…|\.\.\.|⋯))+\s*$')
_NON_ALNUM = re.compile(r'[^a-z0-9]+')
_GNEWS_ARTICLE_ID = re.compile(r'[/=]articles/([A-Za-z0-9_\-=%]+)', re.I)


class NewsResult(list):
    """A ``list`` of article dicts plus provider health metadata.

    Being a list subclass, existing call sites (``len()``, iteration, slicing,
    ``raw_articles or []``, ``rank_articles(raw_articles, ...)``) work without
    any change. ``.ok``/``.providers``/``.partial`` are extra attributes for
    callers that care about provider health.
    """

    __slots__ = ('ok', 'providers', 'partial')

    def __init__(self, items=(), ok=True, providers=None, partial=False):
        super().__init__(items or [])
        self.ok = bool(ok)
        self.providers = dict(providers or {})
        self.partial = bool(partial)

    def __repr__(self):
        return f'NewsResult(len={len(self)}, ok={self.ok}, partial={self.partial}, providers={self.providers})'


# ---------------------------------------------------------------------------
# Config helpers (never raise on missing keys / no app context)
# ---------------------------------------------------------------------------
def _config():
    """Return the live Flask app config, or an empty mapping outside a context."""
    try:
        cfg = _current_app.config if (_current_app and _current_app.config) else {}
    except Exception:
        cfg = {}
    return cfg or {}


def _cfg_get(cfg, key, default=None):
    """Read ``key`` from a config mapping via getattr()/get() with a fallback."""
    if not cfg:
        return default
    try:
        value = getattr(cfg, key, None)
        if value is None and hasattr(cfg, 'get'):
            value = cfg.get(key, None)
    except Exception:
        return default
    return default if value is None else value


def _cfg_int(cfg, key, default, minimum=None, maximum=None):
    raw = _cfg_get(cfg, key, default)
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _locale_params(cfg):
    """Resolve (hl, gl, ceid) from the configured locale params.

    Accepts ``NEWS_LOCALE`` (dict-ish or 'en-IN' string) plus either
    ``NEWS_HL``/``NEWS_GL``/``NEWS_CEID`` or the ``NEWS_LOCALE_HL``/
    ``NEWS_LOCALE_GL``/``NEWS_LOCALE_CEID`` spellings; every key is optional.
    """
    hl = gl = ceid = None
    loc = _cfg_get(cfg, 'NEWS_LOCALE', None)
    if isinstance(loc, dict):
        hl, gl, ceid = loc.get('hl'), loc.get('gl'), loc.get('ceid')
    elif isinstance(loc, str) and '-' in loc:
        parts = loc.split('-', 1)
        hl = loc
        gl = parts[1].upper() if len(parts) > 1 else None
    return (
        str(_cfg_get(cfg, 'NEWS_HL', None) or _cfg_get(cfg, 'NEWS_LOCALE_HL', hl) or DEFAULT_HL),
        str(_cfg_get(cfg, 'NEWS_GL', None) or _cfg_get(cfg, 'NEWS_LOCALE_GL', gl) or DEFAULT_GL),
        str(_cfg_get(cfg, 'NEWS_CEID', None) or _cfg_get(cfg, 'NEWS_LOCALE_CEID', ceid) or DEFAULT_CEID),
    )


def _known_publishers(cfg):
    """Lower-cased publisher names, used to validate trailing " - Source" splits.

    Read from ``PUBLISHERS`` / ``NEWS_PUBLISHERS`` in the app config, else the
    ``config.PUBLISHERS`` module list; empty set if neither is reachable (the
    splitter then uses its conservative no-digits heuristic).
    """
    pubs = _cfg_get(cfg, 'PUBLISHERS', None) or _cfg_get(cfg, 'NEWS_PUBLISHERS', None)
    if not pubs:
        try:
            from config import PUBLISHERS as _module_pubs
            pubs = _module_pubs
        except Exception:
            pubs = []
    out = set()
    for p in (pubs or []):
        try:
            name = str(p).strip().lower()
        except Exception:
            continue
        if name:
            out.add(name)
    return out


def _request_headers():
    return dict(HEADERS)


# ---------------------------------------------------------------------------
# Text / date / image helpers
# ---------------------------------------------------------------------------
def _strip_html(text):
    """Remove HTML tags and unescape entities. Tolerates None / non-str input."""
    if not text:
        return ''
    try:
        clean = re.sub(r'<[^>]+>', ' ', str(text))
    except Exception:
        clean = str(text)
    clean = html_lib.unescape(clean)
    clean = _REMOVED_ARTIFACT.sub(' ', clean)
    clean = _TRAILING_ELLIPSE.sub('', clean)
    return re.sub(r'\s+', ' ', clean).strip()


def _clean_title(text):
    """Unescape entities / strip tags / drop NewsAPI truncation artifacts."""
    return _strip_html(text)


def _normalize_image(url):
    """Return the URL if it is a usable http(s) image, else ''."""
    if not url:
        return ''
    candidate = str(url).strip()
    if len(candidate) < 12 or candidate.lower().startswith('data:'):
        return ''
    low = candidate.lower()
    if not (low.startswith('https://') or low.startswith('http://')):
        return ''
    try:
        parts = urllib.parse.urlsplit(candidate)
    except Exception:
        return ''
    if not parts.netloc or '.' not in parts.netloc:
        return ''
    return candidate


def _pick_category_image(title, desc, fallback_cat='General', existing_image=None):
    """Contextual Unsplash placeholder.

    Skipped entirely when the article already ships a usable ``urlToImage``.
    Matching is deterministic (insertion order of CATEGORY_IMAGES, then an
    explicit keyword hint table) and always falls back to the fallback
    category, then to the 'Default' image.
    """
    usable = _normalize_image(existing_image)
    if usable:
        return usable

    combined = f'{(title or "")} {(desc or "")}'.lower()
    for cat in CATEGORY_IMAGES:
        if cat == 'Default':
            continue
        if cat.lower() in combined:
            return CATEGORY_IMAGES[cat]
    for cat, hints in CATEGORY_KEYWORD_HINTS.items():
        if any(h in combined for h in hints):
            return CATEGORY_IMAGES.get(cat, CATEGORY_IMAGES['Default'])
    return CATEGORY_IMAGES.get(fallback_cat or 'General', CATEGORY_IMAGES['Default'])


# Backward-compatible alias (only ever referenced from this module today).
def CATEGORIES_IMAGES_FOR(title, desc, fallback_cat='General', existing_image=None):
    """DEPRECATED shim for ``_pick_category_image``; kept for external callers."""
    return _pick_category_image(title, desc, fallback_cat, existing_image)


def _parse_rfc822_date(date_str):
    """RFC 822 -> ISO 8601 (UTC aware). Returns '' when absent or unparseable.

    Returning '' (instead of "now") keeps undated articles from getting a fake
    freshness bonus in the ranker.
    """
    if not date_str:
        return ''
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(str(date_str).strip())
    except Exception:
        return ''
    if dt is None:
        return ''
    return _as_utc_iso(dt)


def _as_utc_iso(dt):
    try:
        from datetime import timezone as _tz
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(_tz.utc).isoformat()
    except Exception:
        return ''


def _normalize_iso_date(value):
    """Normalise an arbitrary ISO-8601-ish string to UTC ISO; '' if unusable."""
    if not value:
        return ''
    raw = str(value).strip()
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        return raw if raw[:1].isdigit() else ''
    return _as_utc_iso(dt)


def _is_dated(article):
    return bool((article or {}).get('publishedAt'))


def _has_image(article):
    return bool(_normalize_image((article or {}).get('urlToImage')))


def _title_key(title):
    """Fuzzy dedupe key: first 50 alphanumerics of the lowercased title."""
    cleaned = _NON_ALNUM.sub('', str(title or '').lower())
    return cleaned[:50]


def _canonical_link(url):
    """Canonical dedupe identity for a URL.

    The href itself is left untouched; only this derived key is used for
    deduping Google News ``/articles/<id>`` redirect wrappers.
    """
    if not url:
        return ''
    link = str(url).strip()
    match = _GNEWS_ARTICLE_ID.search(link)
    if not match:
        return link
    token = urllib.parse.unquote(match.group(1))
    try:
        padded = token + '=' * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        text = decoded.decode('utf-8', 'ignore')
        found = re.search(r'https?://[^\s\x00-\x1f"\\]+', text)
        if found:
            return found.group(0).rstrip('\\/').strip()
    except Exception:
        pass
    return token


def _split_title_source(title, source_name, known_publishers):
    """Return (title, source_name) with a conservative trailing " - " split."""
    title = str(title or '').strip()
    source_name = str(source_name or '').strip()
    if source_name or ' - ' not in title:
        return title, source_name

    head, tail = title.rsplit(' - ', 1)
    tail = tail.strip()
    head = head.strip()
    if not head or not _looks_like_publisher(tail, known_publishers):
        return title, ''
    return head, tail


def _looks_like_publisher(tail, known_publishers):
    """A trailing segment is a publisher only if short, word-limited and credible.

    When a publisher list is available it is REQUIRED (membership only) — that
    is what stops "India - Australia series opener" from being mis-split.
    Without a list we fall back to the conservative no-digits heuristic.
    """
    if not tail or len(tail) > 40:
        return False
    if len(tail.split()) > 4:
        return False
    low = tail.lower().strip(' .-–|')
    if not low:
        return False
    if known_publishers:
        return any(low == known or low.startswith(known) or known in low
                   for known in known_publishers)
    return not any(ch.isdigit() for ch in low)


# ---------------------------------------------------------------------------
# RSS provider
# ---------------------------------------------------------------------------
def _element_text(item, tag):
    try:
        el = item.find(tag)
    except Exception:
        return ''
    if el is None or el.text is None:
        return ''
    return str(el.text)


def _fetch_rss_items(feed_url, default_category, timeout, known_publishers, cfg):
    """Internal RSS worker returning ``(articles, provider_status)``.

    Never raises: failures are logged at warning level with the feed URL and
    reported through the status string so the caller can build ``.providers``.
    """
    articles = []
    try:
        resp = requests.get(feed_url, headers=_request_headers(), timeout=timeout)
        if resp.status_code != 200:
            logger.warning('RSS fetch failed (http %s): %s', resp.status_code, feed_url)
            return articles, f'failed(http {resp.status_code})'

        root = ET.fromstring(resp.content)
    except Exception as exc:
        logger.warning('RSS fetch error for %s: %s', feed_url, exc)
        return articles, 'failed(network)'

    try:
        items = root.findall('.//item')
    except Exception as exc:
        logger.warning('RSS parse error for %s: %s', feed_url, exc)
        return articles, 'failed(parse)'

    for item in items:
        try:
            raw_title = _element_text(item, 'title')
            link = _element_text(item, 'link').strip()
            pub_date = _element_text(item, 'pubDate')
            description = _element_text(item, 'description')
            source_el = item.find('source')
            source_name = ''
            if source_el is not None and source_el.text:
                source_name = str(source_el.text).strip()

            title = _clean_title(raw_title)
            title, source_name = _split_title_source(title, source_name, known_publishers)
            clean_desc = _strip_html(description)
            if source_name and clean_desc.endswith(source_name):
                clean_desc = clean_desc[: -len(source_name)].strip()

            if not title or len(title) < 10:
                continue

            articles.append({
                'title': title,
                'description': clean_desc or title,
                'url': link,
                'urlToImage': _pick_category_image(title, clean_desc, default_category),
                'publishedAt': _parse_rfc822_date(pub_date),
                'source': {'name': source_name or 'News Desk'},
                'category': default_category,
                'provider': 'rss',
            })
        except Exception as exc:  # a single malformed item must not kill the feed
            logger.warning('RSS item skipped in %s: %s', feed_url, exc)

    return articles, (f'ok({len(articles)})' if articles else 'empty')


def fetch_from_rss(feed_url, default_category='General', timeout=None, known_publishers=None, cfg=None):
    """Fetch and parse one RSS feed into standardized article dicts (list only).

    Public helper kept for compatibility/backward shim use; the aggregation
    path uses ``_fetch_rss_items`` so it can also report provider health.
    """
    cfg = cfg or _config()
    if timeout is None:
        timeout = _cfg_int(cfg, 'NEWS_TIMEOUT', DEFAULT_TIMEOUT, minimum=1, maximum=30)
    if known_publishers is None:
        known_publishers = _known_publishers(cfg)
    articles, _status = _fetch_rss_items(feed_url, default_category, timeout, known_publishers, cfg)
    return articles


def _google_news_params(cfg):
    hl, gl, ceid = _locale_params(cfg)
    return f'hl={urllib.parse.quote(hl)}&gl={urllib.parse.quote(gl)}&ceid={urllib.parse.quote(ceid)}'


def _google_news_search_url(query, cfg):
    encoded = urllib.parse.quote_plus(query)
    return f'https://news.google.com/rss/search?q={encoded}&{_google_news_params(cfg)}'


def _google_news_top_url(cfg):
    return f'https://news.google.com/rss?{_google_news_params(cfg)}'


# ---------------------------------------------------------------------------
# NewsAPI provider
# ---------------------------------------------------------------------------
def _newsapi_endpoint(base):
    base = str(base or DEFAULT_NEWS_API_BASE).strip().rstrip('/')
    if base.endswith('/v2'):
        return f'{base}/everything'
    if '/v2' in base:
        return f'{base}/everything'
    return f'{base}/v2/everything'


def _newsapi_request(query, page_size, timeout, cfg):
    """Return (articles, status_string). Never raises."""
    api_key = _cfg_get(cfg, 'NEWS_API_KEY', '') or ''
    api_key = str(api_key).strip()
    provider = str(_cfg_get(cfg, 'NEWS_PROVIDER', PROVIDER_AUTO) or PROVIDER_AUTO).lower().strip()
    if provider not in (PROVIDER_AUTO, 'newsapi'):
        return [], 'skipped(provider)'
    if not api_key:
        return [], 'skipped(no key)'
    if not query:
        return [], 'skipped(no query)'

    base = _cfg_get(cfg, 'NEWS_API_BASE', DEFAULT_NEWS_API_BASE)
    url = _newsapi_endpoint(base)
    params = {
        'apiKey': api_key,
        'q': str(query),
        'sortBy': 'publishedAt',
        'pageSize': max(10, min(int(page_size or 30), 100)),
    }
    hl = _locale_params(cfg)[0]
    if hl:
        params['language'] = str(hl).split('-')[-1].lower() or 'en'

    try:
        resp = requests.get(url, params=params, headers=_request_headers(), timeout=timeout)
    except Exception as exc:
        logger.warning('NewsAPI request error: %s', exc)
        return [], 'failed(network)'

    if resp.status_code != 200:
        logger.warning('NewsAPI http %s', resp.status_code)
        return [], f'failed(http {resp.status_code})'
    try:
        data = resp.json()
    except Exception as exc:
        logger.warning('NewsAPI bad json: %s', exc)
        return [], 'failed(bad json)'
    if not isinstance(data, dict) or data.get('status') != 'ok':
        code = (data or {}).get('code', 'unknown') if isinstance(data, dict) else 'unknown'
        logger.warning('NewsAPI status != ok (%s)', code)
        return [], f'failed({code})'

    fallback_cat = query
    articles = []
    for raw in (data.get('articles') or []):
        if not isinstance(raw, dict):
            continue
        title = _clean_title(raw.get('title'))
        if not title or '[Removed]' in str(raw.get('title') or ''):
            continue
        desc = _strip_html(raw.get('description'))
        source_obj = raw.get('source') or {}
        source_name = ''
        if isinstance(source_obj, dict):
            source_name = str(source_obj.get('name') or '').strip()
        image = _normalize_image(raw.get('urlToImage'))
        articles.append({
            'title': title,
            'description': desc or title,
            'url': str(raw.get('url') or '').strip(),
            'urlToImage': _pick_category_image(title, desc, fallback_cat, existing_image=image),
            'publishedAt': _normalize_iso_date(raw.get('publishedAt')),
            'source': {'name': source_name or 'NewsAPI'},
            'category': raw.get('category') or fallback_cat,
            'provider': 'newsapi',
        })
    return articles, f'ok({len(articles)})'


def fetch_news_api(query=None, category=None, country='in', page_size=30, timeout=None, cfg=None):
    """NewsAPI.org helper. Uses ``/v2/everything`` with ``q`` only.

    Returns a (possibly empty) list; failures are logged and reported via
    ``get_real_news``'s ``.providers`` status map rather than raised.
    """
    cfg = cfg or _config()
    if timeout is None:
        timeout = _cfg_int(cfg, 'NEWS_TIMEOUT', DEFAULT_TIMEOUT, minimum=1, maximum=30)
    term = query or category or ''
    items, _status = _newsapi_request(term, page_size, timeout, cfg)
    return items


# ---------------------------------------------------------------------------
# Cache: in-process LRU + SQLite (feed_cache) via database.db.get_db()
# ---------------------------------------------------------------------------
_MEM_CACHE = collections.OrderedDict()   # key -> (expires_at, articles)
_MEM_LOCK = threading.Lock()


def _cache_key(query, category, city, area, publisher):
    """Cache key deliberately EXCLUDES ``limit`` (the payload is the full list)."""
    def norm(v):
        return str(v).strip().lower() if v else ''
    return 'np:news:v1:' + '|'.join(
        f'{name}={norm(value)}'
        for name, value in (('q', query), ('cat', category), ('city', city), ('area', area), ('pub', publisher))
    )


def _mem_get(key, now):
    with _MEM_LOCK:
        entry = _MEM_CACHE.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at <= now:
            _MEM_CACHE.pop(key, None)
            return None
        _MEM_CACHE.move_to_end(key)
        return payload


def _mem_set(key, payload, expires_at):
    with _MEM_LOCK:
        _MEM_CACHE[key] = (expires_at, payload)
        _MEM_CACHE.move_to_end(key)
        while len(_MEM_CACHE) > MEM_CACHE_MAX:
            _MEM_CACHE.popitem(last=False)


def _get_db():
    """Lazily resolve ``database.db.get_db()``; None when unavailable."""
    try:
        from database.db import get_db
    except Exception as exc:
        logger.debug('database.db unavailable (%s); caching disabled', exc)
        return None
    try:
        return get_db()
    except Exception as exc:
        logger.debug('get_db() failed: %s', exc)
        return None


def _row_value(row, index, name):
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        pass
    try:
        return row[index]
    except Exception:
        return None


def _sqlite_get(key, now):
    db = _get_db()
    if db is None:
        return None
    try:
        cursor = db.cursor()
    except Exception as exc:
        logger.debug('cache cursor failed: %s', exc)
        return None
    row = None
    try:
        try:
            cursor.execute(
                'SELECT payload, expires_at, hits FROM feed_cache WHERE cache_key = ? AND expires_at > ?',
                (key, now),
            )
            row = cursor.fetchone()
        except Exception as exc:
            logger.debug('cache read failed (table missing?): %s', exc)
            return None
        if row is None:
            return None
        try:
            cursor.execute('UPDATE feed_cache SET hits = hits + 1 WHERE cache_key = ?', (key,))
            _commit(db)
        except Exception:
            pass
    finally:
        try:
            cursor.close()
        except Exception:
            pass

    payload = _row_value(row, 0, 'payload')
    try:
        articles = json.loads(payload)
    except Exception as exc:
        logger.debug('cache payload undecodable: %s', exc)
        return None
    return articles if isinstance(articles, list) else None


def _commit(db):
    try:
        db.commit()
    except Exception:
        pass


def _sqlite_put(key, payload_json, expires_at, now):
    db = _get_db()
    if db is None:
        return
    try:
        cursor = db.cursor()
    except Exception as exc:
        logger.debug('cache cursor failed: %s', exc)
        return
    try:
        try:
            cursor.execute(
                'INSERT INTO feed_cache (cache_key, payload, expires_at, hits) VALUES (?, ?, ?, 0) '
                'ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload, '
                'expires_at = excluded.expires_at, hits = excluded.hits',
                (key, payload_json, expires_at),
            )
            # Opportunistic maintenance on every write: prune expired, then
            # LRU-ish eviction by hits/expires_at down to CACHE_MAX_ROWS.
            cursor.execute('DELETE FROM feed_cache WHERE expires_at < ?', (now,))
            cursor.execute(
                'DELETE FROM feed_cache WHERE cache_key NOT IN ('
                'SELECT cache_key FROM feed_cache ORDER BY hits DESC, expires_at DESC LIMIT ?)',
                (CACHE_MAX_ROWS,),
            )
            _commit(db)
        except Exception as exc:
            logger.debug('cache write failed (table missing?): %s', exc)
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def _cache_get(key, now, ttl):
    payload = _mem_get(key, now)
    if payload is None:
        payload = _sqlite_get(key, now)
        if payload is not None:
            _mem_set(key, payload, now + ttl)
    return payload


def _cache_put(key, payload, now, ttl):
    expires_at = now + ttl
    _mem_set(key, payload, expires_at)
    try:
        encoded = json.dumps(payload, default=str, separators=(',', ':'))
        if len(encoded) > 2_000_000:  # keep a pathological payload off the disk
            return
        _sqlite_put(key, encoded, expires_at, now)
    except Exception as exc:
        logger.debug('cache encode failed: %s', exc)


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------
def _better_candidate(new, old):
    """Per-provider bias: prefer a real image + a parseable date."""
    new_score = (1 if _has_image(new) else 0) + (1 if _is_dated(new) else 0)
    old_score = (1 if _has_image(old) else 0) + (1 if _is_dated(old) else 0)
    return new_score > old_score


def _dedupe(articles):
    seen_urls = set()
    canonical_links = set()
    title_index = {}   # fuzzy key -> index in output
    deduped = []

    for article in articles:
        if not isinstance(article, dict):
            continue
        url = str(article.get('url') or '').strip()
        t_key = _title_key(article.get('title'))
        canonical = _canonical_link(url) if url else ''

        if url and url in seen_urls:
            continue
        if canonical and canonical in canonical_links:
            continue

        if t_key and t_key in title_index:
            idx = title_index[t_key]
            existing = deduped[idx]
            if _better_candidate(article, existing):
                deduped[idx] = dict(article)
                if url:
                    seen_urls.add(url)
                if canonical:
                    canonical_links.add(canonical)
            continue

        if url:
            seen_urls.add(url)
        if canonical:
            canonical_links.add(canonical)
        if t_key:
            title_index[t_key] = len(deduped)
        deduped.append(article)

    return deduped


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def _build_rss_tasks(query, category, city, area, publisher, cfg):
    """Return a list of (label, feed_url, default_category) work items."""
    tasks = []

    def add(q, cat):
        if len(tasks) >= FEED_QUERY_MAX:
            return
        tasks.append((f'rss:{q[:60]}', _google_news_search_url(q, cfg), cat))

    if query:
        add(str(query).strip(), category or 'General')
        if publisher:
            add(f'{str(query).strip()} source:{publisher}', category or 'General')
    elif city or area:
        location = f'{area} {city}'.strip() if area else str(city).strip()
        parts = [location]
        if category:
            parts.append(str(category))
        if publisher:
            parts.append(f'source:{publisher}')
        add(' '.join(parts).strip(), category or 'Local')
    elif category:
        q_str = str(category)
        if publisher:
            q_str += f' source:{publisher}'
        add(q_str, category)
    elif publisher:
        add(f'source:{publisher}', 'General')
    else:
        if len(tasks) < FEED_QUERY_MAX:
            tasks.append(('rss:top-stories', _google_news_top_url(cfg), 'Top Stories'))

    return tasks


def _clamp_limit(value, cfg=None):
    """Cap the caller's ``limit`` into MIN_LIMIT..MAX_ARTICLES (default 100)."""
    ceiling = MAX_LIMIT
    if cfg is not None:
        ceiling = _cfg_int(cfg, 'MAX_ARTICLES', MAX_LIMIT, minimum=MIN_LIMIT, maximum=MAX_LIMIT)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = 35
    return max(MIN_LIMIT, min(ceiling, limit))


def get_real_news(query=None, category=None, city=None, area=None, publisher=None, limit=35):
    """Fetch live news from Google News RSS (+ NewsAPI when configured).

    Returns a ``NewsResult`` — a ``list`` subclass of article dicts with
    ``.ok`` / ``.providers`` / ``.partial`` attributes — so callers treating it
    as a plain list need no changes. Concurrent providers, a hard wall-clock
    budget, two-layer caching and honest failure semantics: no fabricated
    articles are ever injected.
    """
    cfg = _config()
    limit = _clamp_limit(limit, cfg)
    timeout = _cfg_int(cfg, 'NEWS_TIMEOUT', DEFAULT_TIMEOUT, minimum=1, maximum=30)
    ttl = _cfg_int(cfg, 'NEWS_CACHE_TTL', DEFAULT_CACHE_TTL, minimum=0, maximum=86400)
    provider_mode = str(_cfg_get(cfg, 'NEWS_PROVIDER', PROVIDER_AUTO) or PROVIDER_AUTO).lower().strip()
    now = time.time()

    key = _cache_key(query, category, city, area, publisher)
    if ttl > 0:
        cached = _cache_get(key, now, ttl)
        if cached is not None:
            providers = {'cache': 'hit'}
            return NewsResult(cached[:limit], ok=True, providers=providers, partial=False)

    tasks = []
    if provider_mode in (PROVIDER_AUTO, 'rss'):
        known = _known_publishers(cfg)
        for label, feed_url, default_cat in _build_rss_tasks(query, category, city, area, publisher, cfg):
            tasks.append((label, _fetch_rss_items, (feed_url, default_cat, timeout, known, cfg), {}))

    if provider_mode in (PROVIDER_AUTO, 'newsapi'):
        api_key = str(_cfg_get(cfg, 'NEWS_API_KEY', '') or '').strip()
        search_term = (query or category or (f'{area} {city}'.strip() if (city or area) else ''))
        if not api_key:
            tasks.append(('newsapi', lambda **kw: ([], 'skipped(no key)'), (), {}))
        else:
            tasks.append(('newsapi', _newsapi_request, (search_term, max(limit, 20), timeout, cfg), {}))

    collected = []
    providers = {}
    failed = 0
    succeeded = 0
    budget = max(1.0, timeout * 2.0)

    def _record(label, status, items):
        """Bookkeeping for one provider outcome (never raises)."""
        nonlocal failed, succeeded
        status = status if isinstance(status, str) else str(status)
        providers[label] = status
        if status.startswith('failed'):
            failed += 1
        elif not status.startswith('skipped'):
            succeeded += 1
        collected.extend(items or [])

    if tasks:
        executor = ThreadPoolExecutor(max_workers=MAX_RSS_WORKERS, thread_name_prefix='newsperz-fetch')
        futures = {}
        try:
            for label, func, args, kwargs in tasks:
                try:
                    futures[executor.submit(func, *args, **kwargs)] = label
                except Exception as exc:
                    providers[label] = 'failed(submit)'
                    failed += 1
                    logger.warning('Could not schedule provider %s: %s', label, exc)

            try:
                for future in as_completed(futures, timeout=budget):
                    label = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        providers[label] = 'failed(error)'
                        failed += 1
                        logger.warning('Provider %s raised: %s', label, exc)
                        continue
                    if isinstance(result, tuple):
                        items, status = result
                    else:
                        items, status = result, f'ok({len(result or [])})'
                    _record(label, status, items)
            except Exception as exc:  # concurrent.futures.TimeoutError (budget hit)
                logger.warning('Provider budget (%ss) exceeded: %s', budget, exc)
        finally:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

        for future, label in futures.items():
            if label in providers:
                continue
            if not future.done():
                # Blow the wall-clock budget: report it as a provider failure so
                # `.partial` is honest, without waiting for the slow request.
                providers[label] = 'failed(timeout)'
                failed += 1
                logger.warning('Provider %s exceeded the %ss budget', label, budget)
                continue
            try:
                result = future.result()
                if isinstance(result, tuple):
                    items, status = result
                else:
                    items, status = result, f'ok({len(result or [])})'
                _record(label, status, items)
            except Exception as exc:
                providers[label] = 'failed(error)'
                failed += 1
                logger.warning('Provider %s late failure: %s', label, exc)

    deduped = _dedupe(collected)

    if deduped and ttl > 0:
        _cache_put(key, deduped, now, ttl)

    partial = bool(succeeded and failed)
    if not deduped:
        # NO fabricated content: an honest empty result instead of fake headlines.
        ok = (succeeded > 0 and failed == 0)
        return NewsResult([], ok=ok, providers=providers or {'rss': 'empty'}, partial=partial)

    return NewsResult(deduped[:limit], ok=True, providers=providers, partial=partial)


def get_sample_articles():
    """DEPRECATED: always returns [].

    Fabricated fallback headlines were removed — offline/partial failures are
    reported through ``NewsResult.ok`` / ``.providers`` / ``.partial`` instead.
    Kept only so stale imports keep working.
    """
    return []
