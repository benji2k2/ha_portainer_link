"""Button platform for HA Portainer Link."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.util import dt as dt_util

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

    if coordinator.is_instance_device_enabled():
        entities.append(CheckUpdatesButton(coordinator, entry.entry_id))
        if coordinator.is_prune_button_enabled():
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


class ButtonResultMixin:
    """Report an outcome on the entity, and notify only when it failed.

    A button entity cannot hand anything back to the frontend, so the result of
    a press lands in attributes where a dashboard card shows it straight away.
    Notifications are kept for failures, which are the only outcome worth
    interrupting someone over.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_result: dict[str, Any] = {}

    @property
    def result_attributes(self) -> dict[str, Any]:
        return dict(self._last_result)

    def _record(self, message: str, **extra: Any) -> None:
        """Store the outcome on the entity without notifying."""
        self._last_result = {
            "last_result": message,
            "last_run": dt_util.now().isoformat(timespec="seconds"),
            **extra,
        }
        self.async_write_ha_state()

    async def _fail(self, title: str, message: str, **extra: Any) -> None:
        """Store a failure and raise a notification for it."""
        self._record(message, last_result_ok=False, **extra)
        await _send_notification(self.hass, self.coordinator, title, message)

    def _succeed(self, message: str, **extra: Any) -> None:
        self._record(message, last_result_ok=True, **extra)


class ContainerButton(ButtonResultMixin, BaseContainerEntity, ButtonEntity):
    """Base class for container buttons."""

    def __init__(self, coordinator, entry_id, container_id, name, stack_info) -> None:
        super().__init__(coordinator, entry_id, container_id, name, stack_info)
        self._attr_name = self.label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.result_attributes


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
        if success:
            self._succeed(f"Restarted {self.container_name}")
        else:
            await self._fail("Container Restart Failed", f"Failed to restart {self.container_name}")


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
                await self._fail(
                    "Container Image Pull Failed",
                    f"Could not pull the image for {self.container_name}: {err}",
                )
                return
            await self.coordinator.async_request_refresh()
            self._succeed(f"Pulled the latest image for {self.container_name}")
        finally:
            self._attr_available = True
            self.async_write_ha_state()


class StackButton(ButtonResultMixin, BaseStackEntity, ButtonEntity):
    """Base class for stack buttons."""

    def __init__(self, coordinator, entry_id, stack_name) -> None:
        super().__init__(coordinator, entry_id, stack_name)
        self._attr_name = self.label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.result_attributes


class StackStartButton(StackButton):
    """Start all containers in a stack."""

    entity_suffix = "start"
    label = "Start"
    _attr_icon = "mdi:play-circle"

    async def async_press(self) -> None:
        success = await self.coordinator.api.start_stack(self.endpoint_id, self.stack_name)
        await self.coordinator.async_request_refresh()
        if success:
            self._succeed(f"Started stack {self.stack_name}")
        else:
            await self._fail("Stack Start Failed", f"Failed to start stack {self.stack_name}")


class StackStopButton(StackButton):
    """Stop all containers in a stack."""

    entity_suffix = "stop"
    label = "Stop"
    _attr_icon = "mdi:stop-circle"

    async def async_press(self) -> None:
        success = await self.coordinator.api.stop_stack(self.endpoint_id, self.stack_name)
        await self.coordinator.async_request_refresh()
        if success:
            self._succeed(f"Stopped stack {self.stack_name}")
        else:
            await self._fail("Stack Stop Failed", f"Failed to stop stack {self.stack_name}")


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
            self._succeed(f"Updated stack {self.stack_name}")
        else:
            await self._fail("Stack Update Failed", f"Failed to update stack {self.stack_name}")




class PruneImagesButton(ButtonResultMixin, BaseHubEntity, ButtonEntity):
    """Delete unused images on this Portainer environment.

    Defaults to dangling images only: unused *and* untagged. The prune_all_unused
    option widens it to every unused image, which also drops the images of
    stopped containers - hence not the default for a button that fires without
    any confirmation.
    """

    entity_suffix = "prune_images"
    _attr_name = "Delete unused images"
    _attr_icon = "mdi:broom"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the last outcome, so a dashboard shows it without a notification."""
        return {
            "scope": "all unused images"
            if self.coordinator.is_prune_all_unused()
            else "dangling images only",
            "deletable_now": len(self.coordinator.prunable_images()),
            **self.result_attributes,
        }

    async def async_press(self) -> None:
        all_unused = self.coordinator.is_prune_all_unused()
        before = len(self.coordinator.prunable_images())
        try:
            result = await self.coordinator.api.prune_images(
                self.coordinator.endpoint_id, dangling_only=not all_unused
            )
        except PortainerError as err:
            await self._fail(
                "Image Prune Failed", f"Portainer rejected the prune request: {err}"
            )
            return

        deleted_entries, untagged = count_pruned(result or {})
        reclaimed_bytes = int((result or {}).get("SpaceReclaimed") or 0)
        reclaimed = format_bytes(reclaimed_bytes)
        await self.coordinator.async_refresh()
        remaining = self.coordinator.prunable_images()
        # The response counts freed content ids, layers included, so it cannot say
        # how many images went. The change in what is deletable can.
        removed = max(0, before - len(remaining))

        scope = "unused" if all_unused else "dangling"
        if removed:
            summary = f"Removed {removed} {scope} image(s), reclaimed {reclaimed}"
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

        self._succeed(
            summary,
            last_images_deleted=removed,
            last_tags_dropped=untagged,
            last_freed_content_ids=deleted_entries,
            last_space_reclaimed=reclaimed,
            deletable_before=before,
            deletable_after=len(remaining),
        )


class CheckUpdatesButton(ButtonResultMixin, BaseHubEntity, ButtonEntity):
    """Run a registry check now instead of waiting for the interval.

    Registry checks are deliberately infrequent - they count against the
    registry's pull quota - and the timestamp of the last one survives a
    restart, so nothing else forces one. This does.
    """

    entity_suffix = "check_updates"
    _attr_name = "Check for updates"
    _attr_icon = "mdi:cloud-search-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pending = [
            container_name(container)
            for container_id, container in self.coordinator.containers.items()
            if self.coordinator.get_update_availability(container_id)
        ]
        return {
            "updates_available": len(pending),
            "containers": sorted(pending),
            **self.result_attributes,
        }

    async def async_press(self) -> None:
        await self.coordinator.async_force_registry_check()
        pending = sum(
            1
            for container_id in self.coordinator.containers
            if self.coordinator.get_update_availability(container_id)
        )
        checked = len(self.coordinator.containers)
        self._succeed(
            f"Checked {checked} container(s), {pending} with an update available",
            last_updates_available=pending,
        )
