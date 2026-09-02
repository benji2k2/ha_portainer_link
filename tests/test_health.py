"""Healthcheck-Erkennung und die daraus abgeleiteten Zaehler."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

entity = load("const", "entity")[1]
c = Checker("healthchecks")

c.section("docker haengt den zustand nur an laufende container an")
for status, want in [
    ("Up 2 hours (healthy)", "healthy"),
    ("Up 3 minutes (unhealthy)", "unhealthy"),
    ("Up 5 seconds (health: starting)", "starting"),
    ("Up 2 hours", None),                    # kein HEALTHCHECK definiert
    ("Up 2 hours (Paused)", None),           # darf nicht als fehler zaehlen
    ("Exited (0) 3 days ago", None),
    ("Created", None),
]:
    c(f"{status!r}", entity.container_health({"Status": status}), want)

c.section("inspect-payload")
c("State.Health.Status", entity.container_health({"State": {"Health": {"Status": "unhealthy"}}}), "unhealthy")
c("'none' heisst kein healthcheck", entity.container_health({"State": {"Health": {"Status": "none"}}}), None)
c("ohne Health-block", entity.container_health({"State": {"Running": True}}), None)
c("kein container", entity.container_health(None), None)

c.section("zaehlung ueber einen gemischten satz")
containers = {
    "1": {"Names": ["/plex"],   "Status": "Up 2 hours (healthy)"},
    "2": {"Names": ["/sonarr"], "Status": "Up 3 minutes (unhealthy)"},
    "3": {"Names": ["/radarr"], "Status": "Up 1 hour (unhealthy)"},
    "4": {"Names": ["/nginx"],  "Status": "Up 5 days"},
    "5": {"Names": ["/old"],    "Status": "Exited (0) 3 days ago"},
    "6": {"Names": ["/boot"],   "Status": "Up 5 seconds (health: starting)"},
}
unhealthy = sorted(entity.container_name(x) for x in containers.values()
                   if entity.container_health(x) == entity.HEALTH_UNHEALTHY)
c("anzahl unhealthy", len(unhealthy), 2)
c("welche", unhealthy, ["radarr", "sonarr"])
c("ueberwacht (mit healthcheck)",
  sum(1 for x in containers.values() if entity.container_health(x) is not None), 4)
c("starting zaehlt nicht als fehler", "boot" in unhealthy, False)

c.section("containernamen")
c("fuehrender slash faellt weg", entity.container_name({"Names": ["/plex"]}), "plex")
c("Name als rueckfall", entity.container_name({"Name": "/sonarr"}), "sonarr")
c("gar nichts", entity.container_name({}), "unknown")

sys.exit(c.done())
