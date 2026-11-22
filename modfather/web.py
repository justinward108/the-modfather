"""Dashboard web server: Discord login + theme CRUD API + static single-page app."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from . import catalog, config, themes

log = logging.getLogger("modfather.web")
STATIC = Path(__file__).parent / "static"
COOKIE = "modfather_session"
SESSION_DAYS = 14
DISCORD_API = "https://discord.com/api/v10"


# ------------------------------------------------------------------ signed cookie sessions
def _secret() -> bytes:
    if not config.SESSION_SECRET:
        config.SESSION_SECRET = secrets.token_hex(32)
        log.warning("SESSION_SECRET not set; using a random one (logins reset on restart).")
    return config.SESSION_SECRET.encode()


def sign(data: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def unsign(token: str) -> dict | None:
    try:
        body, mac = token.rsplit(".", 1)
    except (ValueError, AttributeError):
        return None
    good = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


def current_user(request: web.Request) -> dict | None:
    data = unsign(request.cookies.get(COOKIE, ""))
    return data if data and data.get("kind") == "user" else None


def set_session(resp: web.StreamResponse, data: dict, days: float = SESSION_DAYS):
    data = {**data, "exp": time.time() + days * 86400}
    resp.set_cookie(COOKIE, sign(data), max_age=int(days * 86400), httponly=True,
                    samesite="Lax", secure=config.PUBLIC_URL.startswith("https://"), path="/")


# ------------------------------------------------------------------ app
def make_app(store, access) -> web.Application:
    """store: Store; access: object with manageable_guilds(uid) and can_manage(uid, gid)."""

    @web.middleware
    async def guard(request, handler):
        if request.path.startswith("/api/"):
            if request.method in ("POST", "PUT", "DELETE") and \
                    not request.content_type.startswith("application/json"):
                return web.json_response({"error": "JSON requests only"}, status=415)
            if request.path not in ("/api/catalog",) and not current_user(request):
                return web.json_response({"error": "Not logged in"}, status=401)
        try:
            return await handler(request)
        except themes.ThemeError as e:
            return web.json_response({"error": str(e)}, status=400)

    app = web.Application(middlewares=[guard], client_max_size=2 * 1024 * 1024)

    async def require_guild(request) -> int:
        user = current_user(request)
        try:
            gid = int(request.match_info["gid"])
        except ValueError:
            raise web.HTTPNotFound()
        if not await access.can_manage(int(user["id"]), gid):
            raise web.HTTPForbidden(text=json.dumps({"error": "You can't manage this server"}),
                                    content_type="application/json")
        return gid

    async def read_json(request) -> dict:
        try:
            return await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise themes.ThemeError("Request body isn't valid JSON")

    # ---- pages & login
    async def index(request):
        return web.FileResponse(STATIC / "index.html")

    async def login(request):
        if not config.CLIENT_ID:
            return web.Response(text="DISCORD_CLIENT_ID is not configured.", status=500)
        state = secrets.token_urlsafe(16)
        params = {"client_id": config.CLIENT_ID, "redirect_uri": f"{config.PUBLIC_URL}/callback",
                  "response_type": "code", "scope": "identify", "state": state, "prompt": "none"}
        resp = web.HTTPFound(f"https://discord.com/oauth2/authorize?{urlencode(params)}")
        resp.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="Lax", path="/")
        return resp

    async def callback(request):
        if request.query.get("state") != request.cookies.get("oauth_state") or "code" not in request.query:
            return web.Response(text="Login failed (state mismatch). Go back and try again.", status=400)
        form = {"client_id": config.CLIENT_ID, "client_secret": config.CLIENT_SECRET,
                "grant_type": "authorization_code", "code": request.query["code"],
                "redirect_uri": f"{config.PUBLIC_URL}/callback"}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{DISCORD_API}/oauth2/token", data=form) as r:
                if r.status != 200:
                    log.warning("token exchange failed: %s %s", r.status, await r.text())
                    return web.Response(text="Discord login failed. Check the client secret and "
                                             "redirect URL settings.", status=400)
                token = (await r.json())["access_token"]
            async with s.get(f"{DISCORD_API}/users/@me",
                             headers={"Authorization": f"Bearer {token}"}) as r:
                me = await r.json()
        avatar = (f"https://cdn.discordapp.com/avatars/{me['id']}/{me['avatar']}.png?size=64"
                  if me.get("avatar") else None)
        resp = web.HTTPFound("/")
        set_session(resp, {"kind": "user", "id": me["id"],
                           "name": me.get("global_name") or me["username"], "avatar": avatar})
        resp.del_cookie("oauth_state", path="/")
        return resp

    async def logout(request):
        resp = web.HTTPFound("/")
        resp.del_cookie(COOKIE, path="/")
        return resp

    # ---- API
    async def me(request):
        user = current_user(request)
        guilds = await access.manageable_guilds(int(user["id"]))
        return web.json_response({"user": {k: user[k] for k in ("id", "name", "avatar")},
                                  "guilds": guilds})

    async def get_catalog(request):
        return web.json_response({"roles": catalog.mu_roles(), "modifiers": catalog.modifiers(),
                                  "abilities": catalog.AUTO_ABILITIES,
                                  "source_url": "https://www.mafiauniverse.com/forums/database/mafia-roles/",
                                  "defaults": themes.new_theme("New theme")})

    async def list_themes(request):
        gid = await require_guild(request)
        out = []
        for t in await store.list_themes(gid):
            out.append({"id": t["id"], "name": t["name"], "slots": len(t["slots"])})
        return web.json_response(out)

    async def get_theme(request):
        gid = await require_guild(request)
        t = await store.get_theme(gid, request.match_info["tid"])
        if not t:
            raise web.HTTPNotFound()
        return web.json_response(t)

    async def create_theme(request):
        gid = await require_guild(request)
        body = await read_json(request)
        body["id"] = themes.new_theme()["id"]
        t = themes.validate(body)
        await store.save_theme(gid, t)
        return web.json_response(t, status=201)

    async def put_theme(request):
        gid = await require_guild(request)
        tid = request.match_info["tid"]
        if not await store.get_theme(gid, tid):
            raise web.HTTPNotFound()
        body = await read_json(request)
        body["id"] = tid
        t = themes.validate(body)
        await store.save_theme(gid, t)
        return web.json_response(t)

    async def delete_theme(request):
        gid = await require_guild(request)
        await store.delete_theme(gid, request.match_info["tid"])
        return web.json_response({"ok": True})

    async def preview(request):
        body = await read_json(request)
        try:
            t = themes.validate({**body, "id": "preview"})
        except themes.ThemeError as e:
            return web.json_response({"ok": False, "error": str(e), "supported_counts": []})
        return web.json_response({"ok": True, "error": None,
                                  "supported_counts": themes.supported_counts(t)})

    async def starter_pms(request):
        body = await read_json(request)
        t = themes.validate({**body, "id": "starter"})
        only = body.get("only")
        out = {}
        for i, slot in enumerate(t["slots"]):
            if only is None or i in only:
                out[str(i)] = themes.starter_pm(t, slot)
        return web.json_response(out)

    async def list_games(request):
        gid = await require_guild(request)
        out = []
        for g in await store.guild_games(gid):
            out.append({"id": g["id"], "theme": g["theme"]["name"], "phase": g["phase"],
                        "day": g["day"], "players": len(g["players"]) or len(g["signups"]),
                        "alive": sum(1 for p in g["players"].values() if p["alive"]),
                        "winners": g["winners"], "channel_id": str(g["channel_id"]),
                        "created": g["created"]})
        return web.json_response(out)

    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_get("/callback", callback)
    app.router.add_get("/logout", logout)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/catalog", get_catalog)
    app.router.add_post("/api/preview", preview)
    app.router.add_post("/api/starter-pms", starter_pms)
    app.router.add_get("/api/guilds/{gid}/themes", list_themes)
    app.router.add_post("/api/guilds/{gid}/themes", create_theme)
    app.router.add_get("/api/guilds/{gid}/themes/{tid}", get_theme)
    app.router.add_put("/api/guilds/{gid}/themes/{tid}", put_theme)
    app.router.add_delete("/api/guilds/{gid}/themes/{tid}", delete_theme)
    app.router.add_get("/api/guilds/{gid}/games", list_games)
    app.router.add_static("/static/", STATIC)
    return app
