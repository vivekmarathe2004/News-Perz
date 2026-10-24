"""Feed, preferences and bookmark routes.

`get_real_news` returns a list subclass, so the older `raw_articles = get_real_news(...)
try: raw_articles = raw_articles or [] except Exception: ...` shape keeps working even if
the provider module changes again.
"""

import logging
import math
from dataclasses import dataclass, field

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf.csrf import CSRFError

from newsperz.bookmarks import add_bookmark, get_bookmarked_urls, get_saved_items, remove_bookmark
from newsperz.config import CATEGORIES, CITIES_WITH_AREAS, PUBLISHERS, SORT_OPTIONS
from newsperz.db import is_available, scalar
from newsperz.dsa.ranking import compute_article_score, rank_articles, search_articles
from newsperz.prefs import VALID_SORTS, get_user_preferences, save_preferences
from newsperz.services.news_fetcher import get_real_news
from newsperz.story_store import (
    get_related_articles,
    get_story,
    get_story_content,
    parse_body_paragraphs,
    read_story_body,
    remember_articles,
)

log = logging.getLogger(__name__)

news_bp = Blueprint('news', __name__)

PAGE_LIMITS = {'index': 60, 'local': 60, 'category': 60, 'source': 60, 'search': 60, 'api': 60}
API_PAGE_SIZE = 24
INITIAL_CARDS = 12


@dataclass
class FeedQuery:
    """Resolved filters for one page: stored preferences plus any on-page overrides."""
    location: dict
    topics: list
    publishers: list
    pref_sort: str
    city: str = ''
    area: str = ''
    topic: str = ''
    source: str = ''
    query: str = ''
    sort: str = ''
    page_mode: str = 'for_you'
    source_filter: bool = False
    fetch_location: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def uses_filters(self) -> bool:
        return bool(self.topic or self.source_filter)

    @property
    def ranking_topics(self) -> list:
        return [self.topic] if self.topic else self.topics

    @property
    def ranking_publishers(self) -> list:
        return [self.source] if self.source_filter else self.publishers


def _requested(key, default=''):
    return (request.args.get(key) or default).strip()


def build_query(page_mode, *, allow_area=True, use_location=True, allow_query=False, default_sort=None):
    """Merge stored preferences with the query-string filters on this page."""
    location, topics, publishers, pref_sort = get_user_preferences()
    sort = _requested('sort', default_sort or pref_sort)
    if sort not in VALID_SORTS:
        sort = default_sort or 'relevance'

    topic = _requested('topic')
    source = _requested('source')
    return FeedQuery(
        location=location,
        topics=topics,
        publishers=publishers,
        pref_sort=pref_sort,
        city=_requested('city', location.get('city', '') if use_location else ''),
        area=_requested('area', location.get('area', '') if (use_location and allow_area) else ''),
        topic=topic if topic in CATEGORIES else '',
        source=source,
        query=_requested('q') if allow_query else '',
        sort=sort,
        page_mode=page_mode,
        source_filter=bool(source),
    )


def _fetch_kwargs(q: FeedQuery):
    if q.query:
        return {'query': q.query}
    return {
        'category': q.topic or None,
        # Topic and publisher pages fetch broadly and let the ranker apply the
        # location boost; the location-first feeds narrow at the source instead.
        'city': (q.city or None) if q.fetch_location else None,
        'area': (q.area or None) if q.fetch_location else None,
        'publisher': q.source or None,
    }


def fetch_ranked(q: FeedQuery, limit, *, offset=0):
    """Fetch from the provider, rank with the DSA engine, and slice the page."""
    raw = []
    ok, partial = False, False
    try:
        result = get_real_news(limit=max(limit + offset, limit), **_fetch_kwargs(q))
        # NewsResult is a list subclass carrying provider status; plain lists
        # (older provider modules) degrade to "unknown but usable".
        ok = getattr(result, 'ok', True)
        partial = getattr(result, 'partial', False)
        raw = list(result or [])
    except Exception:
        log.exception('news provider failed')
        raw = []

    if q.query:
        raw = search_articles(raw, q.query)

    # Persist the fetched window so every card can open an in-app story page.
    raw = remember_articles(raw)

    ranked = rank_articles(
        articles=raw,
        topics=q.ranking_topics,
        city=q.city or None,
        area=q.area or None,
        publishers=q.ranking_publishers,
        sort_by=q.sort,
    )
    return ranked, ok, partial


