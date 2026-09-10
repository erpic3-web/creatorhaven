"""WSGI entry for hosted deploys (Render):  gunicorn -w 1 --threads 8 -t 600 wsgi:app

One worker on purpose: the store is a single SQLite file, the background sync and the
Postgres backup thread live in this process, and the site is a one-team tool."""
from sf import config
from app import create_app

cfg = config.load()
app = create_app(cfg, start_background=True)
application = app
