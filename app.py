#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 MZALENDO — Manufacturing Operations & Supply Intelligence Platform
===============================================================================
 A mobile-first manufacturing operating system for African SMEs and Jua Kali
 producers. Owners manage the plant from a web dashboard; workers and suppliers
 reach the same system from a basic feature phone over USSD and SMS, powered by
 Africa's Talking.

 Architecture ................ single-file monolith (this file)
 Stack ....................... Flask · SQLAlchemy · SQLite · Tailwind CSS · JS
 Telco ....................... Africa's Talking (USSD, Bulk SMS, Premium SMS)
 Author ...................... Winebald Technologies
 Licence ..................... MIT
===============================================================================
"""

from __future__ import annotations

import base64
import click
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import string
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, date, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash, jsonify, g,
    session, abort, Response, make_response, current_app,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required,
    current_user,
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import func, or_, and_, desc, case
from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

try:  # optional — only needed for local .env development
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass


# =============================================================================
#  SECTION 1 — CONFIGURATION
# =============================================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "instance"))
os.makedirs(DATA_DIR, exist_ok=True)

APP_NAME = "Mzalendo"
APP_TAGLINE = "Manufacturing Operations & Supply Intelligence"
APP_VERSION = "1.0.0"


def _env_str(key: str, default: str = "") -> str:
    """Environment string with surrounding quotes stripped.

    Railway's raw editor (and most .env files) need a value quoted when it
    contains a # — AT_USSD_CODE="*384*1153#" — because an unquoted # starts a
    comment. Some parsers strip the quotes, some store them literally. A sender
    id that reaches Africa's Talking as "MZALENDO" including the quotes is
    rejected as unregistered, and the error gives no hint why.
    """
    raw = (os.environ.get(key) or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1].strip()
    return raw or default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env_str(key)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    """Numeric environment value that tolerates quoting and rubbish.

    A quoted "3" reaching float() raises ValueError during import, which kills
    every worker before it can log anything useful — the container just dies.
    A bad value falls back to the default and says so rather than taking the
    whole application down.
    """
    raw = _env_str(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", key, raw, default)
        return default


def _database_uri() -> str:
    """SQLite by default; honours DATABASE_URL if a platform injects one."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Pin the driver explicitly to the one requirements-postgres.txt
        # installs. Railway injects postgresql://, older platforms inject the
        # legacy postgres:// — and an unqualified URL leaves SQLAlchemy to pick
        # a default, which is how a deployment ends up asking for a driver that
        # is not in the image.
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                url = "postgresql+psycopg2://" + url[len(prefix):]
                break
        return url
    return "sqlite:///" + os.path.join(DATA_DIR, "mzalendo.db")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48)
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- session / cookie hardening -----------------------------------------
    SESSION_COOKIE_NAME = "mzalendo_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("FORCE_HTTPS", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = _env_bool("FORCE_HTTPS", False)
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024          # 8 MB request ceiling
    JSON_SORT_KEYS = False
    TEMPLATES_AUTO_RELOAD = _env_bool("DEBUG", False)
    PREFERRED_URL_SCHEME = "https"

    # --- platform behaviour --------------------------------------------------
    # Defaults to off so that `python3 app.py` works over plain http on a
    # developer's machine. Turning it on also marks the session cookie Secure,
    # which a browser will refuse to store over http — meaning nobody could log
    # in locally. Every deployment path (.env.example, README, railway.json)
    # sets FORCE_HTTPS=1 explicitly, and bootstrap() warns when it is off.
    FORCE_HTTPS = _env_bool("FORCE_HTTPS", False)
    ALLOW_PUBLIC_SIGNUP = _env_bool("ALLOW_PUBLIC_SIGNUP", True)
    # Opt-in demonstration plant. Defaults to off so a real deployment never
    # grows a fictional workshop by accident; set SEED_DEMO_DATA=1 to populate
    # one on first boot for a demo or a walkthrough.
    SEED_DEMO_DATA = _env_bool("SEED_DEMO_DATA", False)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # --- Africa's Talking ----------------------------------------------------
    AT_USERNAME = _env_str("AT_USERNAME", "sandbox")
    AT_API_KEY = _env_str("AT_API_KEY", "")
    AT_SENDER_ID = _env_str("AT_SENDER_ID", "")
    AT_SHORTCODE = _env_str("AT_SHORTCODE", "")
    AT_USSD_CODE = _env_str("AT_USSD_CODE", "*384*1153#")
    # AT_LIVE=1 removes the simulation fallback entirely: a message either goes
    # to the gateway or is recorded as failed with the reason.
    AT_LIVE = _env_bool("AT_LIVE", False)
    # Shared secret appended to the callback URLs registered with Africa's
    # Talking. Without it a public callback accepts anyone's POST, which means
    # a stranger can open USSD sessions and inject delivery reports.
    AT_WEBHOOK_TOKEN = _env_str("AT_WEBHOOK_TOKEN", "")
    AT_ENVIRONMENT = _env_str("AT_ENVIRONMENT", "sandbox")   # sandbox|live
    SMS_ENABLED = _env_bool("SMS_ENABLED", False)

    # --- seed super administrator -------------------------------------------
    SEED_ADMIN_EMAIL = _env_str("SEED_ADMIN_EMAIL", "info@winebald.tech")
    SEED_ADMIN_USERNAME = _env_str("SEED_ADMIN_USERNAME", "winebald")
    SEED_ADMIN_NAME = _env_str("SEED_ADMIN_NAME", "Platform Administrator")
    SEED_ADMIN_PASSWORD = _env_str("SEED_ADMIN_PASSWORD", "223011005@Winebald")


# =============================================================================
#  SECTION 2 — APPLICATION & EXTENSIONS
# =============================================================================

app = Flask(__name__, instance_path=DATA_DIR)
app.config.from_object(Config)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Sign in to reach the plant floor."
login_manager.login_message_category = "info"
login_manager.session_protection = "strong"
login_manager.refresh_view = "login"

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["800 per hour", "80 per minute"],
    storage_uri=app.config["RATELIMIT_STORAGE_URI"],
    strategy="fixed-window",
    headers_enabled=True,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  mzalendo  %(message)s",
)
log = logging.getLogger("mzalendo")


# =============================================================================
#  SECTION 3 — SECURITY HEADERS  (targets an A+ on securityheaders.com)
# =============================================================================

PERMISSIONS_POLICY = ", ".join([
    "accelerometer=()", "ambient-light-sensor=()", "autoplay=()", "battery=()",
    "camera=()", "display-capture=()", "document-domain=()",
    "encrypted-media=()", "fullscreen=(self)", "gamepad=()", "geolocation=()",
    "gyroscope=()", "hid=()", "idle-detection=()", "local-fonts=()",
    "magnetometer=()", "microphone=()", "midi=()", "payment=()",
    "picture-in-picture=()", "publickey-credentials-get=()",
    "screen-wake-lock=()", "serial=()", "usb=()", "xr-spatial-tracking=()",
    "interest-cohort=()",
])


def _webhook_guard():
    """Reject a telco callback that does not carry the shared token.

    Africa's Talking appends whatever query string is registered with the
    channel, so ?token=... arrives on every hop. Compared in constant time.
    When no token is configured the callbacks stay open — fine on a laptop,
    not on a public URL, which is why startup warns about it.
    """
    # The in-dashboard handset simulator reaches the same view through an
    # authenticated, CSRF-protected route. It carries a session rather than the
    # shared token, and loosening the public callback to accept a session would
    # hand any signed-in user's browser a forgeable path into it.
    if getattr(g, "ussd_from_dashboard", False):
        return None
    expected = app.config["AT_WEBHOOK_TOKEN"]
    if not expected:
        return None
    given = request.args.get("token") or request.headers.get("X-Webhook-Token", "")
    if not hmac.compare_digest(str(given), str(expected)):
        log.warning("callback rejected: bad or missing token on %s from %s",
                    request.path, request.remote_addr)
        return make_response("forbidden", 403)
    return None


@app.before_request
def _security_before_request():
    """Issue a per-request CSP nonce and enforce HTTPS in production."""
    g.csp_nonce = secrets.token_urlsafe(18)
    g.request_started = datetime.now(timezone.utc)

    if app.config["FORCE_HTTPS"] and not app.debug and not app.testing:
        # /healthz is the one exemption. Platform health probes reach the
        # container directly over http on the private network, so redirecting
        # them makes a perfectly healthy container look dead and the deploy
        # never goes green. The route returns no session data and sets no
        # cookie, so there is nothing to protect in transit.
        if request.path != "/healthz":
            proto = request.headers.get("X-Forwarded-Proto", request.scheme)
            if proto != "https" and request.method in ("GET", "HEAD"):
                return redirect(request.url.replace("http://", "https://", 1), code=301)


@app.after_request
def _security_after_request(resp: Response) -> Response:
    nonce = getattr(g, "csp_nonce", "")

    # Is this connection actually TLS? Honour the proxy header, since Railway
    # and most PaaS terminate TLS in front of us.
    secure = request.is_secure or (
        request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https"
    )

    csp = (
        "default-src 'self'; "
        "base-uri 'none'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "manifest-src 'self'; "
        "media-src 'self'; "
        "worker-src 'self' blob:"
    )

    # upgrade-insecure-requests tells the browser to re-fetch every subresource
    # over https. On a real deployment that is exactly right. On a developer's
    # machine served over plain http it is a disaster: the browser opens a TLS
    # handshake against a plaintext socket and the page loads without styles.
    # Browsers exempt localhost, but a LAN address like 10.x.x.x is not
    # "potentially trustworthy", so it would fire there. Only send it when the
    # connection is genuinely secure, or when we are enforcing https anyway.
    if secure or app.config["FORCE_HTTPS"]:
        csp += "; upgrade-insecure-requests"

    resp.headers["Content-Security-Policy"] = csp
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = PERMISSIONS_POLICY
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
    resp.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    resp.headers["X-DNS-Prefetch-Control"] = "off"
    resp.headers["Origin-Agent-Cluster"] = "?1"

    if app.config["FORCE_HTTPS"]:
        resp.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )

    # A fingerprinted asset URL is immutable by construction: change the file
    # and the URL changes. Safe to cache for a year.
    if request.path.startswith("/static/") and request.args.get("v"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    # Never let a browser or proxy cache an authenticated page.
    if request.path.startswith(("/dashboard", "/account", "/admin")):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        resp.headers["Pragma"] = "no-cache"

    resp.headers.pop("Server", None)
    resp.headers["Server"] = "Mzalendo"
    return resp


# =============================================================================
#  SECTION 4 — DOMAIN CONSTANTS
# =============================================================================

ROLES = {
    "super_admin": "Super administrator",
    "owner": "Plant owner",
    "manager": "Operations manager",
    "supervisor": "Floor supervisor",
    "viewer": "Read only",
}
ROLE_RANK = {"viewer": 0, "supervisor": 1, "manager": 2, "owner": 3, "super_admin": 4}

WRITE_ROLES = {"super_admin", "owner", "manager"}
FLOOR_ROLES = {"super_admin", "owner", "manager", "supervisor"}
ADMIN_ROLES = {"super_admin", "owner"}

ORDER_STATUSES = [
    "new", "confirmed", "scheduled", "in_production",
    "quality_check", "ready", "dispatched", "completed", "cancelled",
]
ORDER_STATUS_LABELS = {
    "new": "New", "confirmed": "Confirmed", "scheduled": "Scheduled",
    "in_production": "In production", "quality_check": "Quality check",
    "ready": "Ready", "dispatched": "Dispatched", "completed": "Completed",
    "cancelled": "Cancelled",
}
RUN_STATUSES = ["planned", "running", "paused", "blocked", "done", "cancelled"]
PO_STATUSES = ["draft", "sent", "confirmed", "partial", "received", "cancelled"]
MACHINE_STATUSES = ["running", "idle", "maintenance", "down", "retired"]
TICKET_STATUSES = ["open", "assigned", "in_progress", "resolved", "closed"]
SEVERITIES = ["low", "medium", "high", "critical"]
UNITS = ["pcs", "kg", "g", "ton", "m", "m2", "m3", "litre", "roll", "sheet", "box", "bag"]

DEFAULT_STAGES = [
    "Cutting", "Welding", "Grinding", "Painting",
    "Assembly", "Quality check", "Dispatch",
]

FAULT_TYPES = {
    "1": "Not starting", "2": "Overheating", "3": "Strange noise",
    "4": "Leaking", "5": "Other",
}
INCIDENT_TYPES = {
    "1": "Injury", "2": "Near miss", "3": "Fire or burn",
    "4": "Chemical spill", "5": "Electrical hazard", "6": "Other",
}


# =============================================================================
#  SECTION 5 — DATA MODEL
# =============================================================================

def _now() -> datetime:
    """Current instant as a naive UTC datetime.

    Everything is *stored* in UTC. Display is converted to the plant's local
    zone by _to_local() and the dt/ago filters, so the database stays
    unambiguous while the floor sees its own wall clock.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Kenya observes EAT (UTC+3) year round and has never used daylight saving, so
# a fixed offset is exact here and avoids depending on the tzdata package being
# present — which it often is not on Windows or in a slim container.
TZ_OFFSET_HOURS = _env_float("TZ_OFFSET_HOURS", 3.0)
TZ_LABEL = _env_str("TZ_LABEL", "EAT")
LOCAL_OFFSET = timedelta(hours=TZ_OFFSET_HOURS)


def _to_local(value):
    """Naive UTC datetime -> naive local datetime, for display only."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value + LOCAL_OFFSET
    return value


def _from_local(value):
    """Local wall-clock naive datetime -> naive UTC, for storage.

    The inverse of _to_local(). Used when a time is authored in local terms
    (seed data, a shift start typed by hand) but must be stored as UTC.
    """
    if value is None:
        return None
    return value - LOCAL_OFFSET


def _today() -> date:
    """Today's date on the plant's wall clock, not the server's."""
    return (datetime.now(timezone.utc) + LOCAL_OFFSET).date()


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=_now, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now, nullable=False)


class Factory(db.Model, TimestampMixin):
    """A tenant. Every operational record hangs off exactly one factory."""
    __tablename__ = "factories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    sector = db.Column(db.String(80), default="General manufacturing")
    county = db.Column(db.String(80), default="Nairobi")
    address = db.Column(db.String(240), default="")
    phone = db.Column(db.String(32), default="")
    email = db.Column(db.String(160), default="")
    currency = db.Column(db.String(8), default="KES")
    plan = db.Column(db.String(24), default="starter")   # starter|business|enterprise
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Telco configuration (per tenant — marketplace instances differ)
    ussd_code = db.Column(db.String(32), default="")
    at_username = db.Column(db.String(80), default="")
    at_api_key = db.Column(db.String(200), default="")
    at_sender_id = db.Column(db.String(32), default="")
    sms_enabled = db.Column(db.Boolean, default=False, nullable=False)

    # Pulse tuning
    low_stock_grace = db.Column(db.Integer, default=0)
    service_warn_days = db.Column(db.Integer, default=7)
    working_days_per_week = db.Column(db.Integer, default=6)

    users = db.relationship("User", back_populates="factory", lazy="dynamic")

    def __repr__(self):
        return f"<Factory {self.slug}>"


class User(db.Model, UserMixin, TimestampMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(160), default="")
    phone = db.Column(db.String(32), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(24), default="viewer", nullable=False)
    is_active_flag = db.Column("is_active", db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    password_changed_at = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(64))
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer)

    factory = db.relationship("Factory", back_populates="users")

    # -- password ------------------------------------------------------------
    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw, method="pbkdf2:sha256:600000")
        self.password_changed_at = _now()

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # -- flask-login ---------------------------------------------------------
    @property
    def is_active(self):                       # noqa: D401 — flask-login contract
        return bool(self.is_active_flag)

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > _now())

    # -- authorisation -------------------------------------------------------
    def has_role(self, *roles) -> bool:
        return self.role in roles

    def at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK.get(role, 99)

    @property
    def is_super(self) -> bool:
        return self.role == "super_admin"

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES

    @property
    def initials(self) -> str:
        src = (self.full_name or self.username).strip()
        bits = [p for p in src.split() if p]
        if not bits:
            return "??"
        if len(bits) == 1:
            return bits[0][:2].upper()
        return (bits[0][0] + bits[-1][0]).upper()

    @property
    def role_label(self) -> str:
        return ROLES.get(self.role, self.role.title())

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


class Worker(db.Model, TimestampMixin):
    """Shop-floor staff. Identified over USSD/SMS by phone number."""
    __tablename__ = "workers"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    employee_no = db.Column(db.String(32), default="")
    name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(32), index=True, nullable=False)
    trade = db.Column(db.String(80), default="")        # welder, machinist, painter…
    station = db.Column(db.String(80), default="")
    shift = db.Column(db.String(24), default="day")
    pin_hash = db.Column(db.String(255), default="")
    daily_rate = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text, default="")

    def set_pin(self, pin: str):
        self.pin_hash = generate_password_hash(str(pin), method="pbkdf2:sha256:200000")

    def check_pin(self, pin: str) -> bool:
        if not self.pin_hash:
            return True
        return check_password_hash(self.pin_hash, str(pin))


class Supplier(db.Model, TimestampMixin):
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    contact_name = db.Column(db.String(120), default="")
    phone = db.Column(db.String(32), default="")
    email = db.Column(db.String(160), default="")
    address = db.Column(db.String(240), default="")
    materials_supplied = db.Column(db.String(300), default="")
    lead_time_days = db.Column(db.Integer, default=7)
    payment_terms = db.Column(db.String(80), default="On delivery")
    # rolling performance counters
    orders_placed = db.Column(db.Integer, default=0)
    orders_on_time = db.Column(db.Integer, default=0)
    orders_complete = db.Column(db.Integer, default=0)
    defect_reports = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text, default="")

    @property
    def reliability(self) -> int:
        """0–100 composite of punctuality, completeness and defect rate."""
        if not self.orders_placed:
            return 70                      # neutral until we have evidence
        punctual = self.orders_on_time / self.orders_placed
        complete = self.orders_complete / self.orders_placed
        defect = min(1.0, self.defect_reports / max(1, self.orders_placed))
        score = (punctual * 0.45 + complete * 0.40 + (1 - defect) * 0.15) * 100
        return int(round(max(0, min(100, score))))

    @property
    def reliability_band(self) -> str:
        r = self.reliability
        return "ok" if r >= 80 else ("warn" if r >= 60 else "bad")


class Material(db.Model, TimestampMixin):
    __tablename__ = "materials"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    code = db.Column(db.String(48), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80), default="Raw material")
    unit = db.Column(db.String(16), default="pcs")
    quantity = db.Column(db.Float, default=0.0, nullable=False)
    min_stock = db.Column(db.Float, default=0.0, nullable=False)
    reorder_qty = db.Column(db.Float, default=0.0)
    unit_cost = db.Column(db.Float, default=0.0)
    location = db.Column(db.String(80), default="")
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    supplier = db.relationship("Supplier")

    @property
    def stock_state(self) -> str:
        if self.quantity <= 0:
            return "out"
        if self.quantity <= self.min_stock:
            return "low"
        if self.min_stock and self.quantity <= self.min_stock * 1.35:
            return "watch"
        return "ok"

    @property
    def stock_pct(self) -> int:
        target = max(self.min_stock * 3, 1.0)
        return int(round(min(100.0, (self.quantity / target) * 100)))

    @property
    def value(self) -> float:
        return round((self.quantity or 0) * (self.unit_cost or 0), 2)


class StockMovement(db.Model):
    __tablename__ = "stock_movements"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id"), index=True, nullable=False)
    kind = db.Column(db.String(16), default="in")     # in|out|adjust|waste
    quantity = db.Column(db.Float, default=0.0)
    balance_after = db.Column(db.Float, default=0.0)
    reference = db.Column(db.String(80), default="")
    note = db.Column(db.String(240), default="")
    source = db.Column(db.String(16), default="web")  # web|ussd|sms|system
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"))
    created_at = db.Column(db.DateTime, default=_now, index=True)

    material = db.relationship("Material")
    user = db.relationship("User")
    worker = db.relationship("Worker")


class Product(db.Model, TimestampMixin):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    sku = db.Column(db.String(48), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80), default="")
    unit_price = db.Column(db.Float, default=0.0)
    build_days = db.Column(db.Integer, default=3)
    stages = db.Column(db.Text, default="")          # newline separated
    description = db.Column(db.Text, default="")
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    bom = db.relationship("BomItem", back_populates="product",
                          cascade="all, delete-orphan", lazy="selectin")

    @property
    def stage_list(self):
        raw = [s.strip() for s in (self.stages or "").splitlines() if s.strip()]
        return raw or DEFAULT_STAGES

    @property
    def material_cost(self) -> float:
        return round(sum((b.qty_per_unit or 0) * (b.material.unit_cost or 0)
                         for b in self.bom if b.material), 2)

    @property
    def margin_pct(self):
        if not self.unit_price:
            return None
        return int(round((1 - self.material_cost / self.unit_price) * 100))


class BomItem(db.Model):
    """Bill of materials: what one unit of a product consumes."""
    __tablename__ = "bom_items"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), index=True, nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id"), nullable=False)
    qty_per_unit = db.Column(db.Float, default=1.0)

    product = db.relationship("Product", back_populates="bom")
    material = db.relationship("Material")


class Customer(db.Model, TimestampMixin):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    company = db.Column(db.String(160), default="")
    phone = db.Column(db.String(32), default="")
    email = db.Column(db.String(160), default="")
    address = db.Column(db.String(240), default="")
    sms_updates = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text, default="")
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class Order(db.Model, TimestampMixin):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    number = db.Column(db.String(32), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    status = db.Column(db.String(24), default="new", nullable=False, index=True)
    priority = db.Column(db.String(16), default="normal")   # low|normal|high|rush
    due_date = db.Column(db.Date)
    deposit = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text, default="")
    source = db.Column(db.String(16), default="web")

    customer = db.relationship("Customer")
    items = db.relationship("OrderItem", back_populates="order",
                            cascade="all, delete-orphan", lazy="selectin")
    runs = db.relationship("ProductionRun", back_populates="order", lazy="selectin")

    @property
    def total(self) -> float:
        return round(sum((i.quantity or 0) * (i.unit_price or 0) for i in self.items), 2)

    @property
    def days_left(self):
        if not self.due_date:
            return None
        return (self.due_date - _today()).days

    @property
    def is_late(self) -> bool:
        d = self.days_left
        return d is not None and d < 0 and self.status not in ("completed", "cancelled", "dispatched")

    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS.get(self.status, self.status)


class OrderItem(db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), index=True, nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Float, default=1.0)
    unit_price = db.Column(db.Float, default=0.0)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")


class Machine(db.Model, TimestampMixin):
    __tablename__ = "machines"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    code = db.Column(db.String(32), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    kind = db.Column(db.String(80), default="")
    location = db.Column(db.String(80), default="")
    status = db.Column(db.String(24), default="idle", nullable=False)
    commissioned_on = db.Column(db.Date)
    last_service_at = db.Column(db.Date)
    service_interval_days = db.Column(db.Integer, default=90)
    runtime_hours = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text, default="")

    @property
    def next_service(self):
        if not self.last_service_at:
            return None
        return self.last_service_at + timedelta(days=self.service_interval_days or 90)

    @property
    def service_days_left(self):
        ns = self.next_service
        return None if ns is None else (ns - _today()).days

    @property
    def service_state(self) -> str:
        d = self.service_days_left
        if d is None:
            return "unknown"
        if d < 0:
            return "overdue"
        if d <= 7:
            return "due"
        return "ok"


class MaintenanceTicket(db.Model, TimestampMixin):
    __tablename__ = "maintenance_tickets"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    reference = db.Column(db.String(32), index=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"), nullable=False)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"))
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    fault_type = db.Column(db.String(80), default="Other")
    severity = db.Column(db.String(16), default="medium")
    status = db.Column(db.String(24), default="open", nullable=False, index=True)
    description = db.Column(db.Text, default="")
    resolution = db.Column(db.Text, default="")
    source = db.Column(db.String(16), default="web")
    resolved_at = db.Column(db.DateTime)
    downtime_minutes = db.Column(db.Integer, default=0)

    machine = db.relationship("Machine")
    worker = db.relationship("Worker")
    assignee = db.relationship("User")

    @property
    def age_hours(self) -> float:
        end = self.resolved_at or _now()
        return round((end - self.created_at).total_seconds() / 3600, 1)


class ProductionRun(db.Model, TimestampMixin):
    __tablename__ = "production_runs"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    reference = db.Column(db.String(32), index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"))
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"))
    quantity = db.Column(db.Float, default=1.0)
    produced = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(24), default="planned", nullable=False, index=True)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    notes = db.Column(db.Text, default="")

    order = db.relationship("Order", back_populates="runs")
    product = db.relationship("Product")
    machine = db.relationship("Machine")
    worker = db.relationship("Worker")
    stages = db.relationship("RunStage", back_populates="run",
                             cascade="all, delete-orphan",
                             order_by="RunStage.sequence", lazy="selectin")

    @property
    def progress(self) -> int:
        if self.quantity:
            return int(round(min(100.0, (self.produced or 0) / self.quantity * 100)))
        return 0

    @property
    def is_at_risk(self) -> bool:
        if self.status in ("done", "cancelled"):
            return False
        if self.end_date and self.end_date < _today():
            return True
        return self.status == "blocked"


class RunStage(db.Model):
    __tablename__ = "run_stages"
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("production_runs.id"), index=True, nullable=False)
    sequence = db.Column(db.Integer, default=1)
    name = db.Column(db.String(80), nullable=False)
    planned_date = db.Column(db.Date)
    status = db.Column(db.String(16), default="pending")   # pending|active|done
    completed_at = db.Column(db.DateTime)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"))

    run = db.relationship("ProductionRun", back_populates="stages")
    worker = db.relationship("Worker")


class PurchaseOrder(db.Model, TimestampMixin):
    __tablename__ = "purchase_orders"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    number = db.Column(db.String(32), index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    status = db.Column(db.String(24), default="draft", nullable=False, index=True)
    expected_date = db.Column(db.Date)
    received_at = db.Column(db.DateTime)
    notes = db.Column(db.Text, default="")

    supplier = db.relationship("Supplier")
    items = db.relationship("POItem", back_populates="po",
                            cascade="all, delete-orphan", lazy="selectin")

    @property
    def total(self) -> float:
        return round(sum((i.quantity or 0) * (i.unit_cost or 0) for i in self.items), 2)

    @property
    def is_overdue(self) -> bool:
        return bool(self.expected_date and self.expected_date < _today()
                    and self.status not in ("received", "cancelled"))


class POItem(db.Model):
    __tablename__ = "po_items"
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), index=True, nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id"), nullable=False)
    quantity = db.Column(db.Float, default=0.0)
    received_qty = db.Column(db.Float, default=0.0)
    unit_cost = db.Column(db.Float, default=0.0)

    po = db.relationship("PurchaseOrder", back_populates="items")
    material = db.relationship("Material")


class QcInspection(db.Model, TimestampMixin):
    __tablename__ = "qc_inspections"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    reference = db.Column(db.String(32), index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    run_id = db.Column(db.Integer, db.ForeignKey("production_runs.id"))
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    inspector_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    sample_size = db.Column(db.Integer, default=1)
    defects_found = db.Column(db.Integer, default=0)
    status = db.Column(db.String(16), default="pending", nullable=False)  # pending|pass|fail
    standard = db.Column(db.String(80), default="KEBS")
    notes = db.Column(db.Text, default="")

    order = db.relationship("Order")
    run = db.relationship("ProductionRun")
    product = db.relationship("Product")
    inspector = db.relationship("User")
    checks = db.relationship("QcCheck", back_populates="inspection",
                             cascade="all, delete-orphan", lazy="selectin")

    @property
    def pass_rate(self) -> int:
        if not self.checks:
            return 0
        return int(round(sum(1 for c in self.checks if c.passed) / len(self.checks) * 100))


class QcCheck(db.Model):
    __tablename__ = "qc_checks"
    id = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey("qc_inspections.id"), index=True, nullable=False)
    label = db.Column(db.String(160), nullable=False)
    passed = db.Column(db.Boolean, default=False)
    note = db.Column(db.String(240), default="")

    inspection = db.relationship("QcInspection", back_populates="checks")


class SafetyIncident(db.Model, TimestampMixin):
    __tablename__ = "safety_incidents"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    reference = db.Column(db.String(32), index=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"))
    kind = db.Column(db.String(80), default="Near miss")
    severity = db.Column(db.String(16), default="low")
    location = db.Column(db.String(120), default="")
    description = db.Column(db.Text, default="")
    action_taken = db.Column(db.Text, default="")
    status = db.Column(db.String(24), default="open", nullable=False)
    source = db.Column(db.String(16), default="web")
    resolved_at = db.Column(db.DateTime)

    worker = db.relationship("Worker")


class Attendance(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), index=True, nullable=False)
    day = db.Column(db.Date, default=_today, index=True)
    check_in = db.Column(db.DateTime)
    check_out = db.Column(db.DateTime)
    source = db.Column(db.String(16), default="ussd")

    worker = db.relationship("Worker")

    @property
    def hours(self):
        if self.check_in and self.check_out:
            return round((self.check_out - self.check_in).total_seconds() / 3600, 2)
        return None


