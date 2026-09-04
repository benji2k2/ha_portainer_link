"""Shared Home Assistant stubs so each test can import the integration directly.

Home Assistant is not installed here, and installing it to check a digest parser
would be absurd. Every module the integration imports is faked to the smallest
shape that makes the import work, which also keeps the tests honest: anything a
test exercises is the integration's own code, never Home Assistant's.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import pathlib
import sys
import types

SRC = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ha_portainer_link"

FIXED_NOW = _dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _module(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _install_stubs() -> None:
    aiohttp = _module("aiohttp")
    # Anything the integration touches on aiohttp is only used for typing or as
    # an argument we never inspect, except ClientTimeout which is constructed.
    aiohttp.__getattr__ = lambda name: (
        (lambda **kwargs: ("timeout", kwargs)) if name == "ClientTimeout" else object
    )
    aiohttp.__path__ = []
    _module(
        "aiohttp.client_exceptions",
        ClientConnectorCertificateError=type("ClientConnectorCertificateError", (Exception,), {}),
    )

    ha = _module("homeassistant")
    ha.__path__ = []
    helpers = _module("homeassistant.helpers")
    helpers.__path__ = []
    _module(
        "homeassistant.core",
        HomeAssistant=object,
        ServiceCall=object,
        SupportsResponse=types.SimpleNamespace(OPTIONAL="optional", ONLY="only", NONE="none"),
    )
    _module("homeassistant.config_entries", ConfigEntry=object,
            ConfigFlow=object, OptionsFlow=object)
    _module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    _module("homeassistant.helpers.entity", EntityCategory=types.SimpleNamespace(DIAGNOSTIC="diagnostic"))
    _module("homeassistant.helpers.storage", Store=object)
    # DeviceInfo carries via_device_id since 2026.8; the integration checks for
    # it to decide which key to send, so the stub has to carry it too.
    class DeviceInfo(dict):
        __annotations__ = {"identifiers": set, "name": str, "via_device_id": str}
    _module("homeassistant.helpers.device_registry", DeviceEntry=object,
            DeviceInfo=DeviceInfo, async_get=None, async_entries_for_config_entry=None)
    _module("homeassistant.helpers.entity_registry", async_get=None, async_entries_for_device=None)
    _module(
        "homeassistant.helpers.update_coordinator",
        CoordinatorEntity=CoordinatorEntity,
        DataUpdateCoordinator=type("DataUpdateCoordinator", (), {"__init__": lambda s, *a, **k: None}),
        UpdateFailed=Exception,
    )
    _module("homeassistant.util")
    _module("homeassistant.util.dt", now=lambda: FIXED_NOW)
    _module("homeassistant.components")
    _module("homeassistant.components.button", ButtonEntity=object)
    _module("homeassistant.components.switch", SwitchEntity=object)
    _module(
        "homeassistant.components.binary_sensor",
        BinarySensorEntity=object,
        BinarySensorDeviceClass=types.SimpleNamespace(PROBLEM="problem"),
    )
    _module(
        "homeassistant.components.sensor",
        SensorEntity=object,
        SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement"),
        SensorDeviceClass=types.SimpleNamespace(DATA_SIZE="data_size", TIMESTAMP="timestamp"),
    )
    _module(
        "homeassistant.components.update",
        UpdateEntity=object,
        UpdateEntityFeature=types.SimpleNamespace(INSTALL=1),
    )

    # "from homeassistant.helpers import device_registry" resolves an attribute,
    # not a module path, so each submodule has to be hung on its parent.
    for parent, child in (
        ("homeassistant.helpers", "device_registry"),
        ("homeassistant.helpers", "entity_registry"),
        ("homeassistant.helpers", "storage"),
        ("homeassistant.helpers", "entity"),
        ("homeassistant.helpers", "update_coordinator"),
        ("homeassistant", "helpers"),
        ("homeassistant", "util"),
        ("homeassistant", "config_entries"),
        ("homeassistant", "core"),
        ("homeassistant", "components"),
    ):
        setattr(sys.modules[parent], child, sys.modules[f"{parent}.{child}"])


class HomeAssistantError(Exception):
    """Stand-in for the real one, so tests can assert on it."""


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


def load(*names: str):
    """Import integration modules by name, in the order given."""
    _install_stubs()
    package = types.ModuleType("hpl")
    package.__path__ = [str(SRC)]
    sys.modules["hpl"] = package

    loaded = []
    for name in names:
        spec = importlib.util.spec_from_file_location(f"hpl.{name}", SRC / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"hpl.{name}"] = module
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded[0] if len(loaded) == 1 else tuple(loaded)


class Checker:
    """Collects results so a script can report every failure, not just the first."""

    def __init__(self, title: str) -> None:
        print(f"=== {title} ===")
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def __call__(self, label: str, got, want) -> None:
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  {label}\n       -> {got!r}")
        if not ok:
            print(f"       want={want!r}")
            self.failures.append(label)

    def true(self, label: str, got) -> None:
        self(label, bool(got), True)

    def done(self) -> int:
        if self.failures:
            print(f"\n{len(self.failures)} FEHLGESCHLAGEN: {self.failures}")
            return 1
        print("\nALLE TESTS BESTANDEN")
        return 0