def _page_context(q: FeedQuery):
    return {
        'location': q.location,
        'active_city': q.city,
        'active_area': q.area,
        'active_topic': q.topic,
        'active_source': q.source,
        'active_sort': q.sort,
        'topics': q.topics,
        'publishers': q.publishers,
        'query': q.query,
        'page_mode': q.page_mode,
    }


def _render(template, q: FeedQuery, *, featured_story=None, articles=None, count=None,
            total=None, offset=0, page_size=0, has_more=None, extra=None):
    context = _page_context(q)
    context.update({
        'featured_story': featured_story,
        'articles': articles if articles is not None else [],
        'story_count': count if count is not None else (len(articles or []) + (1 if featured_story else 0)),
        'total_stories': total if total is not None else context['story_count'],
        'offset': offset,
        'page_size': page_size,
        'has_more': has_more if has_more is not None else (bool(total) and offset < total),
        'all_categories': CATEGORIES,
        'all_publishers': PUBLISHERS,
        'cities_with_areas': CITIES_WITH_AREAS,
        'bookmarked_urls': get_bookmarked_urls(),
        'filters': {
            'mode': q.page_mode, 'city': q.city, 'area': q.area, 'topic': q.topic,
            'source': q.source, 'query': q.query, 'sort': q.sort,
        },
    })
    if extra:
        context.update(extra)
    return render_template(template, **context)


def _initial_slice(ranked, has_hero):
    """Render a first batch in HTML; the rest arrives via /api/feed (infinite scroll)."""
    start = 1 if has_hero else 0
    cards = ranked[start:start + INITIAL_CARDS]
    return cards, start + len(cards), len(ranked) > start + len(cards)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@news_bp.route('/')
def index():
    """For You: personalised homepage ranked by the DSA engine."""
    q = build_query('for_you')
    ranked, ok, partial = fetch_ranked(q, PAGE_LIMITS['index'])
    hero = ranked[0] if ranked else None
    cards, next_offset, has_more = _initial_slice(ranked, has_hero=True)
    return _render('index.html', q, featured_story=hero, articles=cards,
                   count=len(ranked), total=len(ranked), offset=next_offset, has_more=has_more,
                   extra={'feed_ok': ok, 'feed_partial': partial})


@news_bp.route('/local')
def local_news():
    """Local news for the selected city and neighbourhood."""
    q = build_query('local')
    ranked, ok, partial = fetch_ranked(q, PAGE_LIMITS['local'])
    hero = ranked[0] if ranked else None
    cards, next_offset, has_more = _initial_slice(ranked, has_hero=True)
    return _render('local.html', q, featured_story=hero, articles=cards,
                   count=len(ranked), total=len(ranked), offset=next_offset, has_more=has_more,
                   extra={'available_areas': CITIES_WITH_AREAS.get(q.city, []),
                          'feed_ok': ok, 'feed_partial': partial})


@news_bp.route('/category/<category_name>')
def category(category_name):
    """Topic page."""
    q = build_query('category')
    q.topic = category_name
    q.fetch_location = False
    ranked, ok, partial = fetch_ranked(q, PAGE_LIMITS['category'])
    hero = ranked[0] if ranked else None
    cards, next_offset, has_more = _initial_slice(ranked, has_hero=True)
    return _render('category.html', q, featured_story=hero, articles=cards,
                   count=len(ranked), total=len(ranked), offset=next_offset, has_more=has_more,
                   extra={'category': category_name, 'is_source': False,
                          'feed_ok': ok, 'feed_partial': partial})


@news_bp.route('/source/<source_name>')
def source_news(source_name):
    """Publisher page."""
    q = build_query('source', default_sort='latest')
    q.source = source_name
    q.source_filter = True
    q.fetch_location = False
    ranked, ok, partial = fetch_ranked(q, PAGE_LIMITS['source'])
    hero = ranked[0] if ranked else None
    cards, next_offset, has_more = _initial_slice(ranked, has_hero=True)
    return _render('source.html', q, featured_story=hero, articles=cards,
                   count=len(ranked), total=len(ranked), offset=next_offset, has_more=has_more,
                   extra={'category': source_name, 'is_source': True,
                          'feed_ok': ok, 'feed_partial': partial})


