"""Titik masuk WSGI: `app` untuk waitress/gunicorn, misal `waitress-serve diddy.wsgi:app`."""
from .web import create_app

app = create_app()
