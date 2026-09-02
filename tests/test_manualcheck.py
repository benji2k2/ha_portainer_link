"""Manueller Auslöser: erzwingt einen Registry-Check trotz Intervall.

Seit der Zeitstempel persistiert wird, loest auch ein Reload keinen Sweep mehr
aus - ohne diesen Weg gaebe es bei sechs Stunden Intervall gar keinen.
"""
import sys, pathlib, asyncio, time, types
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, entity, coordinator, button = load(
    "const", "entity", "portainer_api", "coordinator", "button")[0:2] + load(
    "const", "entity", "portainer_api", "coordinator", "button")[3:5]
c = Checker("manueller update-check")

def coord():
    o = coordinator.PortainerDataUpdateCoordinator.__new__(coordinator.PortainerDataUpdateCoordinator)
    o.config = dict(const.DEFAULT_OPTIONS)
    o._last_registry_check = time.time()      # gerade eben geprueft
    o._registry_jitter = 300.0
    o.refreshed = False
    async def refresh(): o.refreshed = True
    o.async_refresh = refresh
    o.containers = {"1": {"Names": ["/plex"]}, "2": {"Names": ["/sonarr"]}, "3": {"Names": ["/nginx"]}}
    o.update_availability = {"1": True, "2": False, "3": True}
    o.get_update_availability = lambda cid: bool(o.update_availability.get(cid, False))
    return o

async def main():
    c.section("ein frischer zeitstempel verhindert normalerweise den sweep")
    o = coord()
    interval = max(int(o.config["update_check_interval"]), 1) * 60 + o._registry_jitter
    c.true("intervall noch nicht abgelaufen", time.time() - o._last_registry_check < interval)

    c.section("der auslöser setzt ihn zurueck")
    await o.async_force_registry_check()
    c("zeitstempel zurueckgesetzt", o._last_registry_check, 0.0)
    c("jitter entfaellt fuer diesen lauf", o._registry_jitter, 0.0)
    c("und ein refresh wurde angestossen", o.refreshed, True)

    c.section("button meldet das ergebnis an sich selbst")
    o2 = coord()
    b = button.CheckUpdatesButton.__new__(button.CheckUpdatesButton)
    b._last_result = {}
    b.async_write_ha_state = lambda: None
    b.coordinator = o2
    await b.async_press()
    attrs = b.result_attributes
    c("text nennt geprueft und gefunden", attrs["last_result"],
      "Checked 3 container(s), 2 with an update available")
    c("als erfolg markiert", attrs["last_result_ok"], True)
    c("anzahl separat", attrs["last_updates_available"], 2)
    c("check wurde wirklich erzwungen", o2._last_registry_check, 0.0)

    c.section("attribute listen die betroffenen container")
    full = b.extra_state_attributes
    c("anzahl", full["updates_available"], 2)
    c("namen", full["containers"], ["nginx", "plex"])

asyncio.run(main())
sys.exit(c.done())