class SmsLog(db.Model):
    __tablename__ = "sms_logs"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True)
    direction = db.Column(db.String(8), default="out")     # out|in
    to_number = db.Column(db.String(32), default="")
    from_number = db.Column(db.String(32), default="")
    message = db.Column(db.Text, default="")
    category = db.Column(db.String(32), default="general")
    status = db.Column(db.String(32), default="queued")
    status_code = db.Column(db.Integer)
    provider_id = db.Column(db.String(80), default="")
    cost = db.Column(db.String(32), default="")
    error = db.Column(db.String(240), default="")
    created_at = db.Column(db.DateTime, default=_now, index=True)


class UssdSession(db.Model):
    __tablename__ = "ussd_sessions"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True)
    session_id = db.Column(db.String(80), index=True)
    phone_number = db.Column(db.String(32), index=True)
    service_code = db.Column(db.String(32), default="")
    network_code = db.Column(db.String(16), default="")
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"))
    last_input = db.Column(db.Text, default="")
    hops = db.Column(db.Integer, default=0)
    status = db.Column(db.String(24), default="active")
    outcome = db.Column(db.String(120), default="")
    started_at = db.Column(db.DateTime, default=_now, index=True)
    ended_at = db.Column(db.DateTime)

    worker = db.relationship("Worker")


class Alert(db.Model):
    __tablename__ = "alerts"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    kind = db.Column(db.String(32), default="general", index=True)
    severity = db.Column(db.String(16), default="info")
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, default="")
    recommendation = db.Column(db.String(300), default="")
    entity_type = db.Column(db.String(40), default="")
    entity_id = db.Column(db.Integer)
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=_now, index=True)


class PulseSnapshot(db.Model):
    __tablename__ = "pulse_snapshots"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True, nullable=False)
    taken_at = db.Column(db.DateTime, default=_now, index=True)
    production = db.Column(db.Integer, default=0)
    inventory = db.Column(db.Integer, default=0)
    orders = db.Column(db.Integer, default=0)
    maintenance = db.Column(db.Integer, default=0)
    suppliers = db.Column(db.Integer, default=0)
    overall = db.Column(db.Integer, default=0)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    actor = db.Column(db.String(80), default="")
    action = db.Column(db.String(48), default="")
    entity = db.Column(db.String(48), default="")
    entity_id = db.Column(db.Integer)
    detail = db.Column(db.String(400), default="")
    ip = db.Column(db.String(64), default="")
    user_agent = db.Column(db.String(240), default="")
    created_at = db.Column(db.DateTime, default=_now, index=True)


# =============================================================================
#  SECTION 6 — HELPERS
# =============================================================================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def _unauthorized():
    if request.accept_mimetypes.best == "application/json" or request.path.startswith("/api/"):
        return jsonify(error="authentication required"), 401
    flash("Sign in to reach the plant floor.", "info")
    return redirect(url_for("login", next=request.full_path))


def slugify(value: str, maxlen: int = 60) -> str:
    value = re.sub(r"[^a-zA-Z0-9\s-]", "", (value or "")).strip().lower()
    value = re.sub(r"[\s_-]+", "-", value)
    return value[:maxlen].strip("-") or "factory"


def norm_phone(raw: str, default_cc: str = "254") -> str:
    """Normalise Kenyan-style numbers to E.164 (+2547XXXXXXXX)."""
    if not raw:
        return ""
    s = re.sub(r"[^\d+]", "", str(raw))
    if s.startswith("+"):
        return s
    if s.startswith("00"):
        return "+" + s[2:]
    if s.startswith("0"):
        return "+" + default_cc + s[1:]
    if s.startswith(default_cc):
        return "+" + s
    if len(s) == 9:
        return "+" + default_cc + s
    return "+" + s


def money(value, currency="KES") -> str:
    try:
        return f"{currency} {float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return f"{currency} 0.00"


