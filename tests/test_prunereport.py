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
mod("homeassistant.util.dt", now=lambda: _dt.datetime(2026,8,28,tzinfo=_dt.timezone.utc))
mod("homeassistant.helpers.update_coordinator",
    CoordinatorEntity=type("CE",(),{"__init__":lambda s,c: setattr(s,"coordinator",c)}),
    DataUpdateCoordinator=object, UpdateFailed=Exception)
mod("homeassistant.components"); mod("homeassistant.components.button", ButtonEntity=object)
pkg = types.ModuleType("hpl"); pkg.__path__=[str(SRC)]; sys.modules["hpl"]=pkg
def load(n):
    sp = importlib.util.spec_from_file_location(f"hpl.{n}", SRC/f"{n}.py")
    m = importlib.util.module_from_spec(sp); sys.modules[f"hpl.{n}"]=m; sp.loader.exec_module(m); return m
load("const"); load("entity"); load("portainer_api"); b = load("button")
b._send_notification = lambda *a, **k: asyncio.sleep(0)

fails=[]
def check(l, got, want):
    ok = got==want
    print(f"{'PASS' if ok else 'FAIL'}  {l}\n       -> {got!r}")
    if not ok: print(f"       want={want!r}"); fails.append(l)

def button(before_imgs, after_imgs, response):
    e = b.PruneImagesButton.__new__(b.PruneImagesButton)
    e._last_result = {}; e.hass = None; e.endpoint_id = 2
    e.async_write_ha_state = lambda: None
    state = {"calls": 0}
    def prunable():
        state["calls"] += 1
        return before_imgs if state["calls"] == 1 else after_imgs
    async def prune(eid, dangling_only=True): return response
    e.coordinator = types.SimpleNamespace(
        api=types.SimpleNamespace(prune_images=prune), endpoint_id=2,
        async_refresh=lambda: asyncio.sleep(0),
        is_prune_all_unused=lambda: False,
        prunable_images=prunable)
    return e

async def main():
    print("=== dein fall: 12 Deleted-eintraege, aber nur 1 image weg ===")
    resp = {"ImagesDeleted": [{"Untagged":"alt:tag"}] + [{"Deleted": f"sha256:{i:02d}"} for i in range(12)],
            "SpaceReclaimed": 175_900_000}
    btn = button([{"Id":"a"}], [], resp)      # vorher 1 loeschbar, nachher 0
    await btn.async_press()
    r = btn.result_attributes
    check("meldet 1 image, nicht 12", r["last_images_deleted"], 1)
    check("layer-zahl nur als diagnose", r["last_freed_content_ids"], 12)
    check("text nennt 1", r["last_result"].startswith("Removed 1 dangling image(s)"), True)
    check("tag-referenz weiter genannt", "1 tag reference(s)" in r["last_result"], True)

    print("\n=== mehrere images ===")
    btn = button([{"Id":"a"},{"Id":"b"},{"Id":"c"}], [{"Id":"c"}],
                 {"ImagesDeleted":[{"Deleted":f"sha256:{i}"} for i in range(30)], "SpaceReclaimed": 1})
    await btn.async_press()
    check("2 von 3 weg", btn.result_attributes["last_images_deleted"], 2)
    check("rest wird erklaert", "1 image(s) remain deletable" in btn.result_attributes["last_result"], True)

    print("\n=== nichts geloescht, nur ein tag entfernt ===")
    btn = button([{"Id":"a"}], [{"Id":"a"}], {"ImagesDeleted":[{"Untagged":"x:1"}], "SpaceReclaimed": 0})
    await btn.async_press()
    check("keine image-loeschung behauptet", btn.result_attributes["last_images_deleted"], 0)
    check("meldet die tag-referenz", btn.result_attributes["last_result"].startswith("Dropped 1 tag reference(s)"), True)

asyncio.run(main())
print("\n" + ("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
