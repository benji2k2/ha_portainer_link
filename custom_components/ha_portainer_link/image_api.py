"""Image and registry helpers for HA Portainer Link."""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Any

from aiohttp.client_exceptions import ClientConnectorCertificateError

_LOGGER = logging.getLogger(__name__)


class PortainerImageAPI:
    """Handle Docker image metadata and explicit image pulls."""

    def __init__(self, base_url: str, auth, config: dict[str, Any] | None = None, ssl_verify: bool = True, session=None):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.config = config or {}
        self.ssl_verify = ssl_verify
        self.session = session
        self._cache_duration = 6 * 3600
        self._rate_limit_checks = 50
        self._rate_limit_period = 6 * 3600
        self._update_cache: dict[str, tuple[bool, float]] = {}
        self._version_cache: dict[str, tuple[str, float]] = {}
        self._digest_cache: dict[str, tuple[str, float]] = {}
        self._last_check_window = time.time()
        self._check_count = 0

    @staticmethod
    def _normalize_digest(value: str | None) -> str | None:
        """Normalize a digest-like value to sha256:... where possible."""
        if not value:
            return None
        value = str(value).strip()
        if "@" in value:
            value = value.rsplit("@", 1)[1]
        if value.startswith("sha256:"):
            return value.lower()
        if value.startswith("sha256-"):
            return "sha256:" + value.split("sha256-", 1)[1].lower()
        if re.fullmatch(r"[a-fA-F0-9]{64}", value):
            return "sha256:" + value.lower()
        return None

    @staticmethod
    def _digest_short(value: str | None) -> str:
        digest = PortainerImageAPI._normalize_digest(value)
        return digest.split(":", 1)[1][:12] if digest else "unknown"

    def _parse_image_ref(self, image: str) -> tuple[str, str, str, str | None]:
        """Return registry, repository, reference, pinned digest for an image ref."""
        ref = (image or "").strip()
        pinned_digest = None
        if "@" in ref:
            ref, pinned_digest = ref.rsplit("@", 1)
            pinned_digest = self._normalize_digest(pinned_digest)

        parts = ref.split("/")
        first = parts[0] if parts else ""
        # A registry only exists when something follows it. Without a slash the
        # whole reference is the repository, and a colon there is the tag
        # separator, not a registry port: "alpine:3.18" is library/alpine at
        # tag 3.18, not a host called "alpine:3.18".
        if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
            registry = first
            repository = "/".join(parts[1:])
        else:
            registry = "registry-1.docker.io"
            repository = "/".join(parts)

        if registry in {"docker.io", "index.docker.io"}:
            registry = "registry-1.docker.io"
        if registry == "registry-1.docker.io" and "/" not in repository:
            repository = f"library/{repository}"

        reference = pinned_digest or "latest"
        last_segment = repository.rsplit("/", 1)[-1]
        if not pinned_digest and ":" in last_segment:
            repository, reference = repository.rsplit(":", 1)

        return registry, repository, reference, pinned_digest

    def _accept_headers(self) -> dict[str, str]:
        return {
            "Accept": ", ".join(
                [
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                ]
            )
        }

    async def _request(self, method: str, url: str, **kwargs):
        session = self.session or self.auth.session
        try:
            return await session.request(method, url, ssl=self.ssl_verify, **kwargs)
        except ClientConnectorCertificateError as err:
            _LOGGER.info("Registry SSL certificate error, retrying with SSL disabled: %s", err)
            self.ssl_verify = False
            return await session.request(method, url, ssl=False, **kwargs)

    async def _get_registry_auth_token(self, authenticate_header: str | None) -> str | None:
        """Fetch a Bearer token from a registry WWW-Authenticate header."""
        if not authenticate_header or "Bearer" not in authenticate_header:
            return None
        parts = dict(re.findall(r'(\w+)="([^"]*)"', authenticate_header))
        realm = parts.get("realm")
        if not realm:
            return None
        params = {}
        if service := parts.get("service"):
            params["service"] = service
        if scope := parts.get("scope"):
            params["scope"] = scope
        session = self.session or self.auth.session
        try:
            async with session.get(realm, params=params, ssl=self.ssl_verify) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("token") or data.get("access_token")
        except ClientConnectorCertificateError:
            async with session.get(realm, params=params, ssl=False) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("token") or data.get("access_token")
        except Exception as err:
            _LOGGER.debug("Failed to fetch registry auth token: %s", err)
        return None

    async def _read_manifest_digests(self, resp, primary_digest: str | None) -> set[str]:
        """Return all digests advertised by a manifest or manifest list response."""
        digests: set[str] = set()
        if primary_digest:
            digests.add(primary_digest)
        try:
            data = await resp.json(content_type=None)
        except Exception:
            return digests

        if config_digest := self._normalize_digest(((data.get("config") or {}).get("digest"))):
            digests.add(config_digest)
        for manifest in data.get("manifests") or []:
            if digest := self._normalize_digest(manifest.get("digest")):
                digests.add(digest)
        return digests

    async def _registry_get_json(self, url: str) -> dict[str, Any] | None:
        """GET a registry document, performing the 401 -> token -> retry dance."""
        headers = self._accept_headers()
        resp = await self._request("GET", url, headers=headers)
        async with resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            if resp.status != 401:
                _LOGGER.debug("Registry request failed for %s: HTTP %s", url, resp.status)
                return None
            token = await self._get_registry_auth_token(
                resp.headers.get("WWW-Authenticate") or resp.headers.get("Www-Authenticate")
            )
        if not token:
            return None
        auth_headers = dict(headers)
        auth_headers["Authorization"] = f"Bearer {token}"
        resp = await self._request("GET", url, headers=auth_headers)
        async with resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            _LOGGER.debug("Authenticated registry request failed for %s: HTTP %s", url, resp.status)
        return None

    @staticmethod
    def _select_platform_manifest(index: dict[str, Any], local_image: dict[str, Any] | None) -> str | None:
        """Pick the manifest matching the local image's platform from an index.

        Attestation entries carry platform "unknown/unknown" and are skipped:
        they are exactly what gets rewritten without the image changing.
        """
        local = local_image or {}
        want_os = (local.get("Os") or "linux").lower()
        want_arch = (local.get("Architecture") or "").lower()
        want_variant = (local.get("Variant") or "").lower()

        candidates = []
        for manifest in index.get("manifests") or []:
            platform = manifest.get("platform") or {}
            p_os = str(platform.get("os") or "").lower()
            p_arch = str(platform.get("architecture") or "").lower()
            if p_os in ("", "unknown") or p_arch in ("", "unknown"):
                continue
            candidates.append((p_os, p_arch, str(platform.get("variant") or "").lower(), manifest.get("digest")))

        if not candidates:
            return None
        for p_os, p_arch, p_variant, digest in candidates:
            if p_os == want_os and p_arch == want_arch and (not want_variant or p_variant == want_variant):
                return digest
        for p_os, p_arch, _variant, digest in candidates:
            if p_os == want_os and p_arch == want_arch:
                return digest
        return None

    async def get_remote_config_digest(
        self, image_name: str, local_image: dict[str, Any] | None = None
    ) -> str | None:
        """Return the remote image config digest, which is docker's image id.

        The manifest (index) digest moves whenever anything in the list is
        rewritten - re-pushed build attestations do it without touching a single
        layer - so comparing it reports updates that ship no new software. The
        config digest only changes when the image itself does, which is what
        docker compares locally.
        """
        try:
            registry, repository, reference, _pinned = self._parse_image_ref(image_name)
            if not repository or reference == "unknown":
                return None
            document = await self._registry_get_json(
                f"https://{registry}/v2/{repository}/manifests/{reference}"
            )
            if not document:
                return None
            if document.get("manifests"):
                child = self._select_platform_manifest(document, local_image)
                if not child:
                    return None
                document = await self._registry_get_json(
                    f"https://{registry}/v2/{repository}/manifests/{child}"
                )
                if not document:
                    return None
            return self._normalize_digest((document.get("config") or {}).get("digest"))
        except Exception as err:
            _LOGGER.debug("Failed to fetch remote config digest for %s: %s", image_name, err)
            return None

    async def get_remote_created(
        self, image_name: str, local_image: dict[str, Any] | None = None
    ) -> str | None:
        """Return when the remote image was built, from its config blob.

        This costs one request beyond resolving the config digest, so callers
        should ask only when an update actually exists rather than for every
        container on every cycle.
        """
        try:
            registry, repository, reference, _pinned = self._parse_image_ref(image_name)
            if not repository or reference == "unknown":
                return None
            document = await self._registry_get_json(
                f"https://{registry}/v2/{repository}/manifests/{reference}"
            )
            if not document:
                return None
            if document.get("manifests"):
                child = self._select_platform_manifest(document, local_image)
                if not child:
                    return None
                document = await self._registry_get_json(
                    f"https://{registry}/v2/{repository}/manifests/{child}"
                )
                if not document:
                    return None
            config_digest = (document.get("config") or {}).get("digest")
            if not config_digest:
                return None
            blob = await self._registry_get_json(
                f"https://{registry}/v2/{repository}/blobs/{config_digest}"
            )
            return (blob or {}).get("created")
        except Exception as err:
            _LOGGER.debug("Failed to fetch remote build date for %s: %s", image_name, err)
            return None

    async def _get_remote_manifest_digests(self, image_name: str) -> tuple[str | None, set[str]]:
        """Return primary remote digest and all manifest-list child digests."""
        try:
            registry, repository, reference, _pinned = self._parse_image_ref(image_name)
            if not repository or reference == "unknown":
                return None, set()
            url = f"https://{registry}/v2/{repository}/manifests/{reference}"
            headers = self._accept_headers()

            resp = await self._request("GET", url, headers=headers)
            async with resp:
                if resp.status == 200:
                    primary = self._normalize_digest(resp.headers.get("Docker-Content-Digest"))
                    return primary, await self._read_manifest_digests(resp, primary)
                if resp.status != 401:
                    _LOGGER.debug("Registry manifest request failed for %s: HTTP %s", image_name, resp.status)
                    return None, set()
                token = await self._get_registry_auth_token(
                    resp.headers.get("WWW-Authenticate") or resp.headers.get("Www-Authenticate")
                )

            if not token:
                return None, set()
            auth_headers = dict(headers)
            auth_headers["Authorization"] = f"Bearer {token}"
            resp = await self._request("GET", url, headers=auth_headers)
            async with resp:
                if resp.status == 200:
                    primary = self._normalize_digest(resp.headers.get("Docker-Content-Digest"))
                    return primary, await self._read_manifest_digests(resp, primary)
                _LOGGER.debug("Authenticated registry manifest request failed for %s: HTTP %s", image_name, resp.status)
        except Exception as err:
            _LOGGER.debug("Failed to fetch remote manifest digest for %s: %s", image_name, err)
        return None, set()

    async def _get_remote_manifest_digest(self, image_name: str) -> str | None:
        """Return the primary remote manifest digest for compatibility callers."""
        primary, _digests = await self._get_remote_manifest_digests(image_name)
        return primary

    @staticmethod
    def _repo_matches_image(repo_digest: str, image_name: str) -> bool:
        """Return whether a RepoDigest belongs to the same repository as image_name."""
        if "@" not in repo_digest:
            return False
        repo_part = repo_digest.split("@", 1)[0]
        try:
            registry, repository, _reference, _digest = PortainerImageAPI("", None)._parse_image_ref(image_name)
        except Exception:
            return True
        candidates = {
            f"{registry}/{repository}",
            repository,
        }
        if registry == "registry-1.docker.io":
            candidates.add(f"docker.io/{repository}")
            candidates.add(f"index.docker.io/{repository}")
            # Docker records official images without their "library/" prefix,
            # so "alpine:3.18" carries the RepoDigest "alpine@sha256:...".
            short = repository[len("library/"):] if repository.startswith("library/") else repository
            candidates.update({short, f"docker.io/{short}", f"index.docker.io/{short}"})
        return repo_part in candidates or repo_part.endswith(f"/{repository}")

    def _local_repo_digest(self, image_data: dict[str, Any], image_name: str | None = None) -> str | None:
        """Return the local repo digest matching image_name when Docker has one."""
        repo_digests = image_data.get("RepoDigests") or []
        for repo_digest in repo_digests:
            if image_name and not self._repo_matches_image(str(repo_digest), image_name):
                continue
            digest = self._normalize_digest(str(repo_digest))
            if digest:
                return digest
        return None

    async def check_image_updates(self, endpoint_id: int, container_id: str) -> bool:
        """Check for image updates by comparing local and remote manifest digests."""
        try:
            current = await self.get_current_digest(endpoint_id, container_id)
            available = await self.get_available_digest(endpoint_id, container_id)
            if current == "unknown" or available == "unknown":
                return False
            return current != available
        except Exception as err:
            _LOGGER.debug("Image update check failed for %s: %s", container_id, err)
            return False

    async def pull_image_update(self, endpoint_id: int, container_id: str) -> bool:
        """Pull the latest image for a container. This is explicit user action only.

        Delegates to PortainerAPI, which consumes the streamed pull response.
        Returning as soon as the status code arrives cancels the pull, since the
        docker API aborts it when the connection closes.
        """
        try:
            return await self.auth.pull_image_update(endpoint_id, container_id)
        except Exception as err:
            _LOGGER.error("Pull failed for container %s: %s", container_id, err)
            return False

    async def get_image_info(self, endpoint_id: int, image_id: str) -> dict[str, Any] | None:
        """Get detailed information about a local Docker image."""
        try:
            url = f"{self.base_url}/api/endpoints/{endpoint_id}/docker/images/{image_id}/json"
            session = self.session or self.auth.session
            async with session.get(url, headers=self.auth.get_headers(), ssl=self.ssl_verify) as resp:
                if resp.status == 200:
                    return await resp.json()
        except ClientConnectorCertificateError:
            session = self.session or self.auth.session
            async with session.get(url, headers=self.auth.get_headers(), ssl=False) as resp:
                if resp.status == 200:
                    self.ssl_verify = False
                    return await resp.json()
        except Exception as err:
            _LOGGER.debug("Failed to get image info for %s: %s", image_id, err)
        return None

    def extract_version_from_image(self, image_data: dict[str, Any]) -> str:
        """Extract a human-readable version from local image metadata."""
        try:
            for tag in image_data.get("RepoTags") or []:
                if ":" in tag:
                    version = tag.rsplit(":", 1)[-1]
                    if version and version != "latest":
                        return version
            labels = image_data.get("Labels") or {}
            for label in (
                "org.opencontainers.image.version",
                "version",
                "VERSION",
                "app.version",
                "build.version",
            ):
                if labels.get(label):
                    return str(labels[label])
            if created := image_data.get("Created"):
                try:
                    parsed = dt.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    return parsed.strftime("%Y.%m.%d")
                except Exception:
                    pass
            return self._digest_short(image_data.get("Id"))
        except Exception as err:
            _LOGGER.debug("Failed to extract image version: %s", err)
            return "unknown"

    async def get_available_version(self, endpoint_id: int, image_name: str) -> str:
        """Return a display version for the remote image without causing update state."""
        if image_name in self._version_cache:
            cached, ts = self._version_cache[image_name]
            if time.time() - ts < self._cache_duration:
                return cached
        try:
            _registry, _repository, reference, pinned_digest = self._parse_image_ref(image_name)
            version = pinned_digest or reference or "unknown"
            self._version_cache[image_name] = (version, time.time())
            return version
        except Exception:
            return "unknown"

    def _check_rate_limit(self) -> bool:
        now = time.time()
        if now - self._last_check_window > self._rate_limit_period:
            self._last_check_window = now
            self._check_count = 0
        if self._check_count >= self._rate_limit_checks:
            return False
        self._check_count += 1
        return True

    async def get_current_digest(self, endpoint_id: int, container_id: str) -> str:
        """Return the local manifest digest for a container image, or unknown."""
        try:
            container_info = await self._get_container_info(endpoint_id, container_id)
            image_name = ((container_info or {}).get("Config") or {}).get("Image")
            image_id = (container_info or {}).get("Image")
            if not image_id:
                return "unknown"
            image_info = await self.get_image_info(endpoint_id, image_id)
            if not image_info:
                return "unknown"
            repo_digest = self._local_repo_digest(image_info, image_name)
            if repo_digest:
                return repo_digest
            # Do not compare remote manifest digests with local image IDs; they are different objects.
            return "unknown"
        except Exception as err:
            _LOGGER.debug("Failed to get current digest for %s: %s", container_id, err)
            return "unknown"

    async def get_available_digest(self, endpoint_id: int, container_id: str) -> str:
        """Return the remote manifest digest for a container image, or unknown."""
        try:
            container_info = await self._get_container_info(endpoint_id, container_id)
            image_name = ((container_info or {}).get("Config") or {}).get("Image")
            if not image_name:
                return "unknown"
            if image_name in self._digest_cache:
                cached, ts = self._digest_cache[image_name]
                if time.time() - ts < self._cache_duration:
                    return cached
            if not self._check_rate_limit():
                return "unknown"
            current = await self.get_current_digest(endpoint_id, container_id)
            primary, remote_digests = await self._get_remote_manifest_digests(image_name)
            if current != "unknown" and current in remote_digests:
                value = current
            else:
                value = primary or "unknown"
            if value != "unknown":
                self._digest_cache[image_name] = (value, time.time())
            return value
        except Exception as err:
            _LOGGER.debug("Failed to get available digest for %s: %s", container_id, err)
            return "unknown"

    async def _get_container_info(self, endpoint_id: int, container_id: str) -> dict[str, Any] | None:
        """Get container inspection data."""
        try:
            url = f"{self.base_url}/api/endpoints/{endpoint_id}/docker/containers/{container_id}/json"
            session = self.session or self.auth.session
            async with session.get(url, headers=self.auth.get_headers(), ssl=self.ssl_verify) as resp:
                if resp.status == 200:
                    return await resp.json()
        except ClientConnectorCertificateError:
            session = self.session or self.auth.session
            async with session.get(url, headers=self.auth.get_headers(), ssl=False) as resp:
                if resp.status == 200:
                    self.ssl_verify = False
                    return await resp.json()
        except Exception as err:
            _LOGGER.debug("Failed to get container info for %s: %s", container_id, err)
        return None