def to_float(raw, default=0.0):
    try:
        if raw in (None, ""):
            return default
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def to_int(raw, default=0):
    try:
        if raw in (None, ""):
            return default
        return int(float(str(raw).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def to_date(raw):
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    return None


def next_ref(model, factory_id: int, prefix: str, field: str = "reference") -> str:
    """Human readable sequential reference, e.g. ORD-0042."""
    count = db.session.query(func.count(model.id)).filter_by(factory_id=factory_id).scalar() or 0
    for bump in range(1, 400):
        candidate = f"{prefix}-{count + bump:04d}"
        exists = db.session.query(model.id).filter(
            getattr(model, field) == candidate, model.factory_id == factory_id
        ).first()
        if not exists:
            return candidate
    return f"{prefix}-{secrets.token_hex(3).upper()}"


PASSWORD_RULES = [
    (r".{12,}", "at least 12 characters"),
    (r"[A-Z]", "one uppercase letter"),
    (r"[a-z]", "one lowercase letter"),
    (r"[0-9]", "one digit"),
    (r"[^A-Za-z0-9]", "one symbol"),
]


def password_problems(pw: str):
    return [msg for pattern, msg in PASSWORD_RULES if not re.search(pattern, pw or "")]


def audit(action: str, entity: str = "", entity_id=None, detail: str = ""):
    try:
        actor = getattr(current_user, "username", "system") if current_user else "system"
        row = AuditLog(
            factory_id=getattr(current_user, "factory_id", None),
            user_id=getattr(current_user, "id", None) if getattr(current_user, "is_authenticated", False) else None,
            actor=actor, action=action, entity=entity, entity_id=entity_id,
            detail=detail[:400],
            ip=(request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:64],
            user_agent=(request.headers.get("User-Agent") or "")[:240],
        )
        db.session.add(row)
        db.session.commit()
    except Exception as exc:                                   # pragma: no cover
        db.session.rollback()
        log.warning("audit failed: %s", exc)


def system_audit(factory_id, actor, action, entity="", entity_id=None, detail=""):
    try:
        db.session.add(AuditLog(factory_id=factory_id, actor=actor, action=action,
                                entity=entity, entity_id=entity_id, detail=detail[:400],
                                ip="telco", user_agent="africastalking"))
        db.session.commit()
    except Exception:                                          # pragma: no cover
        db.session.rollback()


# --- authorisation decorators ------------------------------------------------

def roles_required(*roles):
    def outer(fn):
        @wraps(fn)
        @login_required
        def inner(*a, **kw):
            if current_user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return inner
    return outer


def write_required(fn):
    @wraps(fn)
    @login_required
    def inner(*a, **kw):
        if current_user.role not in WRITE_ROLES:
            abort(403)
        return fn(*a, **kw)
    return inner


def floor_required(fn):
    @wraps(fn)
    @login_required
    def inner(*a, **kw):
        if current_user.role not in FLOOR_ROLES:
            abort(403)
        return fn(*a, **kw)
    return inner


def current_factory_id():
    """Active tenant. Super admins may pivot between factories."""
    if not current_user.is_authenticated:
        return None
    if current_user.is_super:
        chosen = session.get("factory_ctx")
        if chosen:
            return chosen
        first = Factory.query.order_by(Factory.id).first()
        return first.id if first else None
    return current_user.factory_id


def current_factory():
    fid = current_factory_id()
    return db.session.get(Factory, fid) if fid else None


def scoped(model):
    """Every query in the dashboard runs through here — no cross-tenant leaks."""
    return model.query.filter(model.factory_id == current_factory_id())


def get_scoped_or_404(model, obj_id):
    obj = db.session.get(model, obj_id)
    if not obj or obj.factory_id != current_factory_id():
        abort(404)
    return obj


@app.before_request
def _require_plant():
    """Every /dashboard screen belongs to a plant. Insist on one existing.

    A super administrator starts attached to nothing, and a plant can be
    deleted out from under anyone. Six screens dereferenced the current plant
    directly and raised AttributeError; the rest rendered convincingly empty
    pages, which is arguably worse because it looks like real data. One guard
    at the front is better than thirty scattered null checks.
    """
    if not current_user.is_authenticated:
        return None
    if not request.path.startswith("/dashboard"):
        return None
    if request.endpoint in ("switch_factory", "logout"):
        return None
    if current_factory() is not None:
        return None
    if current_user.is_super:
        flash("Create a plant first — every dashboard screen belongs to one.", "info")
        return redirect(url_for("admin_factories"))
    flash("Your account is not attached to a plant. Ask your administrator to "
          "assign you to one.", "warn")
    return redirect(url_for("profile"))


@app.before_request
def _enforce_password_rotation():
    """A user flagged for rotation cannot touch anything but the reset screen."""
    if not current_user.is_authenticated:
        return
    if not current_user.must_change_password:
        return
    allowed = {"change_password", "logout", "static", "healthz", "favicon"}
    if request.endpoint in allowed or request.path.startswith("/static/"):
        return
    if request.path.startswith(("/ussd", "/sms", "/api/telco")):
        return
    return redirect(url_for("change_password"))


# --- pagination --------------------------------------------------------------

class Page:
    def __init__(self, query, page: int, per_page: int = 20):
        self.page = max(1, page)
        self.per_page = per_page
        self.total = query.order_by(None).count()
        self.pages = max(1, (self.total + per_page - 1) // per_page)
        self.page = min(self.page, self.pages)
        self.items = query.limit(per_page).offset((self.page - 1) * per_page).all()

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def window(self):
        lo = max(1, self.page - 2)
        hi = min(self.pages, lo + 4)
        lo = max(1, hi - 4)
        return range(lo, hi + 1)

    @property
    def first_index(self):
        return 0 if not self.total else (self.page - 1) * self.per_page + 1

    @property
    def last_index(self):
        return min(self.total, self.page * self.per_page)


def page_args():
    return to_int(request.args.get("page"), 1) or 1


def qs_without(*keys):
    args = {k: v for k, v in request.args.items() if k not in keys}
    return urllib.parse.urlencode(args)


# =============================================================================
#  SECTION 7 — AFRICA'S TALKING GATEWAY
# =============================================================================

class AfricasTalking:
    """
    Thin, dependency-free client for the Africa's Talking REST API.

    Uses stdlib urllib so the container stays small. When credentials are
    absent the gateway drops into simulation mode: messages are still written
    to the SMS log and shown in the dashboard, which keeps the record honest and
    lets a judge watch the whole flow without spending airtime.
    """

    LIVE_SMS = "https://api.africastalking.com/version1/messaging"
    SANDBOX_SMS = "https://api.sandbox.africastalking.com/version1/messaging"
    LIVE_BULK = "https://api.africastalking.com/version1/messaging/bulk"

    def __init__(self, factory: "Factory | None" = None):
        self.factory = factory
        self.username = (factory.at_username if factory and factory.at_username
                         else app.config["AT_USERNAME"])
        self.api_key = (factory.at_api_key if factory and factory.at_api_key
                        else app.config["AT_API_KEY"])
        self.sender_id = (factory.at_sender_id if factory and factory.at_sender_id
                          else app.config["AT_SENDER_ID"])
        # Endpoint selection. AT_LIVE wins over AT_ENVIRONMENT, because the
        # alternative is someone setting AT_LIVE=1 with real credentials and
        # having every message quietly delivered to the sandbox instead.
        # A username of literally "sandbox" always means sandbox — that is how
        # Africa's Talking identifies the sandbox application.
        env = app.config["AT_ENVIRONMENT"]
        if self.username == "sandbox":
            self.endpoint = self.SANDBOX_SMS
        elif app.config["AT_LIVE"] or env in ("production", "live"):
            self.endpoint = self.LIVE_SMS
        else:
            self.endpoint = self.SANDBOX_SMS
        self.sandbox = self.endpoint == self.SANDBOX_SMS

    @property
    def live(self) -> bool:
        enabled = app.config["SMS_ENABLED"] or (self.factory.sms_enabled if self.factory else False)
        return bool(enabled and self.api_key and self.username)

    def send(self, to, message: str, category: str = "general") -> dict:
        """Send one SMS to one or many recipients. Always returns a summary."""
        numbers = [norm_phone(n) for n in (to if isinstance(to, (list, tuple, set)) else [to])]
        numbers = [n for n in numbers if n and len(n) >= 10]
        if not numbers:
            return {"ok": False, "sent": 0, "reason": "no valid recipient"}

        message = (message or "").strip()[:640]
        fid = self.factory.id if self.factory else None

        if not self.live and app.config["AT_LIVE"]:
            # Strict live mode. Silently "sending" a breakdown alert that never
            # leaves the building is worse than failing loudly, because nobody
            # finds out until the machine has been down for a shift.
            reason = ("no API key" if not self.api_key else
                      "no username" if not self.username else "SMS disabled")
            for n in numbers:
                db.session.add(SmsLog(factory_id=fid, direction="out", to_number=n,
                                      from_number=self.sender_id or "MZALENDO",
                                      message=message, category=category,
                                      status="failed", status_code=0,
                                      error="live mode: " + reason))
            db.session.commit()
            log.error("SMS FAILED (live mode, %s) -> %s", reason, ", ".join(numbers))
            return {"ok": False, "sent": 0, "reason": reason}

        if not self.live:
            for n in numbers:
                db.session.add(SmsLog(factory_id=fid, direction="out", to_number=n,
                                      from_number=self.sender_id or "MZALENDO",
                                      message=message, category=category,
                                      status="simulated", status_code=100,
                                      provider_id="sim-" + secrets.token_hex(5)))
            db.session.commit()
            log.info("SMS (simulated) -> %s :: %s", ", ".join(numbers), message[:70])
            return {"ok": True, "sent": len(numbers), "simulated": True}

        payload = {"username": self.username, "to": ",".join(numbers), "message": message}
        if self.sender_id:
            payload["from"] = self.sender_id
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(
            self.endpoint, data=data, method="POST",
            headers={"apiKey": self.api_key, "Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                body = json.loads(res.read().decode() or "{}")
            recipients = body.get("SMSMessageData", {}).get("Recipients", []) or []
            for r in recipients:
                db.session.add(SmsLog(
                    factory_id=fid, direction="out", to_number=r.get("number", ""),
                    from_number=self.sender_id or "AFRICASTKNG", message=message,
                    category=category, status=r.get("status", "unknown"),
                    status_code=r.get("statusCode"), provider_id=r.get("messageId", ""),
                    cost=r.get("cost", "")))
            if not recipients:
                for n in numbers:
                    db.session.add(SmsLog(factory_id=fid, direction="out", to_number=n,
                                          message=message, category=category,
                                          status="rejected",
                                          error=str(body.get("SMSMessageData", {}).get("Message", ""))[:200]))
            db.session.commit()
            return {"ok": True, "sent": len(recipients), "raw": body}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            self._log_failure(fid, numbers, message, category, f"HTTP {exc.code}: {detail}")
            return {"ok": False, "sent": 0, "reason": f"HTTP {exc.code}"}
        except Exception as exc:                                # pragma: no cover
            self._log_failure(fid, numbers, message, category, str(exc)[:200])
            return {"ok": False, "sent": 0, "reason": str(exc)}

    def _log_failure(self, fid, numbers, message, category, error):
        db.session.rollback()
        for n in numbers:
            db.session.add(SmsLog(factory_id=fid, direction="out", to_number=n,
                                  message=message, category=category,
                                  status="failed", error=error))
        db.session.commit()
        log.error("SMS failed: %s", error)


def notify(factory, recipients, message, category="general"):
    """Fire-and-forget SMS helper used across the operational engine."""
    if not recipients:
        return {"ok": False, "sent": 0}
    return AfricasTalking(factory).send(recipients, message, category)


def factory_managers_phones(factory_id):
    rows = User.query.filter(User.factory_id == factory_id,
                             User.is_active_flag.is_(True),
                             User.role.in_(("owner", "manager"))).all()
    return [u.phone for u in rows if u.phone]


# =============================================================================
#  SECTION 8 — MANUFACTURING PULSE ENGINE
# =============================================================================

PULSE_WEIGHTS = {"production": 0.26, "inventory": 0.22, "orders": 0.22,
                 "maintenance": 0.16, "suppliers": 0.14}


def _band(score: int) -> str:
    return "ok" if score >= 75 else ("warn" if score >= 50 else "bad")


def compute_pulse(factory_id: int) -> dict:
    """
    Operational health for one plant, broken into five subsystems.

    Every subsystem returns a 0-100 score together with the findings that
    produced it, so the dashboard can always answer 'why did this move?'
    rather than presenting an unexplained number.
    """
    today = _today()
    findings = []

    # -- Production ----------------------------------------------------------
    runs = ProductionRun.query.filter(
        ProductionRun.factory_id == factory_id,
        ProductionRun.status.notin_(("done", "cancelled"))).all()
    at_risk = [r for r in runs if r.is_at_risk]
    blocked = [r for r in runs if r.status == "blocked"]
    if runs:
        production = int(round((1 - len(at_risk) / len(runs)) * 100))
    else:
        production = 100
    if at_risk:
        findings.append({
            "area": "production", "severity": "high" if len(at_risk) > 2 else "medium",
            "title": f"{len(at_risk)} production run{'s' if len(at_risk) != 1 else ''} behind schedule",
            "body": "Planned completion dates have passed or the run is blocked on the floor.",
            "action": "Open Production and re-sequence the affected runs or move a worker onto them.",
            "link": "dash_runs",
        })
    if blocked:
        findings.append({
            "area": "production", "severity": "high",
            "title": f"{len(blocked)} run{'s' if len(blocked) != 1 else ''} blocked",
            "body": "A blocked run consumes a slot on the floor without producing anything.",
            "action": "Clear the blocker or release the machine to the next job.",
            "link": "dash_runs",
        })

    # -- Inventory -----------------------------------------------------------
    materials = Material.query.filter_by(factory_id=factory_id, is_active=True).all()
    out = [m for m in materials if m.stock_state == "out"]
    low = [m for m in materials if m.stock_state == "low"]
    if materials:
        healthy = len(materials) - len(out) - len(low)
        inventory = int(round((healthy + len(low) * 0.4) / len(materials) * 100))
    else:
        inventory = 100
    for m in out[:3]:
        findings.append({
            "area": "inventory", "severity": "critical",
            "title": f"{m.name} is out of stock",
            "body": f"Balance is zero against a minimum of {m.min_stock:g} {m.unit}.",
            "action": (f"Raise a purchase order with {m.supplier.name}." if m.supplier
                       else "Assign a supplier and raise a purchase order."),
            "link": "dash_materials",
        })
    if low:
        names = ", ".join(m.name for m in low[:3])
        findings.append({
            "area": "inventory", "severity": "medium",
            "title": f"{len(low)} material{'s' if len(low) != 1 else ''} below minimum",
            "body": f"{names}{' and others' if len(low) > 3 else ''} sit at or under the reorder line.",
            "action": "Review reorder quantities and send purchase orders before the next run starts.",
            "link": "dash_materials",
        })

    # -- Orders --------------------------------------------------------------
    open_orders = Order.query.filter(
        Order.factory_id == factory_id,
        Order.status.notin_(("completed", "cancelled"))).all()
    late = [o for o in open_orders if o.is_late]
    tight = [o for o in open_orders if o.days_left is not None and 0 <= o.days_left <= 2]
    if open_orders:
        orders_score = int(round((1 - (len(late) + 0.4 * len(tight)) / len(open_orders)) * 100))
        orders_score = max(0, min(100, orders_score))
    else:
        orders_score = 100
    if late:
        findings.append({
            "area": "orders", "severity": "critical",
            "title": f"{len(late)} order{'s' if len(late) != 1 else ''} past the promised date",
            "body": "Customers are waiting beyond the date you committed to.",
            "action": "Send a status SMS with a revised date, then prioritise those runs.",
            "link": "dash_orders",
        })
    if tight:
        findings.append({
            "area": "orders", "severity": "medium",
            "title": f"{len(tight)} order{'s' if len(tight) != 1 else ''} due within 48 hours",
            "body": "These need floor capacity today to stay on time.",
            "action": "Confirm the runs are staffed and the materials are drawn.",
            "link": "dash_orders",
        })

    # -- Maintenance ---------------------------------------------------------
    machines = Machine.query.filter(Machine.factory_id == factory_id,
                                    Machine.status != "retired").all()
    down = [m for m in machines if m.status == "down"]
    overdue = [m for m in machines if m.service_state == "overdue"]
    due_soon = [m for m in machines if m.service_state == "due"]
    open_tickets = MaintenanceTicket.query.filter(
        MaintenanceTicket.factory_id == factory_id,
        MaintenanceTicket.status.notin_(("resolved", "closed"))).count()
    if machines:
        penalty = (len(down) * 1.0 + len(overdue) * 0.6 + len(due_soon) * 0.25) / len(machines)
        maintenance = int(round(max(0.0, 1 - penalty) * 100))
        maintenance = max(0, maintenance - min(20, open_tickets * 4))
    else:
        maintenance = 100
    if down:
        findings.append({
            "area": "maintenance", "severity": "critical",
            "title": f"{len(down)} machine{'s' if len(down) != 1 else ''} down",
            "body": ", ".join(m.name for m in down[:4]) + " stopped production.",
            "action": "Assign a technician from Maintenance and record the downtime.",
            "link": "dash_machines",
        })
    if overdue:
        findings.append({
            "area": "maintenance", "severity": "high",
            "title": f"{len(overdue)} machine{'s' if len(overdue) != 1 else ''} overdue for service",
            "body": "Servicing has slipped past the interval you set for these machines.",
            "action": "Book the service window and update the last service date.",
            "link": "dash_machines",
        })

    # -- Suppliers -----------------------------------------------------------
    suppliers = Supplier.query.filter_by(factory_id=factory_id, is_active=True).all()
    weak = [s for s in suppliers if s.reliability < 60]
    overdue_pos = PurchaseOrder.query.filter(
        PurchaseOrder.factory_id == factory_id,
        PurchaseOrder.status.notin_(("received", "cancelled")),
        PurchaseOrder.expected_date < today).count()
    if suppliers:
        supplier_score = int(round(sum(s.reliability for s in suppliers) / len(suppliers)))
        supplier_score = max(0, supplier_score - min(25, overdue_pos * 6))
    else:
        supplier_score = 70
    if weak:
        findings.append({
            "area": "suppliers", "severity": "medium",
            "title": f"{len(weak)} supplier{'s' if len(weak) != 1 else ''} scoring below 60",
            "body": ", ".join(s.name for s in weak[:3]) + " are late or short on deliveries.",
            "action": "Add a second source for the materials these suppliers cover.",
            "link": "dash_suppliers",
        })
    if overdue_pos:
        findings.append({
            "area": "suppliers", "severity": "high",
            "title": f"{overdue_pos} purchase order{'s' if overdue_pos != 1 else ''} past the expected date",
            "body": "Material you have already committed to has not arrived.",
            "action": "Call the supplier, then update the expected date so planning stays honest.",
            "link": "dash_purchase_orders",
        })

    scores = {"production": production, "inventory": inventory, "orders": orders_score,
              "maintenance": maintenance, "suppliers": supplier_score}
    overall = int(round(sum(scores[k] * PULSE_WEIGHTS[k] for k in scores)))

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 4))

    return {
        "scores": scores,
        "overall": overall,
        "bands": {k: _band(v) for k, v in scores.items()},
        "overall_band": _band(overall),
        "findings": findings,
        "counts": {
            "runs": len(runs), "at_risk": len(at_risk), "materials": len(materials),
            "low": len(low), "out": len(out), "orders": len(open_orders),
            "late": len(late), "machines": len(machines), "down": len(down),
            "tickets": open_tickets, "suppliers": len(suppliers),
        },
        "taken_at": _now(),
    }


def snapshot_pulse(factory_id: int, pulse: dict | None = None):
    """Persist one reading, at most one per hour, so trends can be charted."""
    pulse = pulse or compute_pulse(factory_id)
    latest = (PulseSnapshot.query.filter_by(factory_id=factory_id)
              .order_by(desc(PulseSnapshot.taken_at)).first())
    if latest and (_now() - latest.taken_at) < timedelta(hours=1):
        return latest
    snap = PulseSnapshot(factory_id=factory_id, overall=pulse["overall"],
                         **{k: v for k, v in pulse["scores"].items()})
    db.session.add(snap)
    db.session.commit()
    return snap


def raise_alert(factory_id, kind, severity, title, body="", recommendation="",
                entity_type="", entity_id=None, dedupe_hours=6):
    """Create an alert unless an identical one was raised very recently."""
    if dedupe_hours:
        cutoff = _now() - timedelta(hours=dedupe_hours)
        dup = Alert.query.filter(Alert.factory_id == factory_id, Alert.title == title,
                                 Alert.created_at >= cutoff).first()
        if dup:
            return dup
    a = Alert(factory_id=factory_id, kind=kind, severity=severity, title=title,
              body=body, recommendation=recommendation,
              entity_type=entity_type, entity_id=entity_id)
    db.session.add(a)
    db.session.commit()
    return a


def check_material_level(material: Material, factory: Factory, silent=False):
    """Called after every stock movement — the low-stock SMS trigger."""
    if material.stock_state in ("low", "out") and not silent:
        state = "is out of stock" if material.stock_state == "out" else "is below the minimum level"
        raise_alert(
            factory.id, "inventory",
            "critical" if material.stock_state == "out" else "high",
            f"{material.name} {state}",
            f"Balance {material.quantity:g} {material.unit} against a minimum of {material.min_stock:g}.",
            (f"Order {material.reorder_qty:g} {material.unit} from "
             f"{material.supplier.name}." if material.supplier and material.reorder_qty
             else "Raise a purchase order to restore cover."),
            "material", material.id,
        )
        phones = factory_managers_phones(factory.id)
        if phones:
            notify(factory, phones,
                   f"Mzalendo: {material.name} {state}. Current: {material.quantity:g} "
                   f"{material.unit}. Minimum: {material.min_stock:g} {material.unit}.",
                   "stock_alert")


def move_stock(material: Material, kind: str, qty: float, factory: Factory,
               reference="", note="", source="web", user_id=None, worker_id=None):
    """Single choke point for stock changes so the ledger is always complete."""
    qty = abs(to_float(qty))
    if kind in ("out", "waste"):
        material.quantity = max(0.0, (material.quantity or 0) - qty)
    elif kind == "adjust":
        material.quantity = qty
    else:
        material.quantity = (material.quantity or 0) + qty
    material.updated_at = _now()
    db.session.add(StockMovement(
        factory_id=factory.id, material_id=material.id, kind=kind, quantity=qty,
        balance_after=material.quantity, reference=reference, note=note[:240],
        source=source, user_id=user_id, worker_id=worker_id))
    db.session.commit()
    check_material_level(material, factory)
    return material


# =============================================================================
#  SECTION 9 — TEMPLATE CONTEXT & FILTERS
# =============================================================================

def _pairs(values, labelise: bool = True):
    """['a','b'] -> [('a','A'), ('b','B')] for <select> rendering."""
    out = []
    for v in values:
        if isinstance(v, (tuple, list)) and len(v) == 2:
            out.append((v[0], v[1]))
        else:
            out.append((v, str(v).replace("_", " ").capitalize() if labelise else v))
    return out


def _obj_options(rows, label_attr: str = "name", value_attr: str = "id"):
    """SQLAlchemy rows -> [(id, name)] for <select> rendering."""
    return [(getattr(r, value_attr), getattr(r, label_attr)) for r in rows]


_ASSET_FINGERPRINTS = {}


def asset_url(filename: str) -> str:
    """static/… with a content fingerprint appended.

    Without this, a browser that cached app.js keeps running it after a deploy
    while another that fetched fresh runs the new one — the two disagree and
    only one of them shows the bug. A changed file gets a new URL instead.
    """
    if filename not in _ASSET_FINGERPRINTS:
        path = os.path.join(app.static_folder, filename)
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()[:10]
        except OSError:
            digest = APP_VERSION
        _ASSET_FINGERPRINTS[filename] = digest
    return url_for("static", filename=filename) + "?v=" + _ASSET_FINGERPRINTS[filename]


@app.context_processor
def inject_globals():
    fac = current_factory() if current_user.is_authenticated else None
    unread = 0
    if fac:
        unread = Alert.query.filter_by(factory_id=fac.id, is_read=False).count()
    factories_all = []
    if current_user.is_authenticated and current_user.is_super:
        factories_all = Factory.query.order_by(Factory.name).all()
    return {
        "factories_all": factories_all,
        "APP_NAME": APP_NAME,
        "APP_TAGLINE": APP_TAGLINE,
        "APP_VERSION": APP_VERSION,
        "csp_nonce": getattr(g, "csp_nonce", ""),
        "factory": fac,
        "unread_alerts": unread,
        "ROLES": ROLES,
        "ORDER_STATUSES": ORDER_STATUSES,
        "ORDER_STATUS_LABELS": ORDER_STATUS_LABELS,
        "RUN_STATUSES": RUN_STATUSES,
        "PO_STATUSES": PO_STATUSES,
        "MACHINE_STATUSES": MACHINE_STATUSES,
        "TICKET_STATUSES": TICKET_STATUSES,
        "SEVERITIES": SEVERITIES,
        "UNITS": UNITS,
        "FAULT_TYPES": FAULT_TYPES,
        "INCIDENT_TYPES": INCIDENT_TYPES,
        "DEFAULT_STAGES": DEFAULT_STAGES,
        "pairs": _pairs,
        "obj_options": _obj_options,
        "now": _now(),          # naive UTC; the dt filter localises it
        "TZ_LABEL": TZ_LABEL,
        "ALLOW_PUBLIC_SIGNUP": app.config["ALLOW_PUBLIC_SIGNUP"],
        # Public calls to action point at sign-in when self-service signup is
        # switched off, so a visitor never lands on a door that will not open.
        # One source of truth for the public call to action. When self-service
        # signup is switched off there is no second destination to offer, so
        # the header shows a single button rather than "Sign in" twice.
        "cta_url": url_for("signup") if app.config["ALLOW_PUBLIC_SIGNUP"] else url_for("login"),
        "cta_label": "Start free" if app.config["ALLOW_PUBLIC_SIGNUP"] else "Sign in",
        "cta_label_long": ("Set up your plant" if app.config["ALLOW_PUBLIC_SIGNUP"]
                           else "Sign in to your plant"),
        "asset": asset_url,
        "today": _today(),
        "ussd_code": (fac.ussd_code if fac and fac.ussd_code else app.config["AT_USSD_CODE"]),
        "qs_without": qs_without,
    }


@app.template_filter("money")
def _f_money(value, currency=None):
    fac = current_factory() if current_user.is_authenticated else None
    return money(value, currency or (fac.currency if fac else "KES"))


@app.template_filter("money_kpi")
def _f_money_kpi(value, currency=None):
    """Money for a headline tile: drops a trailing .00 so a KPI reads
    'KES 926,500' rather than wrapping. Lossless — anything with real cents
    keeps them, and tables still use the full |money filter."""
    fac = current_factory() if current_user.is_authenticated else None
    cur = currency or (fac.currency if fac else "KES")
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    if abs(v - round(v)) < 0.005:
        return f"{cur} {round(v):,.0f}"
    return f"{cur} {v:,.2f}"


@app.template_filter("qty")
def _f_qty(value):
    try:
        v = float(value or 0)
        return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    except (TypeError, ValueError):
        return "0"


@app.template_filter("dt")
def _f_dt(value, fmt="%d %b %Y, %H:%M"):
    local = _to_local(value)
    return local.strftime(fmt) if local else "—"


@app.template_filter("d")
def _f_d(value, fmt="%d %b %Y"):
    # Date columns are already calendar days; datetimes need shifting first.
    if isinstance(value, datetime):
        value = _to_local(value)
    return value.strftime(fmt) if value else "—"


@app.template_filter("ago")
def _f_ago(value):
    if not value:
        return "—"
    delta = _now() - value
    s = int(delta.total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    if s < 604800:
        return f"{s // 86400}d ago"
    return value.strftime("%d %b %Y")


@app.template_filter("titlecase")
def _f_title(value):
    return str(value or "").replace("_", " ").capitalize()


@app.template_filter("initials")
def _f_initials(value):
    bits = [b for b in str(value or "").split() if b]
    if not bits:
        return "?"
    return (bits[0][0] + (bits[-1][0] if len(bits) > 1 else "")).upper()


# =============================================================================
#  SECTION 10 — PUBLIC SITE
# =============================================================================

@app.route("/")
def home():
    stats = {
        "factories": Factory.query.filter_by(is_active=True).count(),
        "workers": Worker.query.filter_by(is_active=True).count(),
        "messages": SmsLog.query.count(),
        "sessions": UssdSession.query.count(),
    }
    return render_template("public/index.html", stats=stats)


@app.route("/platform")
def platform():
    return render_template("public/platform.html")


@app.route("/pricing")
def pricing():
    return render_template("public/pricing.html")


@app.route("/jua-kali")
def jua_kali():
    return render_template("public/jua_kali.html")


@app.route("/security")
def security_page():
    return render_template("public/security.html")


@app.route("/healthz")
def healthz():
    try:
        db.session.execute(db.text("SELECT 1"))
        status = "ok"
    except Exception as exc:                                   # pragma: no cover
        return jsonify(status="degraded", detail=str(exc)[:120]), 503
    return jsonify(status=status, app=APP_NAME, version=APP_VERSION,
                   time=_now().isoformat() + "Z")


@app.route("/robots.txt")
def robots():
    body = "User-agent: *\nDisallow: /dashboard\nDisallow: /account\nAllow: /\n"
    return Response(body, mimetype="text/plain")


@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="img/favicon.svg"), code=301)


# =============================================================================
#  SECTION 11 — AUTHENTICATION
# =============================================================================

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("12 per minute; 60 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dash_overview"))

    if request.method == "POST":
        ident = (request.form.get("identity") or "").strip().lower()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))

        user = User.query.filter(
            or_(func.lower(User.username) == ident, func.lower(User.email) == ident)
        ).first()

        if user and user.is_locked:
            mins = max(1, int((user.locked_until - _now()).total_seconds() // 60))
            flash(f"This account is locked for another {mins} minute(s) after repeated "
                  "failed attempts.", "error")
            return render_template("auth/login.html", identity=ident), 429

        if not user or not user.check_password(password):
            if user:
                user.failed_logins = (user.failed_logins or 0) + 1
                if user.failed_logins >= 6:
                    user.locked_until = _now() + timedelta(minutes=15)
                    user.failed_logins = 0
                db.session.commit()
            system_audit(getattr(user, "factory_id", None), ident or "unknown",
                         "login_failed", "user", getattr(user, "id", None),
                         request.headers.get("X-Forwarded-For", request.remote_addr or ""))
            flash("Those details do not match an account.", "error")
            return render_template("auth/login.html", identity=ident), 401

        if not user.is_active_flag:
            flash("This account has been deactivated. Talk to your plant owner.", "error")
            return render_template("auth/login.html", identity=ident), 403

        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = _now()
        user.last_login_ip = (request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:64]
        db.session.commit()

        login_user(user, remember=remember, duration=timedelta(days=14))
        session.permanent = True
        if user.is_super:
            session["factory_ctx"] = user.factory_id or (
                Factory.query.order_by(Factory.id).first().id
                if Factory.query.count() else None)
        audit("login", "user", user.id, f"role={user.role}")

        if user.must_change_password:
            flash("Set a new password before you continue.", "warn")
            return redirect(url_for("change_password"))

        nxt = request.args.get("next") or request.form.get("next") or ""
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(url_for("dash_overview"))

    return render_template("auth/login.html", identity="")


@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("6 per minute; 25 per hour", methods=["POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dash_overview"))
    if not app.config["ALLOW_PUBLIC_SIGNUP"]:
        # Not a 403. Nothing is wrong with this visitor's role — the feature is
        # switched off for the whole installation, and telling them their
        # "role does not open this door" is both confusing and untrue when
        # they are not signed in at all.
        flash("New plants are not created here. Ask your plant owner for an "
              "account, or sign in if you already have one.", "info")
        return redirect(url_for("login"))

    form = {k: (v or "").strip() for k, v in request.form.items()}
    errors = {}

    if request.method == "POST":
        required = {
            "factory_name": "Workshop or factory name",
            "full_name": "Your name",
            "username": "Username",
            "email": "Email",
            "password": "Password",
        }
        for field, label in required.items():
            if not form.get(field):
                errors[field] = f"{label} is required."

        uname = form.get("username", "").lower()
        if uname and not re.fullmatch(r"[a-z0-9_.]{3,32}", uname):
            errors["username"] = "Use 3–32 lowercase letters, digits, dots or underscores."
        email = form.get("email", "").lower()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
            errors["email"] = "That email address does not look right."
        if uname and User.query.filter(func.lower(User.username) == uname).first():
            errors["username"] = "That username is taken."
        if email and User.query.filter(func.lower(User.email) == email).first():
            errors["email"] = "An account already uses that email."
        problems = password_problems(form.get("password", ""))
        if problems:
            errors["password"] = "Password needs " + ", ".join(problems) + "."
        if form.get("password") != form.get("password2"):
            errors["password2"] = "The two passwords do not match."
        if not request.form.get("terms"):
            errors["terms"] = "Accept the terms to create an account."

        if not errors:
            base = slugify(form["factory_name"])
            slug = base
            n = 1
            while Factory.query.filter_by(slug=slug).first():
                n += 1
                slug = f"{base}-{n}"

            fac = Factory(
                name=form["factory_name"], slug=slug,
                sector=form.get("sector") or "General manufacturing",
                county=form.get("county") or "Nairobi",
                phone=norm_phone(form.get("phone", "")),
                email=email, plan="starter",
                ussd_code=app.config["AT_USSD_CODE"],
            )
            db.session.add(fac)
            db.session.flush()

            user = User(factory_id=fac.id, username=uname, email=email,
                        full_name=form["full_name"],
                        phone=norm_phone(form.get("phone", "")), role="owner")
            user.set_password(form["password"])
            db.session.add(user)
            db.session.commit()

            login_user(user)
            session.permanent = True
            audit("signup", "factory", fac.id, f"plant={fac.name}")
            flash(f"{fac.name} is live. Start by adding your materials and machines.", "ok")
            return redirect(url_for("dash_overview"))

    return render_template("auth/signup.html", form=form, errors=errors)


@app.route("/logout")
@login_required
def logout():
    audit("logout", "user", current_user.id)
    logout_user()
    session.clear()
    flash("Signed out. The plant keeps running.", "info")
    return redirect(url_for("home"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def change_password():
    forced = bool(current_user.must_change_password)
    errors = {}
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not current_user.check_password(current):
            errors["current_password"] = "That is not your current password."
        problems = password_problems(new)
        if problems:
            errors["new_password"] = "Password needs " + ", ".join(problems) + "."
        if new != confirm:
            errors["confirm_password"] = "The two passwords do not match."
        if new and current and new == current:
            errors["new_password"] = "Choose a password you have not used here before."

        if not errors:
            current_user.set_password(new)
            current_user.must_change_password = False
            db.session.commit()
            audit("password_changed", "user", current_user.id)
            flash("Password updated.", "ok")
            return redirect(url_for("dash_overview"))

    return render_template("auth/change_password.html", forced=forced, errors=errors)


@app.route("/account/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.full_name = (request.form.get("full_name") or "").strip()[:160]
        current_user.phone = norm_phone(request.form.get("phone", ""))
        new_email = (request.form.get("email") or "").strip().lower()
        if new_email and new_email != current_user.email:
            clash = User.query.filter(func.lower(User.email) == new_email,
                                      User.id != current_user.id).first()
            if clash:
                flash("Another account already uses that email.", "error")
                return redirect(url_for("profile"))
            current_user.email = new_email
        db.session.commit()
        audit("profile_updated", "user", current_user.id)
        flash("Profile saved.", "ok")
        return redirect(url_for("profile"))
    recent = (AuditLog.query.filter_by(user_id=current_user.id)
              .order_by(desc(AuditLog.created_at)).limit(12).all())
    return render_template("dash/profile.html", recent=recent)


# =============================================================================
#  SECTION 12 — DASHBOARD OVERVIEW
# =============================================================================

@app.route("/dashboard")
@login_required
def dash_overview():
    fac = current_factory()
    if not fac:
        flash("No plant is attached to this account yet.", "warn")
        return redirect(url_for("admin_factories")) if current_user.is_super else abort(403)

    pulse = compute_pulse(fac.id)
    snapshot_pulse(fac.id, pulse)

    today = _today()
    open_orders = Order.query.filter(Order.factory_id == fac.id,
                                     Order.status.notin_(("completed", "cancelled")))
    kpis = {
        "open_orders": open_orders.count(),
        "due_this_week": open_orders.filter(
            Order.due_date <= today + timedelta(days=7)).count(),
        "active_runs": ProductionRun.query.filter(
            ProductionRun.factory_id == fac.id,
            ProductionRun.status.in_(("planned", "running", "paused", "blocked"))).count(),
        "low_stock": pulse["counts"]["low"] + pulse["counts"]["out"],
        "machines_down": pulse["counts"]["down"],
        "open_tickets": pulse["counts"]["tickets"],
        "workers": Worker.query.filter_by(factory_id=fac.id, is_active=True).count(),
        "present_today": Attendance.query.filter_by(factory_id=fac.id, day=today).count(),
        "stock_value": round(sum(m.value for m in Material.query.filter_by(
            factory_id=fac.id, is_active=True).all()), 2),
        "order_value": round(sum(o.total for o in open_orders.all()), 2),
        "sms_30d": SmsLog.query.filter(SmsLog.factory_id == fac.id,
                                       SmsLog.created_at >= _now() - timedelta(days=30)).count(),
        "ussd_30d": UssdSession.query.filter(UssdSession.factory_id == fac.id,
                                             UssdSession.started_at >= _now() - timedelta(days=30)).count(),
    }

    alerts = (Alert.query.filter_by(factory_id=fac.id)
              .order_by(Alert.is_read.asc(), desc(Alert.created_at)).limit(8).all())

    runs = (ProductionRun.query.filter(ProductionRun.factory_id == fac.id,
                                       ProductionRun.status.notin_(("done", "cancelled")))
            .order_by(ProductionRun.end_date.asc().nulls_last()).limit(6).all())

    upcoming = (Order.query.filter(Order.factory_id == fac.id,
                                   Order.status.notin_(("completed", "cancelled")))
                .order_by(Order.due_date.asc().nulls_last()).limit(6).all())

    low_materials = sorted(
        [m for m in Material.query.filter_by(factory_id=fac.id, is_active=True).all()
         if m.stock_state in ("out", "low", "watch")],
        key=lambda m: m.stock_pct)[:6]

    tickets = (MaintenanceTicket.query.filter(
        MaintenanceTicket.factory_id == fac.id,
        MaintenanceTicket.status.notin_(("resolved", "closed")))
        .order_by(desc(MaintenanceTicket.created_at)).limit(5).all())

    feed = (UssdSession.query.filter_by(factory_id=fac.id)
            .order_by(desc(UssdSession.started_at)).limit(6).all())

    # 14 day production throughput
    since = today - timedelta(days=13)
    per_day = defaultdict(float)
    for r in ProductionRun.query.filter(ProductionRun.factory_id == fac.id,
                                        ProductionRun.updated_at >= datetime.combine(since, datetime.min.time())).all():
        key = (r.end_date or r.updated_at.date())
        if key >= since:
            per_day[key] += (r.produced or 0)
    series = [{"d": (since + timedelta(days=i)).isoformat(),
               "v": round(per_day.get(since + timedelta(days=i), 0), 2)} for i in range(14)]

    history = (PulseSnapshot.query.filter_by(factory_id=fac.id)
               .order_by(desc(PulseSnapshot.taken_at)).limit(30).all())[::-1]

    return render_template("dash/overview.html", pulse=pulse, kpis=kpis, alerts=alerts,
                           runs=runs, upcoming=upcoming, low_materials=low_materials,
                           tickets=tickets, feed=feed, series=series,
                           history=[{"t": _to_local(h.taken_at).strftime("%d %b %H:%M"), "v": h.overall}
                                    for h in history])


@app.route("/dashboard/pulse")
@login_required
def dash_pulse():
    fac = current_factory()
    pulse = compute_pulse(fac.id)
    history = (PulseSnapshot.query.filter_by(factory_id=fac.id)
               .order_by(desc(PulseSnapshot.taken_at)).limit(60).all())[::-1]
    return render_template("dash/pulse.html", pulse=pulse, history=history)


@app.route("/dashboard/switch/<int:factory_id>")
@roles_required("super_admin")
def switch_factory(factory_id):
    fac = db.session.get(Factory, factory_id) or abort(404)
    session["factory_ctx"] = fac.id
    flash(f"Now viewing {fac.name}.", "info")
    return redirect(request.referrer or url_for("dash_overview"))


# =============================================================================
#  SECTION 13 — ALERTS
# =============================================================================

@app.route("/dashboard/alerts")
@login_required
def dash_alerts():
    fac = current_factory()
    q = Alert.query.filter_by(factory_id=fac.id)
    kind = request.args.get("kind", "")
    sev = request.args.get("severity", "")
    unread_only = request.args.get("unread") == "1"
    if kind:
        q = q.filter(Alert.kind == kind)
    if sev:
        q = q.filter(Alert.severity == sev)
    if unread_only:
        q = q.filter(Alert.is_read.is_(False))
    page = Page(q.order_by(desc(Alert.created_at)), page_args(), 25)
    return render_template("dash/alerts.html", page=page, kind=kind, sev=sev,
                           unread_only=unread_only)


@app.post("/dashboard/alerts/<int:alert_id>/read")
@login_required
def alert_read(alert_id):
    a = get_scoped_or_404(Alert, alert_id)
    a.is_read = True
    db.session.commit()
    return redirect(request.referrer or url_for("dash_alerts"))


@app.post("/dashboard/alerts/read-all")
@login_required
def alerts_read_all():
    Alert.query.filter_by(factory_id=current_factory_id(), is_read=False).update(
        {"is_read": True}, synchronize_session=False)
    db.session.commit()
    flash("All alerts marked as read.", "ok")
    return redirect(request.referrer or url_for("dash_alerts"))


@app.post("/dashboard/alerts/<int:alert_id>/delete")
@write_required
def alert_delete(alert_id):
    a = get_scoped_or_404(Alert, alert_id)
    db.session.delete(a)
    db.session.commit()
    flash("Alert removed.", "ok")
    return redirect(request.referrer or url_for("dash_alerts"))


# =============================================================================
#  SECTION 14 — MATERIALS & STOCK
# =============================================================================

@app.route("/dashboard/materials")
@login_required
def dash_materials():
    fac = current_factory()
    q = scoped(Material)
    term = (request.args.get("q") or "").strip()
    state = request.args.get("state", "")
    cat = request.args.get("category", "")
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Material.name.ilike(like), Material.code.ilike(like),
                         Material.category.ilike(like)))
    if cat:
        q = q.filter(Material.category == cat)
    rows = q.order_by(Material.name).all()
    if state:
        rows = [m for m in rows if m.stock_state == state]

    per_page = 20
    p = max(1, page_args())
    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    p = min(p, pages)
    items = rows[(p - 1) * per_page: p * per_page]

    categories = sorted({m.category for m in scoped(Material).all() if m.category})
    totals = {
        "count": total,
        "value": round(sum(m.value for m in rows), 2),
        "low": len([m for m in rows if m.stock_state == "low"]),
        "out": len([m for m in rows if m.stock_state == "out"]),
    }
    return render_template("dash/materials.html", items=items, term=term, state=state,
                           cat=cat, categories=categories, totals=totals,
                           p=p, pages=pages, total=total,
                           suppliers=scoped(Supplier).order_by(Supplier.name).all())


@app.route("/dashboard/materials/new", methods=["GET", "POST"])
@app.route("/dashboard/materials/<int:material_id>/edit", methods=["GET", "POST"])
@write_required
def material_form(material_id=None):
    fac = current_factory()
    item = get_scoped_or_404(Material, material_id) if material_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        code = (f.get("code") or "").strip().upper()
        if not name:
            errors["name"] = "Give the material a name."
        if not code:
            code = next_ref(Material, current_factory_id(), "MAT", "code")
        clash = scoped(Material).filter(Material.code == code,
                                        Material.id != (item.id if item else 0)).first()
        if clash:
            errors["code"] = "Another material already uses that code."

        if not errors:
            obj = item or Material(factory_id=fac.id)
            was_new = item is None
            obj.name, obj.code = name, code
            obj.category = (f.get("category") or "Raw material").strip()
            obj.unit = f.get("unit") or "pcs"
            obj.min_stock = to_float(f.get("min_stock"))
            obj.reorder_qty = to_float(f.get("reorder_qty"))
            obj.unit_cost = to_float(f.get("unit_cost"))
            obj.location = (f.get("location") or "").strip()
            obj.supplier_id = to_int(f.get("supplier_id")) or None
            obj.is_active = f.get("is_active") == "on"
            new_qty = to_float(f.get("quantity"), obj.quantity or 0)
            if was_new:
                obj.quantity = new_qty
                db.session.add(obj)
                db.session.commit()
                if new_qty:
                    db.session.add(StockMovement(
                        factory_id=fac.id, material_id=obj.id, kind="in", quantity=new_qty,
                        balance_after=new_qty, reference="Opening balance",
                        source="web", user_id=current_user.id))
                    db.session.commit()
            else:
                db.session.commit()
                if abs(new_qty - (obj.quantity or 0)) > 1e-9:
                    move_stock(obj, "adjust", new_qty, fac, reference="Manual correction",
                               note="Edited from the materials screen",
                               user_id=current_user.id)
            check_material_level(obj, fac)
            audit("material_saved", "material", obj.id, obj.name)
            flash(f"{obj.name} saved.", "ok")
            return redirect(url_for("dash_materials"))

    return render_template("dash/material_form.html", item=item, errors=errors,
                           suppliers=scoped(Supplier).order_by(Supplier.name).all())


@app.route("/dashboard/materials/<int:material_id>")
@login_required
def material_detail(material_id):
    item = get_scoped_or_404(Material, material_id)
    moves = (StockMovement.query.filter_by(material_id=item.id)
             .order_by(desc(StockMovement.created_at)).limit(60).all())
    used_in = BomItem.query.filter_by(material_id=item.id).all()
    return render_template("dash/material_detail.html", item=item, moves=moves,
                           used_in=used_in)


@app.post("/dashboard/materials/<int:material_id>/move")
@write_required
def material_move(material_id):
    fac = current_factory()
    item = get_scoped_or_404(Material, material_id)
    kind = request.form.get("kind", "in")
    qty = to_float(request.form.get("quantity"))
    if qty <= 0 and kind != "adjust":
        flash("Enter a quantity greater than zero.", "error")
        return redirect(url_for("material_detail", material_id=item.id))
    move_stock(item, kind, qty, fac,
               reference=(request.form.get("reference") or "").strip(),
               note=(request.form.get("note") or "").strip(),
               user_id=current_user.id)
    audit("stock_move", "material", item.id, f"{kind} {qty}")
    flash(f"Stock updated. {item.name} now reads {item.quantity:g} {item.unit}.", "ok")
    return redirect(url_for("material_detail", material_id=item.id))


@app.post("/dashboard/materials/<int:material_id>/delete")
@write_required
def material_delete(material_id):
    item = get_scoped_or_404(Material, material_id)
    if BomItem.query.filter_by(material_id=item.id).count():
        flash("This material is used in a bill of materials. Deactivate it instead.", "error")
        return redirect(url_for("dash_materials"))
    StockMovement.query.filter_by(material_id=item.id).delete()
    POItem.query.filter_by(material_id=item.id).delete()
    name = item.name
    db.session.delete(item)
    db.session.commit()
    audit("material_deleted", "material", material_id, name)
    flash(f"{name} deleted.", "ok")
    return redirect(url_for("dash_materials"))


@app.route("/dashboard/stock-ledger")
@login_required
def dash_stock_ledger():
    q = StockMovement.query.filter_by(factory_id=current_factory_id())
    kind = request.args.get("kind", "")
    mat = to_int(request.args.get("material_id"))
    if kind:
        q = q.filter(StockMovement.kind == kind)
    if mat:
        q = q.filter(StockMovement.material_id == mat)
    page = Page(q.order_by(desc(StockMovement.created_at)), page_args(), 30)
    return render_template("dash/stock_ledger.html", page=page, kind=kind, mat=mat,
                           materials=scoped(Material).order_by(Material.name).all())


# =============================================================================
#  SECTION 15 — SUPPLIERS & PROCUREMENT
# =============================================================================

@app.route("/dashboard/suppliers")
@login_required
def dash_suppliers():
    q = scoped(Supplier)
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Supplier.name.ilike(like), Supplier.contact_name.ilike(like),
                         Supplier.materials_supplied.ilike(like)))
    page = Page(q.order_by(Supplier.name), page_args(), 20)
    return render_template("dash/suppliers.html", page=page, term=term)


@app.route("/dashboard/suppliers/new", methods=["GET", "POST"])
@app.route("/dashboard/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@write_required
def supplier_form(supplier_id=None):
    fac = current_factory()
    item = get_scoped_or_404(Supplier, supplier_id) if supplier_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        if not name:
            errors["name"] = "Give the supplier a name."
        if not errors:
            obj = item or Supplier(factory_id=fac.id)
            obj.name = name
            obj.contact_name = (f.get("contact_name") or "").strip()
            obj.phone = norm_phone(f.get("phone", ""))
            obj.email = (f.get("email") or "").strip().lower()
            obj.address = (f.get("address") or "").strip()
            obj.materials_supplied = (f.get("materials_supplied") or "").strip()
            obj.lead_time_days = to_int(f.get("lead_time_days"), 7)
            obj.payment_terms = (f.get("payment_terms") or "On delivery").strip()
            obj.notes = (f.get("notes") or "").strip()
            obj.is_active = f.get("is_active") == "on"
            if current_user.at_least("owner"):
                obj.orders_placed = to_int(f.get("orders_placed"), obj.orders_placed or 0)
                obj.orders_on_time = to_int(f.get("orders_on_time"), obj.orders_on_time or 0)
                obj.orders_complete = to_int(f.get("orders_complete"), obj.orders_complete or 0)
                obj.defect_reports = to_int(f.get("defect_reports"), obj.defect_reports or 0)
            db.session.add(obj)
            db.session.commit()
            audit("supplier_saved", "supplier", obj.id, obj.name)
            flash(f"{obj.name} saved.", "ok")
            return redirect(url_for("dash_suppliers"))
    return render_template("dash/supplier_form.html", item=item, errors=errors)


@app.route("/dashboard/suppliers/<int:supplier_id>")
@login_required
def supplier_detail(supplier_id):
    item = get_scoped_or_404(Supplier, supplier_id)
    pos = (PurchaseOrder.query.filter_by(supplier_id=item.id)
           .order_by(desc(PurchaseOrder.created_at)).limit(25).all())
    materials = scoped(Material).filter_by(supplier_id=item.id).all()
    return render_template("dash/supplier_detail.html", item=item, pos=pos,
                           materials=materials)


@app.post("/dashboard/suppliers/<int:supplier_id>/delete")
@write_required
def supplier_delete(supplier_id):
    item = get_scoped_or_404(Supplier, supplier_id)
    if PurchaseOrder.query.filter_by(supplier_id=item.id).count():
        flash("This supplier has purchase orders on record. Deactivate it instead.", "error")
        return redirect(url_for("dash_suppliers"))
    Material.query.filter_by(supplier_id=item.id).update({"supplier_id": None})
    name = item.name
    db.session.delete(item)
    db.session.commit()
    audit("supplier_deleted", "supplier", supplier_id, name)
    flash(f"{name} deleted.", "ok")
    return redirect(url_for("dash_suppliers"))


@app.post("/dashboard/suppliers/<int:supplier_id>/message")
@write_required
def supplier_message(supplier_id):
    fac = current_factory()
    item = get_scoped_or_404(Supplier, supplier_id)
    msg = (request.form.get("message") or "").strip()
    if not msg:
        flash("Write a message first.", "error")
    elif not item.phone:
        flash("This supplier has no phone number on file.", "error")
    else:
        res = notify(fac, [item.phone], msg, "supplier")
        flash("Message sent." if res.get("ok") else "The gateway rejected that message.",
              "ok" if res.get("ok") else "error")
        audit("supplier_sms", "supplier", item.id, msg[:80])
    return redirect(url_for("supplier_detail", supplier_id=item.id))


@app.route("/dashboard/purchase-orders")
@login_required
def dash_purchase_orders():
    q = scoped(PurchaseOrder)
    status = request.args.get("status", "")
    if status:
        q = q.filter(PurchaseOrder.status == status)
    page = Page(q.order_by(desc(PurchaseOrder.created_at)), page_args(), 20)
    return render_template("dash/purchase_orders.html", page=page, status=status)


@app.route("/dashboard/purchase-orders/new", methods=["GET", "POST"])
@app.route("/dashboard/purchase-orders/<int:po_id>/edit", methods=["GET", "POST"])
@write_required
def po_form(po_id=None):
    fac = current_factory()
    item = get_scoped_or_404(PurchaseOrder, po_id) if po_id else None
    errors = {}
    materials = scoped(Material).order_by(Material.name).all()
    suppliers = scoped(Supplier).filter_by(is_active=True).order_by(Supplier.name).all()

    if request.method == "POST":
        f = request.form
        supplier_id = to_int(f.get("supplier_id"))
        if not supplier_id:
            errors["supplier_id"] = "Choose the supplier."
        mat_ids = f.getlist("material_id[]")
        qtys = f.getlist("quantity[]")
        costs = f.getlist("unit_cost[]")
        lines = [(to_int(m), to_float(q), to_float(c))
                 for m, q, c in zip(mat_ids, qtys, costs) if to_int(m) and to_float(q) > 0]
        if not lines:
            errors["lines"] = "Add at least one material line."

        if not errors:
            obj = item or PurchaseOrder(factory_id=fac.id,
                                        number=next_ref(PurchaseOrder, fac.id, "PO", "number"))
            obj.supplier_id = supplier_id
            obj.status = f.get("status") or "draft"
            obj.expected_date = to_date(f.get("expected_date"))
            obj.notes = (f.get("notes") or "").strip()
            db.session.add(obj)
            db.session.flush()
            POItem.query.filter_by(po_id=obj.id).delete()
            for mid, qty, cost in lines:
                db.session.add(POItem(po_id=obj.id, material_id=mid,
                                      quantity=qty, unit_cost=cost))
            db.session.commit()
            audit("po_saved", "purchase_order", obj.id, obj.number)
            flash(f"Purchase order {obj.number} saved.", "ok")
            return redirect(url_for("po_detail", po_id=obj.id))

    return render_template("dash/po_form.html", item=item, errors=errors,
                           materials=materials, suppliers=suppliers)


@app.route("/dashboard/purchase-orders/<int:po_id>")
@login_required
def po_detail(po_id):
    item = get_scoped_or_404(PurchaseOrder, po_id)
    return render_template("dash/po_detail.html", item=item)


@app.post("/dashboard/purchase-orders/<int:po_id>/status")
@write_required
def po_status(po_id):
    fac = current_factory()
    item = get_scoped_or_404(PurchaseOrder, po_id)
    new = request.form.get("status")
    if new not in PO_STATUSES:
        abort(400)
    item.status = new
    if new == "sent" and item.supplier and item.supplier.phone:
        lines = "; ".join(f"{i.material.name} x{i.quantity:g}{i.material.unit}"
                          for i in item.items[:4] if i.material)
        notify(fac, [item.supplier.phone],
               f"Mzalendo: {fac.name} has placed purchase order {item.number}. "
               f"{lines}. Expected {item.expected_date or 'as soon as possible'}.",
               "purchase_order")
        item.supplier.orders_placed = (item.supplier.orders_placed or 0) + 1
    db.session.commit()
    audit("po_status", "purchase_order", item.id, new)
    flash(f"Purchase order marked {new.replace('_', ' ')}.", "ok")
    return redirect(url_for("po_detail", po_id=item.id))


@app.post("/dashboard/purchase-orders/<int:po_id>/receive")
@write_required
def po_receive(po_id):
    fac = current_factory()
    item = get_scoped_or_404(PurchaseOrder, po_id)
    received_any = False
    for line in item.items:
        qty = to_float(request.form.get(f"recv_{line.id}"))
        if qty > 0:
            move_stock(line.material, "in", qty, fac,
                       reference=item.number,
                       note=f"Received from {item.supplier.name if item.supplier else 'supplier'}",
                       user_id=current_user.id)
            line.received_qty = (line.received_qty or 0) + qty
            received_any = True
    if not received_any:
        flash("Enter the quantities that actually arrived.", "error")
        return redirect(url_for("po_detail", po_id=item.id))

    full = all((l.received_qty or 0) >= (l.quantity or 0) for l in item.items)
    item.status = "received" if full else "partial"
    item.received_at = _now()
    if item.supplier:
        s = item.supplier
        if full:
            s.orders_complete = (s.orders_complete or 0) + 1
            if item.expected_date and _today() <= item.expected_date:
                s.orders_on_time = (s.orders_on_time or 0) + 1
    db.session.commit()
    audit("po_received", "purchase_order", item.id, item.status)
    flash("Stock received and the ledger updated.", "ok")
    return redirect(url_for("po_detail", po_id=item.id))


@app.post("/dashboard/purchase-orders/<int:po_id>/delete")
@write_required
def po_delete(po_id):
    item = get_scoped_or_404(PurchaseOrder, po_id)
    number = item.number
    db.session.delete(item)
    db.session.commit()
    audit("po_deleted", "purchase_order", po_id, number)
    flash(f"Purchase order {number} deleted.", "ok")
    return redirect(url_for("dash_purchase_orders"))


@app.post("/dashboard/procurement/auto")
@write_required
def procurement_auto():
    """Draft purchase orders for everything sitting under its minimum."""
    fac = current_factory()
    low = [m for m in scoped(Material).filter_by(is_active=True).all()
           if m.stock_state in ("low", "out") and m.supplier_id]
    if not low:
        flash("Nothing is below its minimum with a supplier attached.", "info")
        return redirect(url_for("dash_purchase_orders"))

    grouped = defaultdict(list)
    for m in low:
        grouped[m.supplier_id].append(m)

    made = 0
    for supplier_id, mats in grouped.items():
        supplier = db.session.get(Supplier, supplier_id)
        po = PurchaseOrder(factory_id=fac.id,
                           number=next_ref(PurchaseOrder, fac.id, "PO", "number"),
                           supplier_id=supplier_id, status="draft",
                           expected_date=_today() + timedelta(days=supplier.lead_time_days or 7),
                           notes="Drafted automatically from the reorder line.")
        db.session.add(po)
        db.session.flush()
        for m in mats:
            qty = m.reorder_qty or max(m.min_stock * 2 - m.quantity, m.min_stock or 1)
            db.session.add(POItem(po_id=po.id, material_id=m.id,
                                  quantity=round(qty, 2), unit_cost=m.unit_cost or 0))
        made += 1
    db.session.commit()
    audit("procurement_auto", "purchase_order", None, f"{made} drafts")
    flash(f"{made} draft purchase order(s) prepared. Review them before sending.", "ok")
    return redirect(url_for("dash_purchase_orders"))


# =============================================================================
#  SECTION 16 — PRODUCTS & BILL OF MATERIALS
# =============================================================================

@app.route("/dashboard/products")
@login_required
def dash_products():
    q = scoped(Product)
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Product.name.ilike(like), Product.sku.ilike(like),
                         Product.category.ilike(like)))
    page = Page(q.order_by(Product.name), page_args(), 20)
    return render_template("dash/products.html", page=page, term=term)


