"""Release notes from the GitHub releases of an image's source repository.

Images built with the OCI conventions name their source repository in the
`org.opencontainers.image.source` label. For the ones that do not, the options
take a short list of `image=owner/repo` pairs. Nothing here is specific to a
particular image or host.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Any

from .entity import parse_docker_time

_LOGGER = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
SOURCE_LABEL = "org.opencontainers.image.source"
VERSION_LABEL = "org.opencontainers.image.version"

# A repository's releases rarely change within hours, and GitHub allows only 60
# unauthenticated requests per hour and address - so they are cached, and a
# failure is cached too, briefly, rather than retried on every dialog open.
CACHE_SECONDS = 6 * 3600
FAILURE_CACHE_SECONDS = 15 * 60
MAX_RELEASES = 5
MAX_BODY_CHARS = 6000
# Some projects publish the image before the GitHub release exists, so a
# release up to a day after the new image's build date still belongs to it.
PUBLISH_SLACK = dt.timedelta(days=1)

_GITHUB_REPO = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def normalize_image(image: str | None) -> str:
    """Repository part of an image reference, without tag, digest or Docker Hub prefixes.

    "docker.io/library/redis:7" and "redis" both become "redis", so a source
    override written the way it appears in a compose file matches either.
    """
    if not image:
        return ""
    ref = str(image).strip().lower().split("@", 1)[0]
    last_slash = ref.rfind("/")
    if ":" in ref[last_slash + 1:]:
        ref = ref.rsplit(":", 1)[0]
    for prefix in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
    if ref.startswith("library/"):
        ref = ref[len("library/"):]
    return ref


def parse_overrides(text: str | None) -> dict[str, str]:
    """Parse "image=owner/repo" pairs, separated by commas or line breaks."""
    overrides: dict[str, str] = {}
    for item in re.split(r"[,\n]", text or ""):
        image, sep, repo = item.partition("=")
        repo = github_repo(repo.strip()) or repo.strip().strip("/")
        if sep and normalize_image(image) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            overrides[normalize_image(image)] = repo
    return overrides


def github_repo(source: str | None) -> str | None:
    """Return "owner/repo" for a GitHub URL, or None for anything else."""
    match = _GITHUB_REPO.search(source or "")
    if not match:
        return None
    owner, repo = match.groups()
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return f"{owner}/{repo}"


def resolve_repo(image: str | None, source: str | None, overrides: dict[str, str]) -> str | None:
    """An override wins over the label, so a wrong or missing label can be fixed."""
    return overrides.get(normalize_image(image)) or github_repo(source)


def _plain_version(value: str | None) -> str:
    text = str(value or "").strip().lower()
    return text[1:] if text.startswith("v") and text[1:2].isdigit() else text


def _major(value: str | None) -> str | None:
    match = re.match(r"(\d+)\.", _plain_version(value))
    return match.group(1) if match else None


def _same_line(tag: str | None, major: str) -> bool:
    """Whether a release belongs to the image's major line.

    A tag without a dotted version ("v3") still counts; one that does not start
    with a version at all is a side project sharing the repository - Vector
    publishes its dev tool as "vdev-v0.3.30" next to "v0.59.0".
    """
    found = _major(tag)
    if found is not None:
        return found == major
    return bool(re.match(r"\d", _plain_version(tag)))


def _published(release: dict[str, Any]) -> dt.datetime | None:
    return parse_docker_time(release.get("published_at") or release.get("created_at"))


def select_releases(
    releases: list[dict[str, Any]],
    installed_version: str | None = None,
    available_version: str | None = None,
    installed_built: Any = None,
    available_built: Any = None,
) -> list[dict[str, Any]]:
    """Releases between the running image and the available one, newest first.

    Version labels decide when the available one names a release; build dates
    decide otherwise, which also covers images that carry no version label.
    """
    wanted = _plain_version(available_version)
    # Projects maintaining an older line in parallel - Traefik ships v2.11
    # next to v3 - publish releases of both on the same day. Once a version is
    # known, only its major line belongs to this image.
    major = _major(available_version) or _major(installed_version)
    candidates = [
        r for r in releases
        if not r.get("draft")
        and (not r.get("prerelease") or (wanted and _plain_version(r.get("tag_name")) == wanted))
        and (major is None or _same_line(r.get("tag_name"), major))
    ]
    candidates.sort(key=lambda r: _published(r) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)

    tags = [_plain_version(r.get("tag_name")) for r in candidates]
    installed = _plain_version(installed_version)
    built_from = parse_docker_time(installed_built)

    if wanted and wanted in tags:
        start = tags.index(wanted)
        if installed and installed in tags and tags.index(installed) > start:
            return candidates[start: tags.index(installed)]
        selected = []
        for release in candidates[start:]:
            published = _published(release)
            if selected and (built_from is None or published is None or published <= built_from):
                break
            selected.append(release)
        return selected

    if built_from is None:
        return []
    built_to = parse_docker_time(available_built)
    upper = built_to + PUBLISH_SLACK if built_to else None
    return [
        r for r in candidates
        if (published := _published(r)) is not None
        and published > built_from
        and (upper is None or published <= upper)
    ]


def render(repo: str, selected: list[dict[str, Any]]) -> str:
    """Markdown for Home Assistant's update dialog."""
    url = f"https://github.com/{repo}/releases"
    if not selected:
        return (
            f"No GitHub release of [{repo}]({url}) since the running image was built. "
            "The new image is most likely a rebuild, for example with an updated base image."
        )
    parts = []
    for release in selected[:MAX_RELEASES]:
        title = release.get("name") or release.get("tag_name") or "?"
        published = _published(release)
        heading = f"## {title}" + (f" ({published.date().isoformat()})" if published else "")
        body = (release.get("body") or "").strip() or "_No release notes._"
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS].rstrip() + f"\n\n… [continued on GitHub]({release.get('html_url') or url})"
        parts.append(f"{heading}\n\n{body}")
    if len(selected) > MAX_RELEASES:
        parts.append(f"[{len(selected) - MAX_RELEASES} older releases on GitHub]({url})")
    return "\n\n".join(parts)


class ReleaseNotesClient:
    """Fetch and cache GitHub releases per repository."""

    def __init__(self, session) -> None:
        self.session = session
        self._cache: dict[str, tuple[float, list[dict[str, Any]] | None]] = {}

    async def releases(self, repo: str) -> list[dict[str, Any]] | None:
        now = time.time()
        cached = self._cache.get(repo)
        if cached:
            stamp, value = cached
            if now - stamp < (CACHE_SECONDS if value is not None else FAILURE_CACHE_SECONDS):
                return value

        value = None
        try:
            async with self.session.get(
                f"{GITHUB_API}/repos/{repo}/releases",
                params={"per_page": "30"},
                headers={"Accept": "application/vnd.github+json", "User-Agent": "ha_portainer_link"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    value = data if isinstance(data, list) else None
                else:
                    _LOGGER.debug("GitHub releases of %s: HTTP %s", repo, resp.status)
        except Exception as err:
            _LOGGER.debug("GitHub releases of %s failed: %s", repo, err)
        self._cache[repo] = (now, value)
        return value
