# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.2] - 2026-09-04

### Fixed
- Fixed the deprecation warning about `via_device`, which Home Assistant drops in 2027.8.0. Device links now use `via_device_id`, the registry id of the parent device, which means that device has to exist before the entities pointing at it are added - so the instance device is registered during setup rather than left to whichever platform happens to run first. The old `via_device` tuple is still sent on Home Assistant versions predating `via_device_id`: an unrecognised `device_info` key raises and stops the entity from being created, so the choice is made from what the running Home Assistant declares rather than assumed.

## [0.9.1] - 2026-09-02

### Added
- Added a way to check for updates on demand, which 0.9.0 removed by accident: persisting the registry timestamp means a reload no longer forces a sweep, and with a six hour interval nothing else did either. There is now a "Check for updates" button on the instance device, reporting how many containers it checked and how many have an update, and a `check_updates` action that does the same and returns the result to the caller.

### Removed
- Removed `HANDOFF_RATE_LIMIT.md`, whose analysis is captured in the 0.9.0 entry and commit. It remains in the history.

### Changed
- The test suite is rebuilt around a shared `_harness.py` that stubs Home Assistant once instead of in every file, and covers image reference parsing, digests, device identity and cleanup, health detection, instance aggregates, pull and install behaviour, option wiring, prune reporting and the registry request budget.

## [0.9.0] - 2026-09-02

### Fixed
- Fixed registry checks exhausting Docker Hub's anonymous pull quota, which surfaced as `toomanyrequests` on unrelated `docker compose up` runs sharing the same public IP. Three causes compounded:
  - The update check interval is documented in minutes but was compared against a seconds timestamp, so the 6 hour default ran every 6 minutes - sixty times too often.
  - The timestamp of the last check lived only in memory, so every Home Assistant restart triggered a full sweep no matter how recently one had run. It is now persisted per config entry, along with the cached image data and update states, which also means sensors have values immediately after a restart instead of after the next sweep.
  - Each image was walked three times over - once for the manifest digest, once for the image id, once for the build date - and every request re-fetched a bearer token after a 401. A single walk now yields all three, and tokens are reused while valid. That is five manifest requests per image down to two, and one token fetch instead of three. Registries count manifest requests as pulls, so this is the difference between a check that costs nothing and one that costs like a pull.
- Registry sweeps are spread by up to five minutes of jitter, so restarts and multiple instances do not line up on the same second.

### Added
- Added `tests/`, a set of standalone scripts that stub Home Assistant and exercise the integration directly. No pytest, no Home Assistant install: `for t in tests/test_*.py; do python3 "$t"; done`.

## [0.8.1] - 2026-08-28

### Fixed
- Fixed the prune button still overstating how many images it removed. 0.6.6 replaced the raw entry count with the number of `Deleted` entries, but those are not one per image either: deleting one image also frees its config and every layer no other image holds, each reported separately. Removing a single image with eleven layers therefore claimed twelve. The image count now comes from the change in how many images were deletable before and after the prune, which is the only figure in play that actually counts images. The response's entry counts remain as a `last_freed_content_ids` attribute for diagnostics, and the `prune_images` action reports the same way.

## [0.8.0] - 2026-08-27

### Added
- Added an "Image Built" timestamp sensor per container, reporting when the running image was built. Home Assistant renders it relatively, so how stale a container is becomes readable at a glance - something the version sensors cannot show, since a rolling tag reads "latest" no matter how old the image behind it is. It comes from image data the coordinator already fetches and costs no extra requests. Gated by the existing version sensors option.
- The build date of an available update is exposed as an attribute on that sensor and on the update entity. It needs the remote config blob, so it is fetched only when an update actually exists rather than for every container on every cycle.

## [0.7.3] - 2026-08-27

### Changed
- The update entity now reports image ids rather than manifest digests, completing the change the digest sensors got in 0.7.1. Installed and available versions fall back to the manifest digest only when no image id can be resolved, and then to the version string. The release summary states the image id transition and reports the manifest digest alongside it, since that digest can move without the image changing.

## [0.7.2] - 2026-08-27