@app.route("/dashboard/products/new", methods=["GET", "POST"])
@app.route("/dashboard/products/<int:product_id>/edit", methods=["GET", "POST"])
@write_required
def product_form(product_id=None):
    fac = current_factory()
    item = get_scoped_or_404(Product, product_id) if product_id else None
    errors = {}
    materials = scoped(Material).order_by(Material.name).all()

    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        sku = (f.get("sku") or "").strip().upper()
        if not name:
            errors["name"] = "Give the product a name."
        if not sku:
            sku = next_ref(Product, current_factory_id(), "PRD", "sku")
        clash = scoped(Product).filter(Product.sku == sku,
                                       Product.id != (item.id if item else 0)).first()
        if clash:
            errors["sku"] = "Another product already uses that code."

        if not errors:
            obj = item or Product(factory_id=fac.id)
            obj.name, obj.sku = name, sku
            obj.category = (f.get("category") or "").strip()
            obj.unit_price = to_float(f.get("unit_price"))
            obj.build_days = to_int(f.get("build_days"), 3)
            obj.stages = (f.get("stages") or "").strip()
            obj.description = (f.get("description") or "").strip()
            obj.is_active = f.get("is_active") == "on"
            db.session.add(obj)
            db.session.flush()

            BomItem.query.filter_by(product_id=obj.id).delete()
            for mid, qty in zip(f.getlist("bom_material[]"), f.getlist("bom_qty[]")):
                if to_int(mid) and to_float(qty) > 0:
                    db.session.add(BomItem(product_id=obj.id, material_id=to_int(mid),
                                           qty_per_unit=to_float(qty)))
            db.session.commit()
            audit("product_saved", "product", obj.id, obj.name)
            flash(f"{obj.name} saved.", "ok")
            return redirect(url_for("product_detail", product_id=obj.id))

    return render_template("dash/product_form.html", item=item, errors=errors,
                           materials=materials, default_stages=DEFAULT_STAGES)


@app.route("/dashboard/products/<int:product_id>")
@login_required
def product_detail(product_id):
    item = get_scoped_or_404(Product, product_id)
    buildable = None
    if item.bom:
        caps = []
        for b in item.bom:
            if b.material and b.qty_per_unit:
                caps.append(int((b.material.quantity or 0) // b.qty_per_unit))
        buildable = min(caps) if caps else None
    runs = (ProductionRun.query.filter_by(product_id=item.id)
            .order_by(desc(ProductionRun.created_at)).limit(12).all())
    return render_template("dash/product_detail.html", item=item, buildable=buildable,
                           runs=runs)


@app.post("/dashboard/products/<int:product_id>/delete")
@write_required
def product_delete(product_id):
    item = get_scoped_or_404(Product, product_id)
    if OrderItem.query.filter_by(product_id=item.id).count() or \
       ProductionRun.query.filter_by(product_id=item.id).count():
        flash("This product appears on orders or runs. Deactivate it instead.", "error")
        return redirect(url_for("dash_products"))
    name = item.name
    db.session.delete(item)
    db.session.commit()
    audit("product_deleted", "product", product_id, name)
    flash(f"{name} deleted.", "ok")
    return redirect(url_for("dash_products"))


# =============================================================================
#  SECTION 17 — CUSTOMERS & ORDERS
# =============================================================================

@app.route("/dashboard/customers")
@login_required
def dash_customers():
    q = scoped(Customer)
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Customer.name.ilike(like), Customer.company.ilike(like),
                         Customer.phone.ilike(like)))
    page = Page(q.order_by(Customer.name), page_args(), 20)
    return render_template("dash/customers.html", page=page, term=term)


@app.route("/dashboard/customers/new", methods=["GET", "POST"])
@app.route("/dashboard/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@write_required
def customer_form(customer_id=None):
    fac = current_factory()
    item = get_scoped_or_404(Customer, customer_id) if customer_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        if not name:
            errors["name"] = "Give the customer a name."
        if not errors:
            obj = item or Customer(factory_id=fac.id)
            obj.name = name
            obj.company = (f.get("company") or "").strip()
            obj.phone = norm_phone(f.get("phone", ""))
            obj.email = (f.get("email") or "").strip().lower()
            obj.address = (f.get("address") or "").strip()
            obj.notes = (f.get("notes") or "").strip()
            obj.sms_updates = f.get("sms_updates") == "on"
            obj.is_active = f.get("is_active") == "on"
            db.session.add(obj)
            db.session.commit()
            audit("customer_saved", "customer", obj.id, obj.name)
            flash(f"{obj.name} saved.", "ok")
            return redirect(url_for("dash_customers"))
    return render_template("dash/customer_form.html", item=item, errors=errors)


@app.route("/dashboard/customers/<int:customer_id>")
@login_required
def customer_detail(customer_id):
    item = get_scoped_or_404(Customer, customer_id)
    orders = (Order.query.filter_by(customer_id=item.id)
              .order_by(desc(Order.created_at)).limit(25).all())
    spend = round(sum(o.total for o in orders), 2)
    return render_template("dash/customer_detail.html", item=item, orders=orders, spend=spend)


@app.post("/dashboard/customers/<int:customer_id>/delete")
@write_required
def customer_delete(customer_id):
    item = get_scoped_or_404(Customer, customer_id)
    if Order.query.filter_by(customer_id=item.id).count():
        flash("This customer has orders on record. Deactivate them instead.", "error")
        return redirect(url_for("dash_customers"))
    name = item.name
    db.session.delete(item)
    db.session.commit()
    audit("customer_deleted", "customer", customer_id, name)
    flash(f"{name} deleted.", "ok")
    return redirect(url_for("dash_customers"))


@app.route("/dashboard/orders")
@login_required
def dash_orders():
    q = scoped(Order)
    term = (request.args.get("q") or "").strip()
    status = request.args.get("status", "")
    if term:
        like = f"%{term}%"
        q = q.join(Customer, isouter=True).filter(
            or_(Order.number.ilike(like), Customer.name.ilike(like),
                Customer.company.ilike(like)))
    if status:
        q = q.filter(Order.status == status)
    page = Page(q.order_by(desc(Order.created_at)), page_args(), 20)
    counts = dict(db.session.query(Order.status, func.count(Order.id))
                  .filter(Order.factory_id == current_factory_id())
                  .group_by(Order.status).all())
    return render_template("dash/orders.html", page=page, term=term, status=status,
                           counts=counts)


@app.route("/dashboard/orders/new", methods=["GET", "POST"])
@app.route("/dashboard/orders/<int:order_id>/edit", methods=["GET", "POST"])
@write_required
def order_form(order_id=None):
    fac = current_factory()
    item = get_scoped_or_404(Order, order_id) if order_id else None
    errors = {}
    products = scoped(Product).filter_by(is_active=True).order_by(Product.name).all()
    customers = scoped(Customer).filter_by(is_active=True).order_by(Customer.name).all()

    if request.method == "POST":
        f = request.form
        customer_id = to_int(f.get("customer_id"))
        if not customer_id:
            errors["customer_id"] = "Choose the customer."
        lines = [(to_int(p), to_float(q), to_float(pr))
                 for p, q, pr in zip(f.getlist("product_id[]"), f.getlist("quantity[]"),
                                     f.getlist("unit_price[]"))
                 if to_int(p) and to_float(q) > 0]
        if not lines:
            errors["lines"] = "Add at least one product line."

        if not errors:
            obj = item or Order(factory_id=fac.id,
                                number=next_ref(Order, fac.id, "ORD", "number"))
            was_new = item is None
            obj.customer_id = customer_id
            obj.status = f.get("status") or "new"
            obj.priority = f.get("priority") or "normal"
            obj.due_date = to_date(f.get("due_date"))
            obj.deposit = to_float(f.get("deposit"))
            obj.notes = (f.get("notes") or "").strip()
            db.session.add(obj)
            db.session.flush()
            OrderItem.query.filter_by(order_id=obj.id).delete()
            for pid, qty, price in lines:
                db.session.add(OrderItem(order_id=obj.id, product_id=pid,
                                         quantity=qty, unit_price=price))
            db.session.commit()

            if was_new and obj.customer and obj.customer.sms_updates and obj.customer.phone:
                notify(fac, [obj.customer.phone],
                       f"Mzalendo: {fac.name} has received your order {obj.number}. "
                       f"Total {money(obj.total, fac.currency)}. "
                       f"We will confirm the schedule shortly.", "order")
            audit("order_saved", "order", obj.id, obj.number)
            flash(f"Order {obj.number} saved.", "ok")
            return redirect(url_for("order_detail", order_id=obj.id))

    return render_template("dash/order_form.html", item=item, errors=errors,
                           products=products, customers=customers)


@app.route("/dashboard/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    item = get_scoped_or_404(Order, order_id)
    shortages = order_material_check(item)
    inspections = QcInspection.query.filter_by(order_id=item.id).all()
    return render_template("dash/order_detail.html", item=item, shortages=shortages,
                           inspections=inspections)


def order_material_check(order: Order):
    """What this order will consume against what is on the shelf right now."""
    need = defaultdict(float)
    for line in order.items:
        if not line.product:
            continue
        for b in line.product.bom:
            if b.material:
                need[b.material.id] += (b.qty_per_unit or 0) * (line.quantity or 0)
    rows = []
    for mid, qty in need.items():
        m = db.session.get(Material, mid)
        if not m:
            continue
        rows.append({
            "material": m, "required": round(qty, 2),
            "available": round(m.quantity or 0, 2),
            "gap": round(max(0.0, qty - (m.quantity or 0)), 2),
        })
    rows.sort(key=lambda r: -r["gap"])
    return rows


@app.post("/dashboard/orders/<int:order_id>/status")
@write_required
def order_status(order_id):
    fac = current_factory()
    item = get_scoped_or_404(Order, order_id)
    new = request.form.get("status")
    if new not in ORDER_STATUSES:
        abort(400)
    old = item.status
    item.status = new
    db.session.commit()

    if item.customer and item.customer.sms_updates and item.customer.phone:
        friendly = {
            "confirmed": "has been confirmed",
            "scheduled": "has been scheduled for production",
            "in_production": "has entered production",
            "quality_check": "is in quality check",
            "ready": "is ready for collection",
            "dispatched": "has been dispatched",
            "completed": "is complete. Thank you for your business",
            "cancelled": "has been cancelled",
        }.get(new)
        if friendly:
            notify(fac, [item.customer.phone],
                   f"Mzalendo: Order {item.number} {friendly}.", "order")
    audit("order_status", "order", item.id, f"{old} -> {new}")
    flash(f"Order {item.number} moved to {ORDER_STATUS_LABELS.get(new, new)}.", "ok")
    return redirect(url_for("order_detail", order_id=item.id))


@app.post("/dashboard/orders/<int:order_id>/plan")
@write_required
def order_plan(order_id):
    """Turn an order into scheduled production runs with dated stages."""
    fac = current_factory()
    item = get_scoped_or_404(Order, order_id)
    if not item.items:
        flash("Add product lines before planning production.", "error")
        return redirect(url_for("order_detail", order_id=item.id))

    start = to_date(request.form.get("start_date")) or _today()
    made = 0
    cursor = start
    for line in item.items:
        product = line.product
        if not product:
            continue
        stages = product.stage_list
        run = ProductionRun(
            factory_id=fac.id, reference=next_ref(ProductionRun, fac.id, "RUN"),
            order_id=item.id, product_id=product.id, quantity=line.quantity,
            status="planned", start_date=cursor,
            end_date=cursor + timedelta(days=max(1, len(stages)) - 1))
        db.session.add(run)
        db.session.flush()
        day = cursor
        for i, name in enumerate(stages, start=1):
            db.session.add(RunStage(run_id=run.id, sequence=i, name=name,
                                    planned_date=day, status="pending"))
            day += timedelta(days=1)
        cursor = run.end_date + timedelta(days=1)
        made += 1

    if item.status in ("new", "confirmed"):
        item.status = "scheduled"
    db.session.commit()
    audit("order_planned", "order", item.id, f"{made} runs")
    flash(f"{made} production run(s) scheduled from {start:%d %b}.", "ok")
    return redirect(url_for("dash_runs"))


@app.post("/dashboard/orders/<int:order_id>/delete")
@write_required
def order_delete(order_id):
    item = get_scoped_or_404(Order, order_id)
    number = item.number
    ProductionRun.query.filter_by(order_id=item.id).update({"order_id": None})
    QcInspection.query.filter_by(order_id=item.id).update({"order_id": None})
    db.session.delete(item)
    db.session.commit()
    audit("order_deleted", "order", order_id, number)
    flash(f"Order {number} deleted.", "ok")
    return redirect(url_for("dash_orders"))


# =============================================================================
#  SECTION 18 — PRODUCTION
# =============================================================================

@app.route("/dashboard/production")
@login_required
def dash_runs():
    q = scoped(ProductionRun)
    status = request.args.get("status", "")
    if status:
        q = q.filter(ProductionRun.status == status)
    page = Page(q.order_by(ProductionRun.start_date.asc().nulls_last(),
                           desc(ProductionRun.created_at)), page_args(), 20)
    board = defaultdict(list)
    for r in scoped(ProductionRun).filter(
            ProductionRun.status.notin_(("done", "cancelled"))).all():
        board[r.status].append(r)
    return render_template("dash/runs.html", page=page, status=status, board=board)


@app.route("/dashboard/production/new", methods=["GET", "POST"])
@app.route("/dashboard/production/<int:run_id>/edit", methods=["GET", "POST"])
@floor_required
def run_form(run_id=None):
    fac = current_factory()
    item = get_scoped_or_404(ProductionRun, run_id) if run_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        product_id = to_int(f.get("product_id"))
        if not product_id:
            errors["product_id"] = "Choose the product being made."
        qty = to_float(f.get("quantity"))
        if qty <= 0:
            errors["quantity"] = "Enter how many units this run covers."

        if not errors:
            obj = item or ProductionRun(factory_id=fac.id,
                                        reference=next_ref(ProductionRun, fac.id, "RUN"))
            was_new = item is None
            obj.product_id = product_id
            obj.order_id = to_int(f.get("order_id")) or None
            obj.machine_id = to_int(f.get("machine_id")) or None
            obj.worker_id = to_int(f.get("worker_id")) or None
            obj.quantity = qty
            obj.produced = to_float(f.get("produced"), obj.produced or 0)
            obj.status = f.get("status") or "planned"
            obj.start_date = to_date(f.get("start_date")) or _today()
            obj.end_date = to_date(f.get("end_date"))
            obj.notes = (f.get("notes") or "").strip()
            db.session.add(obj)
            db.session.flush()

            if was_new:
                product = db.session.get(Product, product_id)
                stages = product.stage_list if product else DEFAULT_STAGES
                if not obj.end_date:
                    obj.end_date = obj.start_date + timedelta(days=max(1, len(stages)) - 1)
                day = obj.start_date
                for i, name in enumerate(stages, start=1):
                    db.session.add(RunStage(run_id=obj.id, sequence=i, name=name,
                                            planned_date=day, status="pending"))
                    day += timedelta(days=1)
            db.session.commit()

            if was_new and obj.worker and obj.worker.phone:
                notify(fac, [obj.worker.phone],
                       f"Mzalendo: You are assigned to run {obj.reference} "
                       f"({obj.product.name if obj.product else 'production'}), "
                       f"{obj.quantity:g} units, starting {obj.start_date:%d %b}.", "task")
            audit("run_saved", "production_run", obj.id, obj.reference)
            flash(f"Run {obj.reference} saved.", "ok")
            return redirect(url_for("run_detail", run_id=obj.id))

    return render_template("dash/run_form.html", item=item, errors=errors,
                           products=scoped(Product).filter_by(is_active=True).order_by(Product.name).all(),
                           orders=scoped(Order).filter(Order.status.notin_(("completed", "cancelled"))).order_by(desc(Order.created_at)).all(),
                           machines=scoped(Machine).filter(Machine.status != "retired").order_by(Machine.name).all(),
                           workers=scoped(Worker).filter_by(is_active=True).order_by(Worker.name).all())


@app.route("/dashboard/production/<int:run_id>")
@login_required
def run_detail(run_id):
    item = get_scoped_or_404(ProductionRun, run_id)
    consumption = []
    if item.product:
        for b in item.product.bom:
            if b.material:
                need = (b.qty_per_unit or 0) * (item.quantity or 0)
                consumption.append({
                    "material": b.material, "required": round(need, 2),
                    "available": round(b.material.quantity or 0, 2),
                    "gap": round(max(0.0, need - (b.material.quantity or 0)), 2)})
    return render_template("dash/run_detail.html", item=item, consumption=consumption)


@app.post("/dashboard/production/<int:run_id>/stage/<int:stage_id>")
@floor_required
def run_stage_toggle(run_id, stage_id):
    item = get_scoped_or_404(ProductionRun, run_id)
    stage = db.session.get(RunStage, stage_id)
    if not stage or stage.run_id != item.id:
        abort(404)
    if stage.status == "done":
        stage.status, stage.completed_at = "pending", None
    else:
        stage.status, stage.completed_at = "done", _now()
    done = sum(1 for s in item.stages if s.status == "done")
    if done and item.status == "planned":
        item.status = "running"
    if done == len(item.stages) and item.stages:
        item.status = "done"
        item.produced = item.quantity
    db.session.commit()
    audit("run_stage", "production_run", item.id, f"{stage.name}={stage.status}")
    return redirect(url_for("run_detail", run_id=item.id))


@app.post("/dashboard/production/<int:run_id>/progress")
@floor_required
def run_progress(run_id):
    fac = current_factory()
    item = get_scoped_or_404(ProductionRun, run_id)
    produced = to_float(request.form.get("produced"), item.produced or 0)
    item.produced = max(0.0, min(produced, item.quantity or produced))
    new_status = request.form.get("status")
    if new_status in RUN_STATUSES:
        item.status = new_status
    consume = request.form.get("consume") == "on"
    db.session.commit()

    if consume and item.product:
        for b in item.product.bom:
            if b.material and b.qty_per_unit:
                move_stock(b.material, "out", (b.qty_per_unit or 0) * produced, fac,
                           reference=item.reference,
                           note=f"Consumed by run {item.reference}",
                           user_id=current_user.id)
    if item.status == "blocked":
        raise_alert(fac.id, "production", "high",
                    f"Run {item.reference} is blocked",
                    item.notes or "A supervisor marked this run blocked on the floor.",
                    "Clear the blocker or release the machine to the next job.",
                    "production_run", item.id)
    audit("run_progress", "production_run", item.id, f"{item.produced}/{item.quantity}")
    flash("Production updated.", "ok")
    return redirect(url_for("run_detail", run_id=item.id))


@app.post("/dashboard/production/<int:run_id>/delete")
@write_required
def run_delete(run_id):
    item = get_scoped_or_404(ProductionRun, run_id)
    ref = item.reference
    db.session.delete(item)
    db.session.commit()
    audit("run_deleted", "production_run", run_id, ref)
    flash(f"Run {ref} deleted.", "ok")
    return redirect(url_for("dash_runs"))


@app.route("/dashboard/schedule")
@login_required
def dash_schedule():
    """Two week Gantt style view of everything on the floor."""
    fac = current_factory()
    start = to_date(request.args.get("from")) or _today()
    days = [start + timedelta(days=i) for i in range(14)]
    runs = scoped(ProductionRun).filter(
        ProductionRun.status.notin_(("cancelled",)),
        or_(ProductionRun.end_date >= start, ProductionRun.end_date.is_(None))
    ).order_by(ProductionRun.start_date.asc().nulls_last()).limit(40).all()
    lanes = []
    for r in runs:
        s = r.start_date or start
        e = r.end_date or s
        offset = (s - start).days
        span = max(1, (e - s).days + 1)
        if offset + span <= 0 or offset >= 14:
            continue
        lanes.append({"run": r, "offset": max(0, offset),
                      "span": min(14 - max(0, offset), span + min(0, offset))})
    return render_template("dash/schedule.html", days=days, lanes=lanes, start=start,
                           prev_start=start - timedelta(days=14),
                           next_start=start + timedelta(days=14))


# =============================================================================
#  SECTION 19 — MACHINES & MAINTENANCE
# =============================================================================

@app.route("/dashboard/machines")
@login_required
def dash_machines():
    q = scoped(Machine)
    status = request.args.get("status", "")
    term = (request.args.get("q") or "").strip()
    if status:
        q = q.filter(Machine.status == status)
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Machine.name.ilike(like), Machine.code.ilike(like),
                         Machine.kind.ilike(like)))
    page = Page(q.order_by(Machine.name), page_args(), 20)
    return render_template("dash/machines.html", page=page, status=status, term=term)


@app.route("/dashboard/machines/new", methods=["GET", "POST"])
@app.route("/dashboard/machines/<int:machine_id>/edit", methods=["GET", "POST"])
@write_required
def machine_form(machine_id=None):
    fac = current_factory()
    item = get_scoped_or_404(Machine, machine_id) if machine_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        code = (f.get("code") or "").strip().upper()
        if not name:
            errors["name"] = "Give the machine a name."
        if not code:
            code = next_ref(Machine, current_factory_id(), "MC", "code")
        clash = scoped(Machine).filter(Machine.code == code,
                                       Machine.id != (item.id if item else 0)).first()
        if clash:
            errors["code"] = "Another machine already uses that code."
        if not errors:
            obj = item or Machine(factory_id=fac.id)
            obj.name, obj.code = name, code
            obj.kind = (f.get("kind") or "").strip()
            obj.location = (f.get("location") or "").strip()
            obj.status = f.get("status") or "idle"
            obj.commissioned_on = to_date(f.get("commissioned_on"))
            obj.last_service_at = to_date(f.get("last_service_at"))
            obj.service_interval_days = to_int(f.get("service_interval_days"), 90)
            obj.runtime_hours = to_float(f.get("runtime_hours"))
            obj.notes = (f.get("notes") or "").strip()
            db.session.add(obj)
            db.session.commit()
            audit("machine_saved", "machine", obj.id, obj.name)
            flash(f"{obj.name} saved.", "ok")
            return redirect(url_for("machine_detail", machine_id=obj.id))
    return render_template("dash/machine_form.html", item=item, errors=errors)


@app.route("/dashboard/machines/<int:machine_id>")
@login_required
def machine_detail(machine_id):
    item = get_scoped_or_404(Machine, machine_id)
    tickets = (MaintenanceTicket.query.filter_by(machine_id=item.id)
               .order_by(desc(MaintenanceTicket.created_at)).limit(25).all())
    runs = (ProductionRun.query.filter_by(machine_id=item.id)
            .order_by(desc(ProductionRun.created_at)).limit(8).all())
    downtime = sum(t.downtime_minutes or 0 for t in tickets)
    return render_template("dash/machine_detail.html", item=item, tickets=tickets,
                           runs=runs, downtime=downtime)


@app.post("/dashboard/machines/<int:machine_id>/service")
@write_required
def machine_service(machine_id):
    item = get_scoped_or_404(Machine, machine_id)
    item.last_service_at = to_date(request.form.get("serviced_on")) or _today()
    if item.status == "maintenance":
        item.status = "idle"
    db.session.commit()
    audit("machine_serviced", "machine", item.id, str(item.last_service_at))
    flash(f"{item.name} serviced. Next due {item.next_service:%d %b %Y}.", "ok")
    return redirect(url_for("machine_detail", machine_id=item.id))


@app.post("/dashboard/machines/<int:machine_id>/status")
@floor_required
def machine_status(machine_id):
    fac = current_factory()
    item = get_scoped_or_404(Machine, machine_id)
    new = request.form.get("status")
    if new not in MACHINE_STATUSES:
        abort(400)
    item.status = new
    db.session.commit()
    if new == "down":
        raise_alert(fac.id, "maintenance", "critical", f"{item.name} is down",
                    f"{item.code} at {item.location or 'the plant'} stopped production.",
                    "Assign a technician and record the downtime.", "machine", item.id)
        phones = factory_managers_phones(fac.id)
        if phones:
            notify(fac, phones, f"Mzalendo: Machine {item.name} ({item.code}) is DOWN. "
                                f"Production on this machine has stopped.", "maintenance")
    audit("machine_status", "machine", item.id, new)
    flash(f"{item.name} marked {new}.", "ok")
    return redirect(url_for("machine_detail", machine_id=item.id))


