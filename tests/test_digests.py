"""Welcher Digest entscheidet - und was angezeigt wird.

Der Index-Digest bewegt sich, wenn nur Attestierungen neu gebaut werden; die
Image-ID nicht. Die Werte stammen aus einem echten Fall bei ghcr.io.
"""
import sys, pathlib, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

image_api, sensor, update = load("const", "entity", "portainer_api", "image_api", "sensor", "update")[3:6]
api = image_api.PortainerImageAPI("", None)
c = Checker("digests")

AMD64 = "sha256:ed23b99d94adc4945dbadff3395782e2ba64a4f7296450307619e928ca81e24a"
CONFIG = "sha256:93c91251e7468a6255743144e98ebe59cdeea3c9b0ad74ed2f80cc137ed31227"
IDX_ALT = "sha256:ec79c1a630bf281bdf254ada7ae3aa3b22213d9f79d43c96d1ee1e67b0fe9ff3"
IDX_NEU = "sha256:c7c2a971d39b123f8232915eba64d3e5ba4ff4cc94ab1af3b105923f3eeec6bc"

def index(att_a, att_b):
    return {"manifests": [
        {"digest": AMD64, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:41020d15a6a4b45d611", "platform": {"os": "linux", "architecture": "arm64"}},
        {"digest": att_a, "platform": {"os": "unknown", "architecture": "unknown"}},
        {"digest": att_b, "platform": {"os": "unknown", "architecture": "unknown"}},
    ]}

c.section("plattformauswahl ueberspringt attestierungen")
OLD = index("sha256:fc0896dc0ad6", "sha256:753c34fdec56")
NEW = index("sha256:6f33c9bb94e1", "sha256:a35d4f4d1043")
c("amd64 aus altem index", api._select_platform_manifest(OLD, {"Os":"linux","Architecture":"amd64"}), AMD64)
c("amd64 aus neuem index", api._select_platform_manifest(NEW, {"Os":"linux","Architecture":"amd64"}), AMD64)
c("arm64", api._select_platform_manifest(OLD, {"Os":"linux","Architecture":"arm64"}), "sha256:41020d15a6a4b45d611")
c("unbekannte plattform", api._select_platform_manifest(OLD, {"Os":"linux","Architecture":"riscv64"}), None)
c("nur attestierungen", api._select_platform_manifest(
    {"manifests":[{"digest":"x","platform":{"os":"unknown","architecture":"unknown"}}]}, None), None)

c.section("arm-varianten")
idx = {"manifests":[
    {"digest":"sha256:v6","platform":{"os":"linux","architecture":"arm","variant":"v6"}},
    {"digest":"sha256:v7","platform":{"os":"linux","architecture":"arm","variant":"v7"}}]}
c("v7 exakt", api._select_platform_manifest(idx, {"Os":"linux","Architecture":"arm","Variant":"v7"}), "sha256:v7")
c("ohne variant der erste passende", api._select_platform_manifest(idx, {"Os":"linux","Architecture":"arm"}), "sha256:v6")

c.section("index bewegt sich, image-id nicht")
async def resolve(idx_doc, idx_digest):
    async def fake(url, repository=None):
        if url.endswith("/manifests/latest"): return idx_doc, idx_digest
        if url.endswith(f"/manifests/{AMD64}"): return {"config": {"digest": CONFIG}}, AMD64
        return None, None
    api._registry_get_json = fake
    return await api.get_remote_image_state("ghcr.io/x/y:latest", {"Os":"linux","Architecture":"amd64"})
alt = asyncio.run(resolve(OLD, IDX_ALT))
neu = asyncio.run(resolve(NEW, IDX_NEU))
c("index-digest unterscheidet sich", alt["manifest_digest"] != neu["manifest_digest"], True)
c("image-id ist identisch", (alt["config_digest"], neu["config_digest"]), (CONFIG, CONFIG))
c("-> kein update", alt["config_digest"] == neu["config_digest"], True)

c.section("kuerzen fuer die anzeige")
c("12 hex-zeichen", sensor.short_digest(CONFIG), "93c91251e746")
c("ohne prefix", sensor.short_digest("93c91251e7468a62"), "93c91251e746")
c("laenge einstellbar", sensor.short_digest(CONFIG, 8), "93c91251")
c("None", sensor.short_digest(None), None)
c("'unknown' bleibt", sensor.short_digest("unknown"), "unknown")

c.section("sensoren zeigen die image-id, nicht den index")
def make(cls, data):
    s = cls.__new__(cls)
    s.coordinator = type("C", (), {"image_data": {"cid": data}})()
    type(s).current_container_id = property(lambda x: "cid")
    return s
cur = make(sensor.ContainerCurrentDigestSensor, {"current_digest": IDX_ALT, "current_config_digest": CONFIG})
ava = make(sensor.ContainerAvailableDigestSensor, {"available_digest": IDX_NEU, "available_config_digest": CONFIG})
c("current zeigt image-id", cur.native_value, "93c91251e746")
c("available zeigt image-id", ava.native_value, "93c91251e746")
c("beide gleich -> passt zu 'kein update'", cur.native_value == ava.native_value, True)
c("index bleibt als attribut", cur.extra_state_attributes["manifest_digest"], IDX_ALT)
c("volle image-id als attribut", cur.extra_state_attributes["image_id"], CONFIG)
c("ohne image-id faellt auf index zurueck",
  make(sensor.ContainerCurrentDigestSensor, {"current_digest": IDX_ALT}).native_value, "ec79c1a630bf")

c.section("update-entity")
def ent(data, available):
    e = update.ContainerUpdateEntity.__new__(update.ContainerUpdateEntity)
    e.coordinator = type("C", (), {"image_data": {"cid": data},
                                   "get_update_availability": staticmethod(lambda cid: available)})()
    type(e).current_container_id = property(lambda s: "cid")
    return e
NEUE_ID = "sha256:aabbccdd11223344"
e = ent({"current_config_digest": CONFIG, "available_config_digest": NEUE_ID,
         "current_digest": IDX_ALT, "available_digest": IDX_NEU, "current_version": "latest"}, True)
c("installiert = image-id", e.installed_version, "93c91251e746")
c("verfuegbar = image-id", e.latest_version, "aabbccdd1122")
c("summary nennt beide", e.release_summary,
  "Image id 93c91251e746 -> aabbccdd1122. Manifest digest c7c2a971d39b")
e2 = ent({"current_config_digest": CONFIG, "available_config_digest": CONFIG,
          "available_digest": IDX_NEU, "current_version": "latest"}, False)
c("ohne update zeigt die version", e2.installed_version, "latest")
c("summary sagt unchanged", e2.release_summary,
  "Image id 93c91251e746, unchanged. Manifest digest c7c2a971d39b")

sys.exit(c.done())
