"""Button platform for HA Portainer Link."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity

from .const import CONF_NOTIFY_SERVICE, DATA_COORDINATOR, DOMAIN
from .entity import (
    BaseContainerEntity,
    BaseHubEntity,
    BaseStackEntity,
    container_name,
    count_pruned,
    format_bytes,
)
from .portainer_api import PortainerError


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up Portainer buttons."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list[ButtonEntity] = []

    if coordinator.is_container_buttons_enabled():
        for container_id, container in coordinator.containers.items():
            name = container_name(container)
            stack_info = coordinator.get_container_stack_info(container_id) or {}
            entities.extend(
                [
                    RestartContainerButton(coordinator, entry.entry_id, container_id, name, stack_info),
                    PullUpdateButton(coordinator, entry.entry_id, container_id, name, stack_info),
                ]
            )

    if coordinator.is_instance_device_enabled() and coordinator.is_prune_button_enabled():
        entities.append(PruneImagesButton(coordinator, entry.entry_id))

    if coordinator.is_stack_view_enabled() and coordinator.is_stack_buttons_enabled():
        for stack_name in coordinator.stack_names():
            entities.extend(
                [
                    StackStartButton(coordinator, entry.entry_id, stack_name),
                    StackStopButton(coordinator, entry.entry_id, stack_name),
                    StackUpdateButton(coordinator, entry.entry_id, stack_name),
                ]
            )

    async_add_entities(entities)


async def _send_notification(hass, coordinator, title: str, message: str) -> None:
    """Send a notification without assuming a mobile_app notify target exists."""
    notify_service = (coordinator.config.get(CONF_NOTIFY_SERVICE) or "").strip()
    if notify_service and "." in notify_service:
        domain, service = notify_service.split(".", 1)
        await hass.services.async_call(domain, service, {"title": title, "message": message}, blocking=False)
        return
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {"title": title, "message": message},
        blocking=False,
    )


class ContainerButton(BaseContainerEntity, ButtonEntity):
    """Base class for container buttons."""

    def __init__(self, coordinator, entry_id, container_id, name, stack_info) -> None:
        super().__init__(coordinator, entry_id, container_id, name, stack_info)
        self._attr_name = self.label

    async def _notify(self, title: str, message: str) -> None:
        await _send_notification(self.hass, self.coordinator, title, message)


class RestartContainerButton(ContainerButton):
    """Restart a Docker container."""

    entity_suffix = "restart"
    label = "Restart"
    _attr_icon = "mdi:restart"

    async def async_press(self) -> None:
        container_id = self.current_container_id
        if not container_id:
            return
        success = await self.coordinator.api.restart_container(self.endpoint_id, container_id)
        await self.coordinator.async_request_refresh()
        if not success:
            await self._notify("Container Restart Failed", f"Failed to restart {self.container_name}")


class PullUpdateButton(ContainerButton):
    """Pull the latest image for a Docker container."""

    entity_suffix = "pull_update"
    label = "Pull Update"
    _attr_icon = "mdi:download"

    async def async_press(self) -> None:
        container_id = self.current_container_id
        if not container_id:
            return
        self._attr_available = False
        self.async_write_ha_state()
        try:
            try:
                await self.coordinator.api.pull_image_update(self.endpoint_id, container_id)
            except PortainerError as err:
                await self._notify(
                    "Container Image Pull Failed",
                    f"Could not pull the image for {self.container_name}: {err}",
                )
                return
            await self.coordinator.async_request_refresh()
            await self._notify("Container Image Pulled", f"Pulled latest image for {self.container_name}")
        finally:
            self._attr_available = True
            self.async_write_ha_state()


class StackButton(BaseStackEntity, ButtonEntity):
    """Base class for stack buttons."""

    def __init__(self, coordinator, entry_id, stack_name) -> None:
        super().__init__(coordinator, entry_id, stack_name)
        self._attr_name = self.label

    async def _notify(self, title: str, message: str) -> None:
        await _send_notification(self.hass, self.coordinator, title, message)


class StackStartButton(StackButton):
    """Start all containers in a stack."""

    entity_suffix = "start"
    label = "Start"
    _attr_icon = "mdi:play-circle"

    async def async_press(self) -> None:
        success = await self.coordinator.api.start_stack(self.endpoint_id, self.stack_name)
        await self.coordinator.async_request_refresh()
        if not success:
            await self._notify("Stack Start Failed", f"Failed to start stack {self.stack_name}")


class StackStopButton(StackButton):
    """Stop all containers in a stack."""

    entity_suffix = "stop"
    label = "Stop"
    _attr_icon = "mdi:stop-circle"

    async def async_press(self) -> None:
        success = await self.coordinator.api.stop_stack(self.endpoint_id, self.stack_name)
        await self.coordinator.async_request_refresh()
        if not success:
            await self._notify("Stack Stop Failed", f"Failed to stop stack {self.stack_name}")


class StackUpdateButton(StackButton):
    """Update a Portainer stack."""

    entity_suffix = "update"
    label = "Update"
    _attr_icon = "mdi:update"

    async def async_press(self) -> None:
        result = await self.coordinator.api.update_stack(self.endpoint_id, self.stack_name)
        await self.coordinator.async_request_refresh()
        if isinstance(result, dict):
            success = bool(result.get("ok", False))
        else:
            success = bool(result)
        if success:
            await self._notify("Stack Updated", f"Updated stack {self.stack_name}")
        else:
            await self._notify("Stack Update Failed", f"Failed to update stack {self.stack_name}")




class PruneImagesButton(BaseHubEntity, ButtonEntity):
    """Delete unused images on this Portainer environment.

    Defaults to dangling images only: unused *and* untagged. The prune_all_unused
    option widens it to every unused image, which also drops the images of
    stopped containers - hence not the default for a button that fires without
    any confirmation.
    """

    entity_suffix = "prune_images"
    _attr_name = "Delete unused images"
    _attr_icon = "mdi:broom"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._last_result: dict[str, Any] = {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the last outcome, so a dashboard shows it without a notification."""
        return {
            "scope": "all unused images"
            if self.coordinator.is_prune_all_unused()
            else "dangling images only",
            "deletable_now": len(self.coordinator.prunable_images()),
            **self._last_result,
        }

    async def _notify(self, title: str, message: str) -> None:
        await _send_notification(self.hass, self.coordinator, title, message)

    async def async_press(self) -> None:
        all_unused = self.coordinator.is_prune_all_unused()
        before = len(self.coordinator.prunable_images())
        try:
            result = await self.coordinator.api.prune_images(
                self.coordinator.endpoint_id, dangling_only=not all_unused
            )
        except PortainerError as err:
            self._last_result = {"last_result": f"failed: {err}"}
            self.async_write_ha_state()
            await self._notify("Image Prune Failed", f"Portainer rejected the prune request: {err}")
            return

        deleted, untagged = count_pruned(result or {})
        reclaimed_bytes = int((result or {}).get("SpaceReclaimed") or 0)
        reclaimed = format_bytes(reclaimed_bytes)
        await self.coordinator.async_refresh()
        remaining = self.coordinator.prunable_images()

        scope = "unused" if all_unused else "dangling"
        if deleted:
            summary = f"Removed {deleted} {scope} image(s), reclaimed {reclaimed}"
            if untagged:
                summary += f" (also dropped {untagged} tag reference(s))"
        elif untagged:
            summary = f"Dropped {untagged} tag reference(s), reclaimed {reclaimed}"
        else:
            summary = f"Nothing to remove, reclaimed {reclaimed}"
            if not all_unused:
                # The usual surprise: an unused image that still carries a tag
                # is not dangling, so this mode skips it by design.
                summary += (
                    ". Images that still carry a tag are not dangling;"
                    " enable prune_all_unused to include them"
                )
        if remaining:
            # Docker refuses to remove an image another image is built on, so a
            # shared base layer can survive a prune that removed its children.
            summary += (
                f". {len(remaining)} image(s) remain deletable - docker keeps images that"
                " another image or container still depends on"
            )

        self._last_result = {
            "last_result": summary,
            "last_images_deleted": deleted,
            "last_tags_dropped": untagged,
            "last_space_reclaimed": reclaimed,
            "deletable_before": before,
            "deletable_after": len(remaining),
        }
        self.async_write_ha_state()
        await self._notify("Unused Images Deleted", f"{summary}.")
