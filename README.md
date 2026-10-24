# NewsPerz

NewsPerz is a news reader for India. It pulls live headlines from Google News, ranks them against the city, topics and publishers you care about, and puts the useful ones first. It's a Flask app with SQLite under the hood and plain HTML, CSS and JavaScript on top.

You can browse, search and save stories without an account. If you sign up, your bookmarks and preferences follow you between browsers.

## What it does

- **For You** — a ranked homepage with one featured story, cards below it, and infinite scroll through `/api/feed`.
- **Local** — 11 cities with a list of neighbourhoods each. A local match gets the biggest ranking bump.
- **Topics and publishers** — 13 topics and 10 publishers, all defined in `config.py`.
- **Search** — word-boundary matching. Stories containing every term come first, partial matches after.
- **Story pages** — every article gets a stable internal page with a readable excerpt, reading time, related stories and a link to the publisher.
- **Bookmarks** — guests get 12 in their session; accounts get the full list. Guest bookmarks merge into the account on login.
- **Accounts** — register, log in, log out, with hashed passwords and login throttling.
- **Theme** — light/dark toggle, remembered in `localStorage`.

No build step, no npm, no ORM. The front end is Jinja templates, two CSS files and one JavaScript file.

## Get it running

You need Python 3.9 or newer.

```bash
git clone https://github.com/vivekmarathe2004/News-Perz.git
cd News-Perz

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # Windows: copy .env.example .env
```

Make a secret key and put it in `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Skipping this is fine locally. The app generates `instance/dev_secret_key` the first time it runs, so sessions survive restarts. In production it's required; the app won't start without it.

```bash
python app.py
```

Open http://127.0.0.1:5000. Set `FLASK_DEBUG=1` to use Flask's dev server, otherwise it runs under Waitress with 8 threads. Either way it only binds to localhost.

The database is created automatically at `instance/newsperz.db`, and migrations run at boot. You can also run them yourself:

```bash
flask --app app init-db
flask --app app init-db --force   # re-apply every migration
```

`instance/` is gitignored. `database/schema.sql` is a readable copy of the schema; the real one lives in `database/migrations.py`.

## How the feed is ranked

This part is rule-based, not a model. Every article gets a score and each kind of bonus is capped, so one lucky match can't take over the page.

| Signal | Points | Notes |
|---|---:|---|
| Your area or city | +5 | Area beats city, and it only counts once |
| A topic | +4 title / +2 description | The whole topic contribution caps at +4 |
| A preferred publisher | +3 | One match; an empty source name never counts |
| Keywords | +2 each | Capped at +4 total |
| Recency | +2 / +1 | +2 under 12 hours, +1 under 24 hours, else 0 |

The highest possible score is 18. An article with no date gets zero recency points instead of a fake "just now" bonus, and anything older than 30 days gets nothing there either.

Relevance is the default sort. The other two are `latest` and `sources` (preferred publishers first). Ties fall back to the order the provider returned things in, so the same input always produces the same feed. The scoring code is in `modules/ranking.py`.

## Where things live

```text
.
├── app.py                  # starts the dev server or Waitress
├── config.py               # env vars, cities, topics, publishers
├── backend/
│   ├── __init__.py         # app factory, CSP and security headers
│   ├── auth.py             # register, login, logout
│   ├── news.py             # feed, search, story and API routes
│   ├── models.py           # User model
│   └── errors.py
├── modules/
│   ├── bookmarks.py        # guest and account bookmarks
│   ├── prefs.py            # guest and account preferences
│   ├── ranking.py          # scoring, sorting, search matching
│   ├── news_fetcher.py     # RSS/NewsAPI, dedupe, cache
│   └── story_store.py      # saved previews and article text extraction
├── database/
│   ├── db.py               # SQLite helpers and the init-db command
│   ├── migrations.py       # schema versions
│   └── schema.sql
├── frontend/
│   ├── templates/
│   └── static/
├── docs/
│   ├── ARCHITECTURE.md
│   └── DEPLOYMENT.md
├── requirements.txt
├── .env.example
└── LICENSE
```

`instance/` appears after the first run. It holds the database and the dev secret key, and it's gitignored.

The [architecture notes](docs/ARCHITECTURE.md) cover the request lifecycle, data model and layering rules. Production setup is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Configuration

Everything comes from `.env`. The defaults are fine for local development.

| Variable | Default | What it does |
|---|---|---|
| `SECRET_KEY` | generated in dev | Signs sessions. Required in production |
| `FLASK_ENV` | `development` | Set to `production` for a real deployment |
| `FLASK_DEBUG` | off | Turns on Flask debug mode |
| `PORT` | `5000` | Port used by `python app.py` |
| `DATABASE_PATH` | `instance/newsperz.db` | Where the SQLite file lives |
| `NEWS_PROVIDER` | `auto` | `auto`, `rss` or `newsapi` |
| `NEWS_API_KEY` | empty | Turns on NewsAPI in `auto` mode |
| `NEWS_API_BASE` | `https://newsapi.org/v2` | NewsAPI base URL |
| `NEWS_TIMEOUT` | `4` | Per-request timeout, in seconds |
| `NEWS_CACHE_TTL` | `300` | How long a fetched feed stays cached |
| `NEWS_LOCALE_HL` | `en-IN` | Google News language |
| `NEWS_LOCALE_GL` | `IN` | Google News country |
| `NEWS_LOCALE_CEID` | `IN:en` | Google News edition |
| `STORY_BODY_ENABLED` | on | Turns on article text extraction |
| `STORY_BODY_TIMEOUT` | `7` | Publisher fetch timeout for story text |
| `LOGIN_MAX_ATTEMPTS` | `7` | Failed logins before throttling kicks in |
| `LOGIN_WINDOW_SECONDS` | `900` | Throttling window |
| `SESSION_COOKIE_SECURE` | off | Set to `1` when serving over HTTPS |
| `SESSION_LIFETIME_SECONDS` | `2592000` | 30 days |
| `SEND_CSP_HEADER` | on | Set to `0` to drop the CSP header |

