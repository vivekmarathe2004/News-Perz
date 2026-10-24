# Deployment

This is the practical checklist for putting NewsPerz on a server. For local setup, see the README.

## Before you start

You need Python 3.9+, a virtualenv with `requirements.txt` installed, and a writable `instance/` directory. The app creates the SQLite database and runs migrations on first boot.

Set these in `.env` (or in the process environment):

```bash
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=<64-char-random-hex>
SESSION_COOKIE_SECURE=1
```

`FLASK_ENV=production` without a `SECRET_KEY` makes the app refuse to start. `SESSION_COOKIE_SECURE=1` requires HTTPS, so leave it off if you are testing over plain HTTP.

Optional settings are documented in the README. The ones that usually matter in production are `DATABASE_PATH`, `NEWS_TIMEOUT`, `NEWS_CACHE_TTL` and the `NEWS_LOCALE_*` values.

## Run it

Waitress is the production server. Behind a reverse proxy, bind it to loopback:

```bash
.venv/bin/waitress-serve --listen=127.0.0.1:8080 --call "backend:create_app"
```

Waitress defaults to `0.0.0.0:8080`, so always pass `--listen` explicitly. The default thread count is fine for a small deployment; the app also caps provider concurrency internally.

## Reverse proxy

Terminate TLS at nginx, Caddy or whatever you already run, and forward to Waitress. A minimal nginx server block:

```nginx
server {
    listen 80;
    server_name news.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }
}
```

The app does not enable Werkzeug's `ProxyFix`. If you need `url_for(..., _external=True)` links (the story share URL) to use `https` behind a TLS proxy, add `ProxyFix` to the app or rewrite the scheme at the proxy.

## systemd example

```ini
[Unit]
Description=NewsPerz
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/srv/newsperz
EnvironmentFile=/srv/newsperz/.env
ExecStart=/srv/newsperz/.venv/bin/waitress-serve --listen=127.0.0.1:8080 --call backend:create_app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Make sure `instance/` is owned by the service user, or the database cannot be created.

## Health and logs

`GET /healthz` returns a small JSON object:

```json
{
  "status": "ok",
  "database": true,
  "schema_version": 2,
  "provider": "auto"
}
```

`status` becomes `degraded` when the database is unavailable. Point your uptime check at this endpoint, not at `/`, because `/` depends on an external news provider.

Waitress logs to stdout/stderr. Under systemd, read them with `journalctl -u newsperz -f`.

## Upgrades

1. Stop the service.
2. Back up the database.
3. Pull the new code and install any new requirements.
4. Start the service. Pending migrations run automatically at boot.

You can also run migrations by hand while the service is stopped:

```bash
.venv/bin/flask --app app init-db
```

Never edit an applied migration. Add a new one to `database/migrations.py` and bump `SCHEMA_VERSION` instead.

## Backups

SQLite runs in WAL mode, so copying the database file while the app is running can give you a torn copy. Use the backup command:

```bash
sqlite3 instance/newsperz.db ".backup '/var/backups/newsperz-$(date +%F).db'"
```

That produces a consistent snapshot. Keep `instance/` out of the web root and out of version control.

## SQLite limits

One box, one Waitress process, one writer at a time. That is fine for a personal or small site. If you need multiple app servers or a lot of concurrent writes, move to Postgres or MySQL first; the query layer in `database/db.py` is deliberately small, so the swap stays contained.
