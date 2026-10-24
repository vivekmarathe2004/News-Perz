import pytest

from newsperz import create_app
from newsperz.services.news_fetcher import NewsResult


def _article(index, category='Technology', city='Pune'):
    return {
        'title': f'Test story number {index} about {category} in {city}',
        'description': f'A detailed description for story {index}.',
        'url': f'https://example.com/story/{index}',
        'urlToImage': '',
        'publishedAt': f'2026-10-{(index % 24) + 1:02d}T09:00:00+00:00',
        'source': {'name': 'The Hindu' if index % 2 else 'BBC News'},
        'category': category,
    }


@pytest.fixture
def sample_articles():
    return [_article(i) for i in range(40)]


@pytest.fixture
def app(tmp_path, monkeypatch, sample_articles):
    application = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-secret',
        'DATABASE_PATH': str(tmp_path / 'test.db'),
    })
    application.config['LOGIN_MAX_ATTEMPTS'] = 7

    def fake_fetch(limit=35, **kwargs):
        return NewsResult(sample_articles[:limit], ok=True, providers={'test': 'ok'})

    monkeypatch.setattr('newsperz.news.get_real_news', fake_fetch)
    return application


@pytest.fixture
def client(app):
    return app.test_client()
