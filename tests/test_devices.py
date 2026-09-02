"""Geraete-Identitaeten, Benennung und das Aufraeumen nach Options-Wechseln."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, entity = load("const", "entity")[:2]
init = load("const", "entity", "portainer_api", "coordinator", "__init__")[4]
c = Checker("geraete")

BASE, ENTRY, EP = "http://portainer.fritz.box:9000", "abc123", 2

c.section("identifier duerfen sich nie aendern - daran haengt die historie")
c("container", entity.container_device_id(ENTRY, EP, BASE, "container_plex"),
  f"{ENTRY}_{EP}_{entity.host_key(BASE)}_container_plex")
c("stack", entity.stack_device_id(ENTRY, EP, BASE, "media"),
  f"{ENTRY}_{EP}_{entity.host_key(BASE)}_{entity.stack_key('media')}")
c.true("hub kollidiert mit keinem", entity.hub_device_id(ENTRY, EP, BASE) not in {
    entity.container_device_id(ENTRY, EP, BASE, "container_plex"),
    entity.stack_device_id(ENTRY, EP, BASE, "media")})

c.section("via_device nur wenn das instanz-geraet aktiv ist")
off = entity.container_device_info(ENTRY, EP, BASE, "container_plex", "plex", "cid", via_hub=False)
on = entity.container_device_info(ENTRY, EP, BASE, "container_plex", "plex", "cid", via_hub=True)
c("ohne hub kein via_device", "via_device" in off, False)
c("mit hub verweist er darauf", on.get("via_device"), (const.DOMAIN, entity.hub_device_id(ENTRY, EP, BASE)))
c("identifier bleiben gleich", off["identifiers"], on["identifiers"])

c.section("suffix: umgebungsname statt url-host")
c("ohne instanznamen der hostname",
  entity.container_device_info(ENTRY, EP, BASE, "container_plex", "plex", "cid")["name"], "plex (portainer)")
c("mit instanznamen",
  entity.container_device_info(ENTRY, EP, BASE, "container_plex", "plex", "cid",
                               instance_name="nas-docker")["name"], "plex (nas-docker)")
c("stack ebenso",
  entity.stack_device_info(ENTRY, EP, BASE, "media", instance_name="nas-docker")["name"],
  "Stack: media (nas-docker)")
for leer in (None, ""):
    c(f"instance_name={leer!r} faellt zurueck",
      entity.container_device_info(ENTRY, EP, BASE, "container_plex", "plex", "cid",
                                   instance_name=leer)["name"], "plex (portainer)")

c.section("docker-details am instanz-geraet")
info = {"ServerVersion": "27.3.1", "OperatingSystem": "Alpine Linux v3.20", "Architecture": "x86_64"}
d = entity.hub_device_info(ENTRY, EP, BASE, "nas-docker", info)
c("name", d["name"], "nas-docker")
c("sw_version", d["sw_version"], "Docker 27.3.1")
c("model", d["model"], "Alpine Linux v3.20")
c("hw_version", d["hw_version"], "x86_64")
d0 = entity.hub_device_info(ENTRY, EP, BASE, None, None)
c("ohne daten: hostname", d0["name"], "portainer")
c("ohne daten: kein sw_version", "sw_version" in d0, False)
c("identifier unabhaengig davon", d0["identifiers"], d["identifiers"])

c.section("was nach einem options-wechsel noch existieren darf")
class Coord:
    def __init__(self, stack_view, stack_buttons=True, instance=True):
        self.api = type("A", (), {"base_url": BASE})()
        self.endpoint_id = EP
        self._sv, self._sb, self._inst = stack_view, stack_buttons, instance
        raw = {
            "c1": {"Names": ["/plex"], "Labels": {"com.docker.compose.project": "media",
                                                  "com.docker.compose.service": "plex",
                                                  "com.docker.compose.container-number": "1"}},
            "c2": {"Names": ["/sonarr"], "Labels": {"com.docker.compose.project": "media",
                                                    "com.docker.compose.service": "sonarr",
                                                    "com.docker.compose.container-number": "1"}},
            "c3": {"Names": ["/nginx"], "Labels": {}},
        }
        self.containers = raw
        self.container_stack_info = {}
        for cid, cont in raw.items():
            si = entity.stack_info_from_container(cont)
            if not stack_view:
                si = {"stack_name": None, "service_name": None,
                      "container_number": None, "is_stack_container": False}
            self.container_stack_info[cid] = si
    def get_container_stack_info(self, cid): return self.container_stack_info.get(cid)
    def stack_names(self): return ["media"] if self._sv else []
    def is_stack_view_enabled(self): return self._sv
    def is_stack_buttons_enabled(self): return self._sb
    def is_instance_device_enabled(self): return self._inst

Entry = type("E", (), {"entry_id": ENTRY})
hub = entity.hub_device_id(ENTRY, EP, BASE)
stack = entity.stack_device_id(ENTRY, EP, BASE, "media")
cdev = lambda n: entity.container_device_id(ENTRY, EP, BASE, f"container_{n}")

an = init._active_device_ids(Entry(), Coord(stack_view=True))
c.true("stack-view an: hub aktiv", hub in an)
c.true("stack-view an: stack-geraet aktiv", stack in an)
c.true("stack-view an: nginx einzeln", cdev("nginx") in an)
c("stack-view an: keine einzelgeraete fuer plex/sonarr",
  cdev("plex") in an or cdev("sonarr") in an, False)

aus = init._active_device_ids(Entry(), Coord(stack_view=False))
c("stack-view aus: stack-geraet wird stale", stack in aus, False)
c.true("stack-view aus: plex bekommt ein eigenes", cdev("plex") in aus)
c.true("stack-view aus: hub bleibt", hub in aus)

ohne = init._active_device_ids(Entry(), Coord(stack_view=False, instance=False))
c("instanz-geraet aus: hub wird stale", hub in ohne, False)
c.true("container bleiben unberuehrt", cdev("plex") in ohne and cdev("nginx") in ohne)

c.true("stack-geraet ueberlebt ohne stack-buttons, weil container daran haengen",
       stack in init._active_device_ids(Entry(), Coord(stack_view=True, stack_buttons=False)))

sys.exit(c.done())
