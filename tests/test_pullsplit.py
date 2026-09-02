"""Name und Tag trennen, bevor gepullt wird."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

pa = load("const", "entity", "portainer_api")[2]
split = pa.PortainerAPI.split_image_reference
c = Checker("image-referenz fuer den pull trennen")

c.section("ohne tag NIE leer lassen - sonst zieht die API alle tags")
for image, want in [
    ("nginx", ("nginx", "latest")),
    ("portainer/portainer-ce", ("portainer/portainer-ce", "latest")),
    ("registry.local:5000/app", ("registry.local:5000/app", "latest")),
]:
    c(image, split(image), want)

c.section("mit tag")
for image, want in [
    ("nginx:1.25", ("nginx", "1.25")),
    ("portainer/agent:2.21.4", ("portainer/agent", "2.21.4")),
    ("ghcr.io/user/img:v1", ("ghcr.io/user/img", "v1")),
    ("registry.local:5000/app:2.1", ("registry.local:5000/app", "2.1")),
]:
    c(image, split(image), want)

c.section("der kritische fall: registry-port ist kein tag")
name, tag = split("registry.local:5000/app")
c("port bleibt im namen", name, "registry.local:5000/app")
c("tag faellt auf latest", tag, "latest")

c.section("digest wird als tag durchgereicht")
c("nginx@sha256:abc", split("nginx@sha256:abc"), ("nginx", "sha256:abc"))

sys.exit(c.done())
