"""Zugangsdaten neu konfigurieren, ohne Host, Endpoint oder Entitaeten anzufassen."""
import sys, pathlib, asyncio, types
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

const, cf = load("const", "config_flow")
c = Checker("reconfigure")

STORED = {
    const.CONF_HOST: "https://portainer.example",
    const.CONF_ENDPOINT_ID: 3,
    const.CONF_API_KEY: "alter-key",
    const.CONF_USERNAME: None,
    const.CONF_PASSWORD: "altes-pw",
    const.CONF_VERIFY_SSL: True,
}

seen = []


def fields(schema):
    """Schema keys by name, for the real voluptuous and the harness stub alike."""
    return {m.schema: m for m in getattr(schema, "schema", schema)}


def default_of(marker):
    """None when a field has no default; real voluptuous wraps defaults in callables."""
    d = getattr(marker, "default", None)
    if callable(d):
        return d()
    return None if d is None or type(d).__name__ == "Undefined" else d


def flow(valid=True):
    async def validate(config):
        seen.append(dict(config))
        return valid
    cf._validate_connection = validate
    f = cf.PortainerConfigFlow()
    f.reconfigure_entry = types.SimpleNamespace(data=dict(STORED), options={const.CONF_VERIFY_SSL: False})
    return f


async def main():
    c.section("formular ohne eingabe")
    form = await flow().async_step_reconfigure()
    keys = fields(form["data_schema"])
    c("step", form["step_id"], "reconfigure")
    c("felder", sorted(keys), sorted([const.CONF_API_KEY, const.CONF_USERNAME, const.CONF_PASSWORD]))
    c("host/endpoint nicht im formular", const.CONF_HOST in keys or const.CONF_ENDPOINT_ID in keys, False)
    c("gespeicherter key nicht vorausgefuellt", default_of(keys[const.CONF_API_KEY]), None)
    c("gespeichertes passwort nicht vorausgefuellt", default_of(keys[const.CONF_PASSWORD]), None)
    c("benutzer vorausgefuellt (leer)", default_of(keys[const.CONF_USERNAME]), "")
    c("host als hinweis", form["description_placeholders"], {"host": "https://portainer.example", "endpoint_id": "3"})

    c.section("neuer key")
    f, seen[:] = flow(), []
    result = await f.async_step_reconfigure({const.CONF_API_KEY: "neuer-key", const.CONF_USERNAME: ""})
    c("abgeschlossen", result, {"type": "abort", "reason": "reconfigure_successful"})
    updates = f.updated[1]["data_updates"]
    c("key ersetzt", updates[const.CONF_API_KEY], "neuer-key")
    c("passwort behalten", updates[const.CONF_PASSWORD], "altes-pw")
    c("leerer benutzer wird None", updates[const.CONF_USERNAME], None)
    c("host/endpoint nicht angefasst", const.CONF_HOST in updates or const.CONF_ENDPOINT_ID in updates, False)
    c("pruefung mit neuem key", seen[0][const.CONF_API_KEY], "neuer-key")
    c("pruefung mit verify_ssl aus den optionen", seen[0][const.CONF_VERIFY_SSL], False)
    c("pruefung mit gespeichertem host", seen[0][const.CONF_HOST], "https://portainer.example")

    c.section("leerer key behaelt den alten")
    f = flow()
    await f.async_step_reconfigure({const.CONF_API_KEY: "", const.CONF_PASSWORD: "neues-pw"})
    updates = f.updated[1]["data_updates"]
    c("key behalten", updates[const.CONF_API_KEY], "alter-key")
    c("passwort ersetzt", updates[const.CONF_PASSWORD], "neues-pw")

    c.section("verbindung scheitert")
    f = flow(valid=False)
    result = await f.async_step_reconfigure({const.CONF_API_KEY: "falsch"})
    c("formular erneut", (result["type"], result["step_id"]), ("form", "reconfigure"))
    c("fehler", result["errors"], {"base": "cannot_connect"})
    c("nichts gespeichert", f.updated, None)


asyncio.run(main())
sys.exit(c.done())
