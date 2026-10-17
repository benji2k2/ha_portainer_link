"""Version aus den Tags mit gleichem Digest (Docker Hub, Quay), fuer Images ohne Versions-Label."""
import sys, pathlib, asyncio, types
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, _e, _pa, ia, _rn, tv, co, update = load(
    "const", "entity", "portainer_api", "image_api", "release_notes", "tag_versions", "coordinator", "update")
c = Checker("tag-versionen")

c.section("auswahl des genauesten tags (echte listen vom 14.10.)")
c("uptime-kuma: variante weg", tv.pick_version(["2.5.6-rootless", "2-rootless", "next-rootless"]), "2.5.6")
c("grafana", tv.pick_version(["latest", "13.2", "13.2.4"]), "13.2.4")
c("prometheus: ohne -busybox bevorzugt", tv.pick_version(["v3", "latest", "latest-busybox", "v3.15.0", "v3.15.0-busybox"]), "v3.15.0")
c("headscale: sha-tag ignoriert", tv.pick_version(["sha-0f7d1e53", "stable", "v0", "v0.29", "v0.29.5", "0", "0.29", "0.29.5", "latest"]), "0.29.5")
c("portainer alpine", tv.pick_version(["alpine-sts", "alpine", "2.45.2-alpine"]), "2.45.2")
c("socket-proxy:1", tv.pick_version(["1", "1.13.1"]), "1.13.1")
c("fest gesetzter tag zaehlt mit", tv.pick_version(["1.13.1"]), "1.13.1")
c("vorabversion behaelt ihren zusatz", tv.pick_version(["2.0.0-beta.1", "next"]), "2.0.0-beta.1")
c("nur latest -> keine", tv.pick_version(["latest"]), None)
c("leer", tv.pick_version([]), None)

class Resp:
    def __init__(self, status, payload): self.status, self._p = status, payload
    async def json(self, content_type=None): return self._p
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Session:
    def __init__(self, status=200): self.calls, self.status = [], status
    def get(self, url, params=None):
        self.calls.append((url, params))
        if self.status != 200: return Resp(self.status, {})
        if "quay.io" in url:
            return Resp(200, {"tags": [{"name": "v1.12.1", "manifest_digest": "sha256:aa"}, {"name": "latest", "manifest_digest": "sha256:aa"},
                                       {"name": "v1.12.2", "manifest_digest": "sha256:bb"}]})
        return Resp(200, {"results": [{"name": "2.5.6-rootless", "digest": "sha256:aa"}, {"name": "2-rootless", "digest": "sha256:aa"},
                                      {"name": "2.5.7-rootless", "digest": "sha256:bb"}]})

