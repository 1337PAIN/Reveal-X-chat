"""
Reveal-X Application Initialization
"""

from flask import Flask
from flask_socketio import SocketIO
import os
import secrets

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('REVEAL_X_SECRET_KEY') or secrets.token_urlsafe(32)
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
app.config['SHARES_FOLDER'] = os.path.join(app.root_path, 'shares')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('REVEAL_X_COOKIE_SECURE', '').lower() == 'true'
allowed_origins = os.environ.get('REVEAL_X_ALLOWED_ORIGINS')
strict_cors = os.environ.get('REVEAL_X_STRICT_CORS', '').lower() == 'true'
app.config['ALLOWED_ORIGINS'] = '*'
if strict_cors and allowed_origins:
    app.config['ALLOWED_ORIGINS'] = [
        origin.strip()
        for origin in allowed_origins.split(',')
        if origin.strip()
    ]

# Ensure folders exist
os.makedirs(app.config['SHARES_FOLDER'], exist_ok=True)

socketio = SocketIO(
    app,
    cors_allowed_origins=app.config['ALLOWED_ORIGINS'],
    async_mode='threading',
    max_http_buffer_size=app.config['MAX_CONTENT_LENGTH'],
)

# Import routes after app creation
from app import routes
