import sys, types, importlib.util, pathlib, asyncio, datetime as _dt
SRC = pathlib.Path("/Users/benjamin-d/AI/ha_portainer_link/custom_components/ha_portainer_link")
def mod(n, **a):
    m = types.ModuleType(n)
    for k,v in a.items(): setattr(m,k,v)
    sys.modules[n]=m; return m
ah = mod("aiohttp"); ah.__getattr__ = lambda n: object
mod("aiohttp.client_exceptions", ClientConnectorCertificateError=type("E",(Exception,),{}))
ha = mod("homeassistant"); ha.__path__=[]
mod("homeassistant.helpers"); mod("homeassistant.util")
mod("homeassistant.util.dt", now=lambda: _dt.datetime(2026,8,27,12,0,0, tzinfo=_dt.timezone.utc))
mod("homeassistant.helpers.update_coordinator",
    CoordinatorEntity=type("CE",(),{"__init__":lambda s,c: setattr(s,"coordinator",c)}),
    DataUpdateCoordinator=object, UpdateFailed=Exception)
mod("homeassistant.components"); mod("homeassistant.components.button", ButtonEntity=object)
pkg = types.ModuleType("hpl"); pkg.__path__=[str(SRC)]; sys.modules["hpl"]=pkg
def load(n):
    sp = importlib.util.spec_from_file_location(f"hpl.{n}", SRC/f"{n}.py")
    m = importlib.util.module_from_spec(sp); sys.modules[f"hpl.{n}"]=m; sp.loader.exec_module(m); return m
load("const"); load("entity"); pa = load("portainer_api"); b = load("button")

fails=[]
def check(l, got, want):
    ok = got==want
    print(f"{'PASS' if ok else 'FAIL'}  {l}\n       -> {got!r}")
    if not ok: print(f"       want={want!r}"); fails.append(l)

NOTIFIED = []
async def fake_notify(hass, coordinator, title, message):
    NOTIFIED.append((title, message))
b._send_notification = fake_notify

def make(cls, api, **coord):
    e = cls.__new__(cls)
    b.ButtonResultMixin.__init__.__wrapped__ if False else None
    e._last_result = {}
    e.hass = None
    e.coordinator = types.SimpleNamespace(
        api=api, endpoint_id=2,
        async_request_refresh=lambda: asyncio.sleep(0),
        async_refresh=lambda: asyncio.sleep(0),
        is_prune_all_unused=lambda: False,
        prunable_images=lambda: [],
        **coord)
    e.endpoint_id = 2
    e.async_write_ha_state = lambda: None
    type(e).current_container_id = property(lambda s: "cid")
    type(e).container_name = property(lambda s: "plex")
    return e

async def main():
    print("=== erfolg: KEINE benachrichtigung, ergebnis am button ===")
    NOTIFIED.clear()
    api = types.SimpleNamespace()
    async def restart_ok(e, c): return True
    api.restart_container = restart_ok
    btn = make(b.RestartContainerButton, api)
    await btn.async_press()
    check("keine benachrichtigung", NOTIFIED, [])
    check("ergebnis am button", btn.result_attributes["last_result"], "Restarted plex")
    check("als erfolg markiert", btn.result_attributes["last_result_ok"], True)
    check("zeitstempel gesetzt", btn.result_attributes["last_run"], "2026-08-27T12:00:00+00:00")

    print("\n=== fehler: benachrichtigung UND ergebnis am button ===")
    NOTIFIED.clear()
    async def restart_fail(e, c): return False
    api.restart_container = restart_fail
    btn = make(b.RestartContainerButton, api)
    await btn.async_press()
    check("genau eine benachrichtigung", len(NOTIFIED), 1)
    check("titel nennt den fehler", NOTIFIED[0][0], "Container Restart Failed")
    check("als fehler markiert", btn.result_attributes["last_result_ok"], False)

    print("\n=== pull: erfolg still, fehler laut ===")
    NOTIFIED.clear()
    async def pull_ok(e, c): return True
    api.pull_image_update = pull_ok
    btn = make(b.PullUpdateButton, api)
    await btn.async_press()
    check("erfolg ohne benachrichtigung", NOTIFIED, [])
    check("text am button", btn.result_attributes["last_result"], "Pulled the latest image for plex")

    NOTIFIED.clear()
    async def pull_fail(e, c): raise pa.PortainerError("manifest unknown")
    api.pull_image_update = pull_fail
    btn = make(b.PullUpdateButton, api)
    await btn.async_press()
    check("fehler benachrichtigt", len(NOTIFIED), 1)
    check("grund im text", "manifest unknown" in NOTIFIED[0][1], True)

    print("\n=== prune: erfolg still ===")
    NOTIFIED.clear()
    async def prune_ok(e, dangling_only=True):
        return {"ImagesDeleted":[{"Untagged":"a:1"},{"Deleted":"sha256:a"}], "SpaceReclaimed": 7398000}
    api.prune_images = prune_ok
    btn = make(b.PruneImagesButton, api)
    # vorher ein loeschbares image, nachher keines -> genau eines ist weg
    state = {"n": 0}
    def prunable():
        state["n"] += 1
        return [{"Id": "sha256:a"}] if state["n"] == 1 else []
    btn.coordinator.prunable_images = prunable
    await btn.async_press()
    check("keine benachrichtigung", NOTIFIED, [])
    check("gelöschte aus vorher/nachher", btn.result_attributes["last_images_deleted"], 1)
    check("platz gemeldet", btn.result_attributes["last_space_reclaimed"], "7.1 MB")

asyncio.run(main())
print("\n" + ("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
