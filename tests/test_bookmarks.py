import sqlite3


def _add(client, index=1, **extra):
    payload = {'url': f'https://example.com/saved/{index}', 'title': f'Saved story {index}',
               'source': 'The Hindu', 'description': 'A saved description', 'image': '',
               'publishedAt': '2026-10-24T09:00:00+00:00'}
    payload.update(extra)
    return client.post('/bookmark/add', json=payload)


def test_bookmark_rejects_non_web_urls(client):
    response = client.post('/bookmark/add', json={'url': 'javascript:alert(1)', 'title': 'Nope'})
    assert response.status_code == 400
    assert response.get_json()['reason'] == 'invalid_url'
    assert b'Nope' not in client.get('/bookmarks').data


def test_guest_bookmark_round_trip(client):
    response = _add(client)
    assert response.get_json()['storage'] == 'session'

    page = client.get('/bookmarks')
    assert b'Saved story 1' in page.data

    response = client.post('/bookmark/remove', json={'url': 'https://example.com/saved/1'})
    assert response.get_json()['status'] == 'ok'
    assert b'Saved story 1' not in client.get('/bookmarks').data


def test_guest_bookmarks_survive_trimming(client):
    for index in range(20):
        _add(client, index, description='x' * 400, image='https://img.example.com/' + 'i' * 200)

    page = client.get('/bookmarks')
    assert page.status_code == 200
    saved = page.data.count(b'saved-card')
    assert 1 <= saved <= 12


def test_guest_bookmarks_merge_into_new_account(client, app):
    _add(client, 7)
    response = client.post('/auth/register', data={
        'username': 'merger', 'email': 'merger@example.com', 'password': 'password123',
    })
    assert response.status_code == 302

    db_path = app.config['DATABASE_PATH']
    with sqlite3.connect(db_path) as con:
        rows = con.execute('SELECT article_url, article_title FROM bookmarks').fetchall()
    assert ('https://example.com/saved/7', 'Saved story 7') in rows

    assert b'Saved story 7' in client.get('/bookmarks').data


def test_logged_in_bookmarks_use_database(client, app):
    client.post('/auth/register', data={
        'username': 'dbreader', 'email': 'dbreader@example.com', 'password': 'password123',
    })
    _add(client, 3)

    with sqlite3.connect(app.config['DATABASE_PATH']) as con:
        count = con.execute('SELECT COUNT(*) FROM bookmarks').fetchone()[0]
    assert count == 1

    client.post('/bookmark/remove', json={'url': 'https://example.com/saved/3'})
    with sqlite3.connect(app.config['DATABASE_PATH']) as con:
        count = con.execute('SELECT COUNT(*) FROM bookmarks').fetchone()[0]
    assert count == 0
