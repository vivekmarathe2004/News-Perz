"""Error pages and response hardening.

Errors return HTML for browsers and JSON for the AJAX endpoints, so the bookmark
toggle can tell "not saved" from a network hiccup instead of lying.
"""

import logging

from flask import jsonify, render_template, request
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)

# Endpoints whose failures must never be rendered as an HTML error page.
_JSON_ENDPOINTS = {'news.add_bookmark', 'news.remove_bookmark', 'news.api_feed'}
# Only these paths may be used as a post-login `next` target.
_SAFE_NEXT_PREFIXES = ('/',)
_UNSAFE_NEXT_PREFIXES = ('//', '/\\', '/\t', '/\n')


def wants_json():
    if request.endpoint in _JSON_ENDPOINTS:
        return True
    return request.path.startswith('/api/') or request.is_json


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        return _render(error, 404, 'Not found', 'That page has moved or never existed.')

    @app.errorhandler(405)
    def method_not_allowed(error):
        return _render(error, 405, 'Method not allowed', 'That action is not supported here.')

    @app.errorhandler(429)
    def rate_limited(error):
        return _render(error, 429, 'Slow down', 'Too many requests — try again in a moment.')

    @app.errorhandler(500)
    def server_error(error):
        log.exception('unhandled server error')
        return _render(error, 500, 'Something broke', 'The newsroom hit a snag. Try reloading.')

    @app.errorhandler(Exception)
    def unhandled(error):
        if isinstance(error, HTTPException):
            return error
        log.exception('unhandled exception on %s', request.path)
        return _render(error, 500, 'Something broke', 'The newsroom hit a snag. Try reloading.')


def _render(error, code, title, message):
    if wants_json():
        return jsonify({'status': 'error', 'code': code, 'message': message}), code
    context = {
        'code': code,
        'error_title': title,
        'error_message': message,
        'current_year': _year(),
    }
    context.update(_nav_context())
    return render_template('errors/error.html', **context), code


def _year():
    from datetime import datetime
    return datetime.now().year


def _nav_context():
    """Minimum context so error pages can still render base.html."""
    from newsperz.config import CATEGORIES, PUBLISHERS
    return {
        'all_categories': CATEGORIES,
        'all_publishers': PUBLISHERS,
        'location': {'city': 'Pune', 'area': '', 'state': 'Maharashtra', 'country': 'India'},
        'topics': [],
        'page_mode': 'error',
    }


def is_safe_next(target) -> bool:
    """Reject off-site and scheme-relative redirects after login."""
    if not target:
        return False
    if any(target.startswith(prefix) for prefix in _UNSAFE_NEXT_PREFIXES):
        return False
    return target.startswith(_SAFE_NEXT_PREFIXES) and '://' not in target and '@' not in target
