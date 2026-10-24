# Architecture

NewsPerz is split into five areas. The short version: HTTP handling lives in `backend/`, business logic in `modules/`, persistence in `database/`, presentation in `frontend/`, and settings in `config.py`.

```text
app.py
  └── backend/        Flask app factory, blueprints, models, error handling
        ├── modules/  bookmarks, preferences, ranking, fetching, story storage
        ├── database/ SQLite connection, migrations, schema
        └── frontend/ Jinja templates and static assets
```

| Area | What belongs there |
|---|---|
| `backend/` | Anything that knows about Flask: the app factory, blueprints, request parsing, login, response rendering |
| `modules/` | Code that could be tested without a request context, plus the few modules that touch `session` or `current_user` |
| `database/` | Connections, query helpers, schema versions, and the SQL file |
| `frontend/` | Templates, CSS, JavaScript, images. No JavaScript build step |
| `config.py` | Environment-driven settings and the editorial lists (cities, topics, publishers) |

## Request lifecycle

A feed request goes through the same path whether it comes from a page load or infinite scroll.

1. **Resolve the query.** `build_query()` in `backend/news.py` merges stored preferences with URL parameters. Unknown topics and sort values fall back to safe defaults.
2. **Fetch.** `modules/news_fetcher.py` runs Google News RSS queries in a small thread pool. If `NEWS_API_KEY` is set and the provider mode allows it, a NewsAPI request runs alongside them.
3. **Dedupe.** Articles are collapsed by exact URL, by the publisher URL hidden inside a Google News redirect (`/articles/<id>`), and by a fuzzy title key made from the first 50 alphanumerics.
4. **Cache.** The deduped list goes into a 64-entry in-process LRU and the `feed_cache` SQLite table. The cache key covers query, category, city, area and publisher, but not the requested limit.
5. **Remember.** Each article gets a 16-character story id derived from its URL and is stored in `articles`, so its story page works without another provider call. Existing rows are left alone.
6. **Rank.** `modules/ranking.py` scores every article, annotates it with `match_score`, `match_reasons`, `is_local_match` and `is_preferred_source`, then slices the page.
7. **Render.** Jinja renders the first 12 cards. `/api/feed` returns the next slices as HTML plus JSON.

Provider calls share a wall-clock budget of twice `NEWS_TIMEOUT`. A provider that overruns is reported as failed rather than allowed to hold the page open.

## Ranking

The ranker is deterministic and rule-based.

| Signal | Points | Cap |
|---|---:|---|
| Location (area beats city) | +5 | Once per article |
| Topic in title / description | +4 / +2 | +4 total |
| Preferred publisher | +3 | One match |
| Keyword (up to two) | +2 each | +4 total |
| Recency (≤12h / ≤24h) | +2 / +1 | +2 total |

Maximum score is 18. Undated, future-dated and older-than-30-days articles get no recency points. Sort order is `relevance` (score, then date), `latest` (date, then score) or `sources` (preferred publisher, then score, then date). Ties fall back to the provider's order, so the same data always produces the same feed.

The `top_k` path uses `heapq.nlargest`; everything else is a plain sort.

## Data model

SQLite lives at `instance/newsperz.db` by default. Migrations are append-only in `database/migrations.py`.

| Table | Purpose |
|---|---|
| `users` | Account rows with Werkzeug password hashes |
| `user_interests`, `user_sources` | Topics and publishers per account, as composite-key rows |
| `user_location`, `user_feed_prefs` | Location and sort order per account |
| `bookmarks` | Saved stories, keyed by `(user_id, sha1(article_url))` |
| `feed_cache` | Persisted provider responses with expiry and hit counts |
| `login_attempts` | Failed-login timestamps for throttling |
| `articles` | Story previews kept for 45 days, capped at 6000 rows, unless bookmarked |
| `article_content` | Extracted readable text per story, with a status and fetch time |
| `schema_version` | Applied migration versions |

Guest state is not in these tables. It lives in the signed session cookie: preferences under `guest_prefs`, and at most 12 bookmarks under `guest_saved_items`, trimmed to a 2.8 KB budget. Both are merged into the account on login or registration.

## Failure behaviour

The app is built to keep rendering when the outside world misbehaves.

- **No provider data:** the feed is empty and the page shows a notice. Nothing is fabricated.
- **SQLite unavailable:** `database.db.get_db()` returns `None`, reads return empty results, and writes return a failure the UI can report. Pages still render.
- **Story extraction blocked:** the story page uses the feed summary and links to the publisher. The failure is cached and retried after an hour.
- **Cache table missing:** caching is skipped silently. Requests still work.

## Security boundaries

- CSRF protection is global through Flask-WTF. The JavaScript sends the token from `<meta name="csrf-token">` in `X-CSRFToken`.
- The CSP is nonce-based; scripts and styles are external or nonce-tagged. `X-Frame-Options`, `X-Content-Type-Options` and a referrer policy are set on every response.
- Login throttling counts failures per email and IP in `login_attempts`.
- Post-login redirects pass through `is_safe_next()`; schemes, protocol-relative URLs and embedded credentials are rejected.
- Bookmark and story URLs must be `http` or `https`. Provider images are normalized the same way.
- Story-body extraction resolves DNS, rejects non-global addresses (loopback, private, link-local), follows redirects manually, and stops at 1.5 MB.
- Guest cookies are capped because a session that grows past the browser limit is dropped entirely, which logs the user out.

## Adding things

- **A new page:** add a route to an existing blueprint in `backend/` (or a new blueprint registered in `create_app`), put its template in `frontend/templates/`, and keep query logic in `modules/` if it isn't HTTP-specific.
- **A new table or column:** append a migration to `database/migrations.py` and bump `SCHEMA_VERSION`. Do not edit an applied migration.
- **A new ranking signal:** add it in `modules/ranking.py`, cap it, and update the table in this document and the README.
- **A new provider:** add it to `modules/news_fetcher.py`, return `(articles, status_string)`, and let the existing thread pool report its health.
