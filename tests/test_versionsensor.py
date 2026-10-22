"""Versions-Sensoren zeigen eine Version, nie das Build-Datum.

Vorher las extract_version_from_image die Labels an der falschen Stelle
(oben statt unter Config) und fiel auf das Build-Datum zurueck ("2026.10.22").
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, _e, _pa, ia, sensor = load("const", "entity", "portainer_api", "image_api", "sensor")
c = Checker("version-sensor")
api = ia.PortainerImageAPI("", None)
ID = "sha256:ebeed6f615bf0000000000000000000000000000000000000000000000000000"

c.section("version aus dem image")
c("label unter Config", api.extract_version_from_image({
    "Id": ID, "RepoTags": ["ghcr.io/benji2k2/snappymail:latest"], "Created": "2026-10-22T07:12:46Z",
    "Config": {"Labels": {"org.opencontainers.image.version": "2.38.2"}}}), "2.38.2")
c("tag vor label", api.extract_version_from_image({
    "Id": ID, "RepoTags": ["redis:8.10"], "Config": {"Labels": {"version": "x"}}}), "8.10")
c("ohne label kein datum, sondern die kurze id", api.extract_version_from_image({
    "Id": ID, "RepoTags": ["redis:latest"], "Created": "2026-10-21T10:00:00Z", "Config": {"Labels": None}}),
  "ebeed6f615bf")


def make(cls, data):
    class S(cls):
        current_container_id = property(lambda self: "cid")
    s = S.__new__(S)
    s.coordinator = type("C", (), {"image_data": {"cid": data}})()
    return s

c.section("sensor installiert")
cur = sensor.ContainerCurrentVersionSensor
c("oci-label zuerst", make(cur, {"oci_version": "2.38.2", "tag_version": "x", "current_version": "latest"}).native_value, "2.38.2")
c("sonst die tag-version aus der registry", make(cur, {"tag_version": "8.10.2", "current_version": "ebeed6f615bf"}).native_value, "8.10.2")
c("sonst aus dem lokalen image", make(cur, {"current_version": "ebeed6f615bf"}).native_value, "ebeed6f615bf")
c("nichts bekannt", make(cur, {}).native_value, None)

c.section("sensor verfuegbar")
av = sensor.ContainerAvailableVersionSensor
c("oci-label der registry", make(av, {"available_oci_version": "2.38.2", "available_version": "latest"}).native_value, "2.38.2")
c("tag-version der registry", make(av, {"available_tag_version": "8.10.2", "available_version": "latest"}).native_value, "8.10.2")
c("sonst der verfolgte tag", make(av, {"available_version": "latest"}).native_value, "latest")

sys.exit(c.done())