### Changed
- Buttons no longer send a notification when they succeed. Every button now records its outcome on itself - `last_result`, `last_run` and a `last_result_ok` flag, plus the counts for a prune - so a dashboard card shows what happened the moment the press finishes. Notifications are kept for failures, which are the only outcome worth interrupting someone over, and still carry the reason Portainer gave. Restart, pull, the three stack buttons and prune all behave the same way.

## [0.7.1] - 2026-08-27

### Changed
- Digest sensors now show the image id rather than the manifest digest. Since 0.6.7 the update decision compares image ids, so showing manifest digests meant the two sensors could differ while the update entity correctly reported nothing to install - the manifest digest moves whenever the manifest list is rewritten, which re-pushed build attestations do without changing a layer. The manifest digest stays available as a `manifest_digest` attribute, since that is the digest an image is pinned with, and the sensors fall back to it when no image id can be resolved.

### Fixed
- The local image id is now recorded on every update cycle instead of only inside the registry check window, so the current digest sensor is populated straight away rather than after the first registry check.

## [0.7.0] - 2026-08-27

### Added
- Added a "Deletable images" sensor on the instance device, counting what the prune button would actually remove in its configured scope. Attributes list the images by name, split them into dangling and tagged-but-unused, and report the reclaimable size along with how many images exist and how many are in use.
- Added a `prune_images` action that returns its result to the caller instead of sending a notification, for anyone who wants the outcome in front of them when the action finishes. It reports the scope, how many images and tag references went, the space reclaimed, and what is still deletable afterwards. Takes an optional `all_unused` flag that overrides the configured scope for a single run.
- The prune button now exposes its last outcome as attributes, so a dashboard can show what happened without relying on notifications.

### Changed
- Digest sensors now show a shortened digest (12 characters, no algorithm prefix) instead of 71 characters of hex. The full value moves to a `digest` attribute, and the update comparison keeps using the full digest internally. The update entity's release summary is shortened the same way.
- The prune button explains when images remain deletable after a prune: docker keeps any image another image or container still depends on, so a shared base layer can survive a prune that removed everything built on it.

## [0.6.7] - 2026-08-27

### Fixed
- Fixed updates being offered when nothing had actually changed. Update detection compared the manifest index digest, which moves whenever anything in the manifest list is rewritten - re-pushed build attestations do it without touching a single layer. Installing then pulled an identical image, so no new image appeared and the container was recreated for nothing. Detection now compares the image config digest, which is the image id docker assigns and only changes when the image really does. This is what `docker pull` and Watchtower effectively compare, which is why they stayed quiet in the same situation. Resolving it costs one extra registry request per image and no layer downloads; attestation entries in an index carry the platform `unknown/unknown` and are skipped when picking the platform manifest. The manifest digests remain available as sensors, now purely informational, and the previous comparison still serves as a fallback where a config digest cannot be resolved.

## [0.6.6] - 2026-08-27

### Fixed
- Fixed the prune button overstating how much it removed. `ImagesDeleted` holds one entry per removed tag and one per removed image id, so deleting a single image commonly reports three entries; counting the list length claimed three images. Deleted and untagged entries are now counted separately and reported as what they are.

## [0.6.5] - 2026-08-27

### Added
- Added a `prune_all_unused` option. The prune button removes only dangling images by default - unused *and* untagged - which silently skips an unused image that still carries any tag. Enabling this widens it to every unused image, including those of stopped containers. Off by default, since the button fires without confirmation.
- The prune button exposes its current scope as a `scope` attribute, and reports "dangling" or "unused" in its notification. When it removes nothing in the default mode it now explains why, rather than just reporting zero.

## [0.6.4] - 2026-08-27

### Fixed
- Fixed `enable_prune_button` never appearing in the options form, which made the prune button added in 0.6.2 unreachable. The option key had been inserted as a second positional argument to `vol.Required`, where voluptuous reads it as the error message rather than a schema key. That is valid Python, so it passed a syntax check while silently dropping the option.

## [0.6.3] - 2026-08-27

