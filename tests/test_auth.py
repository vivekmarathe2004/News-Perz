def register(client, username='reader1', email='reader1@example.com', password='password123'):
    return client.post('/auth/register', data={
        'username': username,
        'email': email,
        'password': password,
    }, follow_redirects=False)


def test_register_logs_in_and_redirects_to_onboarding(client):
    response = register(client)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/interests')

    home = client.get('/')
    assert b'reader1' in home.data


def test_register_rejects_duplicate_email(client):
    register(client)
    client.post('/auth/logout')
    response = register(client, username='reader2')
    assert response.status_code == 200
    assert b'already registered' in response.data


def test_register_rejects_weak_password(client):
    response = register(client, password='short')
    assert response.status_code == 200
    assert b'at least 8 characters' in response.data


def test_login_and_safe_next(client):
    register(client)
    client.post('/auth/logout')

    response = client.post('/auth/login?next=/bookmarks', data={
        'email': 'reader1@example.com',
        'password': 'password123',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/bookmarks')


def test_login_throttles_repeated_failures(client, app):
    app.config['LOGIN_MAX_ATTEMPTS'] = 2
    for _ in range(2):
        response = client.post('/auth/login', data={
            'email': 'nobody@example.com',
            'password': 'wrong-password',
        })
        assert response.status_code == 200

    response = client.post('/auth/login', data={
        'email': 'nobody@example.com',
        'password': 'wrong-password',
    })
    assert response.status_code == 429


def test_logout_is_post_only(client):
    register(client)
    assert client.get('/auth/logout').status_code == 405
    assert client.post('/auth/logout').status_code == 302
    assert client.get('/').status_code == 200
