"""Shared entity helpers for HA Portainer Link."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from urllib.parse import urlparse

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

# Home Assistant 2026.8 replaced the (domain, identifier) `via_device` tuple with
# `via_device_id`, the registry id of the parent, and will drop the old key in
# 2027.8. Unknown device_info keys raise and stop the entity from being added, so
# which one to send has to be decided from what this Home Assistant knows.
SUPPORTS_VIA_DEVICE_ID = "via_device_id" in getattr(dr.DeviceInfo, "__annotations__", {})

HEALTH_STATES = ("healthy", "unhealthy", "starting")
HEALTH_UNHEALTHY = "unhealthy"

# Matches the health suffix docker appends to a running container's status
# string: "(healthy)", "(unhealthy)" or "(health: starting)". "(Paused)" and
# plain "Up 2 hours" deliberately do not match.
_HEALTH_PATTERN = re.compile(r"\((?:health: )?(healthy|unhealthy|starting)\)")


def sanitize(value: Any) -> str:
    """Return a stable identifier-safe string."""
    text = str(value or "unknown").strip().strip("/")
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.lower() or "unknown"


def host_display_name(base_url: str) -> str:
    """Return a concise display name for the Portainer host."""
    parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    host = parsed.hostname or base_url
    return host.split(".")[0] if not host.replace(".", "").isdigit() else host


def host_key(base_url: str) -> str:
    """Return a stable host key for identifiers."""
    parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    host = parsed.netloc or parsed.path or base_url
    return sanitize(host)


def container_name(container: dict[str, Any]) -> str:
    """Extract the first Docker container name."""
    names = container.get("Names") or []
    if names:
        return str(names[0]).strip("/")
    return container.get("Name", "unknown").strip("/")


def is_container_running(container: dict[str, Any] | None) -> bool:
    """Return whether a container list/inspect payload represents a running container."""
    if not container:
        return False
    state = container.get("State")
    if isinstance(state, dict):
        return bool(state.get("Running")) or state.get("Status") == "running"
    return str(state or "").lower() == "running"


def count_pruned(result: dict) -> tuple[int, int]:
    """Return the raw (deleted, untagged) entry counts of a prune response.

    Neither number is a count of images. The daemon reports every removed tag as
    its own "Untagged" item and every freed content id as a "Deleted" one, and
    deleting a single image frees its config plus each layer that no other image
    still holds - the same many-lined output `docker rmi` prints. One image with
    eleven layers therefore reports twelve deletions.

    These are useful as diagnostics only. How many *images* went is derived from
    how many were deletable before and after, not from this response.
    """
    items = result.get("ImagesDeleted") or []
    deleted = sum(1 for item in items if isinstance(item, dict) and item.get("Deleted"))
    untagged = sum(1 for item in items if isinstance(item, dict) and item.get("Untagged"))
    return deleted, untagged


def format_bytes(value: int) -> str:
    """Return a compact human-readable size."""
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def parse_docker_time(value: Any) -> dt.datetime | None:
    """Parse a docker timestamp into an aware datetime.

    Docker reports nanoseconds, which fromisoformat rejects - it accepts at most
    microseconds - so the fractional part is truncated to six digits. The zero
    value docker uses for "never" is treated as absent.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.startswith("0001-"):
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def short_digest(value: str | None, length: int = 12) -> str | None:
    """Return a readable digest: no algorithm prefix, truncated.

    Comparisons keep using the full value; this is display only, so that a
    sensor shows "93c91251e746" instead of 71 characters of hex.
    """
    if not value:
        return None
    text = str(value)
    if text.startswith("unknown"):
        return text
    _, _, hexpart = text.rpartition(":")
    return (hexpart or text)[:length]


def container_health(container: dict[str, Any] | None) -> str | None:
    """Return the docker healthcheck state, or None when no healthcheck applies.

    Docker only renders health into the container list status string while the
    container runs, e.g. "Up 2 hours (healthy)" or "Up 5 seconds (health: starting)".
    A container without a HEALTHCHECK stays "Up 2 hours", and the inspect payload
    reports "none" for that same case, so both map to None here.
    """
    if not container:
        return None
    state = container.get("State")
    if isinstance(state, dict):
        status = (state.get("Health") or {}).get("Status")
        return status if status in HEALTH_STATES else None
    match = _HEALTH_PATTERN.search(str(container.get("Status") or ""))
    return match.group(1) if match else None


