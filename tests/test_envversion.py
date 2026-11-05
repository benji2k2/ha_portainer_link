"""Version aus der Umgebungsvariable offizieller Images (REDIS_VERSION=8.10.3).

Echter Fall 05.11.2026: redis:latest ohne Versions-Label, das laufende Index-
Image traegt keinen der 1200 Tags mehr - die Update-Entitaet zeigte nur die
Image-ID, obwohl das Image selbst REDIS_VERSION=8.10.3 mitbringt.
"""
import sys, pathlib, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, entity, _pa, ia, co, sensor, update = load(
    "const", "entity", "portainer_api", "image_api", "coordinator", "sensor", "update")
c = Checker("version aus env")
ev = entity.env_version

c.section("auslesen")
c("redis", ev(["PATH=/usr/bin", "GOSU_VERSION=1.17", "REDIS_VERSION=8.10.3"], "redis:latest"), "8.10.3")
c("mit registry und tag", ev(["NGINX_VERSION=1.29.3"], "docker.io/library/nginx:mainline-alpine"), "1.29.3")
c("mit digest", ev(["NODE_VERSION=24.11.0"], "node@sha256:abc"), "24.11.0")
c("bindestrich im namen", ev(["UPTIME_KUMA_VERSION=2.1.0"], "louislam/uptime-kuma:2"), "2.1.0")
c("debian-epoch und suffix weg", ev(["MARIADB_VERSION=1:11.8.3+maria~ubu2404"], "mariadb:11.8"), "11.8.3")
c("vorabversion bleibt", ev(["REDIS_VERSION=8.12.0-rc1"], "redis:8.12-rc"), "8.12.0-rc1")
c("fremde variable zaehlt nicht", ev(["GOSU_VERSION=1.17"], "redis:latest"), None)
c("keine version im wert", ev(["REDIS_VERSION=unstable"], "redis:latest"), None)
c("einzelne zahl ist keine version", ev(["REDIS_VERSION=8"], "redis:latest"), None)
c("kein env", ev(None, "redis:latest"), None)
c("kein image", ev(["REDIS_VERSION=8.10.3"], None), None)

c.section("reihenfolge")
c("label vor tag vor env", entity.installed_version_of(
    {"oci_version": "1", "tag_version": "2", "env_version": "3"}), "1")
c("tag vor env", entity.installed_version_of({"tag_version": "2", "env_version": "3"}), "2")
c("env als letztes", entity.installed_version_of({"tag_version": None, "env_version": "3"}), "3")
c("verfuegbar ebenso", entity.available_version_of({"available_env_version": "8.10.3"}), "8.10.3")


c.section("registry liefert das env aus dem config-blob mit")
api = ia.PortainerImageAPI("", None)
async def walk():
    async def fake(url, repository=None):
        if url.endswith("/manifests/latest"):
            return {"config": {"digest": "sha256:" + "9" * 64}}, "sha256:" + "1" * 64
        if "/blobs/" in url:
            return {"created": "2026-11-05T07:36:42Z",
                    "config": {"Labels": None, "Env": ["REDIS_VERSION=8.10.3"]}}, None
        return None, None
    api._registry_get_json = fake
    return await api.get_remote_image_state("redis:latest", None, want_created=True)
state = asyncio.run(walk())
c("env im ergebnis", state["env"], ["REDIS_VERSION=8.10.3"])

LOCAL, REMOTE = "sha256:" + "a" * 64, "sha256:" + "b" * 64


class Api:
    async def inspect_container(self, e, cid):
        return {"Id": cid, "Image": LOCAL, "Config": {"Image": "redis:latest"}}
    async def get_image_info(self, e, iid):
        return {"Id": iid, "Created": "2026-10-28T01:38:06Z",
                "Config": {"Labels": None, "Env": ["GOSU_VERSION=1.17", "REDIS_VERSION=8.10.3"]}}
    def extract_version_from_image(self, info): return "aaaaaaaaaaaa"
    async def get_current_digest(self, e, cid): return "sha256:" + "4" * 64
    async def get_remote_image_state(self, name, info, want_created=False):
        return {"manifest_digest": "sha256:" + "f" * 64, "config_digest": REMOTE,
                "created": "2026-11-05T07:36:42Z", "labels": {},
                "env": ["REDIS_VERSION=8.10.3"]}
    async def get_available_version(self, e, name): return "latest"


async def main():
    k = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    k.config = dict(const.DEFAULT_OPTIONS)
    k.api, k.endpoint_id, k._store = Api(), 1, None
    k.containers = {"cid": {}}
    k.image_data, k.update_availability = {}, {}
    k._last_registry_check, k._registry_jitter = 0.0, 0.0
    await k._refresh_image_data_if_due()
    return k

k = asyncio.run(main())
d = k.image_data["cid"]
c.section("coordinator: redis-fall")
c("installiert aus env", d["env_version"], "8.10.3")
c("verfuegbar aus env", d["available_env_version"], "8.10.3")
c("neubau derselben version ist ein update", k.update_availability, {"cid": True})


def make(cls):
    class S(cls):
        current_container_id = property(lambda self: "cid")
    s = S.__new__(S)
    s.coordinator = k
    return s

c.section("anzeige")
c("sensor installiert", make(sensor.ContainerCurrentVersionSensor).native_value, "8.10.3")
c("sensor verfuegbar", make(sensor.ContainerAvailableVersionSensor).native_value, "8.10.3")
u = make(update.ContainerUpdateEntity)
c("update installiert", u.installed_version, "8.10.3 (aaaaaaaaaaaa)")
c("update angeboten", u.latest_version, "8.10.3 (bbbbbbbbbbbb)")

sys.exit(c.done())
