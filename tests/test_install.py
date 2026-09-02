"""Update installieren heisst pullen UND neu erstellen."""
import sys, pathlib, asyncio, types
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker, HomeAssistantError

pa, update = load("const", "entity", "portainer_api", "update")[2:4]
c = Checker("update installieren")

class Api:
    def __init__(self, inspect, result=None):
        self._i, self.calls, self.result = inspect, [], result if result is not None else {"Id": "neu"}
    async def inspect_container(self, e, cid): return self._i
    async def recreate_container(self, e, cid, pull_image=True):
        self.calls.append((e, cid, pull_image))
        if isinstance(self.result, Exception): raise self.result
        return self.result

def entity(api):
    e = update.ContainerUpdateEntity.__new__(update.ContainerUpdateEntity)
    e.coordinator = types.SimpleNamespace(api=api, async_request_refresh=lambda: asyncio.sleep(0))
    e.endpoint_id = 2
    type(e).current_container_id = property(lambda s: "cid")
    type(e).container_name = property(lambda s: "plex")
    return e

async def main():
    c.section("normalfall: recreate mit pull")
    api = Api({"Config": {"Image": "plex:latest"}, "HostConfig": {}})
    await entity(api).async_install(None, False)
    c("recreate mit pull_image=True", api.calls, [(2, "cid", True)])

    c.section("--rm container darf nicht recreated werden")
    api = Api({"Config": {"Image": "tmp:latest"}, "HostConfig": {"AutoRemove": True}})
    try:
        await entity(api).async_install(None, False); c("fehler geworfen", False, True)
    except HomeAssistantError as err:
        c.true("erklaert warum", "cannot be recreated" in str(err))
    c("kein recreate versucht", api.calls, [])

    c.section("auf digest gepinnt: nichts zu pullen")
    api = Api({"Config": {"Image": "sha256:abc123"}, "HostConfig": {}})
    try:
        await entity(api).async_install(None, False); c("fehler geworfen", False, True)
    except HomeAssistantError as err:
        c.true("erklaert warum", "digest" in str(err))
    c("kein recreate versucht", api.calls, [])

    c.section("portainer lehnt ab -> grund im dialog")
    api = Api({"Config": {"Image": "plex:latest"}, "HostConfig": {}},
              result=pa.PortainerError("HTTP 409: container is part of a stack"))
    try:
        await entity(api).async_install(None, False); c("fehler geworfen", False, True)
    except HomeAssistantError as err:
        c.true("nennt container", "plex" in str(err))
        c.true("nennt portainers grund", "part of a stack" in str(err))

    c.section("recreate-endpunkt")
    calls = {}
    class Resp:
        status = 200
        async def json(self): return {"Id": "neu"}
        async def text(self): return ""
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    a = pa.PortainerAPI.__new__(pa.PortainerAPI)
    a.base_url, a.headers = "http://p:9000", {}
    a.session = types.SimpleNamespace(post=lambda url, **kw: (calls.update(url=url, json=kw.get("json")), Resp())[1])
    await a.recreate_container(2, "abc", pull_image=True)
    c("url", calls["url"], "http://p:9000/api/docker/2/containers/abc/recreate")
    c("payload", calls["json"], {"PullImage": True})

asyncio.run(main())
sys.exit(c.done())
