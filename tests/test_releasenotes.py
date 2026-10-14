"""Release Notes: Quelle finden, passende Releases auswaehlen, darstellen, cachen."""
import sys, pathlib, asyncio, types
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

rn = load("const", "entity", "release_notes")[2]
c = Checker("release notes")

c.section("image-namen vereinheitlichen")
c("docker hub library", rn.normalize_image("docker.io/library/redis:7"), "redis")
c("ohne tag", rn.normalize_image("redis"), "redis")
c("registry mit port bleibt", rn.normalize_image("reg.local:5000/team/app:1.2"), "reg.local:5000/team/app")
c("digest weg", rn.normalize_image("ghcr.io/tale/headplane@sha256:abc"), "ghcr.io/tale/headplane")
c("gross/klein egal", rn.normalize_image("Headscale/Headscale:latest"), "headscale/headscale")

c.section("github-repo aus der quellangabe")
c("einfache url", rn.github_repo("https://github.com/traefik/traefik"), "traefik/traefik")
c("mit pfad (authelia)", rn.github_repo("https://github.com/authelia/authelia/tree/8da42b2"), "authelia/authelia")
c(".git weg", rn.github_repo("https://github.com/org/app.git"), "org/app")
c("ssh-form", rn.github_repo("git@github.com:org/app.git"), "org/app")
c("gitlab -> keins", rn.github_repo("https://gitlab.com/hctrdev/x"), None)
c("leer", rn.github_repo(None), None)

c.section("zuordnungen aus den optionen")
ov = rn.parse_overrides("redis=redis/redis, headscale/headscale = juanfont/headscale\n"
                        "b3log/siyuan=https://github.com/siyuan-note/siyuan, kaputt, x=nur-ein-teil")
c("drei gueltige", ov, {"redis": "redis/redis", "headscale/headscale": "juanfont/headscale",
                       "b3log/siyuan": "siyuan-note/siyuan"})
c("override schlaegt label", rn.resolve_repo("redis:latest", "https://github.com/falsch/x", ov), "redis/redis")
c("sonst label", rn.resolve_repo("traefik:latest", "https://github.com/traefik/traefik", ov), "traefik/traefik")
c("weder noch", rn.resolve_repo("mber5/virt-manager", None, ov), None)

def rel(tag, published, pre=False, draft=False, body="text"):
    return {"tag_name": tag, "name": tag, "published_at": published, "prerelease": pre,
            "draft": draft, "body": body, "html_url": f"https://github.com/o/r/releases/tag/{tag}"}
R = [rel("v3.7.16", "2026-10-20T10:00:00Z"), rel("v3.8.0-rc1", "2026-10-18T10:00:00Z", pre=True),
     rel("v3.7.15", "2026-10-13T10:00:00Z"), rel("v3.7.14", "2026-10-06T10:00:00Z"),
     rel("v3.7.13", "2026-09-20T10:00:00Z"), rel("v9.9.9", "2026-10-19T10:00:00Z", draft=True)]
tags = lambda sel: [r["tag_name"] for r in sel]

c.section("auswahl ueber versions-labels")
c("14 -> 16: 16 und 15, ohne rc und entwurf",
  tags(rn.select_releases(R, "v3.7.14", "3.7.16")), ["v3.7.16", "v3.7.15"])
c("v-praefix egal", tags(rn.select_releases(R, "3.7.15", "v3.7.16")), ["v3.7.16"])
c("installierte unbekannt: bis zum build-datum",
  tags(rn.select_releases(R, None, "v3.7.16", installed_built="2026-10-07T00:00:00Z")), ["v3.7.16", "v3.7.15"])
c("rc nur wenn genau der verfuegbar ist",
  tags(rn.select_releases(R, "v3.7.15", "v3.8.0-rc1")), ["v3.8.0-rc1"])

c.section("auswahl ueber build-daten (ohne versions-label)")
c("zwischen den builds",
  tags(rn.select_releases(R, None, None, "2026-10-07T00:00:00Z", "2026-10-14T00:00:00Z")), ["v3.7.15"])
c("release kurz nach dem build zaehlt noch",
  tags(rn.select_releases(R, None, None, "2026-10-07T00:00:00Z", "2026-10-13T02:00:00Z")), ["v3.7.15"])
c("nur neu gebaut -> leer",
  rn.select_releases(R, None, None, "2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z"), [])
c("nanosekunden von docker", tags(rn.select_releases(
  R, None, None, "2026-10-07T00:00:00.123456789Z", "2026-10-14T00:00:00.5Z")), ["v3.7.15"])
c("ohne build-datum -> leer", rn.select_releases(R, None, None), [])

c.section("parallele hauptversionen (traefik v2.11 neben v3)")
P = [rel("v3.7.14", "2026-10-06T10:00:00Z"), rel("v2.11.58", "2026-10-06T09:00:00Z"),
     rel("v3.7.13", "2026-09-20T10:00:00Z"), rel("v2.11.57", "2026-09-20T09:00:00Z")]
c("versionsweg: nur v3", tags(rn.select_releases(P, "v3.7.13", "v3.7.14")), ["v3.7.14"])
c("datumsweg mit bekannter laufender version: nur v3", tags(rn.select_releases(
  P, "v3.7.13", None, "2026-09-21T00:00:00Z", "2026-10-07T00:00:00Z")), ["v3.7.14"])
c("ganz ohne version: beide (nicht entscheidbar)", tags(rn.select_releases(
  P, None, None, "2026-09-21T00:00:00Z", "2026-10-07T00:00:00Z")), ["v3.7.14", "v2.11.58"])

c.section("darstellung")
md = rn.render("o/r", [R[0]])
c.true("ueberschrift mit datum", "## v3.7.16 (2026-10-20)" in md)
c.true("hinweis bei reinem neubau", "rebuild" in rn.render("o/r", []))
long = rn.render("o/r", [rel("v1", "2026-01-01T00:00:00Z", body="x" * 7000)])
c.true("langer text gekuerzt mit link", "continued on GitHub" in long and len(long) < 6300)
many = rn.render("o/r", [rel(f"v{i}", "2026-01-01T00:00:00Z") for i in range(8)])
c.true("mehr als 5 -> verweis", "3 older releases" in many)
c.true("leerer text", "_No release notes._" in rn.render("o/r", [rel("v1", "2026-01-01T00:00:00Z", body="")]))

c.section("github-abruf wird gecacht")
class Resp:
    def __init__(self, status, payload): self.status, self._p = status, payload
    async def json(self, content_type=None): return self._p
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Session:
    def __init__(self, status=200): self.calls, self.status = [], status
    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        return Resp(self.status, R if self.status == 200 else {"message": "rate limit"})

async def main():
    s = Session(); cl = rn.ReleaseNotesClient(s)
    first = await cl.releases("o/r"); await cl.releases("o/r")
    c("liefert die liste", len(first), len(R))
    c("nur ein abruf", len(s.calls), 1)
    c("richtige url", s.calls[0][0], "https://api.github.com/repos/o/r/releases")
    c.true("user-agent gesetzt", s.calls[0][2].get("User-Agent"))
    s2 = Session(403); cl2 = rn.ReleaseNotesClient(s2)
    c("fehler -> None", await cl2.releases("o/r"), None)
    await cl2.releases("o/r")
    c("fehler wird kurz gecacht", len(s2.calls), 1)

asyncio.run(main())
sys.exit(c.done())