### Fixed
- Fixed digest sensors and update detection for official Docker Hub images carrying a tag, such as `alpine:3.18`, `nginx:1.25` or `redis:7`. `_parse_image_ref` treated the tag separator as a registry port whenever the reference had no slash, so `alpine:3.18` parsed as a registry named "alpine:3.18" with an empty repository. The remote lookup then had nothing to query and the local comparison nothing to match, leaving both Current Digest and Available Digest at "unknown" and update detection permanently silent. A registry component now requires something to follow it. Images with a namespace (`linuxserver/plex:latest`) or an explicit registry were unaffected, which is why only official images misbehaved.
- Fixed the local digest lookup for those same images. Docker records official images in `RepoDigests` without their `library/` prefix, so `alpine:3.18` carries `alpine@sha256:...` and no longer matched once the repository resolved to `library/alpine`.

## [0.6.2] - 2026-08-27

### Fixed
- Fixed image pulls, which never actually downloaded anything. The docker API streams pull progress and documents that "the pull is cancelled if the HTTP connection is closed", but the response body was never read: the status code arrives with the headers, before a single byte is fetched, so the connection closed and the daemon aborted the pull straight away.
- Fixed pull failures being reported as success. The API answers 200 and reports errors inside the streamed body, so checking only the status code meant the pull button could never fail. The stream is now parsed and any `error` entry fails the operation.
- Fixed the image reference being passed to `fromImage` unsplit. Without a tag the API pulls *every* tag of a repository, unlike `docker pull`, so an untagged container image triggered a mass download. Name and tag are now separated, defaulting to `latest`, with digests and registry ports (`registry.local:5000/app`) handled correctly.
- Pulls now use their own generous timeout instead of the session default, which could abort a large image mid-download.
- Removed the duplicate pull implementation in `image_api.py`, which carried the same three bugs and was never called; it now delegates.

- Fixed the update entity only pulling the image. Installing an update downloaded the new image and left the container running the old one, so pressing install appeared to do nothing. It now calls Portainer's recreate endpoint, which pulls and rebuilds the container from its existing configuration, preserving volumes, networks, labels and restart policy. Containers running with `--rm` and containers pinned to an image digest are refused with an explanation instead of being destroyed or silently doing nothing, matching the cases Portainer's own UI blocks.

- Failures now say what went wrong. Pull and update errors were logged in full but reached the user as "Failed to pull image for plex", so the reason was only visible to someone reading the Home Assistant log. Portainer's and docker's error responses are now parsed into one readable sentence and carried into the update dialog and the notifications, e.g. "Could not update plex: HTTP 409: container is part of a stack" or "Docker refused to pull plex:latest - manifest unknown". Connection failures name the underlying error instead of disappearing into a generic false.

### Added
- Added an optional "Delete unused images" button on the instance device, off by default. It prunes only dangling images (unused *and* untagged) and reports how many were removed and how much space was reclaimed. Pruning all unused images would also drop the images of stopped containers, which a button that fires without confirmation should not do.

## [0.6.1] - 2026-08-27

### Added
- Added an instance device per Portainer environment, named after the environment itself, with all containers and stacks linked to it via `via_device`.
- Added `unhealthy_containers` sensor on that instance device, counting containers whose healthcheck currently fails. Attributes list the affected container names, how many containers define a `HEALTHCHECK` at all, and the total container count.
- Added instance sensors for total, running and stopped containers, stack count, and containers with an available update. All are aggregated from data the coordinator already holds, so they cost no extra API requests.
- Added the docker daemon version, operating system and architecture to the instance device itself as `sw_version`, `model` and `hw_version`, plus diagnostic sensors for CPU count and total memory, read from `GET /api/endpoints/{id}/docker/info`. Every field is optional; an endpoint that does not answer still yields a usable device.
- Added `enable_healthcheck_sensors` option so the per-container health sensor and health problem sensor can be turned off, matching the existing toggles for resource, version, and update sensors.
- Added `enable_instance_device` option to turn the instance device and its sensor off.

### Changed
- Entity names now describe only the property they report. `has_entity_name` lets Home Assistant compose the display name from the device, so a container's sensors read "Uptime" and "Status" on its device page instead of repeating the container name in every row. Newly created entities get ids of the form `sensor.<container>_<environment>_<property>`; entities that already exist keep the id Home Assistant assigned them on first registration.
- Container and stack devices are now suffixed with the Portainer environment name instead of the URL host, which was frequently just "portainer" and said nothing about which environment a container belonged to. The host name remains the fallback when Portainer does not report an environment name. Device identifiers are unchanged, so existing devices, entity IDs and history are unaffected.

