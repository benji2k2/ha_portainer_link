"""Version of an image without a version label, from the tags sharing its digest.

A floating tag such as `latest` or `2-rootless` points at the same manifest as
a versioned one (`2.5.6-rootless`). Docker Hub and Quay list their tags with
digests through their web APIs, which do not count against the pull quota - one
request per repository instead of one manifest request per tag. Other
registries only offer the latter, so they are left out.
"""

from __future__ import annotations

import logging
import re
import time

_LOGGER = logging.getLogger(__name__)

CACHE_SECONDS = 3600
_VERSION_TAG = re.compile(r"^v?\d+(\.\d+)*([-+].*)?$")


def _tag_score(tag: str) -> tuple[int, int, int]:
    """More version components first, then tags without a suffix, then shorter."""
    core = re.split(r"[-+]", tag, maxsplit=1)[0]
    return (core.count(".") + 1, 0 if core == tag else -1, -len(tag))


def pick_version(tags: list[str]) -> str | None:
    """The most specific version tag, without a variant suffix like -alpine.

    Prereleases keep their suffix ("2.0.0-beta.1"); a purely alphabetic one is a
    build variant and says nothing about the version.
    """
    candidates = [t for t in tags if _VERSION_TAG.match(t)]
    if not candidates:
        return None
    best = max(candidates, key=_tag_score)
    if re.fullmatch(r"v?\d+(\.\d+)*-[A-Za-z]+", best):
        best = best.split("-", 1)[0]
    return best


def _core(version: str) -> str:
    return re.split(r"[-+]", version, maxsplit=1)[0].lstrip("v")


def pick_agreed(tags: list[str]) -> str | None:
    """The most specific version, if every version tag is a prefix of it.

    "3", "3.15" and "3.15.1" agree; "0.58.0" next to "0.59.0" do not, and then
    there is no telling which one an image shared by both belongs to.
    """
    best = pick_version(tags)
    if best is None:
        return None
    top = _core(best)
    for tag in tags:
        if _VERSION_TAG.match(tag):
            core = _core(tag)
            if core != top and not top.startswith(core + "."):
                return None
    return best


class TagMap:
    """Tags by digest, and by the set of platform images behind a digest.

    Some projects push a floating tag and its versioned twin as separate
    multi-platform indexes: `timberio/vector:latest-alpine` and `0.59.0-alpine`
    have different index digests but the very same image for every platform.
    Docker Hub lists those platform images per tag, so a tag whose index
    matches nothing versioned still finds the tags built from identical images.
    Only an identical set counts - a single shared platform could be a stale
    one next to rebuilt others.

    That caution is about the other platforms. When the caller knows which
    platform image it runs, a tag carrying that very image is the same software
    for it: `python:alpine` and `3.15.1-alpine` sit in different indexes whose
    amd64 images are identical, while another platform differs.
    """

    def __init__(self) -> None:
        self.by_digest: dict[str, list[str]] = {}
        self.platforms: dict[str, frozenset[str]] = {}
        self.by_platforms: dict[frozenset[str], list[str]] = {}
        self.by_image: dict[str, list[str]] = {}

    def add(self, name: str | None, digest: str | None, images: list[dict] | None = None) -> None:
        if not name or not digest:
            return
        self.by_digest.setdefault(digest, []).append(name)
        platforms = frozenset(
            image["digest"]
            for image in images or []
            if isinstance(image, dict) and image.get("digest") and image.get("architecture") != "unknown"
        )
        if platforms:
            self.platforms.setdefault(digest, platforms)
            self.by_platforms.setdefault(platforms, []).append(name)
        for image in platforms:
            self.by_image.setdefault(image, []).append(name)

    def version(self, digest: str | None, platform: str | None = None) -> str | None:
        """The version of the tags of this index, refined by those carrying this platform image."""
        tags = self.by_digest.get(digest or "", [])
        if platform and (agreed := pick_agreed(tags + self.by_image.get(platform, []))):
            return agreed
        found = pick_version(tags)
        if found is None and digest in self.platforms:
            found = pick_version(self.by_platforms.get(self.platforms[digest], []))
        return found


class TagVersionClient:
    """Look up the tags sharing a digest on Docker Hub and Quay."""

    def __init__(self, session) -> None:
        self.session = session
        self._cache: dict[str, tuple[float, TagMap | None]] = {}

    @staticmethod
    def supports(registry: str) -> bool:
        return registry in ("registry-1.docker.io", "quay.io")

    async def _digest_map(self, registry: str, repository: str) -> TagMap | None:
        """The most recently updated tags of a repository, by digest."""
        key = f"{registry}/{repository}"
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1]

        result: TagMap | None = None
        try:
            if registry == "quay.io":
                url = f"https://quay.io/api/v1/repository/{repository}/tag/"
                params = {"limit": "100", "onlyActiveTags": "true"}
            else:
                url = f"https://hub.docker.com/v2/repositories/{repository}/tags"
                params = {"page_size": "100", "ordering": "last_updated"}
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = TagMap()
                    for tag in data.get("tags" if registry == "quay.io" else "results") or []:
                        result.add(
                            tag.get("name"),
                            tag.get("manifest_digest" if registry == "quay.io" else "digest"),
                            tag.get("images"),
                        )
                else:
                    _LOGGER.debug("Tag list of %s: HTTP %s", key, resp.status)
        except Exception as err:
            _LOGGER.debug("Tag list of %s failed: %s", key, err)
        self._cache[key] = (time.time(), result)
        return result

    async def versions(
        self, registry: str, repository: str, digests: list[str | None], platforms: list[str | None] | None = None
    ) -> list[str | None]:
        """The version for each digest, None where no versioned tag shares it.

        `platforms` holds the platform image digest running behind each index
        digest, where known; it lets tags count that carry the same image.
        """
        platforms = platforms or [None for _ in digests]
        if not self.supports(registry) or not (any(digests) or any(platforms)):
            return [None for _ in digests]
        mapping = await self._digest_map(registry, repository) or TagMap()
        return [mapping.version(d, p) if d or p else None for d, p in zip(digests, platforms)]

    def clear_cache(self) -> None:
        self._cache.clear()
