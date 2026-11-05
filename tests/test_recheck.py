"""Nach einem Image-Wechsel zwischen zwei Sweeps: Registry fuer diesen Container neu fragen.

Echter Fall 05.11.2026 (scrutiny:master-omnibus): der Sweep
fand 1.79.2, danach erschien 1.79.3, Watchtower zog beim Update 1.79.3. Bis zum
naechsten Sweep bot die Update-Entitaet 1.79.2 an - aelter als das laufende Image.
"""
import sys, pathlib, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, _e, _pa, co = load("const", "entity", "portainer_api", "coordinator")
c = Checker("recheck nach image-wechsel")

ALT, V2, V3 = "sha256:" + "a" * 64, "sha256:" + "b" * 64, "sha256:" + "c" * 64
VERSION = {ALT: "1.79.0", V2: "1.79.2", V3: "1.79.3"}


class Api:
    def __init__(self):
        self.running = {"scrutiny": ALT, "andere": ALT}
        self.remote = V2
        self.remote_calls = []

    async def inspect_container(self, e, cid):
        return {"Id": cid, "Image": self.running[cid], "Config": {"Image": f"x/{cid}:latest"}}
    async def get_image_info(self, e, iid):
        return {"Id": iid, "Config": {"Labels": {"org.opencontainers.image.version": VERSION[iid]}}}
    def extract_version_from_image(self, info): return "latest"
    async def get_current_digest(self, e, cid): return None
    async def get_remote_image_state(self, name, info, want_created=False):
        self.remote_calls.append(name)
        return {"manifest_digest": "sha256:index" + self.remote[-4:], "config_digest": self.remote,
                "labels": {"org.opencontainers.image.version": VERSION[self.remote]}}
    async def get_available_version(self, e, name): return "latest"


class Tags:
    def __init__(self): self.forgotten = []
    def forget(self, registry, repository): self.forgotten.append(repository)
    def clear_cache(self): pass
    async def versions(self, *a, **k): return [None, None]


class Images:
    @staticmethod
    def _parse_image_ref(name):
        repo, tag = name.split(":")
        return "registry-1.docker.io", repo, tag, False


def coordinator(api):
    k = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    k.config = dict(const.DEFAULT_OPTIONS)
    api.images = Images()
    k.api, k.endpoint_id = api, 1
    k.tag_versions = Tags()
    k.saved = 0
    async def save(): k.saved += 1
    k._store = object()
    k._async_save_state = save
    k.containers = {"scrutiny": {}, "andere": {}}
    k.image_data, k.update_availability = {}, {}
    k._last_registry_check, k._registry_jitter = 0.0, 0.0
    return k


async def main():
    api = Api(); k = coordinator(api)

    c.section("sweep findet 1.79.2")
    await k._refresh_image_data_if_due()
    c("beide container gefragt", len(api.remote_calls), 2)
    c("update fuer beide", k.update_availability, {"scrutiny": True, "andere": True})
    c("angeboten wird 1.79.2", k.image_data["scrutiny"]["available_oci_version"], "1.79.2")

    c.section("1.79.3 erscheint, watchtower zieht sie (zwischen zwei sweeps)")
    api.remote = V3
    api.running["scrutiny"] = V3
    saved_before = k.saved
    await k._refresh_image_data_if_due()
    c("nur der gewechselte container neu gefragt", api.remote_calls[2:], ["x/scrutiny:latest"])
    c("kein update mehr fuer scrutiny", k.update_availability["scrutiny"], False)
    c("verfuegbar ist jetzt 1.79.3", k.image_data["scrutiny"]["available_oci_version"], "1.79.3")
    c("laufend 1.79.3", k.image_data["scrutiny"]["oci_version"], "1.79.3")
    c("unveraenderter container behaelt sein update", k.update_availability["andere"], True)
    c("und sein altes ergebnis", k.image_data["andere"]["available_oci_version"], "1.79.2")
    c("tag-cache des repos verworfen", k.tag_versions.forgotten, ["x/scrutiny"])
    c("zustand gespeichert", k.saved, saved_before + 1)

    c.section("naechster lauf ohne wechsel: keine weitere anfrage")
    await k._refresh_image_data_if_due()
    c("keine anfrage", len(api.remote_calls), 3)
    c("nichts gespeichert", k.saved, saved_before + 1)

    c.section("wechsel auf ein image, das noch nicht das neueste ist")
    api.remote = V3
    api.running["andere"] = V2
    await k._refresh_image_data_if_due()
    c("neu gefragt", api.remote_calls[-1], "x/andere:latest")
    c("update auf 1.79.3 bleibt", k.update_availability["andere"], True)
    c("angeboten 1.79.3", k.image_data["andere"]["available_oci_version"], "1.79.3")

    c.section("erster lauf ohne gespeicherte image-id: kein recheck")
    api2 = Api(); k2 = coordinator(api2)
    k2.image_data = {"scrutiny": {"current_config_digest": ALT}, "andere": {"current_config_digest": ALT}}
    k2.update_availability = {"scrutiny": True, "andere": True}
    k2._last_registry_check = 10**12  # sweep nicht faellig
    api2.running["scrutiny"] = V3
    await k2._refresh_image_data_if_due()
    c("keine anfrage", api2.remote_calls, [])

    c.section("registry-daten abgeschaltet: kein recheck")
    api3 = Api(); k3 = coordinator(api3)
    await k3._refresh_image_data_if_due()
    k3.config[const.CONF_ENABLE_UPDATE_SENSORS] = False
    k3.config[const.CONF_ENABLE_VERSION_SENSORS] = False
    api3.running["scrutiny"] = V3
    await k3._refresh_image_data_if_due()
    c("keine weitere anfrage", len(api3.remote_calls), 2)

asyncio.run(main())
sys.exit(c.done())