@news_bp.route('/search')
def search():
    """Keyword search with city / topic / source / sort filters."""
    q = build_query('search', allow_query=True, use_location=False)
    articles, count, offset, has_more = [], 0, 0, False
    if q.query:
        ranked, ok, partial = fetch_ranked(q, PAGE_LIMITS['search'])
        articles, offset, has_more = _initial_slice(ranked, has_hero=False)
        count = len(ranked)
    else:
        ok, partial = True, False
    return _render('search.html', q, articles=articles, count=count, total=count,
                   offset=offset, has_more=has_more,
                   extra={'feed_ok': ok, 'feed_partial': partial,
                          'available_areas': CITIES_WITH_AREAS.get(q.city, [])})


@news_bp.route('/api/feed')
def api_feed():
    """JSON feed for infinite scroll: a slice of the same ranked window as the page."""
    mode = request.args.get('mode', 'for_you')
    q = build_query(mode, allow_query=True, default_sort='latest' if mode == 'source' else None)
    q.fetch_location = mode not in ('category', 'source')
    try:
        offset = max(int(request.args.get('offset', 0) or 0), 0)
    except ValueError:
        offset = 0
    offset = min(offset, PAGE_LIMITS['api'])

    ranked, ok, partial = fetch_ranked(q, PAGE_LIMITS['api'])
    page = ranked[offset:offset + API_PAGE_SIZE]
    has_more = offset + API_PAGE_SIZE < len(ranked)
    bookmarked = get_bookmarked_urls()
    html = render_template('partials/feed_items.html', articles=page, bookmarked_urls=bookmarked) if page else ''
    return jsonify({
        'status': 'ok',
        'html': html,
        'articles': [_article_payload(a, bookmarked) for a in page],
        'total': len(ranked),
        'offset': offset,
        'page_size': API_PAGE_SIZE,
        'next_offset': offset + API_PAGE_SIZE if has_more else None,
        'has_more': has_more,
        'source_ok': ok,
        'partial': partial,
        'filters': {'city': q.city, 'area': q.area, 'topic': q.topic, 'source': q.source,
                    'query': q.query, 'sort': q.sort},
    })


def _article_payload(article, bookmarked_urls=None):
    bookmarked_urls = bookmarked_urls if bookmarked_urls is not None else set()
    source = article.get('source')
    return {
        'title': article.get('title', ''),
        'description': article.get('description', ''),
        'url': article.get('url', ''),
        'image': article.get('urlToImage', ''),
        'publishedAt': article.get('publishedAt', ''),
        'source': (source.get('name', '') if isinstance(source, dict) else str(source or '')),
        'match_score': article.get('match_score', 0),
        'match_reasons': article.get('match_reasons', []),
        'is_local_match': article.get('is_local_match', False),
        'is_preferred_source': article.get('is_preferred_source', False),
        'bookmarked': article.get('url', '') in bookmarked_urls,
    }


@news_bp.route('/interests', methods=['GET', 'POST'])
def interests():
    """Customize my news: location, topics, publishers and sort order."""
    if request.method == 'POST':
        stored = save_preferences(
            city=request.form.get('city'),
            area=request.form.get('area'),
            state=request.form.get('state'),
            country=request.form.get('country'),
            topics=request.form.getlist('topics'),
            publishers=request.form.getlist('publishers'),
            sort_by=request.form.get('sort_by'),
        )
        if stored:
            flash('Your news preferences have been updated!', 'success')
        else:
            flash('We could not save your preferences — the database is unreachable.', 'error')
        return redirect(url_for('news.index'))

    location, current_topics, current_publishers, current_sort = get_user_preferences()
    return render_template(
        'interests.html',
        location=location,
        current_topics=current_topics,
        current_publishers=current_publishers,
        current_sort=current_sort,
        all_categories=CATEGORIES,
        all_publishers=PUBLISHERS,
        cities_with_areas=CITIES_WITH_AREAS,
        sort_options=SORT_OPTIONS,
        page_mode='preferences',
        story_count=0,
        articles=[],
    )


@news_bp.route('/bookmarks')
def bookmarks():
    """Saved articles."""
    saved = get_saved_items()
    return render_template(
        'bookmarks.html',
        bookmarks=saved,
        all_categories=CATEGORIES,
        all_publishers=PUBLISHERS,
        page_mode='bookmarks',
        guest_only=not current_user.is_authenticated,
        bookmark_limit=None,
        articles=[],
        story_count=len(saved),
    )


