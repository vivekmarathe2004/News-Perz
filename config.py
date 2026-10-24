"""NewsPerz configuration.

All runtime settings come from the environment (or an inline `.env`); the only
hard-coded values are safe defaults and the editorial content lists.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / '.env')

_ENV_TRUE = {'1', 'true', 'yes', 'on'}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _ENV_TRUE


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, '') or default)
    except ValueError:
        return default


def _dev_secret_key(instance_dir: Path) -> str:
    """Persist a random key per machine so dev sessions survive restarts.

    A published constant would let anyone forge a Flask-Login cookie.
    """
    key_file = instance_dir / 'dev_secret_key'
    try:
        if key_file.exists():
            existing = key_file.read_text(encoding='utf-8').strip()
            if existing:
                return existing
        instance_dir.mkdir(parents=True, exist_ok=True)
        generated = os.urandom(32).hex()
        key_file.write_text(generated, encoding='utf-8')
        return generated
    except OSError:
        return os.urandom(32).hex()


class Config:
    ENV = 'production' if os.environ.get('FLASK_ENV', '').lower() == 'production' else 'development'
    DEBUG = _env_flag('FLASK_DEBUG', False)
    TESTING = _env_flag('TESTING', False)
    IS_DEV = ENV != 'production' or DEBUG or TESTING

    instance_path = str(PROJECT_ROOT / 'instance')
    SECRET_KEY = os.environ.get('SECRET_KEY') or _dev_secret_key(Path(instance_path))
    if not os.environ.get('SECRET_KEY') and not IS_DEV:
        raise RuntimeError('SECRET_KEY must be set in the environment for production.')

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = _env_flag('SESSION_COOKIE_SECURE', False)
    PERMANENT_SESSION_LIFETIME = _env_int('SESSION_LIFETIME_SECONDS', 60 * 60 * 24 * 30)
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True

    # SQLite lives in instance/ so it is never mistaken for committed data.
    DATABASE_PATH = os.environ.get('DATABASE_PATH') or str(Path(instance_path) / 'newsperz.db')

    # News providers
    NEWS_PROVIDER = os.environ.get('NEWS_PROVIDER', 'auto')  # auto | rss | newsapi
    NEWS_API_KEY = os.environ.get('NEWS_API_KEY', '')
    NEWS_API_BASE = os.environ.get('NEWS_API_BASE', 'https://newsapi.org/v2')
    NEWS_LOCALE_HL = os.environ.get('NEWS_LOCALE_HL', 'en-IN')
    NEWS_LOCALE_GL = os.environ.get('NEWS_LOCALE_GL', 'IN')
    NEWS_LOCALE_CEID = os.environ.get('NEWS_LOCALE_CEID', 'IN:en')
    NEWS_TIMEOUT = _env_int('NEWS_TIMEOUT', 4)
    NEWS_CACHE_TTL = _env_int('NEWS_CACHE_TTL', 300)

    # In-app story reader: extract a short readable excerpt from the publisher.
    STORY_BODY_ENABLED = _env_flag('STORY_BODY_ENABLED', True)
    STORY_BODY_TIMEOUT = _env_int('STORY_BODY_TIMEOUT', 7)

    # Anti-abuse
    LOGIN_MAX_ATTEMPTS = _env_int('LOGIN_MAX_ATTEMPTS', 7)
    LOGIN_WINDOW_SECONDS = _env_int('LOGIN_WINDOW_SECONDS', 900)

    MAX_ARTICLES = 100


# Topics / categories per blueprint
CATEGORIES = [
    'Technology',
    'Cybersecurity',
    'Cricket',
    'Education',
    'Business',
    'Government schemes',
    'Jobs',
    'Health',
    'Science',
    'Entertainment',
    'Sports',
    'Politics',
    'Traffic & transport',
]

# Trusted news publishers
PUBLISHERS = [
    'The Indian Express',
    'BBC News',
    'The Hindu',
    'Hindustan Times',
    'NDTV',
    'Reuters',
    'Times of India',
    'Scroll.in',
    'BusinessLine',
    'Livemint',
]

# Cities and their key neighbourhoods
CITIES_WITH_AREAS = {
    'Pune': ['Hinjewadi', 'Kothrud', 'Baner', 'Viman Nagar', 'Shivajinagar', 'Wakad', 'Hadapsar', 'Pimpri-Chinchwad'],
    'Mumbai': ['Andheri', 'Bandra', 'Colaba', 'BKC', 'Powai', 'Dadar', 'Thane', 'Navi Mumbai'],
    'Delhi': ['Connaught Place', 'South Delhi', 'Dwarka', 'Noida', 'Gurugram', 'Rohini', 'Karol Bagh'],
    'Bangalore': ['Whitefield', 'Koramangala', 'Indiranagar', 'Electronic City', 'HSR Layout', 'Hebbal', 'Jayanagar'],
    'Hyderabad': ['HITEC City', 'Gachibowli', 'Banjara Hills', 'Jubilee Hills', 'Madhapur', 'Secunderabad'],
    'Chennai': ['Adyar', 'T. Nagar', 'Anna Nagar', 'Velachery', 'OMR', 'Mylapore'],
    'Kolkata': ['Salt Lake', 'New Town', 'Park Street', 'Howrah', 'Ballygunge'],
    'Ahmedabad': ['Navrangpura', 'SG Highway', 'Satellite', 'Bodakdev', 'Vastrapur'],
    'Jaipur': ['Malviya Nagar', 'Vaishali Nagar', 'C-Scheme', 'Mansarovar'],
    'Nagpur': ['Dharampeth', 'Civil Lines', 'Sitabuldi', 'Wardha Road'],
    'Nashik': ['College Road', 'Gangapur Road', 'Indira Nagar', 'Panchavati'],
}

INDIA_CITIES = list(CITIES_WITH_AREAS.keys())

# State lookup for the cities above; also used for the "somewhere else" escape hatch.
CITY_STATES = {
    'Pune': 'Maharashtra',
    'Mumbai': 'Maharashtra',
    'Nashik': 'Maharashtra',
    'Delhi': 'Delhi',
    'Jaipur': 'Rajasthan',
    'Bangalore': 'Karnataka',
    'Hyderabad': 'Telangana',
    'Chennai': 'Tamil Nadu',
    'Kolkata': 'West Bengal',
    'Ahmedabad': 'Gujarat',
    'Nagpur': 'Maharashtra',
}

SORT_OPTIONS = [
    ('relevance', 'Most relevant to me'),
    ('latest', 'Latest first'),
    ('sources', 'Preferred sources first'),
]
