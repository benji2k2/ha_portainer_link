"""Update platform for HA Portainer Link."""

from __future__ import annotations

try:
    from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
except ImportError:
    from homeassistant.components.update import UpdateEntity

    UpdateEntityFeature = None
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .const import DATA_COORDINATOR, DOMAIN
from .entity import BaseContainerEntity, container_name, short_digest
from .portainer_api import PortainerError


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up Portainer update entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    if not coordinator.is_update_sensors_enabled():
        return

    entities = [
        ContainerUpdateEntity(
            coordinator,
            entry.entry_id,
            container_id,
            container_name(container),
            coordinator.get_container_stack_info(container_id) or {},
        )
        for container_id, container in coordinator.containers.items()
    ]
    async_add_entities(entities)


class ContainerUpdateEntity(BaseContainerEntity, UpdateEntity):
    """Native update entity for a Docker container image."""

    entity_suffix = "update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = UpdateEntityFeature.INSTALL if UpdateEntityFeature else 1

    def __init__(self, coordinator, entry_id, container_id, name, stack_info) -> None:
        super().__init__(coordinator, entry_id, container_id, name, stack_info)
        self._attr_name = "Update"

    @property
    def installed_version(self):
        data = self._image_data
        if self.coordinator.get_update_availability(self.current_container_id):
            # An update is decided on image ids, so show those: two containers on
            # the same tag would otherwise both read "latest" here.
            return self._identity(data, "current_config_digest", "current_digest") or data.get(
                "current_version"
            )
        return data.get("current_version") or self._identity(
            data, "current_config_digest", "current_digest"
        )

    @property
    def latest_version(self):
        data = self._image_data
        if self.coordinator.get_update_availability(self.current_container_id):
            return self._identity(data, "available_config_digest", "available_digest") or data.get(
                "available_version"
            )
        return self.installed_version

    @staticmethod
    def _identity(data: dict, config_key: str, manifest_key: str) -> str | None:
        """Short image id, falling back to the manifest digest when unresolved."""
        return short_digest(data.get(config_key) or data.get(manifest_key))

    @property
    def in_progress(self) -> bool:
        return False

    @property
    def release_summary(self):
        data = self._image_data
        current = data.get("current_config_digest")
        available = data.get("available_config_digest")
        if current and available:
            summary = (
                f"Image id {short_digest(current)} -> {short_digest(available)}"
                if current != available
                else f"Image id {short_digest(current)}, unchanged"
            )
            manifest = data.get("available_digest")
            if manifest:
                # The manifest digest can move without the image changing, so it
                # is reported alongside rather than as the deciding value.
                summary += f". Manifest digest {short_digest(manifest)}"
            return summary
        current_digest = data.get("current_digest")
        available_digest = data.get("available_digest")
        if current_digest and available_digest:
            return (
                f"Manifest digest: {short_digest(current_digest)}"
                f" -> {short_digest(available_digest)}"
            )
        return None

    @property
    def _image_data(self) -> dict:
        container_id = self.current_container_id
        return self.coordinator.image_data.get(container_id or "", {})

    @property
    def available(self) -> bool:
        return super().available and bool(self._image_data)

    @property
    def is_on(self) -> bool:
        return self.coordinator.get_update_availability(self.current_container_id)

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        """Pull the new image and recreate the container from it.

        Pulling alone leaves the container running the old image, which is why
        pressing install previously appeared to do nothing. Portainer's recreate
        endpoint pulls and rebuilds the container from its existing
        configuration in one step.
        """
        container_id = self.current_container_id
        if not container_id:
            return

        info = await self.coordinator.api.inspect_container(self.endpoint_id, container_id) or {}
        if (info.get("HostConfig") or {}).get("AutoRemove"):
            # Docker deletes such a container the moment it stops, so a recreate
            # would destroy it rather than replace it. Portainer hides the
            # action for these containers as well.
            raise HomeAssistantError(
                f"{self.container_name} runs with --rm and cannot be recreated; "
                "it would be removed instead of replaced"
            )

        image = (info.get("Config") or {}).get("Image") or ""
        if image.lower().startswith("sha256"):
            raise HomeAssistantError(
                f"{self.container_name} is pinned to an image digest, so there is nothing to pull"
            )

        try:
            await self.coordinator.api.recreate_container(
                self.endpoint_id, container_id, pull_image=True
            )
        except PortainerError as err:
            raise HomeAssistantError(f"Could not update {self.container_name}: {err}") from err
        await self.coordinator.async_request_refresh()
