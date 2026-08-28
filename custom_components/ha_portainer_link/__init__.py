"""HA Portainer Link integration setup."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_API_KEY,
    CONF_ENDPOINT_ID,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DATA_API,
    DATA_COORDINATOR,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .coordinator import PortainerDataUpdateCoordinator
from .entity import (
    count_pruned,
    format_bytes,
    container_device_id,
    container_name,
    container_unique_id,
    hub_device_id,
    sanitize,
    stable_container_key,
    stack_device_id,
)
from .portainer_api import PortainerAPI

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "switch", "button", "update"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up HA Portainer Link services."""
    hass.data.setdefault(DOMAIN, {})
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Portainer Link from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    config = {**DEFAULT_OPTIONS, **entry.data, **entry.options}
    api = PortainerAPI(
        config[CONF_HOST],
        config.get(CONF_USERNAME),
        config.get(CONF_PASSWORD),
        config.get(CONF_API_KEY),
        ssl_verify=bool(config.get(CONF_VERIFY_SSL, DEFAULT_OPTIONS[CONF_VERIFY_SSL])),
    )

    try:
        await api.initialize()
        coordinator = PortainerDataUpdateCoordinator(
            hass,
            api,
            int(config[CONF_ENDPOINT_ID]),
            config,
        )
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await api.close()
        raise

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API: api,
        DATA_COORDINATOR: coordinator,
    }
    _migrate_entity_unique_ids(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _cleanup_stale_devices(hass, entry, coordinator)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry platforms and close resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        coordinator = data.get(DATA_COORDINATOR)
        if coordinator:
            await coordinator.async_shutdown()
        elif data.get(DATA_API):
            await data[DATA_API].close()
    has_entries = any(
        key != "_services_registered" and isinstance(value, dict)
        for key, value in hass.data.get(DOMAIN, {}).items()
    )
    if unload_ok and not has_entries:
        _unregister_services(hass)
    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    registered = hass.data[DOMAIN].setdefault("_services_registered", False)
    if registered:
        return

    async def handle_reload(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

    async def handle_refresh(call: ServiceCall) -> None:
        for entry_data in list(hass.data.get(DOMAIN, {}).values()):
            if isinstance(entry_data, dict) and (coordinator := entry_data.get(DATA_COORDINATOR)):
                await coordinator.async_request_refresh()

    async def handle_prune_images(call: ServiceCall) -> dict:
        """Prune images and return the outcome to the caller.

        A button entity cannot report anything back to the frontend, so this
        service exists for anyone who wants the result in front of them the
        moment the action finishes instead of in a notification.
        """
        results = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not isinstance(data, dict):
                continue
            coordinator = data.get(DATA_COORDINATOR)
            if coordinator is None:
                continue
            all_unused = call.data.get("all_unused", coordinator.is_prune_all_unused())
            before = coordinator.prunable_images()
            outcome = {
                "environment": coordinator.endpoint_name or entry.title,
                "scope": "all unused images" if all_unused else "dangling images only",
                "deletable_before": len(before),
            }
            try:
                result = await coordinator.api.prune_images(
                    coordinator.endpoint_id, dangling_only=not all_unused
                )
            except Exception as err:
                outcome["error"] = str(err)
                results.append(outcome)
                continue

            deleted, untagged = count_pruned(result or {})
            reclaimed = int((result or {}).get("SpaceReclaimed") or 0)
            await coordinator.async_refresh()
            remaining = coordinator.prunable_images()
            outcome.update(
                {
                    "images_deleted": deleted,
                    "tags_dropped": untagged,
                    "space_reclaimed": format_bytes(reclaimed),
                    "space_reclaimed_bytes": reclaimed,
                    "deletable_after": len(remaining),
                    "still_deletable": [
                        ", ".join(i.get("RepoTags") or []) or str(i.get("Id", ""))[:19]
                        for i in remaining
                    ][:20],
                }
            )
            results.append(outcome)
        return {"results": results}

    hass.services.async_register(DOMAIN, "reload", handle_reload)
    hass.services.async_register(DOMAIN, "refresh", handle_refresh)
    hass.services.async_register(
        DOMAIN, "prune_images", handle_prune_images, supports_response=SupportsResponse.OPTIONAL
    )
    hass.data[DOMAIN]["_services_registered"] = True


def _unregister_services(hass: HomeAssistant) -> None:
    """Unregister integration services."""
    for service in ("reload", "refresh", "prune_images"):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    hass.data.setdefault(DOMAIN, {})["_services_registered"] = False


def _active_device_ids(
    entry: ConfigEntry,
    coordinator: PortainerDataUpdateCoordinator,
) -> set[str]:
    """Return every device identifier the current options actually produce.

    Toggling an option (stack view, instance device) moves entities onto
    differently identified devices. Anything not in this set is left over from
    a previous configuration.
    """
    base_url = coordinator.api.base_url
    endpoint_id = coordinator.endpoint_id
    active: set[str] = set()

    if coordinator.is_instance_device_enabled():
        active.add(hub_device_id(entry.entry_id, endpoint_id, base_url))

    for container_id, container in coordinator.containers.items():
        name = container_name(container)
        stack_info = coordinator.get_container_stack_info(container_id) or {}
        if stack_info.get("is_stack_container"):
            active.add(
                stack_device_id(entry.entry_id, endpoint_id, base_url, stack_info["stack_name"])
            )
        else:
            active.add(
                container_device_id(
                    entry.entry_id, endpoint_id, base_url, stable_container_key(name, stack_info)
                )
            )

    if coordinator.is_stack_view_enabled() and coordinator.is_stack_buttons_enabled():
        for stack_name in coordinator.stack_names():
            active.add(stack_device_id(entry.entry_id, endpoint_id, base_url, stack_name))

    return active


def _cleanup_stale_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: PortainerDataUpdateCoordinator,
) -> None:
    """Drop devices this entry no longer produces, plus any left without entities.

    Home Assistant keeps registry entries for entities an integration stops
    providing, so a device orphaned by an option change never becomes empty on
    its own and would otherwise linger as "unavailable" forever.
    """
    if not coordinator.containers:
        # An empty fetch is not proof the containers are gone; never clean up on it.
        return

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    active = _active_device_ids(entry, coordinator)

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        device_ids = {value for domain, value in device.identifiers if domain == DOMAIN}
        if device_ids and not (device_ids & active):
            _LOGGER.debug("Removing stale device %s (%s)", device.name, device_ids)
            device_registry.async_remove_device(device.id)
            continue
        if not er.async_entries_for_device(entity_registry, device.id):
            device_registry.async_remove_device(device.id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Let users delete devices the current configuration no longer produces.

    Without this function Home Assistant hides the delete button entirely, which
    is why leftover stack devices could not be removed from the UI.
    """
    data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    coordinator = data.get(DATA_COORDINATOR) if isinstance(data, dict) else None
    if coordinator is None:
        return True

    device_ids = {value for domain, value in device_entry.identifiers if domain == DOMAIN}
    return not (device_ids & _active_device_ids(config_entry, coordinator))


def _migrate_entity_unique_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: PortainerDataUpdateCoordinator,
) -> None:
    """Migrate old container-id-based unique IDs to stable keys."""
    registry = er.async_get(hass)
    suffix_domains = {
        "status": "sensor",
        "cpu_usage": "sensor",
        "memory_usage": "sensor",
        "uptime": "sensor",
        "image": "sensor",
        "current_version": "sensor",
        "available_version": "sensor",
        "current_digest": "sensor",
        "available_digest": "sensor",
        "update_available": "binary_sensor",
        "switch": "switch",
        "restart": "button",
        "pull_update": "button",
        "update": "update",
    }
    for container_id, container in coordinator.containers.items():
        name = container_name(container)
        stack_info = coordinator.get_container_stack_info(container_id) or {}
        stable_key = stable_container_key(name, stack_info)
        old_stack_key = None
        if stack_info.get("is_stack_container"):
            old_stack_key = sanitize(
                f"{stack_info.get('stack_name')}_{stack_info.get('service_name') or name}"
            )
        for suffix, domain in suffix_domains.items():
            new_uid = container_unique_id(entry.entry_id, coordinator.endpoint_id, stable_key, suffix)
            old_uids = [
                f"entry_{entry.entry_id}_endpoint_{coordinator.endpoint_id}_{container_id}_{suffix}",
            ]
            if old_stack_key:
                old_uids.append(
                    f"entry_{entry.entry_id}_endpoint_{coordinator.endpoint_id}_{old_stack_key}_{suffix}"
                )
            for old_uid in old_uids:
                entity_id = registry.async_get_entity_id(domain, DOMAIN, old_uid)
                if entity_id and old_uid != new_uid and not registry.async_get_entity_id(domain, DOMAIN, new_uid):
                    registry.async_update_entity(entity_id, new_unique_id=new_uid)
