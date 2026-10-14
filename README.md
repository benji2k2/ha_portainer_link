# HA Portainer Link

HA Portainer Link is a Home Assistant custom integration for monitoring and controlling Docker containers and stacks through the Portainer API.

The integration is designed to be conservative with network traffic. Runtime container data is polled through one Home Assistant data coordinator per config entry, while registry and update checks are cached and run much less frequently.

## Features

- Container status, image, health, CPU, memory, and uptime sensors.
- Health problem binary sensors that only trigger on a failing Docker healthcheck.
- Container start/stop switches.
- Container restart and pull/update buttons.
- Stack grouping with stack start, stop, and update buttons.
- Native Home Assistant `update` entities for container image updates.
- Binary update-available sensors for automations and dashboards.
- Configurable polling intervals, update-check intervals, feature toggles, notification target, and SSL verification.
- Stable entity and device identifiers that survive Docker container recreation.

## Requirements

- Home Assistant 2023.8.0 or newer.
- Portainer CE/EE with API access.
- A Portainer endpoint ID for the Docker environment you want to monitor.
- Network access from Home Assistant to Portainer.

## Installation

### HACS

1. Open HACS.
2. Add this repository as a custom integration repository.
3. Install `HA Portainer Link`.
4. Restart Home Assistant.
5. Add the integration from **Settings > Devices & services**.

### Manual

1. Copy `custom_components/ha_portainer_link` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings > Devices & services**.

## Setup

During setup, provide:

- **Portainer URL**: for example `https://192.168.1.100:9443`.
- **Username and password** or **API key**.
- **Endpoint ID**: visible in Portainer URLs like `#!/1/docker/containers`; in that example the endpoint ID is `1`.
- **SSL verification**: disable this when your Portainer instance uses a self-signed certificate.

The config flow validates the Portainer connection, credentials or API key, and endpoint before creating the entry.

## Options

Options can be changed from the Home Assistant integration options dialog. Changing options reloads the config entry.

| Option | Default | Description |
| --- | --- | --- |
| Stack view | Enabled | Groups stack containers and exposes stack devices/buttons. |
| Resource sensors | Enabled | Adds CPU, memory, and uptime sensors. |
| Version sensors | Enabled | Adds current/available version and digest sensors. |
| Update sensors | Enabled | Adds native update entities and update-available binary sensors. |
| Stack buttons | Enabled | Adds stack start, stop, and update buttons. |
| Container buttons | Enabled | Adds restart and pull/update buttons. |
| Update interval | 5 minutes | Normal Portainer polling interval for containers, stacks, and metrics. |
| Update check interval | 360 minutes | Registry/image metadata check interval. |
| SSL verification | Enabled | Verifies the Portainer TLS certificate when enabled. |
| Notify service | Empty | Optional notify target like `notify.mobile_app_phone`; persistent notifications are used when empty. |
| Release notes | Enabled | Loads the GitHub releases between the running and the available image into the update dialog. |
| Release note sources | Empty | `image=owner/repo` pairs, comma separated, for images without an `org.opencontainers.image.source` label. Takes precedence over the label. |
| Watchtower URL / token | Empty | Optional. When set, updates are installed by Watchtower on the Docker host instead of Portainer. See below. |

## Update Detection

The integration does not pull images during background checks.

Update availability is derived from cached image metadata:

- Local image digests are read from Docker/Portainer image data.
- Remote manifest digests are read from the image registry.
- Full SHA-256 digests are compared where possible.
- Multi-architecture manifest lists are handled by comparing child manifest digests as well.
- Tags such as `latest`, `lts`, or custom release tags are not treated as semantic versions.
- If a reliable local digest is not available, the integration avoids guessing and does not report a false update.

Explicit user actions can still pull images and recreate containers or stacks:

- Container pull/update button.
- Stack update button.
- Native `update` entity install action.

## Release Notes

The update dialog shows the GitHub releases published since the running image. The repository comes from the image's
`org.opencontainers.image.source` label, or from the *Release note sources* option for images without one, for example
`redis=redis/redis, headscale/headscale=juanfont/headscale`.

