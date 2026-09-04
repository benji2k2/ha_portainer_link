import sys, pathlib, asyncio, types, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, _entity, _api, ia, co = load(
    "const", "entity", "portainer_api", "image_api", "coordinator")

fails=[]
def check(l, got, want):
    ok = got==want
    print(f"{'PASS' if ok else 'FAIL'}  {l}\n       -> {got!r}")
    if not ok: print(f"       want={want!r}"); fails.append(l)

# ---------- registry-simulation, die requests zaehlt ----------
AMD = "sha256:child_amd64"
CFGD = "sha256:93c91251e746aaaa"
class Resp:
    def __init__(self, status, payload=None, digest=None, www=None):
        self.status, self._p, self.headers = status, payload, {}
        if digest: self.headers["Docker-Content-Digest"] = digest
        if www: self.headers["WWW-Authenticate"] = www
    async def json(self, content_type=None): return self._p
    async def text(self): return ""
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class Registry:
    """Zaehlt Manifest-, Blob- und Token-Anfragen getrennt."""
    def __init__(self, require_auth=True):
        self.manifests = self.blobs = self.tokens = 0
        self.require_auth = require_auth
    async def request(self, method, url, headers=None, **kw):
        authed = "Authorization" in (headers or {})
        if "/manifests/" in url:
            if self.require_auth and not authed:
                return Resp(401, www='Bearer realm="https://auth/token",service="reg",scope="repository:x:pull"')
            self.manifests += 1
            if url.endswith("/manifests/latest"):
                return Resp(200, {"manifests":[{"digest":AMD,"platform":{"os":"linux","architecture":"amd64"}}]},
                            digest="sha256:indexdigest")
            return Resp(200, {"config":{"digest":CFGD},"layers":[]}, digest=AMD)
        if "/blobs/" in url:
            if self.require_auth and not authed:
                return Resp(401, www='Bearer realm="https://auth/token",service="reg",scope="repository:x:pull"')
            self.blobs += 1
            return Resp(200, {"created":"2026-08-25T03:17:18.782399976Z"})
        return Resp(404)
    def get(self, url, params=None, **kw):
        # wird als async-kontextmanager benutzt, nicht als coroutine
        self.tokens += 1
        return Resp(200, {"token":"tok"})

def api_with(reg):
    a = ia.PortainerImageAPI("", None)
    a._request = reg.request
    a.session = types.SimpleNamespace(get=reg.get)
    return a

async def main():
    print("=== ein manifest-walk liefert alle drei werte ===")
    reg = Registry(); api = api_with(reg)
    st = await api.get_remote_image_state("alpine:latest", {"Os":"linux","Architecture":"amd64"}, want_created=True)
    check("index-digest", st["manifest_digest"], "sha256:indexdigest")
    check("image-id", st["config_digest"], CFGD)
    check("build-datum", st["created"], "2026-08-25T03:17:18.782399976Z")
    check("nur 2 manifest-anfragen (index + kind)", reg.manifests, 2)
    check("nur 1 blob-anfrage", reg.blobs, 1)
    check("nur EIN token geholt (danach wiederverwendet)", reg.tokens, 1)

    print("\n=== zweiter aufruf nutzt das token weiter ===")
    before = reg.tokens
    await api.get_remote_image_state("alpine:latest", {"Os":"linux","Architecture":"amd64"})
    check("kein weiteres token", reg.tokens, before)
    check("keine 401-runde mehr -> 2 weitere manifeste", reg.manifests, 4)

    print("\n=== vergleich: der alte weg (drei getrennte aufrufe) ===")
    reg2 = Registry(); api2 = api_with(reg2)
    await api2._get_remote_manifest_digests("alpine:latest")
    await api2.get_remote_config_digest("alpine:latest", {"Os":"linux","Architecture":"amd64"})
    await api2.get_remote_created("alpine:latest", {"Os":"linux","Architecture":"amd64"})
    print(f"       drei aufrufe -> {reg2.manifests} manifest-anfragen")
    check("konsolidiert braucht weniger", reg.manifests // 2 < reg2.manifests, True)

    print("\n=== intervall wird als minuten gerechnet ===")
    c = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    c.config = dict(const.DEFAULT_OPTIONS)
    check("default 360 -> 6 stunden",
          max(int(c.config["update_check_interval"]), 1) * 60, 21600)

    print("\n=== persistenz ===")
    saved = {}
    class Store:
        async def async_load(self): return saved or None
        async def async_save(self, d): saved.update(d)
    c._store = Store(); c._last_registry_check = 0.0
    c.image_data = {"c1": {"current_version": "1.2.3"}}
    c.update_availability = {"c1": True}
    await c._async_save_state()
    check("zeitstempel gespeichert", "last_registry_check" in saved, True)

    c2 = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    c2._store = Store(); c2._last_registry_check = 0.0
    c2.image_data = {}; c2.update_availability = {}
    saved["last_registry_check"] = time.time() - 100
    await c2.async_load_persisted_state()
    check("zeitstempel wiederhergestellt", c2._last_registry_check > 0, True)
    check("sensordaten sofort wieder da", c2.image_data["c1"]["current_version"], "1.2.3")
    check("update-status wiederhergestellt", c2.update_availability, {"c1": True})

    print("\n=== zeitstempel aus der zukunft wird verworfen ===")
    c3 = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    c3._store = Store(); c3._last_registry_check = 0.0; c3.image_data={}; c3.update_availability={}
    saved["last_registry_check"] = time.time() + 99999
    await c3.async_load_persisted_state()
    check("nicht uebernommen", c3._last_registry_check, 0.0)

asyncio.run(main())
print("\n" + ("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
