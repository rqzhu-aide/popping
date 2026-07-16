import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# DATA_DIR: where SQLite databases live.
# On Render, point this at the persistent disk mount. Locally, use data/.
DATA_DIR = os.environ.get('DATA_DIR')
if not DATA_DIR:
    if os.path.isdir('/data'):
        DATA_DIR = '/data'
    else:
        DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_DIR = os.path.abspath(DATA_DIR)

# CLASSES_DIR: where course.yaml configs live (in the git repo).
# One subfolder per course slug, each with a course.yaml.
CLASSES_DIR = os.path.join(BASE_DIR, 'classes')

# CONFIG_DIR: kept for backward compatibility; new code uses CLASSES_DIR.
CONFIG_DIR = CLASSES_DIR

DATABASE_SCHEMA = os.path.join(BASE_DIR, 'popping.sql')

# In production (Render), SECRET_KEY must be set explicitly. The hard-coded
# fallback below is for local development only and must never be accepted on Render.
IS_PRODUCTION = os.environ.get('RENDER') is not None
_DEFAULT_DEV_KEY = 'dev-secret-key-change-in-production'
SECRET_KEY = os.environ.get('SECRET_KEY')
if IS_PRODUCTION and (not SECRET_KEY or SECRET_KEY == _DEFAULT_DEV_KEY):
    raise RuntimeError(
        'SECRET_KEY must be set to a secure random value in production.\n'
        'Generate one with:  python -c "import secrets; print(secrets.token_hex(32))"'
    )
if not SECRET_KEY:
    SECRET_KEY = _DEFAULT_DEV_KEY

SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = IS_PRODUCTION
MAX_CONTENT_LENGTH = 2 * 1024 * 1024
