"""Instanz-Geraet: Aggregate, loeschbare Images, Build-Datum."""
import sys, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, entity, coordinator, sensor = load(
    "const", "entity", "portainer_api", "coordinator", "sensor")[0:2] + load(
    "const", "entity", "portainer_api", "coordinator", "sensor")[3:5]
c = Checker("instanz-geraet")

def coord(**opts):
    o = coordinator.PortainerDataUpdateCoordinator.__new__(coordinator.PortainerDataUpdateCoordinator)
    o.config = {**const.DEFAULT_OPTIONS, **opts}
    o.api = type("A", (), {"base_url": "http://p:9000"})()
    o.endpoint_id = 2
    o.endpoint_name = "nas-docker"
    o.docker_info = {"ServerVersion": "27.3.1", "OperatingSystem": "Alpine", "NCPU": 8,
                     "Architecture": "x86_64", "MemTotal": 16749953024}
    o.containers = {
        "1": {"Names": ["/plex"],   "Status": "Up 2 hours (healthy)",  "State": "running", "ImageID": "sha256:aaa"},
        "2": {"Names": ["/sonarr"], "Status": "Up 3 min (unhealthy)",  "State": "running", "ImageID": "sha256:bbb"},
        "3": {"Names": ["/nginx"],  "Status": "Up 5 days",             "State": "running", "ImageID": "sha256:aaa"},
        "4": {"Names": ["/old"],    "Status": "Exited (0) 3 days ago", "State": "exited",  "ImageID": "sha256:ccc"},
    }
    o.stacks = {"media": {}}
    o.container_stack_map = {}
    o.update_availability = {"1": True, "3": True, "4": False}
    o.images = [
        {"Id": "sha256:aaa", "RepoTags": ["plex:latest"],   "Size": 7_000_000},   # benutzt
        {"Id": "sha256:bbb", "RepoTags": ["sonarr:latest"], "Size": 8_000_000},   # benutzt
        {"Id": "sha256:ccc", "RepoTags": ["old:1"],         "Size": 9_000_000},   # benutzt (gestoppt)
        {"Id": "sha256:ddd", "RepoTags": None,              "Size": 7_400_000},   # dangling, frei
        {"Id": "sha256:eee", "RepoTags": ["<none>:<none>"], "Size": 5_000_000},   # dangling, frei
        {"Id": "sha256:fff", "RepoTags": ["demo:alt"],      "Size": 3_000_000},   # getaggt, unbenutzt
    ]
    return o

o = coord()
c.section("aggregate ohne zusaetzliche abrufe")
c("gesamt", len(o.containers), 4)
c("laufend", o.running_container_count(), 3)
c("gestoppt", o.stopped_container_count(), 1)
c("mit update", o.update_available_count(), 2)
c("stacks", len(o.stack_names()), 1)

c.section("loeschbare images - benutzte sind immer ausgenommen")
ids = sorted(i["Id"] for i in o.prunable_images())
c("dangling-modus", ids, ["sha256:ddd", "sha256:eee"])
c("summe", sum(int(i["Size"]) for i in o.prunable_images()), 12_400_000)
a = coord(prune_all_unused=True)
ids_all = sorted(i["Id"] for i in a.prunable_images())
c("all-unused nimmt getaggte dazu", ids_all, ["sha256:ddd", "sha256:eee", "sha256:fff"])
c("auch ein gestoppter container schuetzt sein image", "sha256:ccc" in ids_all, False)
c("benutzte image-ids", sorted(o._used_image_ids()), ["sha256:aaa", "sha256:bbb", "sha256:ccc"])

c.section("dangling-erkennung")
c("keine tags", o._is_dangling({"RepoTags": None}), True)
c("<none>:<none>", o._is_dangling({"RepoTags": ["<none>:<none>"]}), True)
c("echter tag", o._is_dangling({"RepoTags": ["x:1"]}), False)

c.section("unhealthy-zaehler am instanz-sensor")
s = sensor.InstanceUnhealthyContainersSensor.__new__(sensor.InstanceUnhealthyContainersSensor)
s.coordinator = o
c("anzahl", s.native_value, 1)
attrs = s.extra_state_attributes
c("namen", attrs["unhealthy_containers"], ["sonarr"])
c("mit healthcheck", attrs["containers_with_healthcheck"], 2)
c("gesamt", attrs["containers_total"], 4)

c.section("speicher-umrechnung")
mem = sensor.InstanceMemorySensor.__new__(sensor.InstanceMemorySensor)
mem.coordinator = o
c("16749953024 B", mem.native_value, 15.6)
o2 = coord(); o2.docker_info = {}
mem2 = sensor.InstanceMemorySensor.__new__(sensor.InstanceMemorySensor); mem2.coordinator = o2
c("ohne docker-info", mem2.native_value, None)

c.section("build-datum: docker liefert nanosekunden")
REAL = "2026-08-25T03:17:18.782399976Z"
c("gekuerzt auf mikrosekunden", entity.parse_docker_time(REAL),
  dt.datetime(2026, 8, 25, 3, 17, 18, 782399, tzinfo=dt.timezone.utc))
c("ohne bruchteil", entity.parse_docker_time("2026-08-25T03:17:18Z"),
  dt.datetime(2026, 8, 25, 3, 17, 18, tzinfo=dt.timezone.utc))
c("dockers null-datum", entity.parse_docker_time("0001-01-01T00:00:00Z"), None)
c("muell", entity.parse_docker_time("kaputt"), None)
c.true("immer zeitzonenbehaftet", entity.parse_docker_time("2026-08-25T03:17:18").tzinfo)

built = sensor.ContainerImageCreatedSensor.__new__(sensor.ContainerImageCreatedSensor)
built.coordinator = type("C", (), {"image_data": {"cid": {"image_created": REAL,
                                                          "available_image_created": "2026-09-01T09:00:00Z"}}})()
type(built).current_container_id = property(lambda x: "cid")
c("sensorwert ist ein datetime", built.native_value,
  dt.datetime(2026, 8, 25, 3, 17, 18, 782399, tzinfo=dt.timezone.utc))
c("verfuegbares datum als attribut",
  built.extra_state_attributes["available_image_built"], "2026-09-01T09:00:00+00:00")

sys.exit(c.done())