### Fixed
- Fixed leftover devices after an option change. Turning off stack view moved container entities onto new devices while the old stack devices stayed behind as unavailable, because Home Assistant keeps registry entries for entities an integration no longer provides, so the previous cleanup never saw those devices as empty. Cleanup now compares each device against the identifiers the current options actually produce.
- Added `async_remove_config_entry_device`, which is what makes Home Assistant offer the delete button for a device at all. Devices the current configuration still produces stay protected.

## [0.6.0] - 2026-06-12

### Fixed
- Centralized Portainer API/session lifecycle through one coordinator per config entry.
- Removed entity-level polling that caused excessive DNS/API traffic.
- Fixed stopped-container uptime to report not running/unknown instead of stale elapsed time.
- Fixed documented reload and refresh services by registering service handlers.
- Fixed Home Assistant 2026 options-flow compatibility.
- Added stable container device identifiers that survive Docker container ID changes.
- Added native update entities and corrected update availability to use cached coordinator image data.
- Reworked update availability detection to compare full OCI manifest digests and avoid false positives for multi-arch images or images without local `RepoDigests`.

### Changed
- Added runtime options for feature toggles, polling intervals, update-check intervals, SSL verification, and optional notification target.
- Enabled update sensors by default, with infrequent cached registry checks to avoid registry load.
- Removed unused `requests` manifest requirement.

## [0.5.9] - 2025-12-07

### Removed
- Removed automatic Lovelace dashboard creation functionality
- Removed `ha_portainer_link.create_dashboard` service
- Removed `ha_portainer_link.diagnose_dashboard` service
- Removed dashboard configuration options from config flow
- Removed dashboard-related code and dependencies

### Changed
- Simplified integration setup by removing dashboard creation step
- Config flow now directly creates entry after basic configuration
- Reduced codebase complexity by removing dashboard implementation

## [0.5.8] - 2025-12-06

### Fixed
- Fixed silent failures in automatic dashboard creation
- Improved error logging to make dashboard creation failures visible in logs
- Changed debug/warning level logs to error level for dashboard creation issues
- Added full exception stack traces to dashboard creation error logs
- Enhanced error messages when Lovelace dashboard store cannot be found

### Changed
- Dashboard creation errors now log at ERROR level instead of WARNING/DEBUG for better visibility
- Improved diagnostic messages to help identify dashboard creation failures

## [0.5.7] - 2025-12-06

### Fixed
- Improved dashboard creation compatibility with multiple Home Assistant versions
- Enhanced error handling in dashboard creation service
- Fixed dashboard rebuild logic for delayed entity loading

### Changed
- Improved dashboard creation logging and error messages
- Enhanced compatibility detection for Lovelace dashboard API

## [0.5.6] - 2025-12-05

### Fixed
- Dashboard creation stability improvements
- Fixed entity grouping for dashboard views
- Improved stack detection in dashboard generation

## [0.5.5] - 2025-12-04

### Fixed
- Enhanced dashboard API detection for newer Home Assistant versions
- Improved error recovery in dashboard creation process
- Fixed dashboard metadata synchronization

## [0.5.4] - 2025-12-03

### Fixed
- Dashboard creation compatibility fixes for Home Assistant 2024.x
- Improved Lovelace dashboard store detection
- Enhanced fallback mechanisms for dashboard API access

## [0.5.3] - 2025-12-02

### Fixed
- Improved dashboard entity sorting and grouping
- Fixed dashboard view slug generation
- Enhanced error handling for missing entities in dashboard

## [0.5.2] - 2025-12-01

### Fixed
- Dashboard creation timing issues resolved
- Improved delayed dashboard rebuild functionality
- Enhanced entity discovery for dashboard generation

## [0.5.1] - 2025-11-30

### Fixed
- Dashboard creation service error handling improvements
- Fixed dashboard path and title configuration
- Enhanced logging for dashboard operations

## [0.5.0] - 2025-11-25