Releases are chosen by the images' `org.opencontainers.image.version` labels when the available image names a release,
and by the two images' build dates otherwise. When a version is known, only releases of its major line are shown, so a
project maintaining an older line in parallel does not mix the two. A new image without a new release - a rebuild on an
updated base image, for example - says so instead of showing an unrelated release.

Images without a version label get one from the tags sharing their digest - `uptime-kuma:2-rootless` is the same
manifest as `2.5.6-rootless` and reads `2.5.6`. This works for Docker Hub and Quay, whose web APIs list tags with digests
without counting against the pull quota; other registries are left out.

GitHub allows 60 unauthenticated requests per hour and address; releases are cached for six hours per repository and
only fetched when the dialog is opened.

## Installing Through Watchtower (optional)

Without further setup, the install action uses Portainer's recreate endpoint. That fails for the containers Portainer
depends on: Portainer itself, its agent on a remote host, or a reverse proxy in front of it go away in the middle of the
call.

With a [Watchtower](https://github.com/nicholas-fedor/watchtower) URL and token in the options, the install action asks
Watchtower on that Docker host to update exactly this container (`POST /v1/update?container=<name>&async=true`) and
waits until the container runs a new image (up to ten minutes). Watchtower works next to the Docker socket and finishes
on its own, so this works for every container, Watchtower included.

Watchtower needs its HTTP API with the `update` endpoint (`WATCHTOWER_HTTP_API_ENDPOINTS=update`) and a token
(`WATCHTOWER_HTTP_API_TOKEN`). Note that enabling the endpoint stops Watchtower's own scheduled runs unless
`WATCHTOWER_HTTP_API_PERIODIC_POLLS` is set, and that a container marked `monitor-only` - by label or globally - is never
updated, not even on request. The entity's `installs_via` attribute shows which way is configured.

## Services

The integration registers these Home Assistant services:

### `ha_portainer_link.refresh`

Forces all loaded Portainer coordinators to refresh immediately.

### `ha_portainer_link.reload`

Reloads all loaded HA Portainer Link config entries.

## Architecture

- `PortainerAPI` owns the shared HTTP session, authentication headers, SSL setting, and modular API clients.
- `PortainerDataUpdateCoordinator` owns polling, metrics, image data, update availability, and caches.
- Entity platforms read state from the coordinator and do not create their own Portainer sessions.
- Entity network writes are limited to explicit user actions such as switches, buttons, and update installs.
- Device identifiers use stable entry, endpoint, host, stack, service, and container-name based keys instead of volatile Docker container IDs.

## Troubleshooting

### Cannot connect during setup

- Verify the Portainer URL is reachable from Home Assistant.
- Verify the endpoint ID. Portainer endpoint IDs are not always `1` or `2`; check the active Portainer URL or the Portainer endpoints page.
- Disable SSL verification if Portainer uses a self-signed certificate.
- Prefer an API key for testing to avoid password/session issues.

### Update entities are missing

- Check the integration options and ensure **Update sensors** are enabled.
- Reload the integration or call `ha_portainer_link.reload`.
- Call `ha_portainer_link.refresh` after changing options or testing registry behavior.

### Updates are not shown immediately

Normal container data refreshes every 5 minutes by default. Registry/update checks run every 360 minutes by default to avoid DNS and registry load. Call `ha_portainer_link.refresh` to force an immediate coordinator refresh.

### Logger configuration

Use:

```yaml
logger:
  logs:
    ha_portainer_link: debug
```

## Validation Checklist

For release testing, verify at least:

- One standalone container.
- One compose stack.
- One scaled compose service.
- One stopped container.
- One container recreate/update.
- Integration reload/unload without leaked sessions.
- Update checks without background image pulls.
- `latest`, `lts`, and similar tags do not produce guessed false updates.

## Current Limitations

- Bulk start/stop operations are not implemented.
- Container log viewing is not implemented.
- Health entities only report for containers that define a Docker `HEALTHCHECK`; the
  status sensor stays empty for every other container.
- Registry update detection depends on registry support and available local image digest data.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
