"""Versionen und Quellen aus den Image-Labels; Update erlischt nach der Installation;
Watchtower-Token im Optionsformular."""
import sys, pathlib, asyncio, types, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, _e, _pa, ia, co, cf = load(
    "const", "entity", "portainer_api", "image_api", "coordinator", "config_flow")
c = Checker("image-labels")

ALT, NEU = "sha256:alt0000000000", "sha256:neu0000000000"

class Api:
    def __init__(self, image_id):
        self.image_id, self.remote_calls = image_id, 0
    async def inspect_container(self, e, cid):
        return {"Image": self.image_id, "Config": {"Image": "traefik:latest"}}
    async def get_image_info(self, e, iid):
        version = "v3.7.14" if iid == ALT else "v3.7.15"
        return {"Created": "2026-10-06T10:00:00Z", "Config": {"Labels": {
            "org.opencontainers.image.version": version,
            "org.opencontainers.image.source": "https://github.com/traefik/traefik"}}}
    def extract_version_from_image(self, info): return "latest"
    async def get_current_digest(self, e, cid): return None
    async def get_remote_image_state(self, name, info, want_created=False):
        self.remote_calls += 1
        return {"manifest_digest": "sha256:idx", "config_digest": NEU, "created": "2026-10-13T10:00:00Z",
                "labels": {"org.opencontainers.image.version": "v3.7.15"}}
    async def get_available_version(self, e, name): return "latest"

def coordinator(api):
    k = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    k.config = dict(const.DEFAULT_OPTIONS)
    k.api, k.endpoint_id, k._store = api, 3, None
    k.containers = {"cid": {}}
    k.image_data, k.update_availability = {}, {}
    k._last_registry_check, k._registry_jitter = 0.0, 0.0
    return k

async def main():
    c.section("registry-sweep liest die labels")
    api = Api(ALT); k = coordinator(api)
    await k._refresh_image_data_if_due()
    d = k.image_data["cid"]
    c("laufende version", d["oci_version"], "v3.7.14")
    c("quelle", d["oci_source"], "https://github.com/traefik/traefik")
    c("neue version aus dem config-blob", d["available_oci_version"], "v3.7.15")
    c("update erkannt", k.update_availability, {"cid": True})

    c.section("nach der installation (zwischen zwei sweeps)")
    api.image_id = NEU
    await k._refresh_image_data_if_due()
    c("kein registry-abruf dazwischen", api.remote_calls, 1)
    c("update erlischt sofort", k.update_availability, {"cid": False})
    c("laufende version jetzt die neue", k.image_data["cid"]["oci_version"], "v3.7.15")

    c.section("ohne installation bleibt das update stehen")
    api2 = Api(ALT); k2 = coordinator(api2)
    await k2._refresh_image_data_if_due()
    await k2._refresh_image_data_if_due()
    c("weiter verfuegbar", k2.update_availability, {"cid": True})

    c.section("config-blob liefert die labels mit")
    class Reg:
        async def request(self, method, url, headers=None, **kw):
            class R:
                status, headers = 200, {"Docker-Content-Digest": "sha256:m"}
                def __init__(s, p): s._p = p
                async def json(s, content_type=None): return s._p
                async def __aenter__(s): return s
                async def __aexit__(s, *a): return False
            if "/blobs/" in url:
                return R({"created": "2026-10-13T10:00:00Z",
                          "config": {"Labels": {"org.opencontainers.image.version": "v3.7.15"}}})
            return R({"config": {"digest": "sha256:cfg"}, "layers": []})
    a = ia.PortainerImageAPI("", None); a._request = Reg().request
    st = await a.get_remote_image_state("traefik:latest", want_created=True)
    c("labels im zustand", st["labels"], {"org.opencontainers.image.version": "v3.7.15"})

    c.section("watchtower-token im optionsformular")
    created = {}
    def flow(stored):
        f = cf.PortainerOptionsFlowHandler.__new__(cf.PortainerOptionsFlowHandler)
        f._config_entry = types.SimpleNamespace(options=stored)
        f.async_create_entry = lambda title, data: created.update(data) or {"type": "create_entry"}
        f.async_show_form = lambda **kw: {"type": "form", **kw}
        return f
    form = await flow({"watchtower_token": "geheim"}).async_step_init()
    schema = getattr(form["data_schema"], "schema", form["data_schema"])
    defaults = {getattr(k, "schema", k): getattr(k, "default", None) for k in schema}
    c.true("token-feld vorhanden", "watchtower_token" in defaults)
    shown = defaults.get("watchtower_token")
    unset = callable(shown) or shown is None or type(shown).__name__ == "Undefined"   # je nach voluptuous-Version
    c("token wird nicht vorbelegt", None if unset else shown, None)
    await flow({"watchtower_token": "geheim"}).async_step_init({"watchtower_url": "https://wt", "watchtower_token": ""})
    c("leeres feld behaelt den token", created["watchtower_token"], "geheim")
    await flow({"watchtower_token": "geheim"}).async_step_init({"watchtower_url": "https://wt", "watchtower_token": "neu"})
    c("neuer token ersetzt", created["watchtower_token"], "neu")

asyncio.run(main())
sys.exit(c.done())