@app.post("/dashboard/machines/<int:machine_id>/delete")
@write_required
def machine_delete(machine_id):
    item = get_scoped_or_404(Machine, machine_id)
    if MaintenanceTicket.query.filter_by(machine_id=item.id).count():
        flash("This machine has maintenance history. Retire it instead.", "error")
        return redirect(url_for("dash_machines"))
    ProductionRun.query.filter_by(machine_id=item.id).update({"machine_id": None})
    name = item.name
    db.session.delete(item)
    db.session.commit()
    audit("machine_deleted", "machine", machine_id, name)
    flash(f"{name} deleted.", "ok")
    return redirect(url_for("dash_machines"))


@app.route("/dashboard/maintenance")
@login_required
def dash_maintenance():
    q = scoped(MaintenanceTicket)
    status = request.args.get("status", "")
    sev = request.args.get("severity", "")
    if status:
        q = q.filter(MaintenanceTicket.status == status)
    if sev:
        q = q.filter(MaintenanceTicket.severity == sev)
    page = Page(q.order_by(desc(MaintenanceTicket.created_at)), page_args(), 20)
    due = [m for m in scoped(Machine).filter(Machine.status != "retired").all()
           if m.service_state in ("due", "overdue")]
    return render_template("dash/maintenance.html", page=page, status=status, sev=sev,
                           due=due)


@app.route("/dashboard/maintenance/new", methods=["GET", "POST"])
@app.route("/dashboard/maintenance/<int:ticket_id>/edit", methods=["GET", "POST"])
@floor_required
def ticket_form(ticket_id=None):
    fac = current_factory()
    item = get_scoped_or_404(MaintenanceTicket, ticket_id) if ticket_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        machine_id = to_int(f.get("machine_id"))
        if not machine_id:
            errors["machine_id"] = "Choose the machine."
        if not errors:
            obj = item or MaintenanceTicket(
                factory_id=fac.id, reference=next_ref(MaintenanceTicket, fac.id, "MNT"))
            was_new = item is None
            obj.machine_id = machine_id
            obj.worker_id = to_int(f.get("worker_id")) or None
            obj.assigned_to_id = to_int(f.get("assigned_to_id")) or None
            obj.fault_type = (f.get("fault_type") or "Other").strip()
            obj.severity = f.get("severity") or "medium"
            obj.status = f.get("status") or "open"
            obj.description = (f.get("description") or "").strip()
            obj.resolution = (f.get("resolution") or "").strip()
            obj.downtime_minutes = to_int(f.get("downtime_minutes"))
            if obj.status in ("resolved", "closed") and not obj.resolved_at:
                obj.resolved_at = _now()
            if obj.status not in ("resolved", "closed"):
                obj.resolved_at = None
            db.session.add(obj)
            db.session.commit()

            if obj.assignee and obj.assignee.phone and (was_new or f.get("notify") == "on"):
                notify(fac, [obj.assignee.phone],
                       f"Mzalendo: Maintenance {obj.reference} assigned to you. "
                       f"{obj.machine.name if obj.machine else 'Machine'}: {obj.fault_type}. "
                       f"Severity {obj.severity}.", "maintenance")
            audit("ticket_saved", "maintenance_ticket", obj.id, obj.reference)
            flash(f"Maintenance ticket {obj.reference} saved.", "ok")
            return redirect(url_for("dash_maintenance"))

    return render_template("dash/ticket_form.html", item=item, errors=errors,
                           machines=scoped(Machine).filter(Machine.status != "retired").order_by(Machine.name).all(),
                           workers=scoped(Worker).filter_by(is_active=True).order_by(Worker.name).all(),
                           technicians=User.query.filter_by(factory_id=fac.id, is_active_flag=True).order_by(User.full_name).all(),
                           fault_types=list(FAULT_TYPES.values()))


@app.post("/dashboard/maintenance/<int:ticket_id>/resolve")
@floor_required
def ticket_resolve(ticket_id):
    item = get_scoped_or_404(MaintenanceTicket, ticket_id)
    item.status = "resolved"
    item.resolved_at = _now()
    item.resolution = (request.form.get("resolution") or item.resolution or "").strip()
    item.downtime_minutes = to_int(request.form.get("downtime_minutes"), item.downtime_minutes or 0)
    if item.machine and item.machine.status == "down":
        item.machine.status = "idle"
    db.session.commit()
    audit("ticket_resolved", "maintenance_ticket", item.id, item.reference)
    flash(f"{item.reference} resolved.", "ok")
    return redirect(url_for("dash_maintenance"))


@app.post("/dashboard/maintenance/<int:ticket_id>/delete")
@write_required
def ticket_delete(ticket_id):
    item = get_scoped_or_404(MaintenanceTicket, ticket_id)
    ref = item.reference
    db.session.delete(item)
    db.session.commit()
    audit("ticket_deleted", "maintenance_ticket", ticket_id, ref)
    flash(f"{ref} deleted.", "ok")
    return redirect(url_for("dash_maintenance"))


# =============================================================================
#  SECTION 20 — QUALITY CONTROL
# =============================================================================

DEFAULT_QC_CHECKS = [
    "Dimensions within tolerance", "Welds complete and sound",
    "Surface finish acceptable", "Paint coverage even",
    "Fittings and hardware secure", "Labelling and batch code applied",
    "Packaging intact",
]


@app.route("/dashboard/quality")
@login_required
def dash_quality():
    q = scoped(QcInspection)
    status = request.args.get("status", "")
    if status:
        q = q.filter(QcInspection.status == status)
    page = Page(q.order_by(desc(QcInspection.created_at)), page_args(), 20)
    all_rows = scoped(QcInspection).all()
    passed = len([i for i in all_rows if i.status == "pass"])
    failed = len([i for i in all_rows if i.status == "fail"])
    rate = int(round(passed / (passed + failed) * 100)) if (passed + failed) else 100
    return render_template("dash/quality.html", page=page, status=status,
                           passed=passed, failed=failed, rate=rate)


@app.route("/dashboard/quality/new", methods=["GET", "POST"])
@app.route("/dashboard/quality/<int:inspection_id>/edit", methods=["GET", "POST"])
@floor_required
def qc_form(inspection_id=None):
    fac = current_factory()
    item = get_scoped_or_404(QcInspection, inspection_id) if inspection_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        obj = item or QcInspection(factory_id=fac.id,
                                   reference=next_ref(QcInspection, fac.id, "QC"))
        obj.order_id = to_int(f.get("order_id")) or None
        obj.run_id = to_int(f.get("run_id")) or None
        obj.product_id = to_int(f.get("product_id")) or None
        obj.inspector_id = current_user.id
        obj.sample_size = to_int(f.get("sample_size"), 1)
        obj.defects_found = to_int(f.get("defects_found"))
        obj.standard = (f.get("standard") or "KEBS").strip()
        obj.notes = (f.get("notes") or "").strip()
        db.session.add(obj)
        db.session.flush()

        QcCheck.query.filter_by(inspection_id=obj.id).delete()
        labels = f.getlist("check_label[]")
        for idx, label in enumerate(labels):
            label = (label or "").strip()
            if not label:
                continue
            db.session.add(QcCheck(inspection_id=obj.id, label=label,
                                   passed=f.get(f"check_pass_{idx}") == "on",
                                   note=(f.getlist("check_note[]")[idx]
                                         if idx < len(f.getlist("check_note[]")) else "")[:240]))
        db.session.flush()
        submitted = f.get("status")
        if submitted in ("pass", "fail", "pending"):
            obj.status = submitted
        else:
            checks = QcCheck.query.filter_by(inspection_id=obj.id).all()
            obj.status = "pass" if checks and all(c.passed for c in checks) else \
                         ("fail" if checks else "pending")
        db.session.commit()

        if obj.status == "fail":
            raise_alert(fac.id, "quality", "high",
                        f"Inspection {obj.reference} failed",
                        f"{obj.defects_found} defect(s) on a sample of {obj.sample_size}.",
                        "Hold the batch, find the root cause, then re-inspect.",
                        "qc_inspection", obj.id)
            phones = factory_managers_phones(fac.id)
            if phones:
                notify(fac, phones,
                       f"Mzalendo: Quality check {obj.reference} FAILED"
                       + (f" for order {obj.order.number}" if obj.order else "")
                       + ". The batch is on hold.", "quality")
        audit("qc_saved", "qc_inspection", obj.id, f"{obj.reference}={obj.status}")
        flash(f"Inspection {obj.reference} recorded as {obj.status}.", "ok")
        return redirect(url_for("dash_quality"))

    return render_template("dash/qc_form.html", item=item, errors=errors,
                           default_checks=DEFAULT_QC_CHECKS,
                           orders=scoped(Order).filter(Order.status.notin_(("completed", "cancelled"))).order_by(desc(Order.created_at)).all(),
                           runs=scoped(ProductionRun).filter(ProductionRun.status.notin_(("cancelled",))).order_by(desc(ProductionRun.created_at)).all(),
                           products=scoped(Product).order_by(Product.name).all())


@app.route("/dashboard/quality/<int:inspection_id>")
@login_required
def qc_detail(inspection_id):
    item = get_scoped_or_404(QcInspection, inspection_id)
    return render_template("dash/qc_detail.html", item=item)


@app.post("/dashboard/quality/<int:inspection_id>/delete")
@write_required
def qc_delete(inspection_id):
    item = get_scoped_or_404(QcInspection, inspection_id)
    ref = item.reference
    db.session.delete(item)
    db.session.commit()
    audit("qc_deleted", "qc_inspection", inspection_id, ref)
    flash(f"Inspection {ref} deleted.", "ok")
    return redirect(url_for("dash_quality"))


# =============================================================================
#  SECTION 21 — WORKFORCE, SAFETY & ATTENDANCE
# =============================================================================

@app.route("/dashboard/workers")
@login_required
def dash_workers():
    q = scoped(Worker)
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Worker.name.ilike(like), Worker.phone.ilike(like),
                         Worker.trade.ilike(like), Worker.employee_no.ilike(like)))
    page = Page(q.order_by(Worker.name), page_args(), 20)
    today = _today()
    present = {a.worker_id for a in Attendance.query.filter_by(
        factory_id=current_factory_id(), day=today).all()}
    return render_template("dash/workers.html", page=page, term=term, present=present)


@app.route("/dashboard/workers/new", methods=["GET", "POST"])
@app.route("/dashboard/workers/<int:worker_id>/edit", methods=["GET", "POST"])
@write_required
def worker_form(worker_id=None):
    fac = current_factory()
    item = get_scoped_or_404(Worker, worker_id) if worker_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        phone = norm_phone(f.get("phone", ""))
        if not name:
            errors["name"] = "Give the worker a name."
        if not phone or len(phone) < 10:
            errors["phone"] = "A working phone number is required for USSD and SMS."
        clash = scoped(Worker).filter(Worker.phone == phone,
                                      Worker.id != (item.id if item else 0)).first()
        if clash:
            errors["phone"] = "Another worker is registered with that number."

        if not errors:
            obj = item or Worker(factory_id=fac.id)
            was_new = item is None
            obj.name, obj.phone = name, phone
            obj.employee_no = (f.get("employee_no") or "").strip()
            obj.trade = (f.get("trade") or "").strip()
            obj.station = (f.get("station") or "").strip()
            obj.shift = f.get("shift") or "day"
            obj.daily_rate = to_float(f.get("daily_rate"))
            obj.notes = (f.get("notes") or "").strip()
            obj.is_active = f.get("is_active") == "on"
            pin = (f.get("pin") or "").strip()
            if pin:
                if not re.fullmatch(r"\d{4,6}", pin):
                    errors["pin"] = "The PIN must be 4 to 6 digits."
                    return render_template("dash/worker_form.html", item=item, errors=errors)
                obj.set_pin(pin)
            db.session.add(obj)
            db.session.commit()

            if was_new and obj.phone:
                notify(fac, [obj.phone],
                       f"Mzalendo: You are registered at {fac.name}. "
                       f"Dial {fac.ussd_code or app.config['AT_USSD_CODE']} to report "
                       f"production, stock, machine faults and safety incidents."
                       + (f" Your PIN is {pin}." if pin else ""), "worker")
            audit("worker_saved", "worker", obj.id, obj.name)
            flash(f"{obj.name} saved.", "ok")
            return redirect(url_for("dash_workers"))
    return render_template("dash/worker_form.html", item=item, errors=errors)


@app.route("/dashboard/workers/<int:worker_id>")
@login_required
def worker_detail(worker_id):
    item = get_scoped_or_404(Worker, worker_id)
    attendance = (Attendance.query.filter_by(worker_id=item.id)
                  .order_by(desc(Attendance.day)).limit(30).all())
    runs = (ProductionRun.query.filter_by(worker_id=item.id)
            .order_by(desc(ProductionRun.created_at)).limit(10).all())
    tickets = (MaintenanceTicket.query.filter_by(worker_id=item.id)
               .order_by(desc(MaintenanceTicket.created_at)).limit(10).all())
    incidents = (SafetyIncident.query.filter_by(worker_id=item.id)
                 .order_by(desc(SafetyIncident.created_at)).limit(10).all())
    sessions = (UssdSession.query.filter_by(worker_id=item.id)
                .order_by(desc(UssdSession.started_at)).limit(10).all())
    days_present = len([a for a in attendance if a.check_in])
    return render_template("dash/worker_detail.html", item=item, attendance=attendance,
                           runs=runs, tickets=tickets, incidents=incidents,
                           sessions=sessions, days_present=days_present)


@app.post("/dashboard/workers/<int:worker_id>/message")
@write_required
def worker_message(worker_id):
    fac = current_factory()
    item = get_scoped_or_404(Worker, worker_id)
    msg = (request.form.get("message") or "").strip()
    if not msg:
        flash("Write a message first.", "error")
    else:
        notify(fac, [item.phone], f"Mzalendo: {msg}", "worker")
        flash(f"Message sent to {item.name}.", "ok")
        audit("worker_sms", "worker", item.id, msg[:80])
    return redirect(url_for("worker_detail", worker_id=item.id))


@app.post("/dashboard/workers/<int:worker_id>/delete")
@write_required
def worker_delete(worker_id):
    item = get_scoped_or_404(Worker, worker_id)
    name = item.name
    Attendance.query.filter_by(worker_id=item.id).delete()
    ProductionRun.query.filter_by(worker_id=item.id).update({"worker_id": None})
    MaintenanceTicket.query.filter_by(worker_id=item.id).update({"worker_id": None})
    SafetyIncident.query.filter_by(worker_id=item.id).update({"worker_id": None})
    RunStage.query.filter_by(worker_id=item.id).update({"worker_id": None})
    StockMovement.query.filter_by(worker_id=item.id).update({"worker_id": None})
    UssdSession.query.filter_by(worker_id=item.id).update({"worker_id": None})
    db.session.delete(item)
    db.session.commit()
    audit("worker_deleted", "worker", worker_id, name)
    flash(f"{name} removed.", "ok")
    return redirect(url_for("dash_workers"))


@app.post("/dashboard/workers/broadcast")
@write_required
def workers_broadcast():
    fac = current_factory()
    msg = (request.form.get("message") or "").strip()
    shift = request.form.get("shift", "")
    if not msg:
        flash("Write a message first.", "error")
        return redirect(url_for("dash_workers"))
    q = scoped(Worker).filter_by(is_active=True)
    if shift:
        q = q.filter(Worker.shift == shift)
    phones = [w.phone for w in q.all() if w.phone]
    if not phones:
        flash("No workers match that filter.", "warn")
        return redirect(url_for("dash_workers"))
    res = notify(fac, phones, f"Mzalendo: {msg}", "broadcast")
    audit("worker_broadcast", "worker", None, f"{len(phones)} recipients")
    flash(f"Message queued for {res.get('sent', len(phones))} worker(s).", "ok")
    return redirect(url_for("dash_workers"))


@app.route("/dashboard/attendance")
@login_required
def dash_attendance():
    fac = current_factory()
    day = to_date(request.args.get("day")) or _today()
    rows = (Attendance.query.filter_by(factory_id=fac.id, day=day)
            .order_by(Attendance.check_in.asc()).all())
    workers = scoped(Worker).filter_by(is_active=True).order_by(Worker.name).all()
    present_ids = {r.worker_id for r in rows}
    absent = [w for w in workers if w.id not in present_ids]
    week_start = day - timedelta(days=day.weekday())
    week = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        week.append({"day": d,
                     "count": Attendance.query.filter_by(factory_id=fac.id, day=d).count()})
    return render_template("dash/attendance.html", rows=rows, absent=absent, day=day,
                           workers=workers, week=week, total_workers=len(workers))


@app.post("/dashboard/attendance/mark")
@floor_required
def attendance_mark():
    fac = current_factory()
    worker_id = to_int(request.form.get("worker_id"))
    day = to_date(request.form.get("day")) or _today()
    worker = get_scoped_or_404(Worker, worker_id)
    rec = Attendance.query.filter_by(factory_id=fac.id, worker_id=worker.id, day=day).first()
    action = request.form.get("action", "in")
    if action == "in":
        if not rec:
            rec = Attendance(factory_id=fac.id, worker_id=worker.id, day=day,
                             check_in=_now(), source="web")
            db.session.add(rec)
        elif not rec.check_in:
            rec.check_in = _now()
    else:
        if rec and not rec.check_out:
            rec.check_out = _now()
    db.session.commit()
    audit("attendance", "worker", worker.id, f"{action} {day}")
    flash(f"{worker.name} clocked {'in' if action == 'in' else 'out'}.", "ok")
    return redirect(url_for("dash_attendance", day=day.isoformat()))


@app.post("/dashboard/attendance/<int:record_id>/delete")
@write_required
def attendance_delete(record_id):
    rec = get_scoped_or_404(Attendance, record_id)
    day = rec.day
    db.session.delete(rec)
    db.session.commit()
    flash("Attendance record removed.", "ok")
    return redirect(url_for("dash_attendance", day=day.isoformat()))


@app.route("/dashboard/safety")
@login_required
def dash_safety():
    q = scoped(SafetyIncident)
    status = request.args.get("status", "")
    sev = request.args.get("severity", "")
    if status:
        q = q.filter(SafetyIncident.status == status)
    if sev:
        q = q.filter(SafetyIncident.severity == sev)
    page = Page(q.order_by(desc(SafetyIncident.created_at)), page_args(), 20)
    rows = scoped(SafetyIncident).all()
    last = max((r.created_at for r in rows), default=None)
    days_clear = (_now() - last).days if last else None
    return render_template("dash/safety.html", page=page, status=status, sev=sev,
                           days_clear=days_clear, total=len(rows),
                           open_count=len([r for r in rows if r.status == "open"]))


@app.route("/dashboard/safety/new", methods=["GET", "POST"])
@app.route("/dashboard/safety/<int:incident_id>/edit", methods=["GET", "POST"])
@floor_required
def safety_form(incident_id=None):
    fac = current_factory()
    item = get_scoped_or_404(SafetyIncident, incident_id) if incident_id else None
    errors = {}
    if request.method == "POST":
        f = request.form
        desc_ = (f.get("description") or "").strip()
        if not desc_:
            errors["description"] = "Describe what happened."
        if not errors:
            obj = item or SafetyIncident(factory_id=fac.id,
                                         reference=next_ref(SafetyIncident, fac.id, "SAF"))
            was_new = item is None
            obj.worker_id = to_int(f.get("worker_id")) or None
            obj.kind = (f.get("kind") or "Near miss").strip()
            obj.severity = f.get("severity") or "low"
            obj.location = (f.get("location") or "").strip()
            obj.description = desc_
            obj.action_taken = (f.get("action_taken") or "").strip()
            obj.status = f.get("status") or "open"
            if obj.status in ("resolved", "closed") and not obj.resolved_at:
                obj.resolved_at = _now()
            db.session.add(obj)
            db.session.commit()
            if was_new and obj.severity in ("high", "critical"):
                raise_alert(fac.id, "safety", "critical",
                            f"{obj.severity.title()} safety incident: {obj.kind}",
                            desc_[:200], "Attend to the person first, then record the "
                            "corrective action.", "safety_incident", obj.id, dedupe_hours=0)
                phones = factory_managers_phones(fac.id)
                if phones:
                    notify(fac, phones,
                           f"Mzalendo: {obj.severity.upper()} safety incident at "
                           f"{obj.location or fac.name}. {obj.kind}. Attend immediately.",
                           "safety")
            audit("safety_saved", "safety_incident", obj.id, obj.reference)
            flash(f"Incident {obj.reference} recorded.", "ok")
            return redirect(url_for("dash_safety"))
    return render_template("dash/safety_form.html", item=item, errors=errors,
                           workers=scoped(Worker).filter_by(is_active=True).order_by(Worker.name).all(),
                           incident_types=list(INCIDENT_TYPES.values()))


@app.post("/dashboard/safety/<int:incident_id>/delete")
@write_required
def safety_delete(incident_id):
    item = get_scoped_or_404(SafetyIncident, incident_id)
    ref = item.reference
    db.session.delete(item)
    db.session.commit()
    audit("safety_deleted", "safety_incident", incident_id, ref)
    flash(f"Incident {ref} deleted.", "ok")
    return redirect(url_for("dash_safety"))


# =============================================================================
#  SECTION 22 — MESSAGING & TELCO LOGS
# =============================================================================

@app.route("/dashboard/messages")
@login_required
def dash_messages():
    q = SmsLog.query.filter_by(factory_id=current_factory_id())
    cat = request.args.get("category", "")
    direction = request.args.get("direction", "")
    if cat:
        q = q.filter(SmsLog.category == cat)
    if direction:
        q = q.filter(SmsLog.direction == direction)
    page = Page(q.order_by(desc(SmsLog.created_at)), page_args(), 25)
    cats = sorted({r.category for r in SmsLog.query.filter_by(
        factory_id=current_factory_id()).all() if r.category})
    stats = {
        "total": SmsLog.query.filter_by(factory_id=current_factory_id()).count(),
        "out": SmsLog.query.filter_by(factory_id=current_factory_id(), direction="out").count(),
        "inbound": SmsLog.query.filter_by(factory_id=current_factory_id(), direction="in").count(),
        "failed": SmsLog.query.filter(SmsLog.factory_id == current_factory_id(),
                                      SmsLog.status.in_(("failed", "rejected"))).count(),
    }
    return render_template("dash/messages.html", page=page, cat=cat, cats=cats,
                           direction=direction, stats=stats)


@app.post("/dashboard/messages/send")
@write_required
def message_send():
    fac = current_factory()
    raw = (request.form.get("recipients") or "").strip()
    msg = (request.form.get("message") or "").strip()
    if not raw or not msg:
        flash("Add at least one number and a message.", "error")
        return redirect(url_for("dash_messages"))
    numbers = [norm_phone(n) for n in re.split(r"[,\s;]+", raw) if n.strip()]
    res = notify(fac, numbers, msg, request.form.get("category") or "manual")
    audit("sms_sent", "sms", None, f"{len(numbers)} recipients")
    flash(f"Queued for {res.get('sent', 0)} recipient(s)."
          + (" Gateway is in simulation mode." if res.get("simulated") else ""),
          "ok" if res.get("ok") else "error")
    return redirect(url_for("dash_messages"))


@app.route("/dashboard/ussd-sessions")
@login_required
def dash_ussd_sessions():
    q = UssdSession.query.filter_by(factory_id=current_factory_id())
    status = request.args.get("status", "")
    if status:
        q = q.filter(UssdSession.status == status)
    page = Page(q.order_by(desc(UssdSession.started_at)), page_args(), 25)
    total = UssdSession.query.filter_by(factory_id=current_factory_id()).count()
    completed = UssdSession.query.filter_by(factory_id=current_factory_id(),
                                            status="completed").count()
    return render_template("dash/ussd_sessions.html", page=page, status=status,
                           total=total, completed=completed)


@app.route("/dashboard/ussd-simulator")
@login_required
def dash_ussd_simulator():
    fac = current_factory()
    workers = scoped(Worker).filter_by(is_active=True).order_by(Worker.name).limit(30).all()
    return render_template("dash/ussd_simulator.html", workers=workers)


# =============================================================================
#  SECTION 23 — REPORTS & EXPORTS
# =============================================================================

@app.route("/dashboard/reports")
@login_required
def dash_reports():
    fac = current_factory()
    days = to_int(request.args.get("days"), 30) or 30
    since = _now() - timedelta(days=days)
    since_d = since.date()

    orders = scoped(Order).filter(Order.created_at >= since).all()
    runs = scoped(ProductionRun).filter(ProductionRun.created_at >= since).all()
    tickets = scoped(MaintenanceTicket).filter(MaintenanceTicket.created_at >= since).all()
    inspections = scoped(QcInspection).filter(QcInspection.created_at >= since).all()
    movements = StockMovement.query.filter(StockMovement.factory_id == fac.id,
                                           StockMovement.created_at >= since).all()

    by_status = defaultdict(int)
    for o in orders:
        by_status[o.status] += 1

    top_products = defaultdict(float)
    for o in orders:
        for line in o.items:
            if line.product:
                top_products[line.product.name] += (line.quantity or 0)
    top_products = sorted(top_products.items(), key=lambda kv: -kv[1])[:8]

    consumed = defaultdict(float)
    for m in movements:
        if m.kind in ("out", "waste") and m.material:
            consumed[m.material.name] += m.quantity or 0
    consumed = sorted(consumed.items(), key=lambda kv: -kv[1])[:8]

    downtime = defaultdict(int)
    for t in tickets:
        if t.machine:
            downtime[t.machine.name] += t.downtime_minutes or 0
    downtime = sorted(downtime.items(), key=lambda kv: -kv[1])[:8]

    qc_pass = len([i for i in inspections if i.status == "pass"])
    qc_fail = len([i for i in inspections if i.status == "fail"])

    summary = {
        "orders": len(orders),
        "revenue": round(sum(o.total for o in orders), 2),
        "runs": len(runs),
        "units": round(sum(r.produced or 0 for r in runs), 2),
        "tickets": len(tickets),
        "downtime": sum(t.downtime_minutes or 0 for t in tickets),
        "qc_rate": int(round(qc_pass / (qc_pass + qc_fail) * 100)) if (qc_pass + qc_fail) else 100,
        "on_time": int(round(len([o for o in orders if not o.is_late]) / len(orders) * 100)) if orders else 100,
        "sms": SmsLog.query.filter(SmsLog.factory_id == fac.id,
                                   SmsLog.created_at >= since).count(),
        "ussd": UssdSession.query.filter(UssdSession.factory_id == fac.id,
                                         UssdSession.started_at >= since).count(),
    }
    return render_template("dash/reports.html", days=days, summary=summary,
                           by_status=by_status, top_products=top_products,
                           consumed=consumed, downtime=downtime,
                           qc_pass=qc_pass, qc_fail=qc_fail)


EXPORTS = {
    "materials": (Material, ["code", "name", "category", "unit", "quantity",
                             "min_stock", "reorder_qty", "unit_cost", "location"]),
    "products": (Product, ["sku", "name", "category", "unit_price", "build_days"]),
    "suppliers": (Supplier, ["name", "contact_name", "phone", "email",
                             "lead_time_days", "payment_terms"]),
    "customers": (Customer, ["name", "company", "phone", "email", "address"]),
    "orders": (Order, ["number", "status", "priority", "due_date", "created_at"]),
    "workers": (Worker, ["employee_no", "name", "phone", "trade", "station", "shift"]),
    "machines": (Machine, ["code", "name", "kind", "location", "status",
                           "last_service_at", "service_interval_days"]),
    "maintenance": (MaintenanceTicket, ["reference", "fault_type", "severity",
                                        "status", "downtime_minutes", "created_at"]),
    "quality": (QcInspection, ["reference", "status", "sample_size",
                               "defects_found", "standard", "created_at"]),
    "safety": (SafetyIncident, ["reference", "kind", "severity", "location",
                                "status", "created_at"]),
    "stock-ledger": (StockMovement, ["created_at", "kind", "quantity",
                                     "balance_after", "reference", "source"]),
    "messages": (SmsLog, ["created_at", "direction", "to_number", "category",
                          "status", "message"]),
    "pulse": (PulseSnapshot, ["taken_at", "overall", "production", "inventory",
                              "orders", "maintenance", "suppliers"]),
    "attendance": (Attendance, ["day", "check_in", "check_out", "hours", "source"]),
    "purchase-orders": (PurchaseOrder, ["number", "status", "expected_date",
                                        "received_at", "created_at"]),
}


@app.route("/dashboard/export/<dataset>.csv")
@login_required
def export_csv(dataset):
    if dataset not in EXPORTS:
        abort(404)
    model, fields = EXPORTS[dataset]
    rows = model.query.filter_by(factory_id=current_factory_id()).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f.replace("_", " ").title() for f in fields])
    for r in rows:
        writer.writerow([getattr(r, f, "") if getattr(r, f, None) is not None else ""
                         for f in fields])
    audit("export", dataset, None, f"{len(rows)} rows")
    stamp = _today().isoformat()
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="mzalendo-{dataset}-{stamp}.csv"',
                 "X-Content-Type-Options": "nosniff"})


# =============================================================================
#  SECTION 24 — USER MANAGEMENT
# =============================================================================

