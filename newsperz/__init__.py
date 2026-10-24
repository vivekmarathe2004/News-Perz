from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, g, render_template
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from newsperz import config as config_module
from newsperz.config import (
    CATEGORIES,
    CITIES_WITH_AREAS,
    CITY_STATES,
    PUBLISHERS,
    PROJECT_ROOT,
    SORT_OPTIONS,
    Config,
)
from newsperz.db import init_app as init_db
from newsperz.errors import register_error_handlers
from newsperz.models import User

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to save bookmarks and personalise your feed.'
login_manager.login_message_category = 'info'

csrf = CSRFProtect()

#: How many articles a page asks the provider for (kept in one place).
PAGE_LIMITS = {'index': 40, 'local': 35, 'category': 35, 'source': 35, 'search': 40, 'api': 60}


@login_manager.user_loader
def load_user(user_id):
    try:
        return User.get_by_id(int(user_id))
    except (TypeError, ValueError):
        return None


def _clip(text, limit):
    text = text or ''
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


def _iso_to_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _timeago(value):
    parsed = _iso_to_datetime(value)
    if parsed is None:
        return ''
    minutes = int((datetime.now(timezone.utc) - parsed).total_seconds() // 60)
    if minutes < 1:
        return 'just now'
    if minutes < 60:
        return f'{minutes}m ago'
    hours = minutes // 60
    if hours < 24:
        return f'{hours}h ago'
    days = hours // 24
    if days < 7:
        return f'{days}d ago'
    return parsed.strftime('%d %b %Y')


def create_app(test_config=None):
    """Application factory for NewsPerz."""
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / 'newsperz' / 'templates'),
        static_folder=str(PROJECT_ROOT / 'static'),
        static_url_path='/static',
    )

    app.config.from_object(Config)
    app.config['DATABASE_PATH'] = str(Path(app.instance_path) / 'newsperz.db')
    app.config['TEMPLATES_AUTO_RELOAD'] = Config.DEBUG
    if test_config:
        app.config.update(test_config)

    init_db(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from newsperz.migrations import run_migrations
    try:
        run_migrations(app.config['DATABASE_PATH'])
    except Exception:
        # The DB may be missing or locked; error pages and guests can still browse.
        app.logger.exception('schema migration failed')

    _register_security_headers(app)
    register_error_handlers(app)
    _register_context(app)

    from newsperz.auth import auth_bp
    from newsperz.news import news_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(news_bp)

    return app


def _register_security_headers(app):
    @app.before_request
    def _csp_nonce():
        import secrets
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.after_request
    def _headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        if not current_user.is_authenticated:
            response.cache_control.private = True
        if app.config.get('SEND_CSP_HEADER', True):
            nonce = getattr(g, 'csp_nonce', '')
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{nonce}'; "
                "style-src 'self' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; "
                "img-src 'self' data: https:; "
                "connect-src 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'"
            )
        return response


def _register_context(app):
    app.jinja_env.filters['clip'] = _clip
    app.jinja_env.filters['iso_dt'] = _iso_to_datetime
    app.jinja_env.filters['timeago'] = _timeago

    @app.context_processor
    def _nav():
        from newsperz.prefs import get_user_preferences
        location, topics, _publishers, sort_by = get_user_preferences()
        csrf_token = None
        try:
            csrf_token = generate_csrf()
        except Exception:
            app.logger.debug('csrf token unavailable')
        return {
            'location': location,
            'topics': topics,
            'current_sort': sort_by,
            'all_categories': CATEGORIES,
            'all_publishers': PUBLISHERS,
            'cities_with_areas': CITIES_WITH_AREAS,
            'city_states': CITY_STATES,
            'sort_options': SORT_OPTIONS,
            'csrf_token': csrf_token,
            'csp_nonce': getattr(g, 'csp_nonce', ''),
            'csp_enabled': app.config.get('SEND_CSP_HEADER', True),
            'app_env': app.config.get('ENV', 'production'),
            'current_year': datetime.now().year,
        }
