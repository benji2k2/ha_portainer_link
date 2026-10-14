"""Optionaler Installationsweg ueber Watchtower, und dass ohne ihn alles bleibt."""
import sys, pathlib, asyncio, types
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker, HomeAssistantError

const, _e, _pa, _rn, wt, update, co = load(
    "const", "entity", "portainer_api", "release_notes", "watchtower", "update", "coordinator")
c = Checker("watchtower")

c.section("name als literales muster")
c("einfacher name", wt.container_pattern("traefik"), "traefik")
c("punkt maskiert", wt.container_pattern("app.v2"), "app\\.v2")
c("fuehrender slash weg", wt.container_pattern("/node-exporter"), "node-exporter")

class Resp:
    def __init__(self, status, text=""): self.status, self._t = status, text
    async def text(self): return self._t
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Session:
    def __init__(self, status=202, raise_=None): self.calls, self.status, self.raise_ = [], status, raise_
    def post(self, url, params=None, headers=None, ssl=True):
        self.calls.append((url, params, headers))
        if self.raise_: raise self.raise_
        return Resp(self.status, "boom")

async def expect_error(coro, needle):
    try:
        await coro
        return f"kein fehler (erwartet: {needle})"
    except wt.WatchtowerError as err:
        return needle in str(err)

async def main():
    c.section("aufruf an watchtower")
    s = Session(); w = wt.WatchtowerClient(s, "https://wt.example:8443/ ", "tok")
    await w.update_container("traefik")
    url, params, headers = s.calls[0]
    c("url ohne doppelten slash", url, "https://wt.example:8443/v1/update")
    c("gezielt + asynchron", params, {"container": "traefik", "async": "true"})
    c("bearer-token", headers, {"Authorization": "Bearer tok"})
    await wt.WatchtowerClient(Session(200), "https://wt", "t").update_container("x")
    c.true("200 ist auch ok", True)
    c("401 -> token", await expect_error(wt.WatchtowerClient(Session(401), "https://wt", "t").update_container("x"), "token"), True)
    c("404 -> endpunkt fehlt", await expect_error(wt.WatchtowerClient(Session(404), "https://wt", "t").update_container("x"), "update endpoint"), True)
    c("429 -> laeuft schon", await expect_error(wt.WatchtowerClient(Session(429), "https://wt", "t").update_container("x"), "already"), True)
    c("500 -> status + text", await expect_error(wt.WatchtowerClient(Session(500), "https://wt", "t").update_container("x"), "HTTP 500: boom"), True)
    c("nicht erreichbar", await expect_error(
        wt.WatchtowerClient(Session(raise_=OSError("refused")), "https://wt", "t").update_container("x"), "not reachable"), True)

    c.section("coordinator: watchtower nur mit url UND token")
    def coord(**opts):
        k = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
        co.PortainerDataUpdateCoordinator.__init__(
            k, None, types.SimpleNamespace(session="S"), 3, opts)
        return k
    c("ohne optionen kein watchtower", coord().watchtower, None)
    c("nur url reicht nicht", coord(watchtower_url="https://wt").watchtower, None)
    k = coord(watchtower_url=" https://wt/ ", watchtower_token="t")
    c("mit beidem", (k.watchtower.url, k.watchtower.token, k.watchtower.session), ("https://wt", "t", "S"))
    c("release notes standardmaessig an", k.release_notes is not None, True)
    c("abschaltbar", coord(enable_release_notes=False).release_notes, None)
    c("zuordnungen geparst", coord(release_note_sources="redis=redis/redis").release_note_sources, {"redis": "redis/redis"})

    c.section("installieren ueber watchtower")
    update.WATCHTOWER_POLL = 0
    class Api:
        def __init__(self, polls):
            self.polls, self.recreated = list(polls), []
        async def inspect_container(self, e, cid):
            return {"Image": "sha256:alt", "Config": {"Image": "traefik:latest"}, "HostConfig": {}}
        async def get_containers(self, e):
            item = self.polls.pop(0) if self.polls else [{"Names": ["/traefik"], "ImageID": "sha256:alt"}]
            if isinstance(item, Exception): raise item
            return item
        async def recreate_container(self, *a, **k): self.recreated.append(a)
    class W:
        def __init__(self, error=None): self.names, self.error = [], error
        async def update_container(self, name):
            self.names.append(name)
            if self.error: raise self.error
    def entity(api, watchtower):
        e = update.ContainerUpdateEntity.__new__(update.ContainerUpdateEntity)
        e.refreshed, e.states = 0, []
        async def refresh(): e.refreshed += 1
        e.coordinator = types.SimpleNamespace(api=api, watchtower=watchtower, async_request_refresh=refresh)
        e.endpoint_id = 3
        e._installing = False
        e.async_write_ha_state = lambda: e.states.append(e.in_progress)
        type(e).current_container_id = property(lambda s: "cid")
        type(e).container_name = property(lambda s: "traefik")
        return e

    neu = [{"Names": ["/traefik"], "ImageID": "sha256:neu"}]
    api = Api([OSError("portainer kurz weg"), [], [{"Names": ["/traefik"], "ImageID": "sha256:alt"}], neu])
    w = W(); e = entity(api, w)
    await e.async_install(None, False)
    c("watchtower mit dem namen aufgerufen", w.names, ["traefik"])
    c("portainer-recreate NICHT benutzt", api.recreated, [])
    c("fortschritt an, dann aus", e.states, [True, False])
    c("danach aktualisiert", e.refreshed, 1)

    update.WATCHTOWER_TIMEOUT = 0
    e = entity(Api([]), W())
    try:
        await e.async_install(None, False); c("timeout gemeldet", False, True)
    except HomeAssistantError as err:
        c.true("timeout nennt container", "traefik still runs the old image" in str(err))
    c("fortschritt auch nach fehler aus", e.states[-1], False)

    e = entity(Api([]), W(wt.WatchtowerError("Watchtower rejected the token (HTTP 401)")))
    try:
        await e.async_install(None, False); c("fehler weitergereicht", False, True)
    except HomeAssistantError as err:
        c.true("grund im dialog", "rejected the token" in str(err))

    c.section("ohne watchtower: wie bisher ueber portainer")
    api = Api([]); e = entity(api, None)
    await e.async_install(None, False)
    c("recreate benutzt", api.recreated, [(3, "cid")])
    c("fortschritt an, dann aus", e.states, [True, False])

    c.section("entitaet zeigt den weg an")
    e = entity(Api([]), W())
    e.coordinator.image_data = {"cid": {"current_config_digest": "sha256:aa"}}
    c("watchtower", e.extra_state_attributes["installs_via"], "watchtower")
    e.coordinator.watchtower = None
    c("portainer", e.extra_state_attributes["installs_via"], "portainer")

asyncio.run(main())
sys.exit(c.done())
