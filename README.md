# NewsPerz

A personalised, local-first news feed for India — Flask, SQLite, Jinja and vanilla JavaScript. No build step, no frontend framework, no ORM.

NewsPerz pulls live headlines from Google News RSS, ranks every story against what you actually care about (your city, neighbourhood, topics and publishers), and renders the result as a fast server-side feed. You can use the whole thing as a guest; create an account and your saved stories and preferences follow you in.

---

## What it does

- **For You feed** — a ranked homepage with a featured story, then cards. The rest of the page loads through infinite scroll from `/api/feed`.
- **Local feed** — pick from 11 Indian cities and a list of neighbourhoods per city. Local matching gets the biggest ranking bonus.
- **13 topics and 10 publishers** — Technology, Cybersecurity, Cricket, Education, Business, Government schemes, Jobs, Health, Science, Entertainment, Sports, Politics, Traffic & transport. The lists live in `newsperz/config.py`, so they are easy to edit.
- **Search** — word-boundary matching: stories containing every query term come first, partial matches after that. City, topic, source and sort filters work alongside it.
- **In-app story pages** — every fetched article gets a stable 16-character story id and an internal page with a readable excerpt, reading time, related stories and a link to the publisher.
- **Bookmarks** — guests get up to 12 saved stories in their session; logged-in users get unlimited bookmarks in SQLite. Guest bookmarks are merged into the account on login or registration.
- **Preferences** — location, topics, publishers and sort order. Guests keep them in the signed session cookie; accounts keep them in the database.
- **Accounts** — registration, login and logout with Werkzeug password hashing and per-email/IP login throttling.
- **Dark and light theme** — the choice is applied before first paint and remembered in `localStorage`.
- **Honest failure** — if every news provider fails you get an empty feed and a notice, never fabricated headlines. If SQLite is unavailable, the site still renders and degrades instead of throwing a 500 on every request.

## How the ranking works

The "recommendation engine" is deterministic and rule-based — no ML, no opaque model. Each article is scored against your preferences, and every category of bonus is capped so one match cannot dominate the feed:

| Signal | Points | Notes |
|---|---:|---|
| Location | +5 | Area match beats city match; counted once |
| Topics | +4 / +2 | +4 if the topic is in the title, +2 if only in the description; total contribution capped at +4 |
| Preferred publisher | +3 | One match only; an empty source name never matches |
| Keywords | +2 each | Total contribution capped at +4 |
| Recency | +2 / +1 | +2 if ≤12 hours old, +1 if ≤24 hours old, otherwise 0 |

Maximum possible score is **18**. Undated, future-dated and older-than-30-days stories get no recency bonus — they are never treated as "just now".

Three sort modes are available:

- `relevance` (default) — score, then date
- `latest` — date, then score
- `sources` — preferred publisher first, then score, then date

Ties are broken by the original input order, so the same data always produces the same feed. The code lives in `newsperz/dsa/ranking.py`.

## Tech stack

- **Python 3.9+** and **Flask 3.1**
- **Flask-Login** for sessions, **Flask-WTF** for CSRF protection
- **SQLite** through the standard-library `sqlite3` module (no SQLAlchemy)
- **requests** for Google News RSS and NewsAPI
- **Waitress** for production serving
- **Jinja2** templates, plain CSS and one vanilla JS file
- **Google News RSS** as the primary provider; **NewsAPI.org** is optional

## Project layout

```text
.
├── app.py                        # Entry point: dev server or Waitress
├── newsperz/
│   ├── __init__.py               # Application factory, security headers, filters
│   ├── config.py                 # Environment config + editorial lists
│   ├── db.py                     # SQLite connection helpers and `init-db` command
│   ├── migrations.py             # Versioned schema (v2 currently)
│   ├── models.py                 # User model over raw SQL
│   ├── auth.py                   # Register / login / logout, throttling
│   ├── news.py                   # Feed, search, bookmarks, story and API routes
│   ├── prefs.py                  # Guest and account preferences
│   ├── bookmarks.py              # Guest and account bookmarks
│   ├── story_store.py            # Persisted story previews + readable-text extraction
│   ├── errors.py                 # Error pages and safe redirect validation
│   ├── dsa/ranking.py            # Scoring, ranking and search
│   ├── services/news_fetcher.py  # Google News RSS + NewsAPI, cache, dedupe
│   ├── templates/                # Jinja templates and partials
│   └── ...
├── static/
│   ├── css/style.css
│   ├── css/theme.css
│   ├── js/main.js
│   └── img/logo.svg
├── schema.sql                    # Readable reference copy of the schema
├── requirements.txt
├── .env.example
└── LICENSE
```

