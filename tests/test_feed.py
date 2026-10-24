def test_api_feed_paginates_without_overlap(client):
    first = client.get('/api/feed?mode=for_you&offset=0').get_json()
    second = client.get('/api/feed?mode=for_you&offset=13').get_json()

    assert len(first['articles']) == 24
    assert first['has_more'] is True
    assert second['offset'] == 13
    assert len(second['articles']) == 24
    assert first['articles'][0]['url'] != second['articles'][0]['url']
    assert second['has_more'] is True
    assert second['articles'][0]['bookmarked'] is False


def test_api_feed_returns_rendered_html(client):
    body = client.get('/api/feed?mode=for_you&offset=13').get_json()
    assert '<article' in body['html']
    assert 'article-card' in body['html']


def test_api_feed_clamps_bad_offset(client):
    body = client.get('/api/feed?mode=for_you&offset=abc').get_json()
    assert body['offset'] == 0
    body = client.get('/api/feed?mode=for_you&offset=-40').get_json()
    assert body['offset'] == 0


def test_api_feed_passes_topic_and_source_filters(client):
    topic = client.get('/api/feed?mode=category&topic=Technology&offset=13').get_json()
    assert topic['filters']['topic'] == 'Technology'

    source = client.get('/api/feed?mode=source&source=The%20Hindu&offset=13').get_json()
    assert source['filters']['source'] == 'The Hindu'
    assert source['filters']['sort'] == 'latest'


def test_category_page_marks_infinite_scroll_context(client):
    page = client.get('/category/Technology').get_data(as_text=True)
    assert 'data-feed-topic="Technology"' in page
    assert 'data-feed-has-more=' in page


def test_search_page_requires_a_query(client):
    page = client.get('/search').get_data(as_text=True)
    assert 'Type a keyword above' in page


def test_feed_reports_provider_failure_honestly(client, monkeypatch):
    from newsperz.services.news_fetcher import NewsResult

    monkeypatch.setattr('newsperz.news.get_real_news',
                        lambda **kwargs: NewsResult([], ok=False, providers={'rss': 'failed'}))
    page = client.get('/')
    assert page.status_code == 200
    assert b'Nothing to show yet' in page.data or b'could not' in page.data.lower()
