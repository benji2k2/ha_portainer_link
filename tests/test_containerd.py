"""containerd-Image-Store: Image-ID ist der Index-Digest, nicht der Config-Digest.

Ohne Abgleich ueber das Plattform-Manifest galt jeder Container als veraltet.
Die Digests stammen aus einem echten Fall (traefik:latest, Docker 29.9 mit
io.containerd.snapshotter.v1, amd64).
"""
import sys, pathlib, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, _e, _pa, ia, co = load("const", "entity", "portainer_api", "image_api", "coordinator")
c = Checker("containerd")

INDEX = "sha256:6d3e069ee484ab1da5adfdda555a75eb9b1545ef09096a0cc7c3043101986a20"
AMD64 = "sha256:c120e0ab66664c7e124e1cd3d3a10a35e3781c578e75892cadd4b9a90e6c7c0d"
ATTEST = "sha256:3e38380601145b509aab4265d27b94759aa6047c738a76927b8d31c43434edb2"
CONFIG = "sha256:46c727a09232aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INDEX_NEU = "sha256:3733df53e74cbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
AMD64_NEU = "sha256:c0ffee000000cccccccccccccccccccccccccccccccccccccccccccccccccccc"
CONFIG_NEU = "sha256:0badc0de0000dddddddddddddddddddddddddddddddddddddddddddddddddd"
CID = "c0ntainer0000000000000000000000000000000000000000000000000000001"


def local_image(index, amd64, containers=(CID,)):
    return {
        "Id": index, "Os": "linux", "Architecture": "amd64",
        "RepoDigests": [f"traefik@{index}"],
        "Descriptor": {"mediaType": "application/vnd.oci.image.index.v1+json", "digest": index},
        "Config": {"Labels": {"org.opencontainers.image.version": "v3.7.16"}},
        "Manifests": [
            {"ID": amd64, "Kind": "image", "Available": True,
             "ImageData": {"Platform": {"os": "linux", "architecture": "amd64"}, "Containers": list(containers)}},
            {"ID": ATTEST, "Kind": "attestation", "Available": True,
             "Descriptor": {"platform": {"os": "unknown", "architecture": "unknown"}}},
            {"ID": "sha256:arm64", "Kind": "image", "Available": False,
             "ImageData": {"Platform": {"os": "linux", "architecture": "arm64"}, "Containers": []}},
        ],
    }


c.section("lokales plattform-manifest")
c("ueber die container-id", ia.local_platform_digest(local_image(INDEX, AMD64), CID), AMD64)
c("ohne container-id ueber die plattform", ia.local_platform_digest(local_image(INDEX, AMD64, ()), None), AMD64)
c("attestierung nie", ia.local_platform_digest({"Manifests": [
    {"ID": ATTEST, "Kind": "attestation", "Available": True}]}, None), None)
c("klassischer store: keine Manifests", ia.local_platform_digest({"Id": CONFIG, "Os": "linux"}, CID), None)
c("leer", ia.local_platform_digest(None), None)


c.section("registry liefert das plattform-manifest mit")
api = ia.PortainerImageAPI("", None)
async def walk():
    async def fake(url, repository=None):
        if url.endswith("/manifests/latest"):
            return {"manifests": [
                {"digest": AMD64, "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": ATTEST, "platform": {"os": "unknown", "architecture": "unknown"}}]}, INDEX
        if url.endswith(f"/manifests/{AMD64}"):
            return {"config": {"digest": CONFIG}}, AMD64
        return None, None
    api._registry_get_json = fake
    return await api.get_remote_image_state("traefik:latest", {"Os": "linux", "Architecture": "amd64"})
state = asyncio.run(walk())
c("platform_digest", state["platform_digest"], AMD64)
c("config_digest wie bisher", state["config_digest"], CONFIG)

async def walk_single():
    async def fake(url, repository=None):
        if url.endswith("/manifests/latest"):
            return {"config": {"digest": CONFIG}}, AMD64
        return None, None
    api._registry_get_json = fake
    return await api.get_remote_image_state("x/y:latest", None)
c("ohne index: das manifest selbst", asyncio.run(walk_single())["platform_digest"], AMD64)


class Api:
    def __init__(self, info):
        self.info, self.remote = info, (INDEX, AMD64, CONFIG)
        self.remote_calls = 0
    async def inspect_container(self, e, cid):
        return {"Id": CID, "Image": self.info["Id"], "Config": {"Image": "traefik:latest"}}
    async def get_image_info(self, e, iid):
        return self.info
    def extract_version_from_image(self, info): return "latest"
    async def get_current_digest(self, e, cid): return self.info["Id"]
    async def get_remote_image_state(self, name, info, want_created=False):
        self.remote_calls += 1
        index, platform, config = self.remote
        return {"manifest_digest": index, "platform_digest": platform, "config_digest": config,
                "labels": {"org.opencontainers.image.version": "v3.7.16"}}
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
    c.section("aktuelles image: kein update (der eigentliche fehler)")
    api = Api(local_image(INDEX, AMD64)); k = coordinator(api)
    await k._refresh_image_data_if_due()
    d = k.image_data["cid"]
    c("kein update", k.update_availability, {"cid": False})
    c("installiert zeigt die config-id", d["current_config_digest"], CONFIG)
    c("gleich der verfuegbaren", d["current_config_digest"] == d["available_config_digest"], True)

    c.section("zwischen zwei sweeps bleibt es dabei")
    await k._refresh_image_data_if_due()
    c("kein registry-abruf", api.remote_calls, 1)
    c("config-id bleibt", k.image_data["cid"]["current_config_digest"], CONFIG)
    c("weiter kein update", k.update_availability, {"cid": False})

    c.section("echtes update")
    api2 = Api(local_image(INDEX, AMD64)); api2.remote = (INDEX_NEU, AMD64_NEU, CONFIG_NEU)
    k2 = coordinator(api2)
    await k2._refresh_image_data_if_due()
    c("update erkannt", k2.update_availability, {"cid": True})
    c("installiert bleibt der index (config lokal unbekannt)", k2.image_data["cid"]["current_config_digest"], INDEX)

    c.section("nach der installation (zwischen zwei sweeps)")
    api2.info = local_image(INDEX_NEU, AMD64_NEU)
    await k2._refresh_image_data_if_due()
    c("kein registry-abruf dazwischen", api2.remote_calls, 1)
    c("update erlischt sofort", k2.update_availability, {"cid": False})
    c("installiert jetzt die neue config-id", k2.image_data["cid"]["current_config_digest"], CONFIG_NEU)

    c.section("nur attestierungen neu: index anders, plattform gleich")
    api3 = Api(local_image(INDEX, AMD64)); api3.remote = (INDEX_NEU, AMD64, CONFIG)
    k3 = coordinator(api3)
    await k3._refresh_image_data_if_due()
    c("kein update", k3.update_availability, {"cid": False})

    c.section("klassischer store unveraendert")
    classic = {"Id": CONFIG, "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {}}}
    api4 = Api(classic); k4 = coordinator(api4)
    await k4._refresh_image_data_if_due()
    c("kein update", k4.update_availability, {"cid": False})
    c("kein plattform-digest", "current_platform_digest" in k4.image_data["cid"], False)
    api5 = Api(classic); api5.remote = (INDEX_NEU, AMD64_NEU, CONFIG_NEU)
    k5 = coordinator(api5)
    await k5._refresh_image_data_if_due()
    c("update erkannt", k5.update_availability, {"cid": True})

asyncio.run(main())
c.done()