## Getting started

You need Python 3.9 or newer. No Node, npm or build step is required.

```bash
git clone <your-repo-url>
cd NewsPerz

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
```

Generate a secret key for local sessions:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste it into `.env` as `SECRET_KEY=...`. If you skip this in development, NewsPerz generates a random key once and stores it in `instance/dev_secret_key`, so your sessions survive restarts without a hard-coded secret in the repo.

Start the app:

```bash
python app.py
```

Open <http://127.0.0.1:5000>.

With `FLASK_DEBUG=1`, `app.py` runs Flask's development server. Without it, it runs Waitress on 8 threads. Either way it binds to `127.0.0.1` only.

### Database and migrations

The SQLite file defaults to `instance/newsperz.db` and is created automatically. Migrations run at boot. You can also run them manually:

```bash
flask --app app init-db
flask --app app init-db --force   # re-apply every migration
```

`instance/` and all `*.db*` files are gitignored. The authoritative schema is `newsperz/migrations.py`; `schema.sql` is a readable reference copy.

## Configuration

Copy `.env.example` to `.env` and adjust what you need. Only `SECRET_KEY` is mandatory in production.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | generated in dev | Signs sessions. Required when `FLASK_ENV=production` |
| `FLASK_ENV` | `development` | Set to `production` for a real deployment |
| `FLASK_DEBUG` | off | Enables Flask debug mode |
| `PORT` | `5000` | Port used by `python app.py` |
| `DATABASE_PATH` | `instance/newsperz.db` | SQLite file location |
| `NEWS_PROVIDER` | `auto` | `auto`, `rss` or `newsapi` |
| `NEWS_API_KEY` | empty | Enables NewsAPI in `auto` mode |
| `NEWS_API_BASE` | `https://newsapi.org/v2` | NewsAPI base URL |
| `NEWS_TIMEOUT` | `4` | Per-request timeout in seconds |
| `NEWS_CACHE_TTL` | `300` | Feed cache lifetime in seconds |
| `NEWS_LOCALE_HL` | `en-IN` | Google News language |
| `NEWS_LOCALE_GL` | `IN` | Google News country |
| `NEWS_LOCALE_CEID` | `IN:en` | Google News edition |
| `STORY_BODY_ENABLED` | on | Enable readable-text extraction |
| `STORY_BODY_TIMEOUT` | `7` | Publisher fetch timeout for story text |
| `LOGIN_MAX_ATTEMPTS` | `7` | Failed logins before throttling |
| `LOGIN_WINDOW_SECONDS` | `900` | Throttling window |
| `SESSION_COOKIE_SECURE` | off | Set to `1` behind HTTPS |
| `SESSION_LIFETIME_SECONDS` | `2592000` | 30 days |
| `SEND_CSP_HEADER` | on | Set to `0` to disable CSP (not recommended) |

`auto` mode uses Google News RSS and adds NewsAPI when a key is present. NewsAPI's free Developer plan is intended for development, so check their terms before using it in production.

## Routes

| Method | Path | Description |
|---|---|---|
| GET | `/` | Personalised "For You" feed |
| GET | `/local` | Local feed for the selected city and area |
| GET | `/category/<name>` | Topic page |
| GET | `/source/<name>` | Publisher page |
| GET | `/search?q=...` | Search with filters |
| GET/POST | `/interests` | Edit location, topics, publishers and sort order |
| GET | `/bookmarks` | Saved stories |
| POST | `/bookmark/add` | Save a story (JSON) |
| POST | `/bookmark/remove` | Remove a story (JSON) |
| GET | `/story/<story_id>` | In-app story page |
| GET | `/api/story/<story_id>/body` | Lazily extract and cache readable text |
| GET | `/api/feed` | JSON feed slice for infinite scroll |
| GET | `/healthz` | Health check: DB status, schema version, provider |
| GET/POST | `/auth/register` | Create an account |
| GET/POST | `/auth/login` | Log in |
| POST | `/auth/logout` | Log out |

