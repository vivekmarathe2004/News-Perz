"""Registration, login and logout.

Everything here assumes the database may be unavailable, so a broken DB yields a
friendly flash and a 200/503 page instead of a 500 traceback on every request.
"""

import logging
import re
import sqlite3
import time

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from backend.errors import is_safe_next
from backend.models import User
from database.db import execute, query_one
from modules.bookmarks import merge_guest_bookmarks
from modules.prefs import merge_guest_preferences

log = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
USERNAME_RE = re.compile(r'^[A-Za-z0-9._-]{3,32}$')
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128


def _attempts_key(email):
    return f'{(email or "").strip().lower()}|{request.remote_addr or "unknown"}'


def _attempts_in_window(key, window):
    cutoff = time.time() - window
    row = query_one(
        'SELECT COUNT(*) AS n FROM login_attempts WHERE attempt_key = ? AND created_at >= ?',
        (key, cutoff),
    )
    return int(row['n']) if row else 0


def _prune_attempts(key, window):
    execute('DELETE FROM login_attempts WHERE attempt_key = ? AND created_at < ?',
            (key, time.time() - window * 4))


def _record_failed_attempt(key, window):
    now = time.time()
    execute('INSERT INTO login_attempts (attempt_key, created_at) VALUES (?, ?)', (key, now))
    # Keep the table from growing forever on a targeted account.
    execute('DELETE FROM login_attempts WHERE attempt_key = ? AND created_at < ?',
            (key, now - window * 4))


def _clear_attempts(key):
    execute('DELETE FROM login_attempts WHERE attempt_key = ?', (key,))


def _adopt_guest_state(user):
    """Fold the visitor's cookie state into the account after a successful login."""
    moved = merge_guest_bookmarks(user.id)
    merge_guest_preferences(user.id)
    if moved:
        flash(f'Moved {moved} saved {"story" if moved == 1 else "stories"} from this browser to your account.',
              'info')


def _auth_page(template, **context):
    """Auth pages get their own minimal layout, not the feed sidebar chrome."""
    base = {
        'page_mode': 'auth',
        'form_email': request.form.get('email', '') if request.method == 'POST' else '',
        'form_username': request.form.get('username', '') if request.method == 'POST' else '',
        'safe_next': request.args.get('next', '') if is_safe_next(request.args.get('next')) else '',
    }
    base.update(context)
    return render_template(template, **base)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('news.index'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''

        problems = []
        if not username or not email or not password:
            problems.append('All fields are required.')
        elif not USERNAME_RE.match(username):
            problems.append('Username must be 3-32 characters: letters, numbers, dot, dash or underscore.')
        if email and not EMAIL_RE.match(email) or len(email) > 120:
            problems.append('Please enter a valid email address.')
        if password and len(password) < MIN_PASSWORD_LEN:
            problems.append(f'Password must be at least {MIN_PASSWORD_LEN} characters.')
        if len(password) > MAX_PASSWORD_LEN:
            problems.append('That password is too long.')

        if problems:
            flash(' '.join(dict.fromkeys(problems)), 'error')
            return _auth_page('auth/register.html', form_username=username, form_email=email)

        if User.get_by_email(email) or User.get_by_username(username):
            flash('That username or email is already registered.', 'error')
            return _auth_page('auth/register.html', form_username=username, form_email=email)

        try:
            user = User.create(username, email, password)
        except sqlite3.IntegrityError:
            flash('That username or email is already registered.', 'error')
            return _auth_page('auth/register.html', form_username=username, form_email=email)
        except sqlite3.Error:
            log.exception('registration failed')
            flash('We could not create your account right now.', 'error')
            return _auth_page('auth/register.html', form_username=username, form_email=email)

        login_user(user)
        _adopt_guest_state(user)
        flash('Account created. Tell us what you want to read.', 'success')
        return redirect(url_for('news.interests'))

    return _auth_page('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('news.index'))

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        key = _attempts_key(email)
        window = current_app.config['LOGIN_WINDOW_SECONDS']
        limit = current_app.config['LOGIN_MAX_ATTEMPTS']

        if _attempts_in_window(key, window) >= limit:
            flash('Too many failed attempts. Please wait a few minutes and try again.', 'error')
            return _auth_page('auth/login.html', form_email=email), 429

        user = User.get_by_email(email)
        if user and user.verify_password(password):
            if not user.is_active:
                flash('This account has been disabled.', 'error')
                return _auth_page('auth/login.html', form_email=email), 403
            login_user(user)
            session.permanent = True
            _clear_attempts(key)
            _prune_attempts(key, window)
            user.mark_login()
            _adopt_guest_state(user)
            next_page = request.form.get('next') or request.args.get('next')
            if is_safe_next(next_page):
                return redirect(next_page)
            flash(f'Welcome back, {user.username or "reader"}.', 'success')
            return redirect(url_for('news.index'))

        _record_failed_attempt(key, window)
        flash('Invalid email or password.', 'error')
        return _auth_page('auth/login.html', form_email=email)

    return _auth_page('auth/login.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('news.index'))
