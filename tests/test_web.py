import pytest
from aiohttp.test_utils import TestClient, TestServer

from modfather import themes, web
from modfather.store import Store


class FakeAccess:
    async def manageable_guilds(self, uid):
        return [{"id": "111", "name": "Test Server", "icon": None}] if uid == 1 else []

    async def can_manage(self, uid, gid):
        return uid == 1 and gid == 111


@pytest.fixture
async def client(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    await store.open()
    app = web.make_app(store, FakeAccess())
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c, store
    await c.close()
    await store.close()


def login(c, uid):
    c.session.cookie_jar.update_cookies(
        {web.COOKIE: web.sign({"kind": "user", "id": str(uid), "name": "U", "avatar": None,
                               "exp": 9e12})})


SAMPLE = {"name": "Sopranos", "description": "Bada bing", "settings": {"reveal_on_death": "role"},
          "factions": themes.DEFAULT_FACTIONS,
          "custom_roles": [{"key": "custom_abc", "name": "Consigliere Plus", "ability": "role_investigate",
                            "description": "Learns roles"}],
          "slots": [{"character": "Tony", "role_key": "godfather", "faction": "Mafia"},
                    {"character": "Silvio", "role_key": "custom_abc", "faction": "Mafia"},
                    {"character": "Dr. Melfi", "role_key": "doctor", "faction": "Town"},
                    {"character": "Agent Harris", "role_key": "cop", "faction": "Town"},
                    {"character": "Artie", "role_key": "vanilla_townie", "faction": "Town"},
                    {"character": "Adriana", "role_key": "vanilla_townie", "faction": "Town", "min_players": 6}]}


async def test_requires_login(client):
    c, _ = client
    r = await c.get("/api/me")
    assert r.status == 401
    r = await c.get("/api/catalog")
    assert r.status == 200
    data = await r.json()
    assert len(data["roles"]) == 475


async def test_theme_crud_and_permissions(client):
    c, store = client
    login(c, 1)
    me = await (await c.get("/api/me")).json()
    assert me["guilds"][0]["id"] == "111"
    r = await c.post("/api/guilds/111/themes", json=SAMPLE)
    assert r.status == 201, await r.text()
    t = await r.json()
    assert t["slots"][1]["role_key"] == "custom_abc"
    lst = await (await c.get("/api/guilds/111/themes")).json()
    assert lst[0]["slots"] == 6
    t["name"] = "Sopranos 2"
    r = await c.put(f"/api/guilds/111/themes/{t['id']}", json=t)
    assert (await r.json())["name"] == "Sopranos 2"
    pv = await (await c.post("/api/preview", json=t)).json()
    assert pv["ok"] and pv["supported_counts"] == [5, 6]
    bad = dict(t, slots=t["slots"] + [{"character": "Tony", "role_key": "cop", "faction": "Town"}])
    r = await c.put(f"/api/guilds/111/themes/{t['id']}", json=bad)
    assert r.status == 400 and "unique" in (await r.json())["error"]
    # another user can't touch this server
    login(c, 2)
    r = await c.get("/api/guilds/111/themes")
    assert r.status == 403
    login(c, 1)
    r = await c.delete(f"/api/guilds/111/themes/{t['id']}", json={})
    assert r.status == 200
    assert await store.list_themes(111) == []


async def test_tampered_cookie_rejected(client):
    c, _ = client
    tok = web.sign({"kind": "user", "id": "1", "name": "U", "avatar": None, "exp": 9e12})
    bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")      # always change the signature
    c.session.cookie_jar.update_cookies({web.COOKIE: bad})
    assert (await c.get("/api/me")).status == 401


async def test_non_json_mutation_refused(client):
    c, _ = client
    login(c, 1)
    r = await c.post("/api/guilds/111/themes", data="name=x")
    assert r.status == 415


async def test_index_served(client):
    c, _ = client
    r = await c.get("/")
    assert r.status == 200 and "Theme Builder" in await r.text()


async def test_starter_pm_endpoint(client):
    c, _ = client
    login(c, 1)
    r = await c.post("/api/starter-pms", json={**SAMPLE, "only": [0, 2]})
    out = await r.json()
    assert set(out) == {"0", "2"} and "{character}" in out["2"]
    assert len((await (await c.get("/api/catalog")).json())["roles"]) == 475