def stack_info_from_container(container: dict[str, Any]) -> dict[str, Any]:
    """Extract compose stack metadata from container labels."""
    labels = container.get("Labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    stack_name = labels.get("com.docker.compose.project")
    service_name = labels.get("com.docker.compose.service")
    container_number = labels.get("com.docker.compose.container-number")
    return {
        "stack_name": stack_name,
        "service_name": service_name,
        "container_number": container_number,
        "is_stack_container": bool(stack_name),
    }


def stable_container_key(container_name_value: str, stack_info: dict[str, Any]) -> str:
    """Return a stable key that survives Docker container ID changes."""
    if stack_info.get("is_stack_container"):
        parts = [
            stack_info.get("stack_name"),
            stack_info.get("service_name") or container_name_value,
            stack_info.get("container_number") or container_name_value,
        ]
        return "stack_" + "_".join(sanitize(part) for part in parts if part)
    return "container_" + sanitize(container_name_value)


def stack_key(stack_name: str) -> str:
    """Return a stable stack key."""
    return "stack_" + sanitize(stack_name)


def container_unique_id(entry_id: str, endpoint_id: int, stable_key: str, suffix: str) -> str:
    """Return a stable unique_id for a container entity."""
    return f"entry_{entry_id}_endpoint_{endpoint_id}_{sanitize(stable_key)}_{sanitize(suffix)}"


def hub_unique_id(entry_id: str, endpoint_id: int, suffix: str) -> str:
    """Return a stable unique_id for an instance-level entity."""
    return f"entry_{entry_id}_endpoint_{endpoint_id}_instance_{sanitize(suffix)}"


def hub_device_id(entry_id: str, endpoint_id: int, base_url: str) -> str:
    """Return the device identifier for the Portainer instance itself."""
    return f"{entry_id}_{endpoint_id}_{host_key(base_url)}"


def container_device_id(entry_id: str, endpoint_id: int, base_url: str, stable_key: str) -> str:
    """Return the device identifier for a standalone container."""
    return f"{entry_id}_{endpoint_id}_{host_key(base_url)}_{sanitize(stable_key)}"


def stack_device_id(entry_id: str, endpoint_id: int, base_url: str, name: str) -> str:
    """Return the device identifier for a Docker stack."""
    return f"{entry_id}_{endpoint_id}_{host_key(base_url)}_{stack_key(name)}"


def stack_unique_id(entry_id: str, endpoint_id: int, name: str, suffix: str) -> str:
    """Return a stable unique_id for a stack entity."""
    return f"entry_{entry_id}_endpoint_{endpoint_id}_{stack_key(name)}_{sanitize(suffix)}"


def container_device_info(
    entry_id: str,
    endpoint_id: int,
    base_url: str,
    stable_key: str,
    name: str,
    container_id: str | None,
    via_hub: bool = False,
    instance_name: str | None = None,
    hub_registry_id: str | None = None,
) -> dict[str, Any]:
    """Return Home Assistant device info for a standalone container.

    The suffix disambiguates containers of the same name across environments.
    The Portainer environment name says more than the URL host it falls back to,
    which is often just "portainer".
    """
    host_name = instance_name or host_display_name(base_url)
    info = {
        "identifiers": {(DOMAIN, container_device_id(entry_id, endpoint_id, base_url, stable_key))},
        "name": f"{name} ({host_name})",
        "manufacturer": "Docker via Portainer",
        "model": "Docker Container",
        "configuration_url": (
            f"{base_url}/#!/containers/{container_id}/details" if container_id else base_url
        ),
    }
    _link_to_hub(info, entry_id, endpoint_id, base_url, via_hub, hub_registry_id)
    return info


def stack_device_info(
    entry_id: str,
    endpoint_id: int,
    base_url: str,
    name: str,
    via_hub: bool = False,
    instance_name: str | None = None,
    hub_registry_id: str | None = None,
) -> dict[str, Any]:
    """Return Home Assistant device info for a Docker stack."""
    host_name = instance_name or host_display_name(base_url)
    info = {
        "identifiers": {(DOMAIN, stack_device_id(entry_id, endpoint_id, base_url, name))},
        "name": f"Stack: {name} ({host_name})",
        "manufacturer": "Docker via Portainer",
        "model": "Docker Stack",
        "configuration_url": f"{base_url}/#!/stacks/{name}",
    }
    _link_to_hub(info, entry_id, endpoint_id, base_url, via_hub, hub_registry_id)
    return info


def _link_to_hub(
    info: dict[str, Any],
    entry_id: str,
    endpoint_id: int,
    base_url: str,
    via_hub: bool,
    hub_registry_id: str | None,
) -> None:
    """Point a device at the instance device it belongs to.

    The modern key needs the parent's registry id, which only exists once that
    device is registered - hence the setup registering it up front. Without one,
    the link is left out rather than guessed: an unresolvable id is silently
    dropped by the registry anyway.
    """
    if not via_hub:
        return
    if SUPPORTS_VIA_DEVICE_ID:
        if hub_registry_id:
            info["via_device_id"] = hub_registry_id
        return
    info["via_device"] = (DOMAIN, hub_device_id(entry_id, endpoint_id, base_url))


def hub_device_info(
    entry_id: str,
    endpoint_id: int,
    base_url: str,
    endpoint_name: str | None = None,
    docker_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return device info for the Portainer environment that owns the containers.

    Docker daemon details are near-static, so they belong on the device rather
    than in sensors. Every field is optional: an endpoint that never answered
    still yields a usable device.
    """
    info = docker_info or {}
    device: dict[str, Any] = {
        "identifiers": {(DOMAIN, hub_device_id(entry_id, endpoint_id, base_url))},
        "name": endpoint_name or host_display_name(base_url),
        "manufacturer": "Portainer",
        "model": info.get("OperatingSystem") or "Portainer Environment",
        "configuration_url": f"{base_url}/#!/{endpoint_id}/docker/dashboard",
    }
    if server_version := info.get("ServerVersion"):
        device["sw_version"] = f"Docker {server_version}"
    if architecture := info.get("Architecture"):
        device["hw_version"] = str(architecture)
    return device


class BasePortainerEntity(CoordinatorEntity):
    """Base class for coordinator-backed Portainer entities.

    Entity names describe only the property. Home Assistant prefixes the device
    name for display and for the generated entity id, so the container name is
    not repeated inside every entity name.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self.entry_id = entry_id
        self.endpoint_id = coordinator.endpoint_id

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success)


class BaseContainerEntity(BasePortainerEntity):
    """Base class for container-backed entities."""

    entity_suffix = "entity"

    def __init__(
        self,
        coordinator,
        entry_id: str,
        container_id: str,
        name: str,
        stack_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry_id)
        self.container_id = container_id
        self.container_name = name
        self.stack_info = stack_info
        self.stable_key = stable_container_key(name, stack_info)
        self._attr_unique_id = container_unique_id(
            entry_id,
            coordinator.endpoint_id,
            self.stable_key,
            self.entity_suffix,
        )

    @property
    def current_container_id(self) -> str | None:
        current = self.coordinator.get_container_by_stable_id(self.stable_key)
        if current:
            self.container_id = current
            return current
        return self.container_id if self.container_id in self.coordinator.containers else None

    @property
    def container(self) -> dict[str, Any] | None:
        container_id = self.current_container_id
        return self.coordinator.get_container(container_id) if container_id else None

    @property
    def available(self) -> bool:
        return super().available and self.container is not None

    @property
    def device_info(self) -> dict[str, Any]:
        via_hub = self.coordinator.is_instance_device_enabled()
        if self.stack_info.get("is_stack_container"):
            return stack_device_info(
                self.entry_id,
                self.endpoint_id,
                self.coordinator.api.base_url,
                self.stack_info.get("stack_name") or self.container_name,
                via_hub=via_hub,
                instance_name=self.coordinator.endpoint_name,
                hub_registry_id=self.coordinator.hub_device_id,
            )
        return container_device_info(
            self.entry_id,
            self.endpoint_id,
            self.coordinator.api.base_url,
            self.stable_key,
            self.container_name,
            self.current_container_id,
            via_hub=via_hub,
            instance_name=self.coordinator.endpoint_name,
            hub_registry_id=self.coordinator.hub_device_id,
        )


class BaseStackEntity(BasePortainerEntity):
    """Base class for stack-backed entities."""

    entity_suffix = "stack_entity"

    def __init__(self, coordinator, entry_id: str, stack_name_value: str) -> None:
        super().__init__(coordinator, entry_id)
        self.stack_name = stack_name_value
        self._attr_unique_id = stack_unique_id(
            entry_id,
            coordinator.endpoint_id,
            stack_name_value,
            self.entity_suffix,
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.get_stack_containers(self.stack_name))

    @property
    def device_info(self) -> dict[str, Any]:
        return stack_device_info(
            self.entry_id,
            self.endpoint_id,
            self.coordinator.api.base_url,
            self.stack_name,
            via_hub=self.coordinator.is_instance_device_enabled(),
            instance_name=self.coordinator.endpoint_name,
            hub_registry_id=self.coordinator.hub_device_id,
        )


class BaseHubEntity(BasePortainerEntity):
    """Base class for entities that describe the Portainer instance as a whole."""

    entity_suffix = "instance_entity"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = hub_unique_id(entry_id, coordinator.endpoint_id, self.entity_suffix)

    @property
    def device_info(self) -> dict[str, Any]:
        return hub_device_info(
            self.entry_id,
            self.endpoint_id,
            self.coordinator.api.base_url,
            self.coordinator.endpoint_name,
            self.coordinator.docker_info,
        )