The two bookmark endpoints accept JSON and require the CSRF token in an `X-CSRFToken` header. The page exposes it through `<meta name="csrf-token">`.

`GET /api/feed` accepts `mode`, `offset`, `q`, `city`, `area`, `topic`, `source` and `sort`. It returns both rendered card HTML and a JSON representation of the same articles.

## How a request flows

1. `build_query()` merges your saved preferences with the filters in the URL.
2. `get_real_news()` fetches from Google News RSS and, optionally, NewsAPI concurrently.
3. Results are deduped by URL, by the canonical URL hidden in Google News redirects, and by a fuzzy title key.
4. The deduped list is written to a two-layer cache: an in-process LRU of 64 entries in front of the SQLite `feed_cache` table.
5. Articles are persisted as story previews so every card can open an in-app page.
6. `rank_articles()` scores and annotates each story, then slices the requested page.
7. Jinja renders the first 12 cards; the rest arrive through `/api/feed`.

The provider layer has a hard wall-clock budget of twice the request timeout, never fabricates content, and reports partial failures to the UI.

## Security notes

These are deliberate choices, not afterthoughts:

- CSRF protection on every form and JSON write endpoint.
- A nonce-based Content-Security-Policy with no inline scripts or styles, plus `X-Content-Type-Options`, `X-Frame-Options: DENY` and a strict referrer policy.
- HttpOnly and SameSite=Lax session cookies; `SESSION_COOKIE_SECURE` available for HTTPS deployments.
- Login throttling keyed on email plus IP, stored in SQLite.
- Safe-redirect checks on the `next` parameter.
- Only `http://` and `https://` URLs are accepted for bookmarks, story links and images.
- Story-body extraction resolves DNS and refuses private/loopback addresses (SSRF guard), follows redirects manually, caps response size at 1.5 MB and retries failed publishers only after a one-hour cool-off.
- Guest bookmarks are capped at 12 entries and a 2.8 KB cookie budget so the session cookie never grows past the browser limit and silently logs the user out.
- Passwords are hashed with Werkzeug's default method and never appear in `repr()` output or logs.

## Deployment

Run it behind a reverse proxy (nginx, Caddy or similar) with Waitress:

```bash
export FLASK_ENV=production
export SECRET_KEY="<64-char-random-hex>"
export SESSION_COOKIE_SECURE=1

waitress-serve --listen=0.0.0.0:8080 --call "newsperz:create_app"
```

`waitress-serve` binds to `0.0.0.0:8080` by default. Point your proxy at that port and terminate TLS there. `GET /healthz` is a cheap check for the database, schema version and active provider.

SQLite is a single-node choice. It runs in WAL mode with a busy timeout and works well for a personal or small deployment, but it is not the right database for many concurrent writers.

## Known limitations

- **No automated tests yet.** The project is functional, but there is no `pytest` suite or CI configuration in the repo. That is the most obvious next step.
- **The feed is only as good as Google News RSS.** Google can rate-limit or change the feed shape. NewsAPI is an optional fallback for search-style queries, not a full replacement.
- **Some publishers block extraction or paywall their text.** In that case the story page falls back to the feed summary and the publisher link.
- **Editorial lists are India-focused.** Cities, neighbourhoods, topics and publishers are defined in `newsperz/config.py`; using it for another region means editing those lists.
- **No Dockerfile or CI pipeline.** Deployment is a Python process plus a proxy.

## Contributing

Issues and pull requests are welcome. If you change the schema, add an append-only migration to `newsperz/migrations.py` and bump `SCHEMA_VERSION`. If you change ranking behaviour, update the weights table above so the README stays honest.

Before opening a PR, run the app, click through the feed, local, category, source, search, story, bookmark and auth flows, and check `/healthz`.

## License

MIT — see [LICENSE](LICENSE). © 2026 Vivek Marathe.

## Credits

- Headlines from [Google News RSS](https://news.google.com/) and optionally [NewsAPI.org](https://newsapi.org/)
- Placeholder imagery from [Unsplash](https://unsplash.com/)
- Built with [Flask](https://flask.palletsprojects.com/), [Flask-Login](https://flask-login.readthedocs.io/) and [Flask-WTF](https://flask-wtf.readthedocs.io/)