### Added
- Automatic Lovelace dashboard creation with organized views
- Dashboard service `ha_portainer_link.create_dashboard` for manual dashboard creation
- Automatic dashboard generation during integration setup
- Home view with global container update overview
- Stack-specific views with container controls and status
- Standalone container view for non-stack containers
- Configurable dashboard title and URL path during setup
- Delayed dashboard rebuild to include all entities after initial load
- Dashboard configuration options in config flow

### Changed
- Integration now automatically creates dashboard on setup (configurable)
- Dashboard organizes containers by stack with dedicated views
- Improved entity organization in dashboard views

## [0.4.1] - 2025-11-15

### Fixed
- Fixed excessive DNS queries from frequent update checks (GitHub issue #19)
- Implemented 5-minute minimum throttle for update checks in coordinator
- Enhanced rate limiting and caching to prevent excessive registry queries
- Reduced DNS query volume by throttling update checks to maximum once per 5 minutes
- Minor bug fixes and stability improvements
- Enhanced error messages for better troubleshooting

## [0.4.0] - 2025-08-08

### Added
- Comprehensive stack update functionality with multi-step process
- Image pulling for all containers in a stack before update
- Container recreation with proper cleanup and redeployment
- Robust error handling and fallback mechanisms
- Button state management during stack updates
- Enhanced logging and progress tracking for stack operations

### Changed
- Completely reworked stack update process for better reliability
- Enhanced user feedback during stack update operations
- Improved error recovery with automatic fallback mechanisms
- Updated documentation to reflect new stack update capabilities

### Fixed
- Fixed excessive DNS queries by implementing comprehensive session sharing across all API modules
- Reduced DNS lookups through connection pooling and session reuse (addresses GitHub issue #19)
- Entity category configuration for version sensors
- Device registry warnings in Home Assistant logs
- Integration mode handling and feature toggling
- Configuration flow and migration handling

## [0.3.8] - 2024-08-11

### Changed
- Disabled stack update buttons due to reliability issues
- Fixed entity category errors for version sensors (CONFIG → DIAGNOSTIC)
- Removed device registry warnings by eliminating via_device references
- Improved integration stability and error handling

### Fixed
- Entity category configuration for version sensors
- Device registry warnings in Home Assistant logs
- Integration mode handling and feature toggling
- Configuration flow and migration handling

## [0.3.7] - 2025-01-07

### Fixed
- Indentation error in stack update fallback logic
- Stack update error handling for better reliability
- Enhanced debugging output for troubleshooting stack update issues

## [0.3.6] - 2025-01-06

### Fixed
- Stack update recreation issue (containers deleted but not recreated)
- Enhanced stack update process with proper file content retrieval
- Added multiple fallback mechanisms for failed updates

### Changed
- Improved timing with cleanup delays and extended refresh cycles
- Enhanced debugging and error recovery for stack operations

## [0.3.5] - 2025-01-05

### Fixed
- Device registry warnings
- Config flow deprecation warnings
- Binary sensor entity categories
- Container state handling
- SSL certificate handling

### Changed
- Simplified integration modes (Lightweight/Full)
- Better error messages and debugging
- Optimized performance and reduced log noise

## [0.3.4] - 2025-01-04

### Added
- Automatic SSL verification with fallback
- Missing services.yaml file for proper service registration

### Fixed
- Migration handler for config entries from older versions
- Connection issues error handling
- Container state synchronization

### Changed
- Cleaned up unused imports to reduce log noise
- Enhanced migration to handle all version upgrades properly

## [0.3.3] - 2025-01-03

### Changed
- Simplified configuration to two modes (Lightweight/Full)
- Improved device hierarchy organization

### Fixed
- Container switch state synchronization

## [0.3.2] - 2025-01-02

### Added
- Integration modes (Lightweight, Full, Custom)
- Configurable update intervals
- Docker Hub rate limiting protection

## [0.3.1] - 2025-01-01

### Changed
- Refactored to modular API architecture
- Added DataUpdateCoordinator for better performance

### Added
- Automatic container discovery

## [0.3.0] - 2024-12-31

### Changed
- Complete rewrite with modern Home Assistant patterns
- Added stack clustering and organization

### Added
- Comprehensive error handling

---

## [Unreleased]

### Planned
- Re-enable stack update functionality with improved reliability
- Enhanced error recovery mechanisms
- Additional monitoring capabilities