@app.route("/dashboard/users")
@roles_required("super_admin", "owner")
def dash_users():
    q = User.query
    if not current_user.is_super:
        q = q.filter(User.factory_id == current_user.factory_id)
    term = (request.args.get("q") or "").strip()
    role = request.args.get("role", "")
    if term:
        like = f"%{term}%"
        q = q.filter(or_(User.username.ilike(like), User.email.ilike(like),
                         User.full_name.ilike(like)))
    if role:
        q = q.filter(User.role == role)
    page = Page(q.order_by(User.role.desc(), User.username), page_args(), 20)
    return render_template("dash/users.html", page=page, term=term, role=role)


@app.route("/dashboard/users/new", methods=["GET", "POST"])
@app.route("/dashboard/users/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required("super_admin", "owner")
def user_form(user_id=None):
    item = None
    if user_id:
        item = db.session.get(User, user_id) or abort(404)
        if not current_user.is_super and item.factory_id != current_user.factory_id:
            abort(403)
    errors = {}
    generated = None

    if request.method == "POST":
        f = request.form
        uname = (f.get("username") or "").strip().lower()
        email = (f.get("email") or "").strip().lower()
        role = f.get("role") or "viewer"

        if not re.fullmatch(r"[a-z0-9_.]{3,32}", uname or ""):
            errors["username"] = "Use 3–32 lowercase letters, digits, dots or underscores."
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email or ""):
            errors["email"] = "That email address does not look right."
        if User.query.filter(func.lower(User.username) == uname,
                             User.id != (item.id if item else 0)).first():
            errors["username"] = "That username is taken."
        if User.query.filter(func.lower(User.email) == email,
                             User.id != (item.id if item else 0)).first():
            errors["email"] = "An account already uses that email."
        if role not in ROLES:
            errors["role"] = "Choose a valid role."
        if role == "super_admin" and not current_user.is_super:
            errors["role"] = "Only a super administrator can grant that role."
        if item and item.id == current_user.id and role != current_user.role:
            errors["role"] = "You cannot change your own role."

        if not errors:
            obj = item or User(factory_id=(to_int(f.get("factory_id")) or current_user.factory_id)
                               if current_user.is_super else current_user.factory_id)
            obj.username, obj.email, obj.role = uname, email, role
            obj.full_name = (f.get("full_name") or "").strip()[:160]
            obj.phone = norm_phone(f.get("phone", ""))
            if item and item.id != current_user.id:
                obj.is_active_flag = f.get("is_active") == "on"
            elif not item:
                obj.is_active_flag = f.get("is_active") == "on"
            if current_user.is_super and f.get("factory_id"):
                obj.factory_id = to_int(f.get("factory_id")) or None

            if not item:
                pw = (f.get("password") or "").strip()
                if not pw:
                    pw = generate_temp_password()
                    generated = pw
                else:
                    problems = password_problems(pw)
                    if problems:
                        errors["password"] = "Password needs " + ", ".join(problems) + "."
                        return render_template("dash/user_form.html", item=item,
                                               errors=errors, generated=None,
                                               factories=Factory.query.order_by(Factory.name).all())
                obj.set_password(pw)
                obj.must_change_password = True
                obj.created_by_id = current_user.id
            elif f.get("force_reset") == "on":
                obj.must_change_password = True

            db.session.add(obj)
            db.session.commit()
            audit("user_saved", "user", obj.id, f"{obj.username} role={obj.role}")

            if generated:
                flash(f"{obj.username} created. Temporary password: {generated} — "
                      f"share it once, it must be changed at first sign in.", "ok")
            else:
                flash(f"{obj.username} saved.", "ok")
            return redirect(url_for("dash_users"))

    return render_template("dash/user_form.html", item=item, errors=errors,
                           generated=generated,
                           factories=Factory.query.order_by(Factory.name).all())


def generate_temp_password(length=16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*?"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_problems(pw):
            return pw


@app.post("/dashboard/users/<int:user_id>/reset-password")
@roles_required("super_admin", "owner")
def user_reset_password(user_id):
    item = db.session.get(User, user_id) or abort(404)
    if not current_user.is_super and item.factory_id != current_user.factory_id:
        abort(403)
    pw = generate_temp_password()
    item.set_password(pw)
    item.must_change_password = True
    item.failed_logins = 0
    item.locked_until = None
    db.session.commit()
    audit("user_password_reset", "user", item.id, item.username)
    flash(f"Temporary password for {item.username}: {pw} — it must be changed at "
          f"the next sign in.", "warn")
    return redirect(url_for("dash_users"))


@app.post("/dashboard/users/<int:user_id>/toggle")
@roles_required("super_admin", "owner")
def user_toggle(user_id):
    item = db.session.get(User, user_id) or abort(404)
    if not current_user.is_super and item.factory_id != current_user.factory_id:
        abort(403)
    if item.id == current_user.id:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("dash_users"))
    item.is_active_flag = not item.is_active_flag
    item.locked_until = None
    item.failed_logins = 0
    db.session.commit()
    audit("user_toggled", "user", item.id,
          "activated" if item.is_active_flag else "deactivated")
    flash(f"{item.username} {'activated' if item.is_active_flag else 'deactivated'}.", "ok")
    return redirect(url_for("dash_users"))


@app.post("/dashboard/users/<int:user_id>/unlock")
@roles_required("super_admin", "owner")
def user_unlock(user_id):
    item = db.session.get(User, user_id) or abort(404)
    if not current_user.is_super and item.factory_id != current_user.factory_id:
        abort(403)
    item.locked_until = None
    item.failed_logins = 0
    db.session.commit()
    audit("user_unlocked", "user", item.id, item.username)
    flash(f"{item.username} unlocked.", "ok")
    return redirect(url_for("dash_users"))


@app.post("/dashboard/users/<int:user_id>/delete")
@roles_required("super_admin", "owner")
def user_delete(user_id):
    item = db.session.get(User, user_id) or abort(404)
    if not current_user.is_super and item.factory_id != current_user.factory_id:
        abort(403)
    if item.id == current_user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("dash_users"))
    if item.role == "super_admin" and User.query.filter_by(role="super_admin").count() <= 1:
        flash("The last super administrator cannot be deleted.", "error")
        return redirect(url_for("dash_users"))
    name = item.username
    MaintenanceTicket.query.filter_by(assigned_to_id=item.id).update({"assigned_to_id": None})
    QcInspection.query.filter_by(inspector_id=item.id).update({"inspector_id": None})
    StockMovement.query.filter_by(user_id=item.id).update({"user_id": None})
    AuditLog.query.filter_by(user_id=item.id).update({"user_id": None})
    db.session.delete(item)
    db.session.commit()
    audit("user_deleted", "user", user_id, name)
    flash(f"{name} deleted.", "ok")
    return redirect(url_for("dash_users"))


@app.route("/dashboard/audit")
@roles_required("super_admin", "owner")
def dash_audit():
    q = AuditLog.query
    if not current_user.is_super:
        q = q.filter(AuditLog.factory_id == current_user.factory_id)
    action = request.args.get("action", "")
    term = (request.args.get("q") or "").strip()
    if action:
        q = q.filter(AuditLog.action == action)
    if term:
        like = f"%{term}%"
        q = q.filter(or_(AuditLog.actor.ilike(like), AuditLog.detail.ilike(like),
                         AuditLog.entity.ilike(like)))
    page = Page(q.order_by(desc(AuditLog.created_at)), page_args(), 40)
    actions = sorted({a.action for a in AuditLog.query.limit(2000).all() if a.action})
    return render_template("dash/audit.html", page=page, action=action, term=term,
                           actions=actions)


# =============================================================================
#  SECTION 25 — SETTINGS & PLATFORM ADMINISTRATION
# =============================================================================

@app.route("/dashboard/settings", methods=["GET", "POST"])
@roles_required("super_admin", "owner")
def dash_settings():
    fac = current_factory()
    if request.method == "POST":
        f = request.form
        section = f.get("section", "profile")
        if section == "profile":
            fac.name = (f.get("name") or fac.name).strip()
            fac.sector = (f.get("sector") or "").strip()
            fac.county = (f.get("county") or "").strip()
            fac.address = (f.get("address") or "").strip()
            fac.phone = norm_phone(f.get("phone", ""))
            fac.email = (f.get("email") or "").strip().lower()
            fac.currency = (f.get("currency") or "KES").strip().upper()[:8]
        elif section == "telco":
            fac.ussd_code = (f.get("ussd_code") or "").strip()
            fac.at_username = (f.get("at_username") or "").strip()
            new_key = (f.get("at_api_key") or "").strip()
            if new_key and not new_key.startswith("•"):
                fac.at_api_key = new_key
            fac.at_sender_id = (f.get("at_sender_id") or "").strip()
            fac.sms_enabled = f.get("sms_enabled") == "on"
        elif section == "operations":
            fac.service_warn_days = to_int(f.get("service_warn_days"), 7)
            fac.working_days_per_week = to_int(f.get("working_days_per_week"), 6)
            fac.low_stock_grace = to_int(f.get("low_stock_grace"), 0)
        db.session.commit()
        audit("settings_saved", "factory", fac.id, section)
        flash("Settings saved.", "ok")
        return redirect(url_for("dash_settings") + f"#{section}")

    counts = {
        "materials": scoped(Material).count(),
        "products": scoped(Product).count(),
        "workers": scoped(Worker).count(),
        "machines": scoped(Machine).count(),
        "orders": scoped(Order).count(),
        "users": User.query.filter_by(factory_id=fac.id).count(),
    }
    return render_template("dash/settings.html", counts=counts)


@app.post("/dashboard/settings/test-sms")
@roles_required("super_admin", "owner")
@limiter.limit("6 per hour")
def settings_test_sms():
    fac = current_factory()
    to = norm_phone(request.form.get("test_number", "")) or current_user.phone
    if not to:
        flash("Enter a number to test with.", "error")
        return redirect(url_for("dash_settings") + "#telco")
    res = notify(fac, [to], f"Mzalendo: test message from {fac.name}. "
                            f"Your gateway is wired correctly.", "test")
    if res.get("simulated"):
        flash("Gateway is in simulation mode — the message was logged, not sent. "
              "Add your Africa's Talking key and switch SMS on to go live.", "warn")
    elif res.get("ok"):
        flash(f"Test message sent to {to}.", "ok")
    else:
        flash(f"The gateway refused: {res.get('reason', 'unknown error')}", "error")
    return redirect(url_for("dash_settings") + "#telco")


@app.route("/admin/factories")
@roles_required("super_admin")
def admin_factories():
    q = Factory.query
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Factory.name.ilike(like), Factory.slug.ilike(like)))
    page = Page(q.order_by(Factory.name), page_args(), 20)
    stats = {}
    for f in page.items:
        stats[f.id] = {
            "users": User.query.filter_by(factory_id=f.id).count(),
            "workers": Worker.query.filter_by(factory_id=f.id).count(),
            "orders": Order.query.filter_by(factory_id=f.id).count(),
        }
    return render_template("dash/admin_factories.html", page=page, term=term, stats=stats)


@app.route("/admin/factories/new", methods=["GET", "POST"])
@app.route("/admin/factories/<int:factory_id>/edit", methods=["GET", "POST"])
@roles_required("super_admin")
def admin_factory_form(factory_id=None):
    item = db.session.get(Factory, factory_id) if factory_id else None
    if factory_id and not item:
        abort(404)
    errors = {}
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        if not name:
            errors["name"] = "Give the plant a name."
        if not errors:
            obj = item or Factory(slug=slugify(name))
            if not item:
                base, n = obj.slug, 1
                while Factory.query.filter_by(slug=obj.slug).first():
                    n += 1
                    obj.slug = f"{base}-{n}"
            obj.name = name
            obj.sector = (f.get("sector") or "").strip()
            obj.county = (f.get("county") or "").strip()
            obj.address = (f.get("address") or "").strip()
            obj.phone = norm_phone(f.get("phone", ""))
            obj.email = (f.get("email") or "").strip().lower()
            obj.currency = (f.get("currency") or "KES").strip().upper()[:8]
            obj.plan = f.get("plan") or "starter"
            obj.ussd_code = (f.get("ussd_code") or "").strip()
            obj.is_active = f.get("is_active") == "on"
            db.session.add(obj)
            db.session.commit()
            audit("factory_saved", "factory", obj.id, obj.name)
            flash(f"{obj.name} saved.", "ok")
            return redirect(url_for("admin_factories"))
    return render_template("dash/admin_factory_form.html", item=item, errors=errors)


@app.post("/admin/factories/<int:factory_id>/toggle")
@roles_required("super_admin")
def admin_factory_toggle(factory_id):
    item = db.session.get(Factory, factory_id) or abort(404)
    item.is_active = not item.is_active
    db.session.commit()
    audit("factory_toggled", "factory", item.id, str(item.is_active))
    flash(f"{item.name} {'activated' if item.is_active else 'suspended'}.", "ok")
    return redirect(url_for("admin_factories"))


@app.route("/admin/factories/<int:factory_id>/delete", methods=["POST"])
@login_required
@roles_required("super_admin")
def admin_factory_delete(factory_id):
    """Delete a plant and everything in it. Irreversible, so it is guarded."""
    fac = Factory.query.get_or_404(factory_id)

    # Typing the name is the confirmation. A plant can hold years of records and
    # a misplaced click should not be enough to end them.
    typed = (request.form.get("confirm") or "").strip().lower()
    if typed != fac.name.strip().lower():
        flash("Type the plant name exactly to confirm deletion. Nothing was "
              "deleted.", "warn")
        return redirect(url_for("admin_factory_form", factory_id=fac.id))

    name = fac.name
    counts = {
        "materials": Material.query.filter_by(factory_id=fac.id).count(),
        "orders": Order.query.filter_by(factory_id=fac.id).count(),
        "workers": Worker.query.filter_by(factory_id=fac.id).count(),
    }
    purge_factory(fac, protect_emails=(Config.SEED_ADMIN_EMAIL,))

    # Anyone still looking at the deleted plant has to be moved off it.
    if session.get("factory_id") == factory_id:
        session.pop("factory_id", None)

    system_audit(None, current_user.username, "factory_delete", "factory",
                 factory_id,
                 f"{name} ({counts['materials']} materials, {counts['orders']} "
                 f"orders, {counts['workers']} workers)")
    flash(f"{name} and everything in it has been deleted.", "ok")
    return redirect(url_for("admin_factories"))


@app.route("/admin/system")
@roles_required("super_admin")
def admin_system():
    tables = [
        ("Plants", Factory), ("Users", User), ("Workers", Worker),
        ("Materials", Material), ("Products", Product), ("Suppliers", Supplier),
        ("Customers", Customer), ("Orders", Order), ("Production runs", ProductionRun),
        ("Machines", Machine), ("Maintenance", MaintenanceTicket),
        ("Inspections", QcInspection), ("Safety", SafetyIncident),
        ("Stock movements", StockMovement), ("SMS", SmsLog),
        ("USSD sessions", UssdSession), ("Alerts", Alert), ("Audit", AuditLog),
    ]
    counts = [(label, model.query.count()) for label, model in tables]
    db_path = app.config["SQLALCHEMY_DATABASE_URI"]
    size = None
    if db_path.startswith("sqlite:///"):
        p = db_path.replace("sqlite:///", "")
        size = round(os.path.getsize(p) / 1024, 1) if os.path.exists(p) else 0
    env = {
        "Environment": app.config["AT_ENVIRONMENT"],
        "SMS gateway": "live" if app.config["SMS_ENABLED"] else "simulation",
        "Public signup": "open" if app.config["ALLOW_PUBLIC_SIGNUP"] else "closed",
        "HTTPS enforced": "yes" if app.config["FORCE_HTTPS"] else "no",
        "USSD code": app.config["AT_USSD_CODE"],
    }
    return render_template("dash/admin_system.html", counts=counts, size=size, env=env)


# =============================================================================
#  SECTION 26 — USSD  (Africa's Talking callback)
# =============================================================================
#
#  Africa's Talking POSTs sessionId, serviceCode, phoneNumber and text on every
#  keypress. `text` accumulates every entry in the session joined by '*'.
#  Responses begin with CON (keep the session open) or END (close it).
#  A response must arrive inside 10 seconds and must avoid special characters.
#
#  Menu tree
#  ─────────
#  1  My tasks .............. runs assigned to this worker
#  2  Report production ..... pick run → enter units done
#  3  Report stock .......... pick material → issued / received / counted → qty
#  4  Machine fault ......... pick machine → fault → severity
#  5  Safety incident ....... type → severity
#  6  Clock in or out ....... attendance for today
#  7  Stock check ........... anything at or below the reorder line
#
# =============================================================================

USSD_PER_PAGE = 5

# Navigation keys. These are the ones Kenyan services already use, so nobody
# has to learn ours.
USSD_BACK = "0"        # one step back
USSD_HOME = "00"       # main menu, from any depth
USSD_MORE = "98"       # next page of a long list
USSD_EXIT = "99"       # end the session

USSD_FOOTER_ROOT = "99 Exit"
USSD_FOOTER_DEEP = "0 Back  00 Menu  99 Exit"
USSD_FOOTER_PAGED = "98 More  0 Back  00 Menu  99 Exit"
# On a screen that asks for a number, 0 is taken as Back rather than as the
# quantity zero. Reporting zero of something is not a useful entry, and a
# worker who sees the footer is not surprised by it.
USSD_FOOTER_ENTRY = "0 Back  00 Menu  99 Exit"


def ussd_navigate(tokens):
    """Apply the navigation keys to the accumulated path.

    Africa's Talking replays the whole path on every hop, so the menu is a pure
    function of it. That makes navigation a matter of rewriting the path rather
    than remembering anything: Back pops the last step, Menu empties the stack,
    Exit stops the session. The business logic underneath needs no changes.

    Returns (path, exit_requested).
    """
    path = []
    for token in tokens:
        if token == USSD_EXIT:
            return path, True
        if token == USSD_HOME:
            path = []
        elif token == USSD_BACK:
            if path:
                path.pop()
        else:
            path.append(token)
    return path, False


def ussd_lines(*lines) -> str:
    return "\n".join(str(l) for l in lines if l is not None)


def ussd_clean(text: str) -> str:
    """Telcos cannot render many symbols — keep the payload plain."""
    text = re.sub(r"[^\w\s.,:%/()+\-#*\n]", " ", text or "")
    return re.sub(r"[ \t]+", " ", text).strip()


def ussd_menu(title: str, items, page: int, labeller, per_page=USSD_PER_PAGE,
              footer=None) -> str:
    start = page * per_page
    window = items[start:start + per_page]
    lines = [f"{i}. {labeller(o)}" for i, o in enumerate(window, 1)]
    has_more = start + per_page < len(items)
    lines.append(footer if footer else
                 (USSD_FOOTER_PAGED if has_more else USSD_FOOTER_DEEP))
    if not window:
        return "END " + ussd_clean(f"{title}\nNothing to show.")
    return "CON " + ussd_clean(ussd_lines(title, *lines))


def ussd_pick(tokens, items, per_page=USSD_PER_PAGE):
    """
    Walk the tokens for one selection step.
    Returns (chosen, rest, page, error) where a chosen value of None means the
    menu still needs to be shown at `page`.
    """
    page, i = 0, 0
    while i < len(tokens):
        t = tokens[i]
        if t == USSD_MORE and (page + 1) * per_page < len(items):
            page += 1
            i += 1
            continue
        if not t.isdigit():
            return None, tokens[i + 1:], page, "invalid"
        n = int(t)
        window = items[page * per_page:(page + 1) * per_page]
        if 1 <= n <= len(window):
            return window[n - 1], tokens[i + 1:], page, None
        return None, tokens[i + 1:], page, "invalid"
    return None, [], page, None


def ussd_find_worker(phone: str):
    phone = norm_phone(phone)
    variants = {phone, phone.lstrip("+"), "0" + phone[4:] if phone.startswith("+254") else phone}
    worker = (Worker.query.filter(Worker.phone.in_(list(variants)),
                                  Worker.is_active.is_(True))
              .order_by(desc(Worker.updated_at)).first())
    return worker


def ussd_track(session_id, phone, service_code, network_code, text, worker,
               factory_id, status="active", outcome=""):
    row = UssdSession.query.filter_by(session_id=session_id).first()
    if not row:
        row = UssdSession(session_id=session_id, phone_number=norm_phone(phone),
                          service_code=service_code or "", network_code=network_code or "",
                          factory_id=factory_id,
                          worker_id=worker.id if worker else None)
        db.session.add(row)
    row.last_input = (text or "")[:500]
    row.hops = len([t for t in (text or "").split("*") if t != ""]) + 1
    row.status = status
    if outcome:
        row.outcome = outcome[:120]
    if status in ("completed", "abandoned"):
        row.ended_at = _now()
    db.session.commit()
    return row


# A single USSD screen carries roughly 182 GSM characters. Anything longer is
# truncated by the telco, which cuts a menu line in half and leaves the worker
# looking at an option they cannot read. Call sites trim individual labels, but
# a title, a greeting and a plant name are all free text — so the guarantee is
# enforced here, at the one point every screen leaves through.
USSD_MAX_CHARS = 182


def ussd_fit(body: str) -> str:
    """Trim a screen to one payload by dropping whole options, never mid-line."""
    if len(body) <= USSD_MAX_CHARS:
        return body
    lines = body.split("\n")
    if len(lines) < 3:
        return body[:USSD_MAX_CHARS].rstrip()
    head, tail = lines[0], lines[-1]
    # Always keep the last line. It is either a numbered option or the
    # navigation footer, and dropping either strands the reader with no way on.
    keep_tail = True
    middle = lines[1:-1] if keep_tail else lines[1:]
    used = len(head) + (len(tail) + 1 if keep_tail else 0)
    kept = []
    for line in middle:
        if used + len(line) + 1 > USSD_MAX_CHARS:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join([head] + kept + ([tail] if keep_tail else []))


def ussd_response(body: str, session, outcome=""):
    prefix, _, rest = body.partition(" ")
    if prefix in ("CON", "END"):
        body = prefix + " " + ussd_fit(rest)
    if body.startswith("END") and session:
        session.status = "completed"
        session.ended_at = _now()
        if outcome:
            session.outcome = outcome[:120]
        db.session.commit()
    resp = make_response(body, 200)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["at-ussd-hop-metadata"] = (outcome or "menu")[:99].replace("|", "-")
    return resp


