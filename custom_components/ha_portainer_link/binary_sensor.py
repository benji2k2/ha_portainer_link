"""Binary sensor platform for HA Portainer Link."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .const import DATA_COORDINATOR, DOMAIN
from .entity import HEALTH_UNHEALTHY, BaseContainerEntity, container_health, container_name


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up Portainer binary sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    update_sensors_enabled = coordinator.is_update_sensors_enabled()

    entities: list[BinarySensorEntity] = []
    for container_id, container in coordinator.containers.items():
        name = container_name(container)
        stack_info = coordinator.get_container_stack_info(container_id) or {}
        if coordinator.is_healthcheck_sensors_enabled():
            entities.append(
                ContainerHealthProblemSensor(coordinator, entry.entry_id, container_id, name, stack_info)
            )
        if update_sensors_enabled:
            entities.append(
                ContainerUpdateAvailableSensor(coordinator, entry.entry_id, container_id, name, stack_info)
            )

    async_add_entities(entities)


class ContainerUpdateAvailableSensor(BaseContainerEntity, BinarySensorEntity):
    """Binary sensor representing whether a container image update is available."""

    entity_suffix = "update_available"
    _attr_icon = "mdi:update"

    def __init__(self, coordinator, entry_id, container_id, name, stack_info) -> None:
        super().__init__(coordinator, entry_id, container_id, name, stack_info)
        self._attr_name = f"{name} Update Available"

    @property
    def is_on(self) -> bool:
        return self.coordinator.get_update_availability(self.current_container_id)


class ContainerHealthProblemSensor(BaseContainerEntity, BinarySensorEntity):
    """Problem sensor that only reports on when a container's healthcheck fails.

    Docker distinguishes "no healthcheck defined" from an actual failure, so a
    container without a HEALTHCHECK, one that is still starting up, and a
    stopped container all leave this sensor off.
    """

    entity_suffix = "health_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry_id, container_id, name, stack_info) -> None:
        super().__init__(coordinator, entry_id, container_id, name, stack_info)
        self._attr_name = f"{name} Health Problem"

    @property
    def is_on(self) -> bool:
        return container_health(self.container) == HEALTH_UNHEALTHY

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"health": container_health(self.container)}
