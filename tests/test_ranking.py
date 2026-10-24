from datetime import datetime, timedelta, timezone

from newsperz.dsa.ranking import rank_articles, search_articles


def _article(title, description='', source='', url='', published=None):
    return {
        'title': title,
        'description': description,
        'source': {'name': source} if source else {'name': ''},
        'url': url,
        'publishedAt': published or '',
    }


def test_local_and_topic_matches_outrank_generic_story():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()
    articles = [
        _article('Generic market update', published=recent),
        _article('Pune metro expansion approved', 'Hinjewadi commuters rejoice', published=recent),
        _article('New AI chip announced for datacentres', 'Technology', published=recent),
    ]
    ranked = rank_articles(articles, topics=['Technology'], city='Pune', area='Hinjewadi')
    assert ranked[0]['title'].startswith('Pune metro')
    assert ranked[0]['is_local_match'] is True
    assert ranked[0]['match_score'] > ranked[-1]['match_score']


def test_empty_source_never_counts_as_preferred():
    article = _article('A story without provenance')
    ranked = rank_articles([article], publishers=['The Hindu'])
    assert ranked[0]['is_preferred_source'] is False


def test_undated_articles_get_no_recency_bonus():
    dated = _article('Fresh story about technology', published=datetime.now(timezone.utc).isoformat())
    undated = _article('Undated story about technology')
    ranked = rank_articles([undated, dated], topics=['Technology'])
    scores = {item['title']: item['match_score'] for item in ranked}
    assert scores['Undated story about technology'] < scores['Fresh story about technology']


def test_search_is_word_boundary_aware():
    articles = [
        _article('AI reshapes newsrooms'),
        _article('The airline industry is recovering'),
    ]
    results = search_articles(articles, 'AI')
    assert [item['title'] for item in results] == ['AI reshapes newsrooms']

    articles = [
        _article('Artificial intelligence reshapes newsrooms'),
        _article('The art market is recovering'),
    ]
    results = search_articles(articles, 'art')
    assert [item['title'] for item in results] == ['The art market is recovering']


def test_ranking_is_deterministic_for_ties():
    articles = [_article(f'Identical title number {i}', published='') for i in range(5)]
    first = [item['title'] for item in rank_articles(articles)]
    second = [item['title'] for item in rank_articles(articles)]
    assert first == second
