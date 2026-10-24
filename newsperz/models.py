"""User model over the SQLite layer (no ORM)."""

import sqlite3
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from newsperz.db import execute, query_one

_SELECT = 'SELECT id, username, email, password_hash, created_at, last_login_at FROM users WHERE '


class User(UserMixin):
    id = None
    username = ''
    email = ''
    password_hash = ''
    created_at = None
    last_login_at = None
    is_active = True

    def __init__(self, row=None, **fields):
        if row is not None:
            fields = dict(row)
        self.id = fields.get('id')
        self.username = fields.get('username') or ''
        self.email = fields.get('email') or ''
        self.password_hash = fields.get('password_hash') or ''
        self.created_at = fields.get('created_at')
        self.last_login_at = fields.get('last_login_at')

    def __repr__(self):
        # Never include password_hash: reprs leak into error pages and logs.
        return f'<User {self.id} {self.username}>'

    @classmethod
    def _from_row(cls, row):
        return cls(row) if row is not None else None

    @classmethod
    def get_by_id(cls, user_id):
        return cls._from_row(query_one(_SELECT + 'id = ?', (user_id,)))

    @classmethod
    def get_by_email(cls, email):
        if not email:
            return None
        return cls._from_row(query_one(_SELECT + 'email = ?', (email.strip().lower(),)))

    @classmethod
    def get_by_username(cls, username):
        if not username:
            return None
        return cls._from_row(query_one(_SELECT + 'username = ?', (username.strip(),)))

    @classmethod
    def create(cls, username, email, password):
        """Insert a user. Raises sqlite3.IntegrityError on a duplicate."""
        user_id = execute(
            'INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
            (username.strip(), email.strip().lower(), generate_password_hash(password)),
        )
        if user_id is None:
            raise sqlite3.Error('user could not be stored')
        return cls(id=user_id, username=username.strip(), email=email.strip().lower())

    def verify_password(self, password: str) -> bool:
        if not self.password_hash or not password:
            return False
        try:
            return check_password_hash(self.password_hash, password)
        except ValueError:
            return False

    def mark_login(self):
        execute(
            'UPDATE users SET last_login_at = ? WHERE id = ?',
            (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), self.id),
        )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)
        execute('UPDATE users SET password_hash = ? WHERE id = ?', (self.password_hash, self.id))