@news_bp.route('/bookmark/add', methods=['POST'])
def add_bookmark_route():
    """AJAX endpoint to bookmark an article."""
    data = request.get_json(silent=True) or {}
    result = add_bookmark(
        url=data.get('url') or '',
        title=data.get('title') or '',
        source=data.get('source') or '',
        description=data.get('description') or '',
        image=data.get('image') or '',
        published_at=data.get('publishedAt') or '',
    )
    if not result.get('saved'):
        status = 400 if result.get('reason') in ('missing_url', 'invalid_url') else 503
        return jsonify({'status': 'error', 'reason': result.get('reason'),
                        'message': 'Could not save that story.'}), status
    return jsonify({'status': 'ok', 'storage': result.get('storage'),
                    'existing': result.get('existing', False)})


@news_bp.route('/bookmark/remove', methods=['POST'])
def remove_bookmark_route():
    """AJAX endpoint to remove an article from bookmarks."""
    data = request.get_json(silent=True) or {}
    result = remove_bookmark(url=data.get('url') or '')
    if not result.get('saved'):
        status = 400 if result.get('reason') == 'missing_url' else 503
        return jsonify({'status': 'error', 'reason': result.get('reason'),
                        'message': 'Could not remove that story.'}), status
    return jsonify({'status': 'ok', 'storage': result.get('storage')})


@news_bp.errorhandler(CSRFError)
def handle_csrf_error(error):
    from newsperz.errors import wants_json
    if wants_json():
        return jsonify({'status': 'error', 'code': 400, 'message': 'Your session expired. Reload the page.'}), 400
    return render_template('errors/error.html', code=400, error_title='Session expired',
                           error_message='Please reload the page and try again.',
                           all_categories=CATEGORIES, all_publishers=PUBLISHERS,
                           location=None, topics=[], page_mode='error', current_year=None), 400


def _story_reading_minutes(text):
    words = len(str(text or '').split())
    return max(1, math.ceil(words / 220)) if words else 1


@news_bp.route('/story/<story_id>')
def story(story_id):
    """Read a story inside NewsPerz, with the publisher one click away."""
    item = get_story(story_id)
    if not item:
        return render_template(
            'story.html',
            story=None,
            story_id=story_id,
            related=[],
            paragraphs=[],
            content_status='missing',
            match_reasons=[],
            reading_minutes=0,
            bookmarked_urls=[],
            page_mode='story',
        ), 404

    location, topics, publishers, _sort = get_user_preferences()
    _score, reasons = compute_article_score(
        item,
        topics=topics,
        city=location.get('city'),
        area=location.get('area'),
        publishers=publishers,
    )
    content = get_story_content(story_id)
    paragraphs = []
    if content.get('status') == 'ok' and content.get('body'):
        paragraphs = parse_body_paragraphs(content['body'])
    reading_minutes = _story_reading_minutes(
        ' '.join(paragraphs) if paragraphs else item.get('description')
    )
    return render_template(
        'story.html',
        story=item,
        story_id=story_id,
        related=get_related_articles(item, limit=4),
        paragraphs=paragraphs,
        content_status=content.get('status', '') if content else '',
        match_reasons=reasons,
        reading_minutes=reading_minutes,
        bookmarked_urls=get_bookmarked_urls(),
        share_url=url_for('news.story', story_id=story_id, _external=True),
        page_mode='story',
    )


@news_bp.route('/api/story/<story_id>/body')
def story_body(story_id):
    """Lazily extract readable text for a story and cache it."""
    item = get_story(story_id)
    if not item:
        return jsonify({'status': 'missing', 'paragraphs': []}), 404
    result = read_story_body(item)
    paragraphs = parse_body_paragraphs(result.get('body')) if result.get('status') == 'ok' else []
    return jsonify({
        'status': result.get('status', 'error'),
        'story_id': story_id,
        'paragraphs': paragraphs,
        'reading_minutes': _story_reading_minutes(' '.join(paragraphs)),
    })


@news_bp.route('/healthz')
def healthz():
    db_up = is_available()
    return jsonify({
        'status': 'ok' if db_up else 'degraded',
        'database': db_up,
        'schema_version': scalar('SELECT MAX(version) FROM schema_version', (), default=0) if db_up else None,
        'provider': current_app.config.get('NEWS_PROVIDER', 'auto'),
    })