async def main():
    c.section("docker hub und quay")
    s = Session(); cl = tv.TagVersionClient(s)
    c("hub: laufend + verfuegbar", await cl.versions("registry-1.docker.io", "louislam/uptime-kuma", ["sha256:aa", "sha256:bb"]), ["2.5.6", "2.5.7"])
    c("hub-url", s.calls[0][0], "https://hub.docker.com/v2/repositories/louislam/uptime-kuma/tags")
    await cl.versions("registry-1.docker.io", "louislam/uptime-kuma", ["sha256:aa", None])
    c("zweiter aufruf aus dem cache", len(s.calls), 1)
    c("quay", await cl.versions("quay.io", "prometheus/node-exporter", ["sha256:aa", "sha256:bb"]), ["v1.12.1", "v1.12.2"])
    c("quay-url", s.calls[-1][0], "https://quay.io/api/v1/repository/prometheus/node-exporter/tag/")
    before = len(s.calls)
    c("ghcr nicht unterstuetzt", await cl.versions("ghcr.io", "x/y", ["sha256:aa"]), [None])
    c("dafuer keine anfrage", len(s.calls), before)
    c("unbekannter digest", await cl.versions("registry-1.docker.io", "louislam/uptime-kuma", ["sha256:zz"]), [None])
    cl.clear_cache(); await cl.versions("registry-1.docker.io", "louislam/uptime-kuma", ["sha256:aa"])
    c("nach clear_cache neu geholt", len(s.calls), before + 1)
    c("http-fehler -> keine version", await tv.TagVersionClient(Session(429)).versions("registry-1.docker.io", "a/b", ["sha256:aa"]), [None])

    c.section("gleiche plattform-images, anderer index (vector 17.10.)")
    def img(arch, d): return {"architecture": arch, "os": "linux", "digest": d}
    gleich = [img("amd64", "sha256:p1"), img("arm64", "sha256:p2"), img("arm", "sha256:p3")]
    class Hub(Session):
        def get(self, url, params=None):
            self.calls.append((url, params))
            return Resp(200, {"results": [
                {"name": "nightly-2026-10-17-alpine", "digest": "sha256:n1", "images": [img("amd64", "sha256:n9")]},
                {"name": "latest-alpine", "digest": "sha256:la", "images": gleich},
                {"name": "0.X-alpine", "digest": "sha256:x0", "images": gleich},
                {"name": "0.59.X-alpine", "digest": "sha256:x1", "images": gleich},
                {"name": "0.59.0-alpine", "digest": "sha256:v0", "images": gleich + [img("unknown", "sha256:att")]},
                # teilt nur amd64 (arm64 neu gebaut) -> zaehlt nicht
                {"name": "0.58.0-alpine", "digest": "sha256:v8", "images": [img("amd64", "sha256:p1"), img("arm64", "sha256:q2"), img("arm", "sha256:p3")]},
                {"name": "latest-debian", "digest": "sha256:ld", "images": [img("amd64", "sha256:d1")]},
                {"name": "1.0.0", "digest": "sha256:solo"},
            ]})
    h = tv.TagVersionClient(Hub())
    c("index ohne versions-tag -> ueber plattform-images", await h.versions("registry-1.docker.io", "timberio/vector", ["sha256:la"]), ["0.59.0"])
    c("attestation (unknown) zaehlt nicht mit", await h.versions("registry-1.docker.io", "timberio/vector", ["sha256:v0"]), ["0.59.0"])
    c("nur eine plattform gleich -> keine version", await h.versions("registry-1.docker.io", "timberio/vector", ["sha256:ld"]), [None])
    c("tag ohne images-liste: wie bisher", await h.versions("registry-1.docker.io", "timberio/vector", ["sha256:solo"]), ["1.0.0"])
    c("nightly ohne versions-zwilling", await h.versions("registry-1.docker.io", "timberio/vector", ["sha256:n1"]), [None])
    c("eine anfrage fuer alles", len(h.session.calls), 1)

    c.section("coordinator schlaegt nur bei bedarf nach")
    class Client:
        def __init__(self): self.calls = []
        async def versions(self, registry, repository, digests):
            self.calls.append((registry, repository, tuple(digests)))
            return ["2.5.6", "2.5.7" if digests[1] == "sha256:bb" else "2.5.6"]
    k = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    k.api = types.SimpleNamespace(images=ia.PortainerImageAPI("", None))
    k.tag_versions = Client(); k.image_data = {}
    data = {"current_digest": "sha256:aa", "available_digest": "sha256:bb"}
    await k._refresh_tag_versions("cid", "louislam/uptime-kuma:2-rootless", data, True)
    c("versionen gesetzt", (data["tag_version"], data["available_tag_version"]), ("2.5.6", "2.5.7"))
    c("repository fuer hub", k.tag_versions.calls[0][:2], ("registry-1.docker.io", "louislam/uptime-kuma"))
    k.image_data = {"cid": dict(data)}
    again = {"current_digest": "sha256:aa"}
    await k._refresh_tag_versions("cid", "louislam/uptime-kuma:2-rootless", again, False)
    c("gleicher digest, kein sweep -> keine anfrage", len(k.tag_versions.calls), 1)
    changed = {"current_digest": "sha256:bb"}
    await k._refresh_tag_versions("cid", "louislam/uptime-kuma:2-rootless", changed, False)
    c("neues image -> sofort nachgeschlagen", len(k.tag_versions.calls), 2)
    labelled = {"current_digest": "sha256:aa", "oci_version": "v3.7.14"}
    await k._refresh_tag_versions("cid2", "traefik:latest", labelled, True)
    c("mit versions-label nie", len(k.tag_versions.calls), 2)

    c.section("anzeige in der update-entitaet")
    e = update.ContainerUpdateEntity.__new__(update.ContainerUpdateEntity)
    e.coordinator = types.SimpleNamespace(image_data={"cid": {"current_config_digest": "sha256:93c91251e746aa",
        "available_config_digest": "sha256:aabbccdd112233", "tag_version": "2.5.6", "available_tag_version": "2.5.7"}},
        get_update_availability=lambda cid: True)
    type(e).current_container_id = property(lambda s: "cid")
    c("installiert", e.installed_version, "2.5.6 (93c91251e746)")
    c("verfuegbar", e.latest_version, "2.5.7 (aabbccdd1122)")
    e.coordinator.image_data["cid"]["oci_version"] = "v9"
    c("label hat vorrang", e.installed_version, "v9 (93c91251e746)")

asyncio.run(main())
sys.exit(c.done())
