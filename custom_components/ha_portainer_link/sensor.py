"""Sensor platform for HA Portainer Link."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .const import DATA_COORDINATOR, DOMAIN
from .entity import (
    HEALTH_UNHEALTHY,
    BaseContainerEntity,
    BaseHubEntity,
    container_health,
    container_name,
    is_container_running,
    short_digest,
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up Portainer sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list[SensorEntity] = []

    if coordinator.is_instance_device_enabled():
        entities.extend(
            [
                InstanceContainersSensor(coordinator, entry.entry_id),
                InstanceRunningContainersSensor(coordinator, entry.entry_id),
                InstanceStoppedContainersSensor(coordinator, entry.entry_id),
                InstanceUnhealthyContainersSensor(coordinator, entry.entry_id),
                InstanceStacksSensor(coordinator, entry.entry_id),
                InstanceReclaimableImagesSensor(coordinator, entry.entry_id),
                InstanceProcessorsSensor(coordinator, entry.entry_id),
                InstanceMemorySensor(coordinator, entry.entry_id),
            ]
        )
        if coordinator.is_update_sensors_enabled():
            entities.append(InstanceUpdatesAvailableSensor(coordinator, entry.entry_id))

    for container_id, container in coordinator.containers.items():
        name = container_name(container)
        stack_info = coordinator.get_container_stack_info(container_id) or {}
        entities.append(ContainerStatusSensor(coordinator, entry.entry_id, container_id, name, stack_info))
        entities.append(ContainerImageSensor(coordinator, entry.entry_id, container_id, name, stack_info))
        if coordinator.is_healthcheck_sensors_enabled():
            entities.append(ContainerHealthSensor(coordinator, entry.entry_id, container_id, name, stack_info))
        if coordinator.is_resource_sensors_enabled():
            entities.extend(
                [
                    ContainerCPUSensor(coordinator, entry.entry_id, container_id, name, stack_info),
                    ContainerMemorySensor(coordinator, entry.entry_id, container_id, name, stack_info),
                    ContainerUptimeSensor(coordinator, entry.entry_id, container_id, name, stack_info),
                ]
            )
        if coordinator.is_version_sensors_enabled():
            entities.extend(
                [
                    ContainerCurrentVersionSensor(coordinator, entry.entry_id, container_id, name, stack_info),
                    ContainerAvailableVersionSensor(coordinator, entry.entry_id, container_id, name, stack_info),
                    ContainerCurrentDigestSensor(coordinator, entry.entry_id, container_id, name, stack_info),
                    ContainerAvailableDigestSensor(coordinator, entry.entry_id, container_id, name, stack_info),
                ]
            )

    async_add_entities(entities)


class PortainerContainerSensor(BaseContainerEntity, SensorEntity):
    """Base class for container sensors."""

    icon_name = "mdi:docker"

    def __init__(self, coordinator, entry_id: str, container_id: str, name: str, stack_info: dict[str, Any]) -> None:
        super().__init__(coordinator, entry_id, container_id, name, stack_info)
        self._attr_name = self.label
        self._attr_icon = self.icon_name

    @property
    def native_value(self):
        return None

    def metric(self, key: str):
        container_id = self.current_container_id
        return self.coordinator.metrics.get(container_id or "", {}).get(key)

    def image_value(self, key: str):
        container_id = self.current_container_id
        return self.coordinator.image_data.get(container_id or "", {}).get(key)


class ContainerStatusSensor(PortainerContainerSensor):
    entity_suffix = "status"
    label = "Status"

    @property
    def native_value(self):
        container = self.container
        if not container:
            return None
        state = container.get("State")
        if isinstance(state, dict):
            return state.get("Status") or ("running" if state.get("Running") else "stopped")
        return state or "unknown"


class ContainerHealthSensor(PortainerContainerSensor):
    entity_suffix = "health"
    label = "Health"
    icon_name = "mdi:heart-pulse"

    @property
    def native_value(self):
        """Return healthy/unhealthy/starting, or None if the container has no healthcheck."""
        return container_health(self.container)


class ContainerCPUSensor(PortainerContainerSensor):
    entity_suffix = "cpu_usage"
    label = "CPU Usage"
    icon_name = "mdi:cpu-64-bit"
    _attr_native_unit_of_measurement = "%"

    @property
    def native_value(self):
        return self.metric("cpu_percent")


class ContainerMemorySensor(PortainerContainerSensor):
    entity_suffix = "memory_usage"
    label = "Memory Usage"
    icon_name = "mdi:memory"
    _attr_native_unit_of_measurement = "MB"

    @property
    def native_value(self):
        return self.metric("memory_mb")


class ContainerUptimeSensor(PortainerContainerSensor):
    entity_suffix = "uptime"
    label = "Uptime"
    icon_name = "mdi:clock-outline"

    @property
    def native_value(self):
        if not is_container_running(self.container):
            return "Not running"
        uptime_s = self.metric("uptime_s")
        if uptime_s is None:
            return None
        days, remainder = divmod(int(uptime_s), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


class ContainerImageSensor(PortainerContainerSensor):
    entity_suffix = "image"
    label = "Image"
    icon_name = "mdi:docker"

    @property
    def native_value(self):
        container = self.container
        if not container:
            return None
        return self.image_value("image_name") or container.get("Image")


class ContainerCurrentVersionSensor(PortainerContainerSensor):
    entity_suffix = "current_version"
    label = "Current Version"
    icon_name = "mdi:tag-text"

    @property
    def native_value(self):
        return self.image_value("current_version")


class ContainerAvailableVersionSensor(PortainerContainerSensor):
    entity_suffix = "available_version"
    label = "Available Version"
    icon_name = "mdi:tag-plus"

    @property
    def native_value(self):
        return self.image_value("available_version")


class DigestSensor(PortainerContainerSensor):
    """Digest sensor showing a shortened value, with the full one as attribute."""

    icon_name = "mdi:fingerprint"
    digest_key = ""
    config_digest_key = ""

    @property
    def native_value(self):
        return short_digest(self.image_value(self.digest_key))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "digest": self.image_value(self.digest_key),
            "image_id": self.image_value(self.config_digest_key),
        }


class ContainerCurrentDigestSensor(DigestSensor):
    entity_suffix = "current_digest"
    label = "Current Digest"
    digest_key = "current_digest"
    config_digest_key = "current_config_digest"


class ContainerAvailableDigestSensor(DigestSensor):
    entity_suffix = "available_digest"
    label = "Available Digest"
    digest_key = "available_digest"
    config_digest_key = "available_config_digest"


class InstanceUnhealthyContainersSensor(BaseHubEntity, SensorEntity):
    """Number of containers on this Portainer environment reporting unhealthy.

    Only containers that define a HEALTHCHECK can ever be counted here, so a
    value of 0 means "nothing is failing", not "everything was checked".
    """

    entity_suffix = "unhealthy_containers"
    _attr_name = "Unhealthy containers"
    _attr_icon = "mdi:heart-broken"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _unhealthy_names(self) -> list[str]:
        return sorted(
            container_name(container)
            for container in self.coordinator.containers.values()
            if container_health(container) == HEALTH_UNHEALTHY
        )

    @property
    def native_value(self) -> int:
        return len(self._unhealthy_names())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        monitored = sum(
            1 for container in self.coordinator.containers.values() if container_health(container) is not None
        )
        return {
            "unhealthy_containers": self._unhealthy_names(),
            "containers_with_healthcheck": monitored,
            "containers_total": len(self.coordinator.containers),
        }


class InstanceCountSensor(BaseHubEntity, SensorEntity):
    """Base class for instance-level counts derived from coordinator data."""

    _attr_state_class = SensorStateClass.MEASUREMENT


class InstanceContainersSensor(InstanceCountSensor):
    entity_suffix = "containers"
    _attr_name = "Containers"
    _attr_icon = "mdi:docker"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.containers)


class InstanceRunningContainersSensor(InstanceCountSensor):
    entity_suffix = "running_containers"
    _attr_name = "Running containers"
    _attr_icon = "mdi:play-circle-outline"

    @property
    def native_value(self) -> int:
        return self.coordinator.running_container_count()


class InstanceStoppedContainersSensor(InstanceCountSensor):
    entity_suffix = "stopped_containers"
    _attr_name = "Stopped containers"
    _attr_icon = "mdi:stop-circle-outline"

    @property
    def native_value(self) -> int:
        return self.coordinator.stopped_container_count()


class InstanceStacksSensor(InstanceCountSensor):
    entity_suffix = "stacks"
    _attr_name = "Stacks"
    _attr_icon = "mdi:layers-outline"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.stack_names())


class InstanceUpdatesAvailableSensor(InstanceCountSensor):
    entity_suffix = "updates_available"
    _attr_name = "Updates available"
    _attr_icon = "mdi:package-up"

    @property
    def native_value(self) -> int:
        return self.coordinator.update_available_count()


class InstanceProcessorsSensor(BaseHubEntity, SensorEntity):
    """CPU count reported by the docker daemon."""

    entity_suffix = "processors"
    _attr_name = "Processors"
    _attr_icon = "mdi:cpu-64-bit"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self.coordinator.docker_info.get("NCPU")


class InstanceMemorySensor(BaseHubEntity, SensorEntity):
    """Total memory reported by the docker daemon."""

    entity_suffix = "memory_total"
    _attr_name = "Memory total"
    _attr_icon = "mdi:memory"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = "GB"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        total = self.coordinator.docker_info.get("MemTotal")
        if not isinstance(total, (int, float)):
            return None
        return round(total / (1024 ** 3), 2)


def _image_label(image: dict[str, Any]) -> str:
    """Name an image for display: its tags, or a short id when untagged."""
    tags = [t for t in (image.get("RepoTags") or []) if t and t != "<none>:<none>"]
    if tags:
        return ", ".join(tags)
    return f"<none> ({short_digest(image.get('Id'))})"


class InstanceReclaimableImagesSensor(BaseHubEntity, SensorEntity):
    """How many images the prune button would remove right now.

    Follows the button's configured scope, and excludes images a container still
    references, since docker refuses to remove those.
    """

    entity_suffix = "reclaimable_images"
    _attr_name = "Deletable images"
    _attr_icon = "mdi:image-off-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        return len(self.coordinator.prunable_images())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        prunable = self.coordinator.prunable_images()
        total = sum(int(image.get("Size") or 0) for image in prunable)
        dangling = sum(1 for image in prunable if self.coordinator._is_dangling(image))
        return {
            "scope": "all unused images"
            if self.coordinator.is_prune_all_unused()
            else "dangling images only",
            "reclaimable_bytes": total,
            "dangling": dangling,
            "tagged_but_unused": len(prunable) - dangling,
            "images": [_image_label(image) for image in prunable][:50],
            "images_total": len(self.coordinator.images),
            "images_in_use": len(self.coordinator._used_image_ids()),
        }
