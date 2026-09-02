"""Wie Image-Referenzen zerlegt werden - Grundlage jeder Registry-Abfrage."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

image_api = load("const", "image_api")[1]
api = image_api.PortainerImageAPI("", None)
c = Checker("image-referenzen zerlegen")
D = "registry-1.docker.io"

def ref(name, reg, repo, tag):
    got = api._parse_image_ref(name)[:3]
    c(name, got, (reg, repo, tag))

c.section("offizielle images MIT tag (waren bis 0.6.3 kaputt)")
ref("alpine:3.18", D, "library/alpine", "3.18")
ref("nginx:1.25", D, "library/nginx", "1.25")
ref("redis:7", D, "library/redis", "7")
ref("postgres:16.2", D, "library/postgres", "16.2")

c.section("offizielle images ohne tag")
ref("alpine", D, "library/alpine", "latest")
ref("alpine:latest", D, "library/alpine", "latest")

c.section("images mit namespace")
ref("linuxserver/plex:latest", D, "linuxserver/plex", "latest")
ref("portainer/portainer-ce:2.21.4", D, "portainer/portainer-ce", "2.21.4")

c.section("fremde registries - der doppelpunkt ist hier ein port")
ref("ghcr.io/starosdev/scrutiny:1-omnibus", "ghcr.io", "starosdev/scrutiny", "1-omnibus")
ref("ghcr.io/analogj/scrutiny", "ghcr.io", "analogj/scrutiny", "latest")
ref("registry.local:5000/app:2.1", "registry.local:5000", "app", "2.1")
ref("registry.local:5000/app", "registry.local:5000", "app", "latest")
ref("localhost:5000/app:1", "localhost:5000", "app", "1")
ref("docker.io/library/alpine:3.18", D, "library/alpine", "3.18")

c.section("digest-referenzen")
reg, repo, reference, pinned = api._parse_image_ref("alpine@sha256:abc123")
c("registry und repository", (reg, repo), (D, "library/alpine"))
c("digest wird als referenz benutzt", reference, "sha256:abc123")
c("und als pinned gemeldet", pinned, "sha256:abc123")

c.section("lokaler abgleich gegen RepoDigests")
for image, repo_digest, want in [
    ("alpine:3.18", "alpine@sha256:abc", True),           # docker laesst library/ weg
    ("alpine:3.18", "nginx@sha256:abc", False),
    ("linuxserver/plex:latest", "linuxserver/plex@sha256:abc", True),
    ("ghcr.io/starosdev/scrutiny:1-omnibus", "ghcr.io/starosdev/scrutiny@sha256:abc", True),
    ("alpine:3.18", "kein-digest", False),
]:
    c(f"{image} vs {repo_digest}", api._repo_matches_image(repo_digest, image), want)

sys.exit(c.done())
