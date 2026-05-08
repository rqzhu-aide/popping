import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# DATA_DIR: where SQLite databases live.
# On Render with a mounted disk, use /data.
# Fallback to local project data/ folder.
if os.path.isdir('/data'):
    DATA_DIR = '/data'
else:
    DATA_DIR = os.path.join(BASE_DIR, 'data')

# CONFIG_DIR: where course.json files live (in the git repo).
# On Render these are in the project source, not on the disk.
CONFIG_DIR = os.path.join(BASE_DIR, 'data')

DATABASE_SCHEMA = os.path.join(BASE_DIR, 'popping.sql')
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