@app.route("/webhooks/ussd", methods=["GET", "POST"])
@app.route("/ussd", methods=["POST", "GET"])
@app.route("/ussd/callback", methods=["POST", "GET"])
@csrf.exempt
@limiter.limit("240 per minute")
def ussd_callback():
    guard = _webhook_guard()
    if guard:
        return guard
    session_id = request.values.get("sessionId") or "sim-" + secrets.token_hex(6)
    service_code = request.values.get("serviceCode", "")
    phone = request.values.get("phoneNumber", "")
    network = request.values.get("networkCode", "")
    text = (request.values.get("text") or "").strip()

    tokens = [t for t in text.split("*")] if text else []
    tokens = [t.strip() for t in tokens]
    tokens, wants_exit = ussd_navigate(tokens)
    if wants_exit:
        sess = ussd_track(session_id, phone, service_code, network, text, None,
                          None, status="completed", outcome="exited")
        return ussd_response("END Thank you. Goodbye.", sess, "exited")

    worker = ussd_find_worker(phone)
    if not worker:
        ussd_track(session_id, phone, service_code, network, text, None, None,
                   "completed", "unregistered")
        return ussd_response(
            "END " + ussd_clean(
                "This number is not registered on Mzalendo. "
                "Ask your supervisor to add you to the plant, then dial again."),
            None)

    fac = db.session.get(Factory, worker.factory_id)
    if not fac or not fac.is_active:
        return ussd_response("END " + ussd_clean(
            "Your plant account is not active. Contact the owner."), None)

    sess = ussd_track(session_id, phone, service_code, network, text, worker, fac.id)

    # ---- root menu ---------------------------------------------------------
    if not tokens or tokens == [""]:
        first = worker.name.split()[0] if worker.name else "there"
        return ussd_response("CON " + ussd_clean(ussd_lines(
            f"MZALENDO - {fac.name[:22]}",
            f"Habari {first[:12]}",
            "1. My tasks",
            "2. Report production",
            "3. Report stock",
            "4. Machine fault",
            "5. Safety incident",
            "6. Clock in or out",
            "7. Stock check",
            USSD_FOOTER_ROOT)), sess, "root")

    choice = tokens[0]
    rest = tokens[1:]

    # ---- 1. My tasks -------------------------------------------------------
    if choice == "1":
        runs = (ProductionRun.query.filter(
            ProductionRun.factory_id == fac.id,
            ProductionRun.worker_id == worker.id,
            ProductionRun.status.notin_(("done", "cancelled")))
            .order_by(ProductionRun.start_date.asc().nulls_last()).all())
        if not runs:
            return ussd_response("END " + ussd_clean(
                "You have no open tasks. Check with your supervisor."), sess, "tasks_empty")
        chosen, _, page, err = ussd_pick(rest, runs)
        if chosen is None:
            if err:
                return ussd_response("END Invalid choice. Dial again.", sess, "invalid")
            return ussd_response(ussd_menu(
                "MY TASKS", runs, page,
                lambda r: f"{r.product.name[:14] if r.product else r.reference} "
                          f"{r.produced:g}/{r.quantity:g}"), sess, "tasks")
        stages = [s.name for s in chosen.stages if s.status != "done"][:3]
        return ussd_response("END " + ussd_clean(ussd_lines(
            f"{chosen.reference}",
            f"{chosen.product.name if chosen.product else 'Production'}",
            f"Target {chosen.quantity:g}, done {chosen.produced:g}",
            f"Due {chosen.end_date:%d %b}" if chosen.end_date else None,
            ("Next: " + ", ".join(stages)) if stages else None,
            f"Machine: {chosen.machine.name}" if chosen.machine else None)),
            sess, "task_detail")

    # ---- 2. Report production ---------------------------------------------
    if choice == "2":
        runs = (ProductionRun.query.filter(
            ProductionRun.factory_id == fac.id,
            ProductionRun.status.notin_(("done", "cancelled")))
            .order_by(case((ProductionRun.worker_id == worker.id, 0), else_=1),
                      ProductionRun.start_date.asc().nulls_last()).all())
        if not runs:
            return ussd_response("END " + ussd_clean(
                "There are no open production runs to report against."), sess, "prod_empty")
        chosen, tail, page, err = ussd_pick(rest, runs)
        if chosen is None:
            if err:
                return ussd_response("END Invalid choice. Dial again.", sess, "invalid")
            return ussd_response(ussd_menu(
                "REPORT PRODUCTION", runs, page,
                lambda r: f"{r.reference} {r.product.name[:12] if r.product else ''}"),
                sess, "prod_pick")
        if not tail:
            return ussd_response("CON " + ussd_clean(ussd_lines(
                f"{chosen.reference} - {chosen.product.name[:18] if chosen.product else ''}",
                f"Done so far {chosen.produced:g} of {chosen.quantity:g}",
                "Enter units completed now", USSD_FOOTER_ENTRY)), sess, "prod_qty")
        qty = to_float(tail[0], -1)
        if qty < 0:
            return ussd_response("END Enter a number. Dial again.", sess, "invalid")
        before = chosen.produced or 0
        chosen.produced = min((before + qty), chosen.quantity or (before + qty))
        if chosen.status == "planned":
            chosen.status = "running"
        if chosen.quantity and chosen.produced >= chosen.quantity:
            chosen.status = "done"
        chosen.worker_id = chosen.worker_id or worker.id
        db.session.commit()
        system_audit(fac.id, worker.name, "ussd_production", "production_run",
                     chosen.id, f"+{qty:g} units")
        if chosen.status == "done":
            raise_alert(fac.id, "production", "info",
                        f"Run {chosen.reference} completed",
                        f"{worker.name} reported the final units from the floor.",
                        "Book a quality inspection before dispatch.",
                        "production_run", chosen.id, dedupe_hours=0)
            phones = factory_managers_phones(fac.id)
            if phones:
                notify(fac, phones, f"Mzalendo: Run {chosen.reference} "
                                    f"({chosen.product.name if chosen.product else ''}) "
                                    f"is complete. Book quality check.", "production")
        return ussd_response("END " + ussd_clean(ussd_lines(
            "Recorded. Thank you.",
            f"{chosen.reference} now {chosen.produced:g} of {chosen.quantity:g}",
            "Run complete." if chosen.status == "done" else None)),
            sess, "prod_saved")

    # ---- 3. Report stock ---------------------------------------------------
    if choice == "3":
        materials = (Material.query.filter_by(factory_id=fac.id, is_active=True)
                     .order_by(Material.name).all())
        if not materials:
            return ussd_response("END No materials are registered yet.", sess, "stock_empty")
        chosen, tail, page, err = ussd_pick(rest, materials)
        if chosen is None:
            if err:
                return ussd_response("END Invalid choice. Dial again.", sess, "invalid")
            return ussd_response(ussd_menu(
                "REPORT STOCK", materials, page,
                lambda m: f"{m.name[:16]} {m.quantity:g}{m.unit}"), sess, "stock_pick")
        if not tail:
            return ussd_response("CON " + ussd_clean(ussd_lines(
                f"{chosen.name[:20]} - now {chosen.quantity:g} {chosen.unit}",
                "1. Issued to floor",
                "2. Received into store",
                "3. Counted, set balance",
                "4. Wasted or damaged", USSD_FOOTER_DEEP)), sess, "stock_kind")
        kind_map = {"1": "out", "2": "in", "3": "adjust", "4": "waste"}
        kind = kind_map.get(tail[0])
        if not kind:
            return ussd_response("END Invalid choice. Dial again.", sess, "invalid")
        if len(tail) < 2:
            verb = {"out": "issued", "in": "received", "adjust": "counted on the shelf",
                    "waste": "wasted"}[kind]
            return ussd_response("CON " + ussd_clean(ussd_lines(
                f"Enter quantity {verb} in {chosen.unit}",
                USSD_FOOTER_ENTRY)), sess, "stock_qty")
        qty = to_float(tail[1], -1)
        if qty < 0:
            return ussd_response("END Enter a number. Dial again.", sess, "invalid")
        move_stock(chosen, kind, qty, fac, reference="USSD",
                   note=f"Reported by {worker.name} over USSD",
                   source="ussd", worker_id=worker.id)
        system_audit(fac.id, worker.name, "ussd_stock", "material", chosen.id,
                     f"{kind} {qty:g}")
        warn = ""
        if chosen.stock_state in ("low", "out"):
            warn = "Below minimum. The office has been alerted."
        return ussd_response("END " + ussd_clean(ussd_lines(
            "Stock updated. Thank you.",
            f"{chosen.name[:20]} now {chosen.quantity:g} {chosen.unit}",
            warn or None)), sess, "stock_saved")

    # ---- 4. Machine fault --------------------------------------------------
    if choice == "4":
        machines = (Machine.query.filter(Machine.factory_id == fac.id,
                                         Machine.status != "retired")
                    .order_by(Machine.name).all())
        if not machines:
            return ussd_response("END No machines are registered yet.", sess, "mach_empty")
        chosen, tail, page, err = ussd_pick(rest, machines)
        if chosen is None:
            if err:
                return ussd_response("END Invalid choice. Dial again.", sess, "invalid")
            return ussd_response(ussd_menu(
                "SELECT MACHINE", machines, page,
                lambda m: f"{m.name[:18]}"), sess, "mach_pick")
        if not tail:
            return ussd_response("CON " + ussd_clean(ussd_lines(
                f"{chosen.name[:20]} - what is wrong",
                "1. Not starting",
                "2. Overheating",
                "3. Strange noise",
                "4. Leaking",
                "5. Other", USSD_FOOTER_DEEP)), sess, "mach_fault")
        fault = FAULT_TYPES.get(tail[0])
        if not fault:
            return ussd_response("END Invalid choice. Dial again.", sess, "invalid")
        if len(tail) < 2:
            return ussd_response("CON " + ussd_clean(ussd_lines(
                "How bad is it",
                "1. Machine still runs",
                "2. Running slowly",
                "3. Machine has stopped", USSD_FOOTER_DEEP)), sess, "mach_severity")
        sev_map = {"1": "low", "2": "medium", "3": "critical"}
        severity = sev_map.get(tail[1])
        if not severity:
            return ussd_response("END Invalid choice. Dial again.", sess, "invalid")

        ticket = MaintenanceTicket(
            factory_id=fac.id, reference=next_ref(MaintenanceTicket, fac.id, "MNT"),
            machine_id=chosen.id, worker_id=worker.id, fault_type=fault,
            severity=severity, status="open", source="ussd",
            description=f"Reported from the floor by {worker.name} over USSD.")
        db.session.add(ticket)
        if severity == "critical":
            chosen.status = "down"
        db.session.commit()

        raise_alert(fac.id, "maintenance",
                    "critical" if severity == "critical" else "high",
                    f"{chosen.name}: {fault}",
                    f"{worker.name} reported this from {chosen.location or 'the floor'}.",
                    "Assign a technician from Maintenance.",
                    "maintenance_ticket", ticket.id, dedupe_hours=0)
        phones = factory_managers_phones(fac.id)
        if phones:
            notify(fac, phones,
                   f"Mzalendo: {chosen.name} ({chosen.code}) reported {fault.lower()} "
                   f"by {worker.name}. Severity {severity}. Ticket {ticket.reference}.",
                   "maintenance")
        system_audit(fac.id, worker.name, "ussd_fault", "maintenance_ticket",
                     ticket.id, f"{chosen.code} {fault}")
        return ussd_response("END " + ussd_clean(ussd_lines(
            f"Logged as {ticket.reference}.",
            f"{chosen.name[:18]} - {fault}",
            "A technician has been alerted.")), sess, "mach_saved")

    # ---- 5. Safety incident ------------------------------------------------
    if choice == "5":
        if not rest:
            return ussd_response("CON " + ussd_clean(ussd_lines(
                "SAFETY INCIDENT",
                "1. Injury",
                "2. Near miss",
                "3. Fire or burn",
                "4. Chemical spill",
                "5. Electrical hazard",
                "6. Other", USSD_FOOTER_DEEP)), sess, "safety_kind")
        kind = INCIDENT_TYPES.get(rest[0])
        if not kind:
            return ussd_response("END Invalid choice. Dial again.", sess, "invalid")
        if len(rest) < 2:
            return ussd_response("CON " + ussd_clean(ussd_lines(
                "How serious is it",
                "1. No injury",
                "2. Minor, first aid",
                "3. Serious, needs a clinic",
                "4. Emergency", USSD_FOOTER_DEEP)), sess, "safety_severity")
        sev_map = {"1": "low", "2": "medium", "3": "high", "4": "critical"}
        severity = sev_map.get(rest[1])
        if not severity:
            return ussd_response("END Invalid choice. Dial again.", sess, "invalid")

        incident = SafetyIncident(
            factory_id=fac.id, reference=next_ref(SafetyIncident, fac.id, "SAF"),
            worker_id=worker.id, kind=kind, severity=severity,
            location=worker.station or "", status="open", source="ussd",
            description=f"Reported over USSD by {worker.name}.")
        db.session.add(incident)
        db.session.commit()

        raise_alert(fac.id, "safety",
                    "critical" if severity in ("high", "critical") else "medium",
                    f"Safety incident: {kind}",
                    f"{worker.name} reported this from "
                    f"{worker.station or 'the floor'}. Severity {severity}.",
                    "Attend to the person first, then record the corrective action.",
                    "safety_incident", incident.id, dedupe_hours=0)
        phones = factory_managers_phones(fac.id)
        if phones:
            notify(fac, phones,
                   f"Mzalendo: SAFETY - {kind} reported by {worker.name} at "
                   f"{worker.station or fac.name}. Severity {severity}. "
                   f"Reference {incident.reference}.", "safety")
        system_audit(fac.id, worker.name, "ussd_safety", "safety_incident",
                     incident.id, f"{kind} {severity}")
        return ussd_response("END " + ussd_clean(ussd_lines(
            f"Recorded as {incident.reference}.",
            "Your supervisor has been alerted.",
            "If anyone is hurt, get help first.")), sess, "safety_saved")

    # ---- 6. Attendance -----------------------------------------------------
    if choice == "6":
        today = _today()
        rec = Attendance.query.filter_by(factory_id=fac.id, worker_id=worker.id,
                                         day=today).first()
        if not rest:
            state = ("You are not clocked in yet." if not rec
                     else (f"Clocked in at {_to_local(rec.check_in):%H:%M}."
                           if not rec.check_out
                           else f"Clocked out at {_to_local(rec.check_out):%H:%M}."))
            return ussd_response("CON " + ussd_clean(ussd_lines(
                f"ATTENDANCE {today:%d %b}", state, "1. Clock in", "2. Clock out",
                USSD_FOOTER_DEEP)),
                sess, "attend")
        if rest[0] == "1":
            if rec and rec.check_in:
                return ussd_response("END " + ussd_clean(
                    f"You already clocked in at {_to_local(rec.check_in):%H:%M}."), sess, "attend_dup")
            if not rec:
                rec = Attendance(factory_id=fac.id, worker_id=worker.id, day=today,
                                 source="ussd")
                db.session.add(rec)
            rec.check_in = _now()
            db.session.commit()
            system_audit(fac.id, worker.name, "ussd_clock_in", "worker", worker.id)
            return ussd_response("END " + ussd_clean(
                f"Clocked in at {_to_local(rec.check_in):%H:%M}. Have a safe shift."),
                sess, "clock_in")
        if rest[0] == "2":
            if not rec or not rec.check_in:
                return ussd_response("END You have not clocked in today.",
                                     sess, "attend_none")
            if rec.check_out:
                return ussd_response("END " + ussd_clean(
                    f"You already clocked out at {_to_local(rec.check_out):%H:%M}."),
                    sess, "attend_dup")
            rec.check_out = _now()
            db.session.commit()
            system_audit(fac.id, worker.name, "ussd_clock_out", "worker", worker.id)
            return ussd_response("END " + ussd_clean(ussd_lines(
                f"Clocked out at {_to_local(rec.check_out):%H:%M}.",
                f"Hours today {rec.hours}")), sess, "clock_out")
        return ussd_response("END Invalid choice. Dial again.", sess, "invalid")

    # ---- 7. Stock check ----------------------------------------------------
    if choice == "7":
        materials = Material.query.filter_by(factory_id=fac.id, is_active=True).all()
        low = sorted([m for m in materials if m.stock_state in ("out", "low", "watch")],
                     key=lambda m: m.stock_pct)[:6]
        if not low:
            return ussd_response("END " + ussd_clean(
                "All materials are above their minimum. Nothing to reorder."),
                sess, "stock_ok")
        lines = [f"{m.name[:14]} {m.quantity:g}{m.unit} min {m.min_stock:g}" for m in low]
        return ussd_response("END " + ussd_clean(ussd_lines("BELOW MINIMUM", *lines)),
                             sess, "stock_check")

    return ussd_response("END Invalid choice. Dial again.", sess, "invalid")


@app.route("/dashboard/ussd/simulate", methods=["POST"])
@login_required
def ussd_simulate():
    """Run a USSD hop from the dashboard simulator.

    Deliberately the same engine as the public callback — a simulator that ran
    different code would prove nothing. It differs only in how it
    authenticates: a signed-in session and a CSRF token, rather than the shared
    webhook secret, which has no business being handed to a browser.
    """
    g.ussd_from_dashboard = True
    return ussd_callback()


@app.route("/webhooks/ussd/event", methods=["GET", "POST"])
@app.route("/ussd/events", methods=["POST"])
@csrf.exempt
def ussd_events():
    guard = _webhook_guard()
    if guard:
        return guard
    """End-of-session notification from Africa's Talking."""
    session_id = request.values.get("sessionId", "")
    row = UssdSession.query.filter_by(session_id=session_id).first()
    if row:
        status = (request.values.get("status") or "").lower()
        row.status = {"success": "completed", "incomplete": "abandoned",
                      "failed": "failed"}.get(status, row.status)
        row.hops = to_int(request.values.get("hopsCount"), row.hops)
        row.ended_at = _now()
        if request.values.get("errorMessage"):
            row.outcome = request.values.get("errorMessage")[:120]
        db.session.commit()
    return Response("OK", mimetype="text/plain")


# =============================================================================
#  SECTION 27 — SMS CALLBACKS
# =============================================================================

SMS_HELP = ("Mzalendo commands: STOCK <material> to check a balance, "
            "IN <material> <qty>, OUT <material> <qty>, DOWN <machine code>, "
            "TASKS for your jobs.")


@app.route("/webhooks/sms/delivery", methods=["GET", "POST"])
@app.route("/sms/delivery", methods=["POST"])
@csrf.exempt
def sms_delivery():
    guard = _webhook_guard()
    if guard:
        return guard
    """Delivery report callback."""
    provider_id = request.values.get("id", "")
    row = SmsLog.query.filter_by(provider_id=provider_id).first()
    if row:
        row.status = request.values.get("status", row.status)
        if request.values.get("failureReason"):
            row.error = request.values.get("failureReason")[:240]
        db.session.commit()
    return Response("OK", mimetype="text/plain")


@app.route("/webhooks/sms/optout", methods=["GET", "POST"])
@app.route("/sms/optout", methods=["POST"])
@csrf.exempt
def sms_optout():
    guard = _webhook_guard()
    if guard:
        return guard
    phone = norm_phone(request.values.get("phoneNumber", ""))
    for c in Customer.query.filter(Customer.phone == phone).all():
        c.sms_updates = False
    db.session.commit()
    log.info("opt-out recorded for %s", phone)
    return Response("OK", mimetype="text/plain")


@app.route("/webhooks/sms/subscription", methods=["GET", "POST"])
@app.route("/sms/subscription", methods=["POST"])
@csrf.exempt
def sms_subscription():
    guard = _webhook_guard()
    if guard:
        return guard
    db.session.add(SmsLog(
        direction="in", from_number=norm_phone(request.values.get("phoneNumber", "")),
        to_number=request.values.get("shortCode", ""), category="subscription",
        message=f"{request.values.get('updateType', '')} {request.values.get('keyword', '')}",
        status="received"))
    db.session.commit()
    return Response("OK", mimetype="text/plain")


@app.route("/webhooks/sms/inbound", methods=["GET", "POST"])
@app.route("/sms/incoming", methods=["POST"])
@csrf.exempt
@limiter.limit("240 per minute")
def sms_incoming():
    guard = _webhook_guard()
    if guard:
        return guard
    """
    Two-way SMS. A worker without airtime for a USSD session can still text a
    short command to the shortcode and get an answer back.
    """
    frm = norm_phone(request.values.get("from", ""))
    text = (request.values.get("text") or "").strip()
    to = request.values.get("to", "")

    worker = ussd_find_worker(frm)
    fac = db.session.get(Factory, worker.factory_id) if worker else None

    db.session.add(SmsLog(factory_id=fac.id if fac else None, direction="in",
                          from_number=frm, to_number=to, message=text[:600],
                          category="command", status="received"))
    db.session.commit()

    if not worker or not fac:
        return Response("OK", mimetype="text/plain")

    parts = text.split()
    cmd = (parts[0].upper() if parts else "")
    reply = None

    if cmd in ("HELP", "MENU", "?"):
        reply = SMS_HELP
    elif cmd == "TASKS":
        runs = ProductionRun.query.filter(
            ProductionRun.factory_id == fac.id, ProductionRun.worker_id == worker.id,
            ProductionRun.status.notin_(("done", "cancelled"))).limit(4).all()
        reply = ("Mzalendo tasks: " + "; ".join(
            f"{r.reference} {r.product.name if r.product else ''} "
            f"{r.produced:g}/{r.quantity:g}" for r in runs)) if runs else \
            "Mzalendo: you have no open tasks."
    elif cmd == "STOCK" and len(parts) >= 2:
        term = " ".join(parts[1:])
        m = Material.query.filter(Material.factory_id == fac.id,
                                  Material.name.ilike(f"%{term}%")).first()
        reply = (f"Mzalendo: {m.name} balance {m.quantity:g} {m.unit}, "
                 f"minimum {m.min_stock:g}." if m
                 else f"Mzalendo: no material matches '{term}'.")
    elif cmd in ("IN", "OUT") and len(parts) >= 3:
        qty = to_float(parts[-1], -1)
        term = " ".join(parts[1:-1])
        m = Material.query.filter(Material.factory_id == fac.id,
                                  Material.name.ilike(f"%{term}%")).first()
        if m and qty >= 0:
            move_stock(m, "in" if cmd == "IN" else "out", qty, fac,
                       reference="SMS", note=f"Texted by {worker.name}",
                       source="sms", worker_id=worker.id)
            reply = f"Mzalendo: {m.name} now {m.quantity:g} {m.unit}."
        else:
            reply = "Mzalendo: could not read that. Use IN <material> <qty>."
    elif cmd == "DOWN" and len(parts) >= 2:
        code = parts[1].upper()
        mc = Machine.query.filter(Machine.factory_id == fac.id,
                                  func.upper(Machine.code) == code).first()
        if mc:
            mc.status = "down"
            ticket = MaintenanceTicket(
                factory_id=fac.id, reference=next_ref(MaintenanceTicket, fac.id, "MNT"),
                machine_id=mc.id, worker_id=worker.id, fault_type="Other",
                severity="critical", status="open", source="sms",
                description=f"Reported by SMS from {worker.name}: {text[:180]}")
            db.session.add(ticket)
            db.session.commit()
            raise_alert(fac.id, "maintenance", "critical", f"{mc.name} reported down by SMS",
                        text[:200], "Assign a technician.", "maintenance_ticket",
                        ticket.id, dedupe_hours=0)
            notify(fac, factory_managers_phones(fac.id),
                   f"Mzalendo: {mc.name} reported DOWN by {worker.name}. "
                   f"Ticket {ticket.reference}.", "maintenance")
            reply = f"Mzalendo: logged {ticket.reference} for {mc.name}."
        else:
            reply = f"Mzalendo: no machine with code {code}."
    else:
        reply = SMS_HELP

    if reply:
        notify(fac, [frm], reply, "reply")
    return Response("OK", mimetype="text/plain")


# =============================================================================
#  SECTION 28 — JSON API (dashboard widgets and integrations)
# =============================================================================

@app.route("/api/pulse")
@login_required
def api_pulse():
    fac = current_factory()
    if not fac:
        return jsonify(error="no plant"), 404
    return jsonify(compute_pulse(fac.id) | {"taken_at": _now().isoformat() + "Z"})


@app.route("/api/pulse/history")
@login_required
def api_pulse_history():
    rows = (PulseSnapshot.query.filter_by(factory_id=current_factory_id())
            .order_by(desc(PulseSnapshot.taken_at)).limit(60).all())[::-1]
    return jsonify([{
        "t": r.taken_at.isoformat() + "Z", "overall": r.overall,
        "production": r.production, "inventory": r.inventory, "orders": r.orders,
        "maintenance": r.maintenance, "suppliers": r.suppliers} for r in rows])


@app.route("/api/alerts")
@login_required
def api_alerts():
    rows = (Alert.query.filter_by(factory_id=current_factory_id(), is_read=False)
            .order_by(desc(Alert.created_at)).limit(20).all())
    return jsonify({"count": len(rows), "items": [{
        "id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title,
        "body": a.body, "recommendation": a.recommendation,
        "created_at": a.created_at.isoformat() + "Z"} for a in rows]})


@app.route("/api/search")
@login_required
def api_search():
    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify(results=[])
    like = f"%{term}%"
    fid = current_factory_id()
    results = []

    for m in Material.query.filter(Material.factory_id == fid,
                                   or_(Material.name.ilike(like),
                                       Material.code.ilike(like))).limit(5):
        results.append({"type": "Material", "label": m.name, "meta": f"{m.quantity:g} {m.unit}",
                        "url": url_for("material_detail", material_id=m.id)})
    for o in Order.query.filter(Order.factory_id == fid,
                                Order.number.ilike(like)).limit(5):
        results.append({"type": "Order", "label": o.number, "meta": o.status_label,
                        "url": url_for("order_detail", order_id=o.id)})
    for p in Product.query.filter(Product.factory_id == fid,
                                  or_(Product.name.ilike(like),
                                      Product.sku.ilike(like))).limit(5):
        results.append({"type": "Product", "label": p.name, "meta": p.sku,
                        "url": url_for("product_detail", product_id=p.id)})
    for w in Worker.query.filter(Worker.factory_id == fid,
                                 or_(Worker.name.ilike(like),
                                     Worker.phone.ilike(like))).limit(5):
        results.append({"type": "Worker", "label": w.name, "meta": w.trade or w.phone,
                        "url": url_for("worker_detail", worker_id=w.id)})
    for mc in Machine.query.filter(Machine.factory_id == fid,
                                   or_(Machine.name.ilike(like),
                                       Machine.code.ilike(like))).limit(5):
        results.append({"type": "Machine", "label": mc.name, "meta": mc.status,
                        "url": url_for("machine_detail", machine_id=mc.id)})
    for c in Customer.query.filter(Customer.factory_id == fid,
                                   or_(Customer.name.ilike(like),
                                       Customer.company.ilike(like))).limit(5):
        results.append({"type": "Customer", "label": c.name, "meta": c.company or c.phone,
                        "url": url_for("customer_detail", customer_id=c.id)})
    return jsonify(results=results[:18])


@app.route("/api/product/<int:product_id>")
@login_required
def api_product(product_id):
    p = get_scoped_or_404(Product, product_id)
    return jsonify(id=p.id, name=p.name, sku=p.sku, unit_price=p.unit_price,
                   build_days=p.build_days,
                   bom=[{"material": b.material.name if b.material else "",
                         "qty": b.qty_per_unit,
                         "unit": b.material.unit if b.material else ""} for b in p.bom])


@app.route("/api/material/<int:material_id>")
@login_required
def api_material(material_id):
    m = get_scoped_or_404(Material, material_id)
    return jsonify(id=m.id, name=m.name, code=m.code, unit=m.unit,
                   quantity=m.quantity, min_stock=m.min_stock,
                   unit_cost=m.unit_cost, state=m.stock_state)


# =============================================================================
#  SECTION 29 — ERROR HANDLERS
# =============================================================================

