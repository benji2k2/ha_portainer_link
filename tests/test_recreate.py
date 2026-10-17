"""Neu erstellter Container (neue Docker-ID) behaelt das Ergebnis der Registry-Pruefung (Homepage 17.10.)."""
import sys, pathlib, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

co = load("const", "entity", "portainer_api", "image_api", "release_notes", "tag_versions", "coordinator")[6]
c = Checker("neu erstellter container")


def koordinator():
    k = co.PortainerDataUpdateCoordinator.__new__(co.PortainerDataUpdateCoordinator)
    k.config = {}
    k.containers, k.container_stack_map, k.container_stack_info, k.stable_container_map = {}, {}, {}, {}
    k.image_data, k.update_availability = {}, {}
    return k


def ct(cid, name):
    return {"Id": cid, "Names": ["/" + name], "Labels": {}}


c.section("gleicher name, neue id")
k = koordinator()
k._process_containers([ct("alt", "homepage"), ct("x1", "grafana")])
k.image_data = {"alt": {"current_config_digest": "sha256:v25", "available_config_digest": "sha256:v26",
                        "available_oci_version": "v2.6.0"}, "x1": {"current_config_digest": "sha256:g"}}
k.update_availability = {"alt": True, "x1": False}
k._process_containers([ct("neu", "homepage"), ct("x1", "grafana")])
c("update bleibt sichtbar", k.get_update_availability("neu"), True)
c("verfuegbare version uebernommen", k.image_data["neu"].get("available_oci_version"), "v2.6.0")
c("alte id weg", ("alt" in k.image_data, "alt" in k.update_availability), (False, False))
c("unbeteiligter container unveraendert", (k.image_data["x1"], k.update_availability["x1"]), ({"current_config_digest": "sha256:g"}, False))

c.section("keine falsche uebernahme")
k = koordinator()
k._process_containers([ct("a", "homepage")])
k.image_data, k.update_availability = {"a": {"available_config_digest": "sha256:v26"}}, {"a": True}
k._process_containers([ct("b", "anderer")])
c("anderer name -> nichts", k.get_update_availability("b"), False)
k = koordinator()
k._process_containers([ct("a", "homepage")])
k.image_data, k.update_availability = {"a": {"x": 1}, "b": {"y": 2}}, {"a": True, "b": False}
k._process_containers([ct("a", "homepage"), ct("b", "homepage-2")])
c("gleiche id -> unveraendert", (k.image_data["a"], k.update_availability["a"]), ({"x": 1}, True))
k = koordinator()
k._process_containers([ct("a", "homepage")])
k.image_data, k.update_availability = {"a": {"alt": 1}, "n": {"schon": 1}}, {"a": True, "n": False}
k._process_containers([ct("n", "homepage")])
c("nachfolger hat schon eigene daten -> nicht ueberschreiben", (k.image_data["n"], k.update_availability["n"]), ({"schon": 1}, False))

c.section("neu erstellt waehrend ha aus war (zuordnung gespeichert)")
class Store:
    def __init__(self): self.data = None
    async def async_save(self, data): self.data = data
    async def async_load(self): return self.data

async def main():
    store = Store()
    k = koordinator(); k._store = store; k._last_registry_check = 0.0
    k._process_containers([ct("alt", "homepage")])
    k.image_data, k.update_availability = {"alt": {"available_oci_version": "v2.6.0"}}, {"alt": True}
    await k._async_save_state()
    c("zuordnung mitgespeichert", store.data.get("stable_container_map"), {"container_homepage": "alt"})
    n = koordinator(); n._store = store; n._last_registry_check = 0.0
    await n.async_load_persisted_state()
    n._process_containers([ct("neu", "homepage")])
    c("nach neustart: update bleibt", n.get_update_availability("neu"), True)
    c("nach neustart: version bleibt", n.image_data["neu"].get("available_oci_version"), "v2.6.0")

asyncio.run(main())
c.done()
