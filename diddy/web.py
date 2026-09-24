"""Pembuatan Flask app: registrasi blueprint, error handler, header keamanan, dan UI statis."""
import os
import secrets

from flask import Flask, jsonify, send_from_directory

from .config import DATA, PKG_DIR
from .core.errors import ApiError
from .db import close_db, init_db

from . import auth, dashboards, hosts, system, users
from .deploy import routes as deploy_routes
from .dhcp import routes as dhcp_routes
from .dns import routes as dns_routes
from .ipam import routes as ipam_routes

BLUEPRINTS = [auth.bp, dashboards.bp, ipam_routes.bp, dhcp_routes.bp, hosts.bp, dns_routes.bp,
              deploy_routes.bp, system.bp, users.bp]


def _secret():
    p = os.path.join(DATA, "secret.key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return open(p).read().strip()


def create_app():
    app = Flask(__name__, static_folder=os.path.join(PKG_DIR, "static"), static_url_path="/static")
    app.config["JSON_SORT_KEYS"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.secret_key = _secret()

    @app.errorhandler(ApiError)
    def _api_error(e):
        return jsonify(error=str(e)), e.status

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    app.teardown_appcontext(close_db)
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
    init_db()
    return app
