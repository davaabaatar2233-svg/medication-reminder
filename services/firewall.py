"""
================================================================================
 PART 2 of 3 · THE SERVER  (app.py + services/ + languages/)
 This file is the BOUNCER. It checks every visitor before any page runs.
 See TEAM_GUIDE.md for who explains which part.
================================================================================

Firewall: a small shield in front of every page.

It runs before any route and stops four kinds of bad traffic:

  1. Too many requests from one address  -> 429 "slow down"   (rate limit)
  2. Guessing PINs / codes too fast       -> stricter limit on sensitive pages
  3. Robots scanning for weak spots       -> blocked paths like /wp-admin, /.env
  4. Strange requests                      -> odd methods, huge bodies

An address that keeps breaking the rules is blocked for BAN_MINUTES.

Everything is kept in memory (one gunicorn worker), so it resets when the
server restarts. That is fine for a small site; Render itself already
gives HTTPS and protection against very large network attacks.
"""
import re
import threading
import time
from collections import defaultdict, deque

from flask import jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from services import translate

WINDOW = 60                      # seconds that each limit counts over

# (requests per WINDOW) for each kind of request
LIMIT_ALL = 120                  # any page, image, script
LIMIT_WRITE = 30                 # any POST (saving, confirming a dose)
LIMIT_SENSITIVE = 10             # PIN unlock, creating / opening profiles
LIMIT_SIMULATOR = 300            # the simulator's Play button asks up to 2x a second

SENSITIVE = re.compile(r"^/(create|open)$|^/c/[^/]+/(unlock|delete)$")

STRIKES_TO_BAN = 5               # rule breaks before an address is blocked
BAN_MINUTES = 10

MAX_BODY = 64 * 1024             # 64 KB: our forms are tiny
ALLOWED_METHODS = {"GET", "POST", "HEAD"}

# Paths that only attack robots ask for. Sanakh has none of them.
SCANNER = re.compile(
    r"(\.php|\.asp|\.env|\.git|\.sql|\.bak|wp-|wordpress|phpmyadmin|/admin|/cgi-bin|"
    r"\.\./|%2e%2e|/etc/passwd|xmlrpc)", re.IGNORECASE)

# Static files are cheap and many load per page, so they don't count.
NOT_COUNTED = ("/static/", "/sw.js", "/favicon.ico")


class Firewall:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits = defaultdict(deque)      # (ip, kind) -> times of recent requests
        self.strikes = defaultdict(int)     # ip -> rule breaks
        self.banned = {}                    # ip -> time the block ends
        self.last_cleanup = time.time()

    # ---------------------------------------------------------------- checks
    def too_many(self, ip, kind, limit, now):
        """Record one request; True if this address is over the limit."""
        q = self.hits[(ip, kind)]
        while q and q[0] <= now - WINDOW:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False

    def strike(self, ip, now):
        self.strikes[ip] += 1
        if self.strikes[ip] >= STRIKES_TO_BAN:
            self.banned[ip] = now + BAN_MINUTES * 60
            self.strikes[ip] = 0

    def is_banned(self, ip, now):
        until = self.banned.get(ip)
        if until and until > now:
            return int(until - now)
        self.banned.pop(ip, None)
        return 0

    def cleanup(self, now):
        """Forget old addresses so memory doesn't grow forever."""
        if now - self.last_cleanup < 300:
            return
        self.last_cleanup = now
        for key in [k for k, q in self.hits.items() if not q or q[-1] <= now - WINDOW]:
            del self.hits[key]
        for ip in [ip for ip, until in self.banned.items() if until <= now]:
            del self.banned[ip]

    def decide(self, ip, method, path, body_size):
        """Return None (let it through) or (status, retry_after_seconds)."""
        now = time.time()
        with self.lock:
            self.cleanup(now)

            wait = self.is_banned(ip, now)
            if wait:
                return 403, wait

            if method not in ALLOWED_METHODS:
                self.strike(ip, now)
                return 405, 0
            if body_size and body_size > MAX_BODY:
                self.strike(ip, now)
                return 413, 0
            if SCANNER.search(path):
                self.strike(ip, now)
                return 404, 0

            path = path.split("?", 1)[0]         # the limits look at the page, not ?options
            if path.startswith(NOT_COUNTED):
                return None
            if path == "/api/simulate":          # pure maths, saves nothing: its own bigger limit
                if self.too_many(ip, "sim", LIMIT_SIMULATOR, now):
                    self.strike(ip, now)
                    return 429, WINDOW
                return None
            if self.too_many(ip, "all", LIMIT_ALL, now):
                self.strike(ip, now)
                return 429, WINDOW
            if method == "POST":
                if self.too_many(ip, "write", LIMIT_WRITE, now):
                    self.strike(ip, now)
                    return 429, WINDOW
                if SENSITIVE.search(path) and self.too_many(ip, "sensitive", LIMIT_SENSITIVE, now):
                    self.strike(ip, now)
                    return 429, WINDOW
            return None


MESSAGE_KEY = {403: "fw.blocked", 405: "fw.bad", 413: "fw.bad", 404: "fw.bad", 429: "fw.slow"}


def protect(app, behind_proxy=False):
    """Switch the firewall on for this Flask app."""
    if behind_proxy:
        # Render sits in front of us; trust exactly one proxy hop for the visitor's address.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config.setdefault("MAX_CONTENT_LENGTH", MAX_BODY)
    wall = Firewall()
    app.extensions["firewall"] = wall

    @app.before_request
    def firewall_check():
        if not app.config.get("FIREWALL", True):
            return None
        verdict = wall.decide(request.remote_addr or "?", request.method,
                              request.full_path, request.content_length)
        if verdict is None:
            return None
        status, retry = verdict
        app.logger.warning("firewall: %s %s %s -> %s", request.remote_addr, request.method,
                           request.path, status)
        lang = translate.normalize(request.cookies.get("lang")) or translate.DEFAULT
        text = translate.t(lang, MESSAGE_KEY[status])
        if request.path.startswith("/api/"):
            resp = jsonify({"error": text})
        else:
            resp = app.response_class(
                f'<!doctype html><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width,initial-scale=1"><title>Sanakh</title>'
                f'<p style="font:20px system-ui;max-width:30em;margin:3em auto;padding:0 1em">'
                f'{text}</p>', mimetype="text/html")
        resp.status_code = status
        if retry:
            resp.headers["Retry-After"] = str(retry)
        return resp

    return wall
