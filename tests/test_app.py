import pytest

from newsperz import create_app


PAGES = [
    '/',
    '/local',
    '/category/Technology',
    '/source/The%20Hindu',
    '/search?q=technology',
    '/search',
    '/interests',
    '/bookmarks',
    '/auth/login',
    '/auth/register',
]


@pytest.mark.parametrize('path', PAGES)
def test_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b'NewsPerz' in response.data


def test_unknown_page_uses_error_layout(client):
    response = client.get('/definitely-not-a-page')
    assert response.status_code == 404
    assert b'404' in response.data
    assert b'site-header' in response.data


def test_healthz(client):
    body = client.get('/healthz').get_json()
    assert body['status'] == 'ok'
    assert body['database'] is True
    assert body['schema_version'] == 1


def test_security_headers(client):
    response = client.get('/')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'DENY'
    csp = response.headers['Content-Security-Policy']
    assert "default-src 'self'" in csp
    assert "script-src 'self' 'nonce-" in csp
    assert "'unsafe-inline'" not in csp


def test_csrf_is_enforced(tmp_path):
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'csrf-test',
        'DATABASE_PATH': str(tmp_path / 'csrf.db'),
    })
    client = app.test_client()
    assert client.post('/interests', data={'city': 'Pune'}).status_code == 400
    assert client.post('/bookmark/add', json={'url': 'https://example.com/x'}).status_code == 400


def test_safe_next_rejects_external_targets():
    from newsperz.errors import is_safe_next

    assert is_safe_next('/bookmarks') is True
    assert is_safe_next('//evil.example') is False
    assert is_safe_next('https://evil.example') is False
    assert is_safe_next('/\\evil') is False
    assert is_safe_next('') is False
