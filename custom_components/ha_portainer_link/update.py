"""Update platform for HA Portainer Link."""

from __future__ import annotations

import asyncio
import logging

try:
    from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
except ImportError:
    from homeassistant.components.update import UpdateEntity

    UpdateEntityFeature = None
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .const import DATA_COORDINATOR, DOMAIN
from .entity import BaseContainerEntity, container_name, parse_docker_time, short_digest
from .portainer_api import PortainerError
from .release_notes import render, resolve_repo, select_releases
from .watchtower import WatchtowerError

_LOGGER = logging.getLogger(__name__)

# How long an update through Watchtower may take until the container runs a
# new image: pulling a large image on a slow line takes minutes.
WATCHTOWER_TIMEOUT = 600
WATCHTOWER_POLL = 5


def _supported_features() -> int:
    if UpdateEntityFeature is None:
        return 1
    features = UpdateEntityFeature.INSTALL
    # Both exist since Home Assistant 2022.4; looked up defensively anyway, as
    # the INSTALL fallback above already does for even older versions.
    for name in ("RELEASE_NOTES", "PROGRESS"):
        features |= getattr(UpdateEntityFeature, name, 0)
    return features


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

    def __init__(self, coordinator, entry_id, container_id, name, stack_info) -> None:
        super().__init__(coordinator, entry_id, container_id, name, stack_info)
        self._attr_name = "Update"
        self._installing = False

    @property
    def supported_features(self) -> int:
        return _supported_features()

    @staticmethod
    def _label(version: str | None, image_id: str | None) -> str | None:
        """Version and image id together, e.g. "v3.7.14 (93c91251e746)".

        The id has to stay in: an update is decided on image ids, and two images
        can carry the same version label - a rebuild with a new base image does.
        It also keeps a skipped update specific to that one image.
        """
        short = short_digest(image_id)
        if version and short:
            return f"{version} ({short})"
        return version or short

    @property
    def installed_version(self):
        data = self._image_data
        image_id = data.get("current_config_digest") or data.get("current_digest")
        if data.get("oci_version") or image_id:
            return self._label(data.get("oci_version"), image_id)
        return data.get("current_version")

    @property
    def latest_version(self):
        data = self._image_data
        if not self.coordinator.get_update_availability(self.current_container_id):
            return self.installed_version
        image_id = data.get("available_config_digest") or data.get("available_digest")
        return self._label(data.get("available_oci_version"), image_id) or data.get("available_version")

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Image ids carry no order; the registry comparison already decided."""
        return self.coordinator.get_update_availability(self.current_container_id)

    @property
    def title(self) -> str | None:
        """The software, as opposed to the container: "traefik", not "Update"."""
        image = self._image_data.get("image_name") or ""
        name = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]
        return name or None

    @property
    def in_progress(self) -> bool:
        return self._installing

    @property
    def _repo(self) -> str | None:
        data = self._image_data
        return resolve_repo(
            data.get("image_name"),
            data.get("available_oci_source") or data.get("oci_source"),
            getattr(self.coordinator, "release_note_sources", {}) or {},
        )

    @property
    def release_url(self) -> str | None:
        if repo := self._repo:
            return f"https://github.com/{repo}/releases"
        source = self._image_data.get("oci_source")
        return source if str(source or "").startswith("http") else None

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

    async def async_release_notes(self) -> str | None:
        """GitHub releases between the running image and the available one."""
        client = getattr(self.coordinator, "release_notes", None)
        repo = self._repo
        if client is None or not repo:
            return self.release_summary
        releases = await client.releases(repo)
        if releases is None:
            return f"Could not load the releases of [{repo}](https://github.com/{repo}/releases) from GitHub."
        data = self._image_data
        update = self.coordinator.get_update_availability(self.current_container_id)
        selected = select_releases(
            releases,
            installed_version=data.get("oci_version"),
            available_version=data.get("available_oci_version") if update else data.get("oci_version"),
            installed_built=data.get("image_created"),
            available_built=data.get("available_image_created") if update else data.get("image_created"),
        )
        if not update and not selected:
            return f"Running the latest image. [Releases of {repo}](https://github.com/{repo}/releases)"
        return render(repo, selected)

    @property
    def extra_state_attributes(self) -> dict:
        """Build dates for the running and the available image."""
        data = self._image_data
        installed = parse_docker_time(data.get("image_created"))
        available = parse_docker_time(data.get("available_image_created"))
        return {
            "installed_image_built": installed.isoformat() if installed else None,
            "available_image_built": available.isoformat() if available else None,
            "installs_via": "watchtower" if getattr(self.coordinator, "watchtower", None) else "portainer",
        }

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
        configuration in one step; with a Watchtower configured, Watchtower on
        the host does the same without depending on Portainer staying up.
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

        # With PROGRESS declared, Home Assistant leaves the progress display to
        # the entity, so it is set here for both ways of installing.
        self._installing = True
        self.async_write_ha_state()
        try:
            watchtower = getattr(self.coordinator, "watchtower", None)
            if watchtower is not None:
                await self._install_with_watchtower(watchtower, info.get("Image"))
            else:
                try:
                    await self.coordinator.api.recreate_container(
                        self.endpoint_id, container_id, pull_image=True
                    )
                except PortainerError as err:
                    raise HomeAssistantError(f"Could not update {self.container_name}: {err}") from err
        finally:
            self._installing = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()

    async def _install_with_watchtower(self, watchtower, image_before: str | None) -> None:
        """Trigger Watchtower, then wait until the container runs a different image.

        Watchtower answers before it replaces anything, so the result has to be
        observed: the container comes back under a new id with a new image.
        Errors while polling are expected - updating Portainer, its agent or the
        proxy in front of it makes Portainer unreachable for a moment.
        """
        try:
            await watchtower.update_container(self.container_name)
        except WatchtowerError as err:
            raise HomeAssistantError(f"Could not update {self.container_name}: {err}") from err

        loop = asyncio.get_running_loop()
        deadline = loop.time() + WATCHTOWER_TIMEOUT
        while loop.time() < deadline:
            await asyncio.sleep(WATCHTOWER_POLL)
            try:
                containers = await self.coordinator.api.get_containers(self.endpoint_id) or []
            except Exception as err:
                _LOGGER.debug("Polling after a Watchtower update failed: %s", err)
                continue
            current = next((c for c in containers if container_name(c) == self.container_name), None)
            if current and current.get("ImageID") and current.get("ImageID") != image_before:
                return
        raise HomeAssistantError(
            f"{self.container_name} still runs the old image after {WATCHTOWER_TIMEOUT // 60} minutes; "
            "Watchtower's log or notification tells why"
        )
