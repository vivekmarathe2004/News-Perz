"""
NewsPerz — DSA Recommendation Engine
------------------------------------
Deterministic, rule-based scoring with *capped* (i.e. honest) weights:

  Location   +5  area match preferred over city match; only ever counted once
  Topics     +4  a topic found in the title, +2 if only in the description.
                 Each topic is scored once (title wins, description is a
                 weaker second). The TOTAL topic contribution is capped at +4,
                 so six matching topics can no longer inflate a score to 24+.
  Publisher  +3  one preferred-source match only. An empty source name never
                 matches (the old ``'' in p_lower`` bug awarded +3 + a bogus
                 "Preferred source" badge to unknown-provenance articles).
  Keywords   +2  per matching keyword, total capped at +4.
  Recency    +2  <= 12h, +1 <= 24h, otherwise 0. Undated articles (the fetcher
                 emits ``publishedAt == ''``) get 0, future-dated and >30-day
                 old items also get 0 — never a fake freshness bonus.

Maximum achievable score is therefore 5 + 4 + 3 + 4 + 2 = 18.

Sorting (all keys are (int, float, int) tuples — raw dicts are never compared —
and ties fall back to the original input index, so the order is deterministic):
  'relevance' (default; unknown values are treated as this): score DESC, date DESC
  'latest'  : date DESC, score DESC
  'sources' : preferred-source flag DESC, score DESC, date DESC
An optional ``top_k`` uses ``heapq.nlargest`` on the relevance path.

Public API (imported by newsperz/news.py):
  rank_articles(articles, topics=, city=, area=, publishers=, keywords=,
                sort_by='relevance', top_k=None) -> list[dict]
      Each returned dict is a shallow copy annotated with:
        'match_score' (int), 'match_reasons' (list[str]),
        'is_local_match' (bool), 'is_preferred_source' (bool)
      Badge label formats are stable and rendered by templates:
        "Local match: {area}" / "Topic: {t}" / "Preferred source: {name}" /
        "Keyword: {kw}"
  search_articles(articles, query) -> list[dict]
      Token-based (word-boundary aware) search, AND pass first then OR
      fallback; items are shallow copies annotated with 'search_hits' (int).
  compute_article_score(article, topics=, city=, area=, publishers=,
                        keywords=) -> (int, list[str])
  parse_date(article) -> datetime (tz-aware UTC; epoch sentinel when undated)
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass
from datetime import datetime, timezone

# --- Capped weights (see module docstring) ---------------------------------
LOCATION_BONUS = 5
TOPIC_TITLE_BONUS = 4
TOPIC_DESC_BONUS = 2
TOPIC_TOTAL_CAP = 4
PUBLISHER_BONUS = 3
KEYWORD_BONUS = 2
KEYWORD_TOTAL_CAP = 4
RECENCY_FRESH_BONUS = 2          # <= 12h
RECENCY_RECENT_BONUS = 1         # <= 24h
HOURS_FRESH = 12.0
HOURS_RECENT = 24.0
MAX_USEFUL_AGE_HOURS = 24.0 * 30

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_EPOCH_TS = 0.0

_WORD_RE = re.compile(r'\w+', re.UNICODE)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(article: dict) -> datetime:
    """Safely parse ``publishedAt`` into a tz-aware UTC datetime.

    Missing / empty / unparseable dates return the epoch sentinel
    (``1970-01-01T00:00:00+00:00``) instead of "now", so undated articles are
    treated as undated (zero recency bonus, sorted last).
    """
    dt = _article_dt(article)
    return dt if dt is not None else EPOCH


def _article_dt(article: dict):
    """Return a tz-aware UTC datetime, or None when the article is undated."""
    if not isinstance(article, dict):
        return None
    published = article.get('publishedAt', '')
    if not published:
        return None
    raw = str(published).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        return None
    return _to_utc(dt)


def _timestamp(dt) -> float:
    """Monotonic-ish epoch seconds used for fast, comparable sort keys."""
    if dt is None:
        return _EPOCH_TS
    try:
        return dt.timestamp()
    except Exception:
        return _EPOCH_TS


def _recency_bonus(dt, now: datetime) -> int:
    """+2 <= 12h, +1 <= 24h, else 0. Undated / future / >30d => 0."""
    if dt is None:
        return 0
    age_hours = (now - dt).total_seconds() / 3600.0
    if age_hours < 0 or age_hours > MAX_USEFUL_AGE_HOURS:
        return 0
    if age_hours <= HOURS_FRESH:
        return RECENCY_FRESH_BONUS
    if age_hours <= HOURS_RECENT:
        return RECENCY_RECENT_BONUS
    return 0


# ---------------------------------------------------------------------------
# Preferences, precomputed once per rank_articles() call
# ---------------------------------------------------------------------------
@dataclass
class _Prefs:
    """Lowercased/pre-parsed user preferences (built once, reused per article)."""
    topics: tuple = ()               # original display labels
    topic_terms: tuple = ()          # (label, lowered label)
    city: str = ''
    city_lower: str = ''
    area: str = ''
    area_lower: str = ''
    publishers: tuple = ()           # original labels
    publisher_terms: tuple = ()      # (label, lowered label) with non-empty parts
    keywords: tuple = ()             # stripped original labels
    keyword_terms: tuple = ()        # (label, lowered) non-empty only
    has_location: bool = False
    has_topics: bool = False
    has_publishers: bool = False
    has_keywords: bool = False


def _build_prefs(topics=None, city=None, area=None, publishers=None, keywords=None) -> _Prefs:
    clean_topics = tuple(
        str(t).strip() for t in (topics or []) if t and str(t).strip()
    )
    clean_pubs = tuple(
        str(p).strip() for p in (publishers or []) if p and str(p).strip()
    )
    clean_kw = tuple(
        str(k).strip() for k in (keywords or []) if k and str(k).strip()
    )
    city_s = str(city).strip() if city else ''
    area_s = str(area).strip() if area else ''

    pub_terms = tuple(
        (p, p.lower()) for p in clean_pubs
        if any(ch.isalnum() for ch in p)
    )
    return _Prefs(
        topics=clean_topics,
        topic_terms=tuple((t, t.lower()) for t in clean_topics),
        city=city_s,
        city_lower=city_s.lower(),
        area=area_s,
        area_lower=area_s.lower(),
        publishers=clean_pubs,
        publisher_terms=pub_terms,
        keywords=clean_kw,
        keyword_terms=tuple((k, k.lower()) for k in clean_kw),
        has_location=bool(area_s or city_s),
        has_topics=bool(clean_topics),
        has_publishers=bool(pub_terms),
        has_keywords=bool(clean_kw),
    )


def _article_text(article: dict):
    """Return (title_lower, description_lower, combined_lower) once per article."""
    if not isinstance(article, dict):
        return '', '', ''
    title = str(article.get('title') or '').lower()
    desc = str(article.get('description') or '').lower()
    return title, desc, f'{title} {desc}'


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _score_article(article: dict, prefs: _Prefs, now: datetime) -> dict:
    """Score one article; returns score, reasons and the real boolean flags."""
    title, desc, combined = _article_text(article)

    score = 0
    reasons = []
    is_local_match = False
    is_preferred_source = False
    matched_location = ''

    # 1. Location (+5, area wins over city, never both)
    if prefs.has_location:
        if prefs.area_lower and prefs.area_lower in combined:
            matched_location = prefs.area
        elif prefs.city_lower and prefs.city_lower in combined:
            matched_location = prefs.city
        if matched_location:
            score += LOCATION_BONUS
            reasons.append(f'Local match: {matched_location}')
            is_local_match = True

    # 2. Topics: each topic scored once (title preferred, description weaker);
    #    the whole category contribution is capped at TOPIC_TOTAL_CAP.
    if prefs.has_topics:
        topic_score = 0
        for label, lowered in prefs.topic_terms:
            if lowered in title:
                topic_score += TOPIC_TITLE_BONUS
            elif lowered in desc:
                topic_score += TOPIC_DESC_BONUS
            else:
                continue
            reasons.append(f'Topic: {label}')
        if topic_score:
            score += min(topic_score, TOPIC_TOTAL_CAP)

    # 3. Preferred publisher (+3, one match only, both sides must be non-empty)
    source_obj = article.get('source') if isinstance(article, dict) else None
    if isinstance(source_obj, dict):
        source_name = str(source_obj.get('name') or '').strip()
    elif source_obj:
        source_name = str(source_obj).strip()
    else:
        source_name = ''
    source_lower = source_name.lower()

    if prefs.has_publishers and source_lower and any(ch.isalnum() for ch in source_lower):
        for label, lowered in prefs.publisher_terms:
            if not lowered:
                continue
            if lowered == source_lower or lowered in source_lower or source_lower in lowered:
                score += PUBLISHER_BONUS
                reasons.append(f'Preferred source: {source_name}')
                is_preferred_source = True
                break

    # 4. Keywords (+2 each, capped at KEYWORD_TOTAL_CAP)
    if prefs.has_keywords:
        keyword_score = 0
        for label, lowered in prefs.keyword_terms:
            if lowered and lowered in combined:
                keyword_score += KEYWORD_BONUS
                reasons.append(f'Keyword: {label}')
        if keyword_score:
            score += min(keyword_score, KEYWORD_TOTAL_CAP)

    # 5. Recency (+0..+2; undated gets 0)
    dt = _article_dt(article)
    score += _recency_bonus(dt, now)

    return {
        'score': int(score),
        'reasons': reasons,
        'is_local_match': is_local_match,
        'is_preferred_source': is_preferred_source,
        'ts': _timestamp(dt),
    }


def compute_article_score(
    article: dict,
    topics: list = None,
    city: str = None,
    area: str = None,
    publishers: list = None,
    keywords: list = None,
) -> tuple:
    """Score an article against user preferences. Returns ``(score, reasons)``.

    Weights are capped exactly as in ``rank_articles`` (see module docstring);
    pass ``topics``/``publishers``/``keywords`` as lists of labels.
    """
    prefs = _build_prefs(topics, city, area, publishers, keywords)
    detail = _score_article(article, prefs, datetime.now(timezone.utc))
    return detail['score'], detail['reasons']


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def rank_articles(
    articles: list,
    topics: list = None,
    city: str = None,
    area: str = None,
    publishers: list = None,
    keywords: list = None,
    sort_by: str = 'relevance',
    top_k: int = None,
) -> list:
    """Rank and annotate articles (Timsort / ``heapq.nlargest``).

    Returns a list of shallow copies annotated with ``match_score`` (int),
    ``match_reasons`` (list[str]), ``is_local_match`` (bool) and
    ``is_preferred_source`` (bool). The input list and its dicts are never
    mutated. ``top_k`` (optional) keeps only the k best on the relevance path;
    ``None`` means a full sort.
    """
    if not articles:
        return []

    prefs = _build_prefs(topics, city, area, publishers, keywords)
    now = datetime.now(timezone.utc)

    scored = []
    for index, article in enumerate(articles):
        if not isinstance(article, dict):
            continue
        detail = _score_article(article, prefs, now)
        item = dict(article)  # shallow copy: annotate without mutating input
        item['match_score'] = detail['score']
        item['match_reasons'] = detail['reasons']
        # Real booleans computed during scoring (not string-matched labels).
        item['is_local_match'] = detail['is_local_match']
        item['is_preferred_source'] = detail['is_preferred_source']
        scored.append((detail['score'], detail['ts'], 1 if detail['is_preferred_source'] else 0, index, item))

    if not scored:
        return []

    mode = str(sort_by or 'relevance').lower().strip()

    try:
        k = int(top_k) if top_k is not None else None
    except (TypeError, ValueError):
        k = None

    if mode == 'latest':
        # Newest first, then score, then input order.
        scored.sort(key=lambda r: (-r[1], -r[0], r[3]))
        return [r[4] for r in scored]

    if mode == 'sources':
        # Preferred publishers first, then score, then newest, then input order.
        scored.sort(key=lambda r: (-r[2], -r[0], -r[1], r[3]))
        return [r[4] for r in scored]

    # Default: 'relevance' (anything unknown falls back here).
    if k is not None and 0 < k < len(scored):
        # heapq.nlargest avoids the full O(n log n) sort; deterministic because
        # the comparator includes the input index, and the k winners are re-sorted.
        selected = heapq.nlargest(k, scored, key=lambda r: (r[0], r[1], -r[3]))
        selected.sort(key=lambda r: (-r[0], -r[1], r[3]))
        return [r[4] for r in selected]

    scored.sort(key=lambda r: (-r[0], -r[1], r[3]))
    return [r[4] for r in scored]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def _compile_terms(query: str):
    """Word-boundary-aware regexes for a query: (phrase, [(term, compiled)])."""
    raw = str(query or '').strip().lower()
    if not raw:
        return '', []
    words = [w for w in _WORD_RE.findall(raw) if w]
    terms = []
    seen = set()
    for word in words:
        if word in seen:
            continue
        seen.add(word)
        try:
            pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.UNICODE)
        except re.error:
            continue
        terms.append((word, pattern))
    if not terms:
        return '', []
    phrase = ' '.join(words) if len(words) > 1 else terms[0][0]
    return phrase, terms


def search_articles(articles: list, query: str) -> list:
    """Token-based linear-scan search, O(n) with one compiled regex per term.

    Matching is word-boundary aware, so a query for ``Science`` does not match
    an article whose only overlap is a different phrase such as
    ``science fiction`` being requested as ``science fiction`` (partial-phrase
    queries fall back to the OR pass). Items matching *every* term (AND pass)
    come first — with an extra hit for the exact full phrase — followed by the
    partial (OR) matches. Each result is a shallow copy annotated with
    ``search_hits`` (int); the input list and its dicts are never mutated.
    """
    if not articles:
        return []

    raw_query = str(query or '').strip()
    phrase, terms = _compile_terms(raw_query)
    if not terms:
        if not raw_query:
            # Nothing to filter on: return neutral copies so callers can always
            # read `search_hits`.
            return [dict(a, search_hits=0) for a in articles if isinstance(a, dict)]
        return []  # a query with no word characters matches nothing

    strict = []   # matched every term (AND)
    partial = []  # matched at least one term (OR)

    for article in articles:
        if not isinstance(article, dict):
            continue
        title, desc, combined = _article_text(article)
        blob = f'{title} {desc}'
        hits = 0
        for _term, pattern in terms:
            if pattern.search(blob):
                hits += 1
        if hits == 0:
            continue

        exact_phrase = bool(phrase) and phrase in blob
        item = dict(article)
        item['search_hits'] = hits + (1 if exact_phrase else 0)
        if hits == len(terms):
            strict.append((-item['search_hits'], len(strict), item))
        else:
            partial.append((-item['search_hits'], len(partial), item))

    strict.sort(key=lambda r: (r[0], r[1]))
    partial.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in strict] + [r[2] for r in partial]
