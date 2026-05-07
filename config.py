import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Render persistent disk path
if os.path.isdir('/opt/render/project/src/instance'):
    BASE_DIR = '/opt/render/project/src/instance'

DATABASE = os.path.join(BASE_DIR, 'popping.db')
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')