"""Data coordinator for HA Portainer Link."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ENABLE_CONTAINER_BUTTONS,
    CONF_ENABLE_HEALTHCHECK_SENSORS,
    CONF_ENABLE_INSTANCE_DEVICE,
    CONF_ENABLE_PRUNE_BUTTON,
    CONF_PRUNE_ALL_UNUSED,
    CONF_ENABLE_RESOURCE_SENSORS,
    CONF_ENABLE_STACK_BUTTONS,
    CONF_ENABLE_STACK_VIEW,
    CONF_ENABLE_UPDATE_SENSORS,
    CONF_ENABLE_VERSION_SENSORS,
    CONF_RELEASE_NOTE_SOURCES,
    CONF_RELEASE_NOTES,
    CONF_UPDATE_CHECK_INTERVAL,
    CONF_UPDATE_INTERVAL,
    CONF_WATCHTOWER_TOKEN,
    CONF_WATCHTOWER_URL,
    DEFAULT_OPTIONS,
)
from .entity import container_name, is_container_running, stable_container_key, stack_info_from_container
from .image_api import local_platform_digest
from .portainer_api import PortainerAPI
from .release_notes import SOURCE_LABEL, VERSION_LABEL, ReleaseNotesClient, parse_overrides
from .tag_versions import TagVersionClient
from .watchtower import WatchtowerClient

_LOGGER = logging.getLogger(__name__)


class PortainerDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator for Portainer container, stack, metric, and image data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: PortainerAPI,
        endpoint_id: int,
        config: dict[str, Any],
        store=None,
    ) -> None:
        self.config = {**DEFAULT_OPTIONS, **config}
        update_interval = int(self.config.get(CONF_UPDATE_INTERVAL, DEFAULT_OPTIONS[CONF_UPDATE_INTERVAL]))
        super().__init__(
            hass,
            _LOGGER,
            name=f"ha_portainer_link_{endpoint_id}",
            update_interval=dt.timedelta(minutes=max(update_interval, 1)),
        )
        self.api = api
        self.endpoint_id = endpoint_id
        self.endpoint_name: str | None = None
        # Registry id of the instance device, filled in during setup so
        # container devices can point at it with via_device_id.
        self.hub_device_id: str | None = None
        self.docker_info: dict[str, Any] = {}
        self.images: list[dict[str, Any]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.stacks: dict[str, dict[str, Any]] = {}
        self.container_stack_map: dict[str, str] = {}
        self.container_stack_info: dict[str, dict[str, Any]] = {}
        self.stable_container_map: dict[str, str] = {}
        self.metrics: dict[str, dict[str, Any]] = {}
        self.image_data: dict[str, dict[str, Any]] = {}
        self.update_availability: dict[str, bool] = {}
        self._store = store
        self._last_registry_check = 0.0
        # Spread the first due sweep after a restart so several instances, or a
        # host rebooting, do not all hit the registry in the same second.
        self._registry_jitter = random.uniform(0, 300)
        session = getattr(api, "session", None)
        self.release_notes = ReleaseNotesClient(session) if self.config.get(CONF_RELEASE_NOTES) else None
        self.release_note_sources = parse_overrides(self.config.get(CONF_RELEASE_NOTE_SOURCES))
        self.tag_versions = TagVersionClient(session)
        # Optional: without a Watchtower URL and token, updates are installed
        # through Portainer's recreate endpoint exactly as before.
        watchtower_url = str(self.config.get(CONF_WATCHTOWER_URL) or "").strip()
        watchtower_token = str(self.config.get(CONF_WATCHTOWER_TOKEN) or "").strip()
        self.watchtower = (
            WatchtowerClient(session, watchtower_url, watchtower_token)
            if watchtower_url and watchtower_token
            else None
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch fresh data from Portainer."""
        try:
            if self.api.containers:
                endpoint_exists = await self.api.containers.check_endpoint_exists(self.endpoint_id)
                if not endpoint_exists:
                    raise UpdateFailed(f"Endpoint {self.endpoint_id} does not exist")

            # The environment name also labels container and stack devices, so it
            # is fetched regardless of whether the instance device is enabled.
            if self.endpoint_name is None:
                await self._refresh_endpoint_name()

            if self.is_instance_device_enabled():
                await self._refresh_docker_info()
                await self._refresh_images()
            else:
                self.docker_info = {}
                self.images = []

            containers = await self.api.get_containers(self.endpoint_id)
            stacks = await self.api.get_stacks(self.endpoint_id)
            self._process_containers(containers or [])
            self.stacks = {stack.get("Name"): stack for stack in (stacks or []) if stack.get("Name")}

            if self.is_resource_sensors_enabled():
                await self._refresh_metrics()
            else:
                self.metrics = {}

            await self._refresh_image_data_if_due()

            return {
                "containers": self.containers,
                "docker_info": self.docker_info,
                "stacks": self.stacks,
                "container_stack_map": self.container_stack_map,
                "metrics": self.metrics,
                "image_data": self.image_data,
                "update_availability": self.update_availability,
            }
        except Exception as err:
            raise UpdateFailed(f"Failed to update Portainer data: {err}") from err

    def _process_containers(self, containers: list[dict[str, Any]]) -> None:
        """Normalize container list data into lookup maps."""
        previous_ids = dict(self.stable_container_map)
        self.containers = {}
        self.container_stack_map = {}
        self.container_stack_info = {}
        self.stable_container_map = {}

        for container in containers:
            container_id = container.get("Id")
            if not container_id:
                continue
            name = container_name(container)
            stack_info = stack_info_from_container(container)
            if not self.is_stack_view_enabled():
                stack_info = {
                    "stack_name": None,
                    "service_name": None,
                    "container_number": None,
                    "is_stack_container": False,
                }

            self.containers[container_id] = container
            self.container_stack_info[container_id] = stack_info
            if stack_info.get("is_stack_container"):
                self.container_stack_map[container_id] = stack_info["stack_name"]
            self.stable_container_map[stable_container_key(name, stack_info)] = container_id

        self._carry_over_recreated(previous_ids)

    def _carry_over_recreated(self, previous_ids: dict[str, str]) -> None:
        """Hand the registry result of a replaced container to its successor.

        Image data and update availability are keyed by Docker container id, and
        recreating a container - `docker compose up -d` after a compose change,
        a restore, anything but an image update - gives it a new one. Without
        this the successor started with nothing and its update vanished until
        the next registry sweep, hours later. The registry result belongs to
        the image, so it is carried over; if the successor runs a different
        image, the next refresh still compares it against what was found.
        """
        for key, new_id in self.stable_container_map.items():
            old_id = previous_ids.get(key)
            if not old_id or old_id == new_id or old_id in self.containers:
                continue
            if old_id in self.image_data and new_id not in self.image_data:
                self.image_data[new_id] = self.image_data.pop(old_id)
            if old_id in self.update_availability and new_id not in self.update_availability:
                self.update_availability[new_id] = self.update_availability.pop(old_id)

    async def _refresh_metrics(self) -> None:
        """Refresh resource metrics with bounded concurrency."""
        metrics: dict[str, dict[str, Any]] = {}
        semaphore = asyncio.Semaphore(4)

        async def refresh_one(container_id: str, container: dict[str, Any]) -> None:
            async with semaphore:
                item: dict[str, Any] = {}
                if is_container_running(container):
                    stats = await self.api.get_container_stats(self.endpoint_id, container_id)
                    item.update(self._parse_stats(stats or {}))
                    started_at = await self._get_started_at(container_id)
                    if started_at:
                        item["uptime_s"] = max(0, int((dt.datetime.now(dt.timezone.utc) - started_at).total_seconds()))
                else:
                    item["uptime_s"] = None
                metrics[container_id] = item

        await asyncio.gather(*(refresh_one(cid, container) for cid, container in self.containers.items()))
        self.metrics = metrics

    @staticmethod
    def _parse_stats(stats: dict[str, Any]) -> dict[str, Any]:
        """Extract CPU and memory metrics from Docker stats."""
        parsed: dict[str, Any] = {}
        cpu_stats = stats.get("cpu_stats") or {}
        precpu_stats = stats.get("precpu_stats") or {}
        cpu_total = ((cpu_stats.get("cpu_usage") or {}).get("total_usage") or 0)
        precpu_total = ((precpu_stats.get("cpu_usage") or {}).get("total_usage") or 0)
        system_total = cpu_stats.get("system_cpu_usage") or 0
        pre_system_total = precpu_stats.get("system_cpu_usage") or 0
        system_delta = system_total - pre_system_total
        cpu_delta = cpu_total - precpu_total
        if system_delta > 0 and cpu_delta >= 0:
            online_cpus = cpu_stats.get("online_cpus") or 1
            parsed["cpu_percent"] = round((cpu_delta / system_delta) * online_cpus * 100.0, 2)

        memory_usage = (stats.get("memory_stats") or {}).get("usage")
        if memory_usage is not None:
            parsed["memory_mb"] = round(memory_usage / (1024 * 1024), 2)
        return parsed

    async def _get_started_at(self, container_id: str) -> dt.datetime | None:
        """Return StartedAt for a running container."""
        try:
            info = await self.api.inspect_container(self.endpoint_id, container_id)
            started_at = ((info or {}).get("State") or {}).get("StartedAt")
            if not started_at or started_at.startswith("0001-"):
                return None
            parsed = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except Exception as err:
            _LOGGER.debug("Failed to parse uptime for %s: %s", container_id, err)
            return None

    async def _refresh_image_data_if_due(self) -> None:
        """Refresh image metadata and optional registry data."""
        if not (self.is_version_sensors_enabled() or self.is_update_sensors_enabled()):
            self.image_data = {}
            self.update_availability = {}
            return

        # Wall clock, not monotonic: the timestamp is persisted across restarts,
        # and a monotonic reference point does not survive one.
        now = time.time()
        # The option is in minutes, like update_interval right above. Comparing
        # it against seconds made the default 6 minutes instead of 6 hours, and
        # every one of those sweeps counts against the registry's pull quota.
        interval_minutes = int(
            self.config.get(CONF_UPDATE_CHECK_INTERVAL, DEFAULT_OPTIONS[CONF_UPDATE_CHECK_INTERVAL])
        )
        interval = max(interval_minutes, 1) * 60 + self._registry_jitter
        registry_data_enabled = self.is_version_sensors_enabled() or self.is_update_sensors_enabled()
        include_registry = registry_data_enabled and (now - self._last_registry_check >= interval)
        if include_registry:
            if getattr(self, "tag_versions", None):
                self.tag_versions.clear_cache()
            self._last_registry_check = now
            # A fresh jitter each time, so a fixed interval does not drift into
            # lockstep with anything else polling the same registry.
            self._registry_jitter = random.uniform(0, 300)

        image_data: dict[str, dict[str, Any]] = {}
        update_availability = dict(self.update_availability)
        semaphore = asyncio.Semaphore(3)

        async def refresh_one(container_id: str) -> None:
            async with semaphore:
                data: dict[str, Any] = {}
                try:
                    info = await self.api.inspect_container(self.endpoint_id, container_id)
                    image_name = ((info or {}).get("Config") or {}).get("Image")
                    image_id = (info or {}).get("Image")
                    image_info = None
                    if image_name:
                        data["image_name"] = image_name
                    if image_id:
                        # The local image id is available every cycle; only the
                        # remote counterpart needs a registry window.
                        data["current_config_digest"] = image_id
                        image_info = await self.api.get_image_info(self.endpoint_id, image_id)
                        if image_info:
                            data["current_version"] = self.api.extract_version_from_image(image_info)
                            data["image_created"] = image_info.get("Created")
                            labels = (image_info.get("Config") or {}).get("Labels") or {}
                            data["oci_version"] = labels.get(VERSION_LABEL)
                            data["oci_source"] = labels.get(SOURCE_LABEL)
                            if platform := local_platform_digest(image_info, (info or {}).get("Id")):
                                data["current_platform_digest"] = platform
                                self._adopt_config_digest(container_id, data)
                        current_digest = await self.api.get_current_digest(self.endpoint_id, container_id)
                        if current_digest:
                            data["current_digest"] = current_digest
                    if include_registry and image_name:
                        # One walk of the manifest yields all three values. Every
                        # manifest request counts against the registry's pull
                        # quota, so asking three times over would triple the cost
                        # of a check that downloads nothing.
                        remote = await self.api.get_remote_image_state(
                            image_name, image_info, want_created=True
                        )
                        available_digest = remote.get("manifest_digest")
                        if available_digest:
                            data["available_digest"] = available_digest

                        # The manifest digest moves whenever the index is
                        # rewritten - re-pushed build attestations do it without
                        # changing a single layer. The config digest is the image
                        # id docker itself compares, so it decides here and the
                        # manifest digests stay purely informational.
                        remote_config = remote.get("config_digest")
                        if remote.get("platform_digest"):
                            data["available_platform_digest"] = remote["platform_digest"]
                        if remote_config:
                            data["available_config_digest"] = remote_config
                            if created := remote.get("created"):
                                data["available_image_created"] = created
                            remote_labels = remote.get("labels") or {}
                            data["available_oci_version"] = remote_labels.get(VERSION_LABEL)
                            if remote_labels.get(SOURCE_LABEL):
                                data["available_oci_source"] = remote_labels[SOURCE_LABEL]
                            self._adopt_config_digest(container_id, data)
                            update_availability[container_id] = bool(
                                image_id and remote_config != data["current_config_digest"]
                            )
                        elif available_digest:
                            current_digest = data.get("current_digest")
                            update_availability[container_id] = bool(
                                current_digest
                                and not str(current_digest).startswith("unknown")
                                and current_digest != available_digest
                            )
                        data["available_version"] = await self.api.get_available_version(self.endpoint_id, image_name)
                    await self._refresh_tag_versions(container_id, image_name, data, include_registry)
                except Exception as err:
                    _LOGGER.debug("Failed to refresh image data for %s: %s", container_id, err)
                image_data[container_id] = {**self.image_data.get(container_id, {}), **data}

        await asyncio.gather(*(refresh_one(container_id) for container_id in self.containers))
        self.image_data = image_data
        # An update installed between two sweeps - from here, by Watchtower or
        # by hand - leaves the container on exactly the image the last sweep
        # found. Clearing it now beats showing a stale update for hours.
        for container_id, data in image_data.items():
            current = data.get("current_config_digest")
            if current and current == data.get("available_config_digest"):
                update_availability[container_id] = False
                if container_id in self.update_availability:
                    self.update_availability[container_id] = False
        if include_registry and self.is_update_sensors_enabled():
            self.update_availability = {
                container_id: bool(update_availability.get(container_id, False))
                for container_id in self.containers
            }
        elif not self.is_update_sensors_enabled():
            self.update_availability = {}

        if include_registry:
            # After the availability map is final, so a restart restores the
            # update states exactly as the sweep left them.
            await self._async_save_state()

    def _adopt_config_digest(self, container_id: str, data: dict[str, Any]) -> None:
        """Give a containerd-store image the config digest the registry found for it.

        There the local image id is the index digest, which never equals the
        registry's config digest - every container looked outdated. When the
        local platform manifest is the one the registry walk resolved, the
        config digest is that walk's, and everything comparing image ids
        (update state, display, clearing after an install) works unchanged.
        """
        local = data.get("current_platform_digest")
        if not local:
            return
        source = data if data.get("available_platform_digest") else self.image_data.get(container_id, {})
        config = source.get("available_config_digest")
        if config and local == source.get("available_platform_digest"):
            data["current_config_digest"] = config

    async def _refresh_tag_versions(
        self, container_id: str, image_name: str | None, data: dict[str, Any], include_registry: bool
    ) -> None:
        """Versions from the tags sharing the image digests, for images without a version label.

        Looked up on every sweep and whenever the running image changed - after an
        update the new version shows right away instead of six hours later.
        """
        client = getattr(self, "tag_versions", None)
        if client is None or not image_name or data.get("oci_version"):
            return
        previous = self.image_data.get(container_id, {})
        current = data.get("current_digest")
        current = None if not current or str(current).startswith("unknown") else current
        if not include_registry and previous.get("tag_version_for") == current:
            return
        available = data.get("available_digest") or previous.get("available_digest")
        try:
            registry, repository, _reference, _pinned = self.api.images._parse_image_ref(image_name)
            installed_version, available_version = await client.versions(registry, repository, [current, available])
        except Exception as err:
            _LOGGER.debug("Tag version lookup for %s failed: %s", image_name, err)
            return
        if installed_version is None and available_version:
            # The index can be re-pushed - other platforms rebuilt, attestations
            # renewed - while this platform's image stays the same. No tag points
            # at the old index then, but the image is the available one, and so
            # is its version (redis:latest on 22.10.2026).
            current_config = data.get("current_config_digest")
            available_config = data.get("available_config_digest") or previous.get("available_config_digest")
            if current_config and current_config == available_config:
                installed_version = available_version
        data["tag_version"] = installed_version
        data["available_tag_version"] = available_version
        data["tag_version_for"] = current

    async def _refresh_endpoint_name(self) -> None:
        """Look up the Portainer environment name once per config entry."""
        try:
            info = await self.api.get_endpoint_info(self.endpoint_id)
        except Exception as err:
            _LOGGER.debug("Failed to read endpoint name: %s", err)
            return
        name = (info or {}).get("Name")
        if name:
            self.endpoint_name = str(name)

    async def _refresh_docker_info(self) -> None:
        """Refresh docker daemon info; keep the previous payload on failure."""
        try:
            info = await self.api.get_docker_info(self.endpoint_id)
        except Exception as err:
            _LOGGER.debug("Failed to read docker info: %s", err)
            return
        if isinstance(info, dict):
            self.docker_info = info

    def running_container_count(self) -> int:
        return sum(1 for container in self.containers.values() if is_container_running(container))

    def stopped_container_count(self) -> int:
        return len(self.containers) - self.running_container_count()

    def update_available_count(self) -> int:
        return sum(1 for container_id in self.containers if self.get_update_availability(container_id))

    async def _refresh_images(self) -> None:
        """Refresh the local image list; keep the previous one on failure."""
        try:
            images = await self.api.get_images(self.endpoint_id)
        except Exception as err:
            _LOGGER.debug("Failed to list images: %s", err)
            return
        if isinstance(images, list):
            self.images = images

    def _used_image_ids(self) -> set[str]:
        """Image ids referenced by a container on this endpoint."""
        used = set()
        for container in self.containers.values():
            if image_id := container.get("ImageID"):
                used.add(str(image_id))
        return used

    @staticmethod
    def _is_dangling(image: dict[str, Any]) -> bool:
        tags = image.get("RepoTags")
        return not tags or tags == ["<none>:<none>"]

    def prunable_images(self) -> list[dict[str, Any]]:
        """Images the prune button would remove in its current scope.

        Docker never removes an image a container still references, so those are
        excluded here regardless of scope - otherwise the count would promise
        more than a prune can deliver.
        """
        used = self._used_image_ids()
        all_unused = self.is_prune_all_unused()
        result = []
        for image in self.images:
            if str(image.get("Id")) in used:
                continue
            if not all_unused and not self._is_dangling(image):
                continue
            result.append(image)
        return result

    async def async_force_registry_check(self) -> None:
        """Run a registry sweep now, whatever the interval says.

        Persisting the timestamp means a reload no longer triggers one, so there
        has to be a deliberate way to ask - otherwise a six hour interval leaves
        no way to check after updating something by hand.
        """
        self._last_registry_check = 0.0
        self._registry_jitter = 0.0
        await self.async_refresh()

    async def async_load_persisted_state(self) -> None:
        """Restore the registry timestamp and cached image data from disk.

        Without this every Home Assistant restart triggers a full registry sweep,
        because the timestamp of the last one lived only in memory. Registries
        count those manifest requests as pulls, so a few restarts can exhaust an
        anonymous quota on their own.
        """
        if self._store is None:
            return
        try:
            stored = await self._store.async_load() or {}
        except Exception as err:
            _LOGGER.debug("Could not load persisted state: %s", err)
            return
        timestamp = stored.get("last_registry_check")
        if isinstance(timestamp, (int, float)) and timestamp <= time.time():
            self._last_registry_check = float(timestamp)
        if isinstance(stored.get("image_data"), dict):
            # Restores the sensors immediately instead of leaving them empty
            # until the next sweep is actually due.
            self.image_data = stored["image_data"]
        if isinstance(stored.get("update_availability"), dict):
            self.update_availability = {
                key: bool(value) for key, value in stored["update_availability"].items()
            }
        if isinstance(stored.get("stable_container_map"), dict):
            # Lets the first refresh recognise a container recreated while
            # Home Assistant was down.
            self.stable_container_map = {
                str(key): str(value) for key, value in stored["stable_container_map"].items()
            }

    async def _async_save_state(self) -> None:
        """Persist what a restart would otherwise throw away."""
        if self._store is None:
            return
        try:
            await self._store.async_save(
                {
                    "last_registry_check": self._last_registry_check,
                    "image_data": self.image_data,
                    "update_availability": self.update_availability,
                    "stable_container_map": getattr(self, "stable_container_map", {}),
                }
            )
        except Exception as err:
            _LOGGER.debug("Could not persist state: %s", err)

    def get_container(self, container_id: str | None) -> dict[str, Any] | None:
        return self.containers.get(container_id or "")

    def get_stack(self, stack_name: str) -> dict[str, Any] | None:
        return self.stacks.get(stack_name)

    def get_container_stack(self, container_id: str) -> str | None:
        return self.container_stack_map.get(container_id)

    def get_container_stack_info(self, container_id: str) -> dict[str, Any] | None:
        return self.container_stack_info.get(container_id)

    def get_update_availability(self, container_id: str | None) -> bool:
        return bool(self.update_availability.get(container_id or "", False))

    def get_container_by_stable_id(self, stable_id: str) -> str | None:
        return self.stable_container_map.get(stable_id)

    def get_stack_containers(self, stack_name: str) -> list[dict[str, Any]]:
        return [
            container
            for container_id, container in self.containers.items()
            if self.container_stack_map.get(container_id) == stack_name
        ]

    def get_standalone_containers(self) -> list[dict[str, Any]]:
        return [
            container
            for container_id, container in self.containers.items()
            if container_id not in self.container_stack_map
        ]

    def stack_names(self) -> list[str]:
        names = set(self.stacks)
        names.update(name for name in self.container_stack_map.values() if name)
        return sorted(names)

    def is_stack_view_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_STACK_VIEW))

    def is_resource_sensors_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_RESOURCE_SENSORS))

    def is_version_sensors_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_VERSION_SENSORS))

    def is_update_sensors_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_UPDATE_SENSORS))

    def is_stack_buttons_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_STACK_BUTTONS))

    def is_container_buttons_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_CONTAINER_BUTTONS))

    def is_healthcheck_sensors_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_HEALTHCHECK_SENSORS))

    def is_instance_device_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_INSTANCE_DEVICE))

    def is_prune_button_enabled(self) -> bool:
        return bool(self.config.get(CONF_ENABLE_PRUNE_BUTTON))

    def is_prune_all_unused(self) -> bool:
        return bool(self.config.get(CONF_PRUNE_ALL_UNUSED))

    async def async_shutdown(self) -> None:
        """Close API resources."""
        await self.api.close()