`auto` uses Google News RSS and adds NewsAPI only when a key is present. NewsAPI's free Developer plan is meant for development, so read their terms before putting it in production.

## Routes

| Method | Path | What it does |
|---|---|---|
| GET | `/` | Personalised For You feed |
| GET | `/local` | Local feed for the selected city and area |
| GET | `/category/<name>` | Topic page |
| GET | `/source/<name>` | Publisher page |
| GET | `/search?q=...` | Search with filters |
| GET/POST | `/interests` | Edit location, topics, publishers and sort order |
| GET | `/bookmarks` | Saved stories |
| POST | `/bookmark/add` | Save a story (JSON) |
| POST | `/bookmark/remove` | Remove a story (JSON) |
| GET | `/story/<story_id>` | Story page inside the app |
| GET | `/api/story/<story_id>/body` | Fetch and cache the readable text |
| GET | `/api/feed` | JSON feed slice for infinite scroll |
| GET | `/healthz` | Database status, schema version, provider |
| GET/POST | `/auth/register` | Create an account |
| GET/POST | `/auth/login` | Log in |
| POST | `/auth/logout` | Log out |

The two bookmark endpoints take JSON and expect the CSRF token in an `X-CSRFToken` header. It's available in the page's `<meta name="csrf-token">` tag.

`/api/feed` accepts `mode`, `offset`, `q`, `city`, `area`, `topic`, `source` and `sort`, and returns both card HTML and the same articles as JSON.

## What happens on a page load

1. `build_query()` combines your saved preferences with whatever filters are in the URL.
2. `get_real_news()` fetches from Google News RSS, plus NewsAPI if it's configured, in parallel.
3. Results are deduped by URL, by the publisher URL hidden inside Google News redirects, and by a fuzzy title key.
4. The deduped list goes into a 64-entry in-process cache backed by the `feed_cache` table in SQLite.
5. Each article gets a story id and is saved as a preview so its story page works without another provider call.
6. `rank_articles()` scores and annotates everything, then slices the page.
7. Jinja renders the first 12 cards; the rest arrive from `/api/feed`.

Providers get a wall-clock budget of twice `NEWS_TIMEOUT`. If all of them fail you get an empty feed and a notice, not fake headlines.

## Security and failure behaviour

- CSRF protection is on for every form and JSON write endpoint.
- A nonce-based CSP blocks inline scripts, alongside the usual nosniff, frame and referrer headers.
- Login attempts are throttled per email and IP, with the timestamps stored in SQLite.
- Only `http://` and `https://` URLs are accepted for bookmark and story links, and provider images are normalized to `http(s)` too.
- Article text extraction resolves DNS and refuses private or loopback addresses, follows redirects by hand, caps the response at 1.5 MB, and waits an hour before retrying a publisher that failed.
- Guest bookmarks are capped at 12 entries and a 2.8 KB cookie budget, so the session cookie can't grow past the browser limit and quietly log someone out.
- If SQLite is unavailable, pages still render for guests and writes fail with a friendly message instead of a 500.

## Deployment

Put Waitress behind a reverse proxy and set a real secret:

```bash
export FLASK_ENV=production
export SECRET_KEY="<64-char-random-hex>"
export SESSION_COOKIE_SECURE=1

waitress-serve --listen=0.0.0.0:8080 --call "backend:create_app"
```

`waitress-serve` listens on `0.0.0.0:8080` by default. Terminate TLS at the proxy and point it at that port. `GET /healthz` is a cheap way to check the database, schema version and active provider.

SQLite is a single-node choice. It runs in WAL mode with a busy timeout and is fine for a personal or small deployment, but it isn't the database you want behind a lot of concurrent writes.

## Rough edges

- **No tests right now.** There's no pytest suite or CI config in the repo. That's the first thing I'd add next.
- **Google News RSS is the weak point.** It can rate-limit or change shape. NewsAPI helps for search-style queries, but it isn't a full replacement.
- **Some publishers block extraction or paywall their text.** Those story pages fall back to the feed summary and the link out.
- **The content lists are India-only.** Cities, neighbourhoods, topics and publishers are hard-coded in `config.py`; another region means editing them.
- **No Dockerfile or CI.** Deployment is a Python process and a proxy for now.

## License

MIT. See [LICENSE](LICENSE). © 2026 Vivek Marathe.

## Thanks

Headlines come from [Google News RSS](https://news.google.com/), with optional help from [NewsAPI.org](https://newsapi.org/). Placeholder images are from [Unsplash](https://unsplash.com/). Built with [Flask](https://flask.palletsprojects.com/), [Flask-Login](https://flask-login.readthedocs.io/) and [Flask-WTF](https://flask-wtf.readthedocs.io/).
