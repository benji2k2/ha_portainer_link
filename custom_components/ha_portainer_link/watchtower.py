"""Optional: let a Watchtower instance on the Docker host carry out an update.

Portainer recreates a container through its own API, which fails for the
containers Portainer itself depends on - Portainer, its agent, or the reverse
proxy in front of it go away in the middle of the call. Watchtower runs next to
the Docker socket on the host and finishes the job on its own once triggered
with `async=true`, so it can replace any of them, itself included.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

_REGEX_SPECIAL = set(".+*?()|[]{}^$\\")


class WatchtowerError(Exception):
    """Watchtower refused or could not be reached."""


def container_pattern(name: str) -> str:
    """Watchtower matches `container` as an anchored regex; keep a name literal.

    Docker names may contain dots, which would otherwise match any character.
    """
    return "".join(f"\\{ch}" if ch in _REGEX_SPECIAL else ch for ch in name.strip("/"))


class WatchtowerClient:
    """Trigger targeted updates through Watchtower's HTTP API."""

    def __init__(self, session, url: str, token: str, ssl_verify: bool = True) -> None:
        self.session = session
        self.url = url.strip().rstrip("/")
        self.token = token
        self.ssl_verify = ssl_verify

    async def update_container(self, name: str) -> None:
        """Ask for an update of one container and return once Watchtower accepted it."""
        try:
            async with self.session.post(
                f"{self.url}/v1/update",
                params={"container": container_pattern(name), "async": "true"},
                headers={"Authorization": f"Bearer {self.token}"},
                ssl=self.ssl_verify,
            ) as resp:
                status = resp.status
                text = "" if status in (200, 202) else (await resp.text())[:200]
        except Exception as err:
            raise WatchtowerError(f"Watchtower at {self.url} is not reachable: {err}") from err

        if status in (200, 202):
            return
        if status == 401:
            raise WatchtowerError("Watchtower rejected the token (HTTP 401)")
        if status == 404:
            raise WatchtowerError("Watchtower has no update endpoint; enable it with WATCHTOWER_HTTP_API_ENDPOINTS=update")
        if status == 429:
            raise WatchtowerError("Watchtower is already running an update; try again in a minute")
        raise WatchtowerError(f"Watchtower answered HTTP {status}" + (f": {text}" if text else ""))