def _wants_json():
    return (request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json")


@app.errorhandler(400)
def err_400(e):
    if _wants_json():
        return jsonify(error="bad request"), 400
    return render_template("errors/error.html", code="400", title="That request did not add up",
                           body="Something in the form was missing or malformed. "
                                "Go back, check the fields and send it again."), 400


@app.errorhandler(403)
def err_403(e):
    if _wants_json():
        return jsonify(error="forbidden"), 403
    return render_template("errors/error.html", code="403", title="This area is restricted",
                           body="Your role does not open this door. Ask the plant owner "
                                "to widen your access if you need it."), 403


@app.errorhandler(404)
def err_404(e):
    if _wants_json():
        return jsonify(error="not found"), 404
    return render_template("errors/404.html"), 404


@app.errorhandler(405)
def err_405(e):
    if _wants_json():
        return jsonify(error="method not allowed"), 405
    return render_template("errors/error.html", code="405", title="Wrong way in",
                           body="That address does not accept this kind of request."), 405


@app.errorhandler(413)
def err_413(e):
    return render_template("errors/error.html", code="413", title="That file is too heavy",
                           body="Uploads are capped at 8 MB. Compress the file and try again."), 413


@app.errorhandler(429)
def err_429(e):
    if _wants_json():
        return jsonify(error="rate limited"), 429
    return render_template("errors/error.html", code="429", title="Slow down a moment",
                           body="Too many requests came from this connection. "
                                "Wait a minute, then carry on."), 429


@app.errorhandler(CSRFError)
def err_csrf(e):
    return render_template("errors/error.html", code="400", title="This form expired",
                           body="Security tokens last eight hours. Reload the page "
                                "and submit again."), 400


@app.errorhandler(500)
def err_500(e):                                                # pragma: no cover
    db.session.rollback()
    log.exception("unhandled error")
    return render_template("errors/error.html", code="500", title="The line stopped",
                           body="Something failed on our side. The fault has been "
                                "logged and nothing you entered was lost."), 500


@app.errorhandler(Exception)
def err_unhandled(e):                                          # pragma: no cover
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    db.session.rollback()
    log.exception("unhandled exception")
    return render_template("errors/error.html", code="500", title="The line stopped",
                           body="Something failed on our side. The fault has been "
                                "logged and nothing you entered was lost."), 500


# =============================================================================
#  SECTION 30 — SEEDING
# =============================================================================

def seed_demo_plant():
    """
    A believable Kamukunji metal workshop, so the dashboard tells a real story
    the first time it is opened.
    """
    if Factory.query.filter_by(slug="kamukunji-metalworks").first():
        return

    fac = Factory(
        name="Kamukunji Metalworks", slug="kamukunji-metalworks",
        sector="Metal fabrication", county="Nairobi",
        address="Kamukunji Jua Kali Shed, Off Landhies Road",
        phone="+254711000100", email="workshop@kamukunji.example",
        currency="KES", plan="business", ussd_code=Config.AT_USSD_CODE,
        sms_enabled=False)
    db.session.add(fac)
    db.session.flush()

    owner = User(factory_id=fac.id, username="owino", email="owino@kamukunji.example",
                 full_name="Richard Owino", phone="+254711000100", role="owner")
    owner.set_password("Kamukunji@2026!")
    manager = User(factory_id=fac.id, username="wanjiku",
                   email="wanjiku@kamukunji.example", full_name="Grace Wanjiku",
                   phone="+254711000101", role="manager")
    manager.set_password("Kamukunji@2026!")
    supervisor = User(factory_id=fac.id, username="mutiso",
                      email="mutiso@kamukunji.example", full_name="Daniel Mutiso",
                      phone="+254711000102", role="supervisor")
    supervisor.set_password("Kamukunji@2026!")
    db.session.add_all([owner, manager, supervisor])

    suppliers = [
        Supplier(factory_id=fac.id, name="Devki Steel Depot", contact_name="Peter Kimani",
                 phone="+254720111222", email="sales@devkidepot.example",
                 address="Industrial Area, Nairobi",
                 materials_supplied="Mild steel sheet, angle iron, square tube",
                 lead_time_days=4, payment_terms="30 days",
                 orders_placed=18, orders_on_time=16, orders_complete=17, defect_reports=1),
        Supplier(factory_id=fac.id, name="Gikomba Hardware", contact_name="Alice Njeri",
                 phone="+254733222333", email="alice@gikombahw.example",
                 address="Gikomba Market", materials_supplied="Hinges, locks, handles, rivets",
                 lead_time_days=2, payment_terms="On delivery",
                 orders_placed=25, orders_on_time=21, orders_complete=24, defect_reports=2),
        Supplier(factory_id=fac.id, name="Basco Paints Agent", contact_name="Samuel Otieno",
                 phone="+254701444555", email="orders@bascoagent.example",
                 address="Ngara", materials_supplied="Enamel paint, thinner, primer",
                 lead_time_days=7, payment_terms="50 percent deposit",
                 orders_placed=9, orders_on_time=4, orders_complete=7, defect_reports=2),
    ]
    db.session.add_all(suppliers)
    db.session.flush()

    mats = [
        ("MS-2MM", "Mild steel sheet 2mm", "Metal", "sheet", 6, 12, 40, 2150, "Rack A1", 0),
        ("MS-1MM", "Mild steel sheet 1mm", "Metal", "sheet", 31, 10, 30, 1580, "Rack A2", 0),
        ("AL-40", "Aluminium sheet 1.2mm", "Metal", "sheet", 0, 8, 24, 3400, "Rack A3", 0),
        ("SQ-25", "Square tube 25x25x1.5", "Metal", "m", 240, 80, 200, 340, "Rack B1", 0),
        ("ANG-40", "Angle iron 40x40", "Metal", "m", 96, 60, 180, 410, "Rack B2", 0),
        ("HNG-100", "Butt hinges 100mm", "Hardware", "pcs", 118, 150, 400, 110, "Bin C1", 1),
        ("LCK-STD", "Cabinet locks", "Hardware", "pcs", 64, 40, 150, 260, "Bin C2", 1),
        ("HDL-CH", "Chrome handles", "Hardware", "pcs", 210, 60, 200, 180, "Bin C3", 1),
        ("PNT-BLK", "Enamel paint black", "Finishing", "litre", 22, 10, 40, 820, "Store D", 2),
        ("PNT-GRY", "Enamel paint grey", "Finishing", "litre", 7, 10, 40, 820, "Store D", 2),
        ("THN-01", "Paint thinner", "Finishing", "litre", 15, 8, 30, 540, "Store D", 2),
        ("WLD-25", "Welding rods 2.5mm", "Consumable", "kg", 9, 10, 30, 480, "Store E", 0),
        ("GRD-DSC", "Grinding discs 7in", "Consumable", "pcs", 46, 20, 60, 320, "Store E", 0),
    ]
    material_map = {}
    for code, name, cat, unit, qty, minimum, reorder, cost, loc, sup_idx in mats:
        m = Material(factory_id=fac.id, code=code, name=name, category=cat, unit=unit,
                     quantity=qty, min_stock=minimum, reorder_qty=reorder,
                     unit_cost=cost, location=loc, supplier_id=suppliers[sup_idx].id)
        db.session.add(m)
        db.session.flush()
        material_map[code] = m
        db.session.add(StockMovement(factory_id=fac.id, material_id=m.id, kind="in",
                                     quantity=qty, balance_after=qty,
                                     reference="Opening balance", source="system",
                                     created_at=_now() - timedelta(days=21)))

    machines = [
        Machine(factory_id=fac.id, code="PRS-A", name="Sheet press A", kind="Press",
                location="Bay 1", status="running",
                commissioned_on=date(2019, 3, 14),
                last_service_at=_today() - timedelta(days=104),
                service_interval_days=90, runtime_hours=8420),
        Machine(factory_id=fac.id, code="LTH-B", name="Lathe B", kind="Turning",
                location="Bay 2", status="idle",
                commissioned_on=date(2021, 7, 2),
                last_service_at=_today() - timedelta(days=31),
                service_interval_days=60, runtime_hours=3110),
        Machine(factory_id=fac.id, code="WLD-C", name="Arc welder C", kind="Welding",
                location="Bay 2", status="down",
                commissioned_on=date(2020, 11, 20),
                last_service_at=_today() - timedelta(days=58),
                service_interval_days=60, runtime_hours=5240),
        Machine(factory_id=fac.id, code="GRD-D", name="Bench grinder D", kind="Grinding",
                location="Bay 3", status="running",
                commissioned_on=date(2022, 1, 9),
                last_service_at=_today() - timedelta(days=12),
                service_interval_days=90, runtime_hours=1980),
        Machine(factory_id=fac.id, code="SPR-E", name="Spray booth E", kind="Finishing",
                location="Bay 4", status="idle",
                commissioned_on=date(2023, 5, 30),
                last_service_at=_today() - timedelta(days=84),
                service_interval_days=90, runtime_hours=1240),
    ]
    db.session.add_all(machines)
    db.session.flush()

    workers = [
        Worker(factory_id=fac.id, employee_no="KM-01", name="Joseph Kamau",
               phone="+254712000001", trade="Welder", station="Bay 2", shift="day",
               daily_rate=1400),
        Worker(factory_id=fac.id, employee_no="KM-02", name="Mary Achieng",
               phone="+254712000002", trade="Fabricator", station="Bay 1", shift="day",
               daily_rate=1300),
        Worker(factory_id=fac.id, employee_no="KM-03", name="Peter Kariuki",
               phone="+254712000003", trade="Painter", station="Bay 4", shift="day",
               daily_rate=1200),
        Worker(factory_id=fac.id, employee_no="KM-04", name="Faith Nyambura",
               phone="+254712000004", trade="Assembler", station="Bay 3", shift="day",
               daily_rate=1150),
        Worker(factory_id=fac.id, employee_no="KM-05", name="Brian Omondi",
               phone="+254712000005", trade="Machinist", station="Bay 2", shift="night",
               daily_rate=1500),
        Worker(factory_id=fac.id, employee_no="KM-06", name="Esther Wambui",
               phone="+254712000006", trade="Quality checker", station="Bay 5",
               shift="day", daily_rate=1350),
    ]
    for w in workers:
        w.set_pin("1234")
    db.session.add_all(workers)
    db.session.flush()

    products = [
        ("CAB-MTL", "Metal cabinet 4 door", "Furniture", 24500, 6,
         [("MS-1MM", 3), ("SQ-25", 12), ("HNG-100", 8), ("LCK-STD", 4),
          ("HDL-CH", 4), ("PNT-GRY", 1.2), ("WLD-25", 0.6)]),
        ("BOX-STD", "Metal storage box", "Household", 3800, 2,
         [("MS-1MM", 1), ("HNG-100", 2), ("LCK-STD", 1), ("PNT-BLK", 0.4),
          ("WLD-25", 0.2)]),
        ("JKO-16", "Charcoal jiko 16 inch", "Household", 2100, 2,
         [("MS-2MM", 0.6), ("ANG-40", 1.2), ("WLD-25", 0.25)]),
        ("WBR-HD", "Heavy duty wheelbarrow", "Site equipment", 8900, 4,
         [("MS-2MM", 1.2), ("SQ-25", 4), ("ANG-40", 2), ("PNT-BLK", 0.8),
          ("WLD-25", 0.5)]),
        ("GTE-SW", "Swing gate 3m", "Fabrication", 46000, 7,
         [("SQ-25", 34), ("ANG-40", 18), ("MS-2MM", 2), ("PNT-BLK", 2.5),
          ("WLD-25", 1.4), ("GRD-DSC", 3)]),
    ]
    product_map = {}
    for sku, name, cat, price, days, bom in products:
        p = Product(factory_id=fac.id, sku=sku, name=name, category=cat,
                    unit_price=price, build_days=days,
                    stages="\n".join(DEFAULT_STAGES),
                    description=f"Made to order at {fac.name}.")
        db.session.add(p)
        db.session.flush()
        product_map[sku] = p
        for mcode, qty in bom:
            db.session.add(BomItem(product_id=p.id, material_id=material_map[mcode].id,
                                   qty_per_unit=qty))

    customers = [
        Customer(factory_id=fac.id, name="Gladys Njoki", company="Njoki Hardware, Thika",
                 phone="+254722334455", email="gladys@njokihw.example",
                 address="Thika Town"),
        Customer(factory_id=fac.id, name="St Anne's Academy", company="St Anne's Academy",
                 phone="+254733445566", email="procurement@stannes.example",
                 address="Kasarani"),
        Customer(factory_id=fac.id, name="Kevin Mwangi", company="Mwangi Contractors",
                 phone="+254700556677", email="kevin@mwangicon.example",
                 address="Ruiru"),
        Customer(factory_id=fac.id, name="Umoja Retail", company="Umoja Retail Ltd",
                 phone="+254711667788", email="buying@umojaretail.example",
                 address="Embakasi"),
    ]
    db.session.add_all(customers)
    db.session.flush()

    order_specs = [
        ("ORD-0001", customers[1], "in_production", "high", 3, [("CAB-MTL", 12)]),
        ("ORD-0002", customers[0], "scheduled", "normal", 9, [("BOX-STD", 60), ("JKO-16", 40)]),
        ("ORD-0003", customers[2], "confirmed", "rush", -2, [("GTE-SW", 2)]),
        ("ORD-0004", customers[3], "new", "normal", 16, [("WBR-HD", 15)]),
        ("ORD-0005", customers[0], "completed", "normal", -12, [("JKO-16", 80)]),
        ("ORD-0006", customers[1], "quality_check", "normal", 1, [("BOX-STD", 25)]),
    ]
    orders = []
    for number, cust, status, priority, due_offset, lines in order_specs:
        o = Order(factory_id=fac.id, number=number, customer_id=cust.id, status=status,
                  priority=priority, due_date=_today() + timedelta(days=due_offset),
                  created_at=_now() - timedelta(days=max(1, 20 - due_offset)))
        db.session.add(o)
        db.session.flush()
        for sku, qty in lines:
            p = product_map[sku]
            db.session.add(OrderItem(order_id=o.id, product_id=p.id, quantity=qty,
                                     unit_price=p.unit_price))
        orders.append(o)
    db.session.flush()

    run_specs = [
        (orders[0], "CAB-MTL", 12, 7, "running", -2, 4, workers[0], machines[0]),
        (orders[1], "BOX-STD", 60, 0, "planned", 1, 3, workers[1], machines[1]),
        (orders[2], "GTE-SW", 2, 0, "blocked", -6, -1, workers[0], machines[2]),
        (orders[5], "BOX-STD", 25, 25, "done", -8, -3, workers[3], machines[1]),
        (orders[3], "WBR-HD", 15, 0, "planned", 4, 8, workers[4], machines[3]),
    ]
    for order, sku, qty, produced, status, start_off, end_off, worker, machine in run_specs:
        p = product_map[sku]
        run = ProductionRun(
            factory_id=fac.id, reference=next_ref(ProductionRun, fac.id, "RUN"),
            order_id=order.id, product_id=p.id, machine_id=machine.id,
            worker_id=worker.id, quantity=qty, produced=produced, status=status,
            start_date=_today() + timedelta(days=start_off),
            end_date=_today() + timedelta(days=end_off))
        db.session.add(run)
        db.session.flush()
        day = run.start_date
        for i, stage in enumerate(DEFAULT_STAGES, start=1):
            done = status == "done" or (status == "running" and i <= 3)
            db.session.add(RunStage(run_id=run.id, sequence=i, name=stage,
                                    planned_date=day, status="done" if done else "pending",
                                    completed_at=_now() - timedelta(days=1) if done else None,
                                    worker_id=worker.id))
            day += timedelta(days=1)

    db.session.add_all([
        MaintenanceTicket(factory_id=fac.id, reference="MNT-0001", machine_id=machines[2].id,
                          worker_id=workers[0].id, fault_type="Not starting",
                          severity="critical", status="open", source="ussd",
                          description="Reported from the floor over USSD. No arc, "
                                      "breaker trips on start.",
                          created_at=_now() - timedelta(hours=5)),
        MaintenanceTicket(factory_id=fac.id, reference="MNT-0002", machine_id=machines[0].id,
                          worker_id=workers[1].id, fault_type="Strange noise",
                          severity="medium", status="assigned", source="ussd",
                          description="Knocking sound under load on the press ram.",
                          created_at=_now() - timedelta(days=2)),
        MaintenanceTicket(factory_id=fac.id, reference="MNT-0003", machine_id=machines[3].id,
                          worker_id=workers[2].id, fault_type="Overheating",
                          severity="low", status="resolved", source="web",
                          description="Motor housing hot after long runs.",
                          resolution="Cleaned vents and replaced the fan belt.",
                          downtime_minutes=90, resolved_at=_now() - timedelta(days=4),
                          created_at=_now() - timedelta(days=5)),
    ])

    insp = QcInspection(factory_id=fac.id, reference="QC-0001", order_id=orders[5].id,
                        product_id=product_map["BOX-STD"].id, sample_size=10,
                        defects_found=1, status="fail", standard="KEBS KS 2570",
                        notes="One unit had uneven paint on the lid.")
    db.session.add(insp)
    db.session.flush()
    for label in DEFAULT_QC_CHECKS:
        db.session.add(QcCheck(inspection_id=insp.id, label=label,
                               passed=label != "Paint coverage even",
                               note="" if label != "Paint coverage even" else "Rework lid"))

    db.session.add_all([
        SafetyIncident(factory_id=fac.id, reference="SAF-0001", worker_id=workers[2].id,
                       kind="Near miss", severity="medium", location="Bay 4",
                       description="Sheet slipped off the trestle while being carried.",
                       action_taken="Trestles re-spaced and a second carrier assigned.",
                       status="resolved", source="ussd",
                       resolved_at=_now() - timedelta(days=6),
                       created_at=_now() - timedelta(days=7)),
        SafetyIncident(factory_id=fac.id, reference="SAF-0002", worker_id=workers[0].id,
                       kind="Fire or burn", severity="low", location="Bay 2",
                       description="Spark caught an overall sleeve. No injury.",
                       status="open", source="ussd",
                       created_at=_now() - timedelta(days=1)),
    ])

    for i, w in enumerate(workers):
        for d in range(6):
            day = _today() - timedelta(days=d)
            if d == 0 and i >= 4:
                continue
            base = datetime.combine(day, datetime.min.time())
            db.session.add(Attendance(
                factory_id=fac.id, worker_id=w.id, day=day,
                # Authored as local shift times, stored as UTC.
                check_in=_from_local(base + timedelta(hours=7, minutes=(i * 7) % 40)),
                check_out=_from_local(base + timedelta(hours=17, minutes=(i * 5) % 30)) if d else None,
                source="ussd"))

    po = PurchaseOrder(factory_id=fac.id, number="PO-0001", supplier_id=suppliers[0].id,
                       status="sent", expected_date=_today() - timedelta(days=2),
                       notes="Urgent restock for the gate order.")
    db.session.add(po)
    db.session.flush()
    db.session.add_all([
        POItem(po_id=po.id, material_id=material_map["MS-2MM"].id, quantity=30,
               unit_cost=2150),
        POItem(po_id=po.id, material_id=material_map["AL-40"].id, quantity=24,
               unit_cost=3400),
    ])

    db.session.commit()

    for phone, text, outcome in [
        ("+254712000001", "4*3*1*3", "mach_saved"),
        ("+254712000002", "3*1*1*4", "stock_saved"),
        ("+254712000003", "5*2*2", "safety_saved"),
        ("+254712000004", "6*1", "clock_in"),
        ("+254712000005", "1", "tasks"),
    ]:
        w = Worker.query.filter_by(phone=phone).first()
        db.session.add(UssdSession(
            factory_id=fac.id, session_id="seed-" + secrets.token_hex(5),
            phone_number=phone, service_code=fac.ussd_code, network_code="63902",
            worker_id=w.id if w else None, last_input=text,
            hops=len(text.split("*")) + 1, status="completed", outcome=outcome,
            started_at=_now() - timedelta(hours=secrets.randbelow(40) + 1),
            ended_at=_now() - timedelta(hours=1)))

    for to, msg, cat in [
        ("+254711000100", "Mzalendo: Aluminium sheet 1.2mm is out of stock. "
                          "Current: 0 sheet. Minimum: 8 sheet.", "stock_alert"),
        ("+254711000100", "Mzalendo: Arc welder C (WLD-C) reported not starting by "
                          "Joseph Kamau. Severity critical. Ticket MNT-0001.", "maintenance"),
        ("+254722334455", "Mzalendo: Order ORD-0005 is complete. Thank you for your "
                          "business.", "order"),
        ("+254733445566", "Mzalendo: Order ORD-0001 has entered production.", "order"),
        ("+254712000001", "Mzalendo: You are assigned to run RUN-0001 (Metal cabinet "
                          "4 door), 12 units, starting today.", "task"),
    ]:
        db.session.add(SmsLog(factory_id=fac.id, direction="out", to_number=to,
                              from_number="MZALENDO", message=msg, category=cat,
                              status="simulated", status_code=101,
                              created_at=_now() - timedelta(hours=secrets.randbelow(30) + 1)))
    db.session.commit()

    pulse = compute_pulse(fac.id)
    for f in pulse["findings"][:6]:
        raise_alert(fac.id, f["area"], f["severity"], f["title"], f["body"],
                    f["action"], dedupe_hours=0)
    for i in range(12):
        drift = (i % 5) - 2
        db.session.add(PulseSnapshot(
            factory_id=fac.id, taken_at=_now() - timedelta(hours=(12 - i) * 6),
            production=max(0, min(100, pulse["scores"]["production"] + drift * 3)),
            inventory=max(0, min(100, pulse["scores"]["inventory"] + drift * 4)),
            orders=max(0, min(100, pulse["scores"]["orders"] + drift * 2)),
            maintenance=max(0, min(100, pulse["scores"]["maintenance"] + drift * 5)),
            suppliers=max(0, min(100, pulse["scores"]["suppliers"] + drift)),
            overall=max(0, min(100, pulse["overall"] + drift * 3))))
    db.session.commit()
    log.info("demo plant seeded: %s", fac.name)


DEMO_SLUG = "kamukunji-metalworks"


def purge_factory(fac, protect_emails=()):
    """Delete a plant and every record inside it.

    Shared by the demo teardown and the delete button, so the two can never
    drift apart — a plant removed by hand leaves exactly as little behind as
    one removed by flipping SEED_DEMO_DATA.
    """
    protect = {e.strip().lower() for e in protect_emails if e}

    for user in User.query.filter_by(factory_id=fac.id).all():
        if user.role == "super_admin" or (user.email or "").lower() in protect:
            user.factory_id = None
        else:
            db.session.delete(user)
    db.session.flush()

    # Line items hang off a product, order, run, PO or inspection rather than
    # the plant, so they have to be addressed through their parent or they are
    # left orphaned.
    for child, parent_fk, parent in (
            (BomItem, BomItem.product_id, Product),
            (OrderItem, OrderItem.order_id, Order),
            (RunStage, RunStage.run_id, ProductionRun),
            (POItem, POItem.po_id, PurchaseOrder),
            (QcCheck, QcCheck.inspection_id, QcInspection)):
        parent_ids = [row.id for row in parent.query.filter_by(factory_id=fac.id).all()]
        if parent_ids:
            child.query.filter(parent_fk.in_(parent_ids)).delete(synchronize_session=False)
    db.session.flush()

    for model in reversed(MIGRATION_ORDER):
        if model is Factory or model is User:
            continue
        if hasattr(model, "factory_id"):
            model.query.filter_by(factory_id=fac.id).delete(synchronize_session=False)

    db.session.delete(fac)
    db.session.commit()


def remove_demo_plant():
    """Delete the demonstration plant when SEED_DEMO_DATA is off.

    Flipping the flag to 0 is therefore enough to hand a populated instance
    over as a clean one. Only the plant carrying DEMO_SLUG is touched; real
    plants and the configured administrator are never affected.
    """
    fac = Factory.query.filter_by(slug=DEMO_SLUG).first()
    if not fac:
        return False
    purge_factory(fac, protect_emails=(Config.SEED_ADMIN_EMAIL,))
    log.info("demonstration plant removed (SEED_DEMO_DATA is off)")
    return True


def seed_super_admin():
    """Guarantee a super administrator matching the configured variables.

    Runs on every boot, not just the first. An installation must never be
    lockable-out of itself: if the account was disabled, locked by failed
    sign-ins, or demoted, this puts it back. The password is left alone once
    set, so rotating it in the interface is not undone on the next deploy.
    """
    username = (Config.SEED_ADMIN_USERNAME or "admin").strip().lower()
    email = (Config.SEED_ADMIN_EMAIL or "admin@example.com").strip().lower()

    admin = (User.query.filter_by(email=email).first()
             or User.query.filter_by(username=username).first()
             or User.query.filter_by(role="super_admin").first())

    if admin is None:
        fac = Factory.query.filter_by(slug=DEMO_SLUG).first() or \
            Factory.query.order_by(Factory.id).first()
        admin = User(
            factory_id=fac.id if fac else None,
            username=username, email=email,
            full_name=Config.SEED_ADMIN_NAME,
            role="super_admin", is_active_flag=True,
            must_change_password=True)       # forced rotation at first sign in
        admin.set_password(Config.SEED_ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()
        log.info("super administrator created: %s <%s>", admin.username, admin.email)
        return admin

    changed = []
    if admin.username != username:
        admin.username, _ = username, changed.append("username")
    if admin.email != email:
        admin.email, _ = email, changed.append("email")
    if admin.role != "super_admin":
        admin.role, _ = "super_admin", changed.append("role")
    if not admin.is_active_flag:
        admin.is_active_flag, _ = True, changed.append("reactivated")
    if getattr(admin, "locked_until", None):
        admin.locked_until, _ = None, changed.append("unlocked")
    if getattr(admin, "failed_logins", 0):
        admin.failed_logins = 0
    if admin.factory_id and not Factory.query.get(admin.factory_id):
        admin.factory_id, _ = None, changed.append("detached from a deleted plant")

    if changed:
        db.session.commit()
        log.info("super administrator reconciled (%s): %s", ", ".join(changed), admin.username)
    return admin


def bootstrap(force_demo=None):
    """Create the schema, the bootstrap account, and optionally a demo plant."""
    db.create_all()
    demo = Config.SEED_DEMO_DATA if force_demo is None else force_demo
    if demo:
        seed_demo_plant()
    else:
        remove_demo_plant()
    # Always last, so it can pick up a plant that was just created and detach
    # itself from one that was just removed.
    seed_super_admin()

    # A quiet nudge rather than a hard failure: FORCE_HTTPS is off by default so
    # local development works, but leaving it off in front of real users means
    # no HSTS and a session cookie that travels unencrypted.
    if Config.AT_LIVE:
        missing = [k for k, v in (("AT_USERNAME", Config.AT_USERNAME),
                                  ("AT_API_KEY", Config.AT_API_KEY)) if not v]
        if missing:
            log.error("AT_LIVE=1 but %s not set — every send will be recorded "
                      "as failed. Set them or unset AT_LIVE.", " and ".join(missing))
        elif Config.AT_USERNAME == "sandbox":
            log.warning("AT_LIVE=1 with the sandbox username: messages reach the "
                        "Africa's Talking simulator, not a handset.")
        if not Config.AT_WEBHOOK_TOKEN:
            log.warning("AT_LIVE=1 with no AT_WEBHOOK_TOKEN — your callbacks accept "
                        "any caller. Anyone who finds the URL can open USSD sessions.")
    if not Config.FORCE_HTTPS:
        log.warning("FORCE_HTTPS is off — fine locally, but set FORCE_HTTPS=1 "
                    "in any deployment so HSTS and Secure cookies are enabled.")
    if not os.environ.get("SECRET_KEY"):
        log.warning("SECRET_KEY is unset, so a random one was generated for this "
                    "process. Sessions will be dropped on every restart. Set one "
                    "with: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"")


# Foreign keys point backwards through this list, so copying in order never
# violates a constraint on the destination.
MIGRATION_ORDER = [
    Factory, User, Worker, Supplier, Material, StockMovement, Product, BomItem,
    Customer, Order, OrderItem, Machine, MaintenanceTicket, ProductionRun,
    RunStage, PurchaseOrder, POItem, QcInspection, QcCheck, SafetyIncident,
    Attendance, SmsLog, UssdSession, Alert, PulseSnapshot, AuditLog,
]


# =============================================================================
#  SECTION 31 — CLI
# =============================================================================

@app.cli.command("init-db")
def cli_init_db():
    """Create tables and the super administrator account."""
    bootstrap()
    print("Database ready.")


@app.cli.command("reset-db")
def cli_reset_db():
    """Drop everything and rebuild. Development only."""
    db.drop_all()
    bootstrap()
    print("Database rebuilt.")


@app.cli.command("create-admin")
def cli_create_admin():
    """Force-create or reset the seeded super administrator."""
    user = User.query.filter(
        func.lower(User.username) == Config.SEED_ADMIN_USERNAME.lower()).first()
    if user:
        user.set_password(Config.SEED_ADMIN_PASSWORD)
        user.must_change_password = True
        user.role = "super_admin"
        user.is_active_flag = True
        db.session.commit()
        print(f"Reset {user.username}. A password change is required at next sign in.")
    else:
        seed_super_admin()
        print("Super administrator created.")


def _safe_uri(uri: str) -> str:
    """Database URI with the password masked, for printing."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", uri or "")


@app.cli.command("db-check")
def cli_db_check():
    """Backend, reachability and row counts for the configured database."""
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    shown = _safe_uri(uri)
    print(f"  uri      {shown}")
    print(f"  backend  {uri.split(':', 1)[0]}")
    with app.app_context():
        try:
            db.session.execute(db.text("SELECT 1"))
            print("  reach    ok")
        except Exception as exc:
            print(f"  reach    FAILED — {exc}")
            return
        total = 0
        for model in MIGRATION_ORDER:
            n = model.query.count()
            total += n
            if n:
                print(f"    {model.__tablename__:22s} {n:>6}")
        print(f"  rows     {total}")


@app.cli.command("db-copy")
@click.option("--reset", is_flag=True, help="Empty the destination first.")
def cli_db_copy(reset):
    """Copy every row from SQLite into the database in DATABASE_URL.

    Run with DATABASE_URL pointing at the *destination*. The source is the
    local SQLite file. Ids are preserved so foreign keys stay intact, which
    means Postgres sequences must be realigned afterwards — otherwise the next
    insert collides with a migrated id. That step is done here.
    """
    dest_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if dest_uri.startswith("sqlite"):
        print("  DATABASE_URL still points at SQLite. Nothing to migrate to.")
        return
    src_uri = "sqlite:///" + os.path.join(DATA_DIR, "mzalendo.db")
    print(f"  from   {src_uri}")
    print(f"  to     {_safe_uri(dest_uri)}")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    src = sessionmaker(bind=create_engine(src_uri))()

    with app.app_context():
        db.create_all()
        if reset:
            for model in reversed(MIGRATION_ORDER):
                db.session.execute(db.text(f"DELETE FROM {model.__tablename__}"))
            db.session.commit()
            print("  destination emptied")
        occupied = [m.__tablename__ for m in MIGRATION_ORDER if m.query.count()]
        if occupied:
            print(f"  destination is not empty: {', '.join(occupied)}")
            print("  re-run with --reset to overwrite.")
            return

        moved = 0
        for model in MIGRATION_ORDER:          # parents before children
            rows = src.query(model).all()
            for row in rows:
                data = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                db.session.execute(model.__table__.insert().values(**data))
            if rows:
                print(f"    {model.__tablename__:22s} {len(rows):>6}")
            moved += len(rows)
        db.session.commit()

        # Realign sequences. Inserting explicit ids leaves them behind, and the
        # very next record created through the UI would collide.
        if dest_uri.startswith("postgres"):
            for model in MIGRATION_ORDER:
                t = model.__tablename__
                db.session.execute(db.text(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1), true)"))
            db.session.commit()
            print("  sequences realigned")
        print(f"  {moved} rows copied")


@app.cli.command("at-check")
def cli_at_check():
    """Print the messaging configuration. Sends nothing, costs nothing."""
    with app.app_context():
        fac = Factory.query.filter_by(is_active=True).first()
        at = AfricasTalking(fac)
        mode = ("LIVE (strict)" if Config.AT_LIVE else
                "live" if at.live else "SIMULATION — messages are logged, not sent")
        sandbox = "sandbox" in at.endpoint
        print(f"  mode         {mode}")
        print(f"  endpoint     {at.endpoint}")
        print(f"  username     {at.username or '(unset)'}")
        print(f"  api key      {'set (' + at.api_key[:6] + '…)' if at.api_key else '(unset)'}")
        print(f"  sender id    {at.sender_id or '(none — default sender)'}")
        print(f"  ussd code    {Config.AT_USSD_CODE}")
        print(f"  webhook tok  {'set' if Config.AT_WEBHOOK_TOKEN else '(unset — callbacks are OPEN)'}")
        if sandbox:
            print("\n  NOTE: this is the sandbox endpoint. USSD answers only in the")
            print("        Africa's Talking web simulator, never on a real handset,")
            print("        and SMS reaches the simulator inbox rather than a phone.")
        if Config.AT_LIVE and not at.api_key:
            print("\n  ERROR: AT_LIVE=1 with no API key. Every send will be recorded failed.")


@app.cli.command("sms-test")
@click.argument("phone")
def cli_sms_test(phone):
    """Send one real SMS and print exactly what the gateway said."""
    with app.app_context():
        fac = Factory.query.filter_by(is_active=True).first()
        at = AfricasTalking(fac)
        res = at.send(phone, "Mzalendo test message. If you can read this, the "
                             "gateway is wired up correctly.", category="test")
        print(f"  result   {res}")
        row = SmsLog.query.order_by(SmsLog.id.desc()).first()
        if row:
            print(f"  logged   status={row.status} code={row.status_code} "
                  f"id={row.provider_id or '-'} cost={row.cost or '-'}")
            if row.error:
                print(f"  error    {row.error}")
        print("\n  'Success' means the gateway accepted it, not that it arrived.")
        print("  The delivery-report callback is what confirms delivery.")


@app.cli.command("pulse")
def cli_pulse():
    """Print the current Manufacturing Pulse for every plant."""
    for fac in Factory.query.all():
        p = compute_pulse(fac.id)
        print(f"\n{fac.name}  —  overall {p['overall']}%")
        for key, val in p["scores"].items():
            print(f"   {key:<12} {val:>3}%")
        for f in p["findings"][:5]:
            print(f"   ! [{f['severity']}] {f['title']}")


# =============================================================================
#  SECTION 32 — ENTRYPOINT
# =============================================================================

def _bootstrap_once():
    """Create and seed the schema exactly once, whatever starts the process.

    Every Gunicorn worker imports this module, so without serialisation they
    all reach create_all() and the seed at the same moment. One wins; the
    others fail on a half-created table and exit with code 3, the master gives
    up with "Worker failed to boot", and the platform health check reports the
    service as unavailable — with nothing in the logs that names the cause.

    An exclusive file lock makes the workers take turns. The second one through
    finds the tables already there and the seed functions no-ops.
    """
    try:
        import fcntl
    except ImportError:
        # No fcntl on Windows, where this only ever runs as a single process.
        with app.app_context():
            bootstrap()
        return

    # Poll for the lock rather than blocking on it. A worker that blocks
    # forever on a lock held by a wedged sibling never reaches the health
    # check, and the platform reports "service unavailable" with nothing in
    # the logs to explain it. After the timeout, proceed anyway: create_all()
    # and the seed are both idempotent, so the worst case is the race we were
    # avoiding, which is better than a container that never starts.
    lock_path = os.path.join(DATA_DIR, ".bootstrap.lock")
    deadline = time.monotonic() + 30
    with open(lock_path, "w") as handle:
        acquired = False
        while time.monotonic() < deadline:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(0.25)
        if not acquired:
            log.warning("bootstrap lock busy for 30s; continuing without it")
        try:
            with app.app_context():
                bootstrap()
        finally:
            if acquired:
                fcntl.flock(handle, fcntl.LOCK_UN)


# Commands whose whole purpose is to inspect or move a database must not try
# to connect to it first. Without this, `flask db-check` against an unreachable
# server dies during import with a SQLAlchemy traceback instead of reporting
# the very thing it exists to report.
_DB_ONLY_COMMANDS = {"db-check", "db-copy"}

if not (_DB_ONLY_COMMANDS & set(sys.argv)):
    try:
        _bootstrap_once()
    except OperationalError as exc:
        # A readable four-line failure beats two hundred frames, especially in
        # a container log where this is all the operator will see.
        log.error("cannot open the database")
        log.error("  uri        %s", _safe_uri(app.config["SQLALCHEMY_DATABASE_URI"]))
        log.error("  data dir   %s (exists: %s)", DATA_DIR, os.path.isdir(DATA_DIR))
        log.error("  override   DATABASE_URL is %s",
                  "set" if os.environ.get("DATABASE_URL") else "not set")
        log.error("  detail     %s", str(exc.orig or exc)[:200])
        raise SystemExit(1)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = _env_bool("DEBUG", False)
    log.info("%s v%s starting on port %s", APP_NAME, APP_VERSION, port)
    log.info("Open http://127.0.0.1:%s", port)
    if port == 5000:
        log.info("If port 5000 is taken on macOS it is usually AirPlay Receiver; "
                 "run with PORT=5001 python3 app.py instead.")
    app.run(host=host, port=port, debug=debug)
