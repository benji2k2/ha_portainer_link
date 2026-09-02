"""Pull: der Stream muss gelesen werden, sonst bricht Docker ab."""
import sys, pathlib, asyncio, json
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

pa = load("const", "entity", "portainer_api")[2]
c = Checker("image-pull")

class Content:
    def __init__(self, lines, consumed): self.lines, self.consumed = lines, consumed
    def __aiter__(self): return self._gen()
    async def _gen(self):
        for line in self.lines:
            self.consumed.append(line)
            yield line.encode()

class Resp:
    def __init__(self, status, lines, consumed, text=""):
        self.status, self.content, self._text = status, Content(lines, consumed), text
    async def text(self): return self._text
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class Session:
    def __init__(self, status, lines, text=""):
        self.status, self.lines, self.text = status, lines, text
        self.consumed, self.params = [], None
    def post(self, url, **kw):
        self.params = kw.get("params")
        return Resp(self.status, self.lines, self.consumed, self.text)

def api(session):
    a = pa.PortainerAPI.__new__(pa.PortainerAPI)
    a.base_url, a.headers, a.session = "http://p:9000", {}, session
    async def inspect(eid, cid): return {"Config": {"Image": "portainer/portainer-ce:2.21.4"}}
    a.inspect_container = inspect
    return a

OK_STREAM = [json.dumps(x) + "\n" for x in [
    {"status": "Pulling from portainer/portainer-ce", "id": "2.21.4"},
    {"status": "Pulling fs layer", "id": "a1b2"},
    {"status": "Downloading", "progressDetail": {"current": 50, "total": 100}},
    {"status": "Status: Downloaded newer image"},
]]
ERR_STREAM = [json.dumps(x) + "\n" for x in [
    {"status": "Pulling from portainer/portainer-ce"},
    {"errorDetail": {"message": "manifest unknown"}, "error": "manifest unknown"},
]]

async def main():
    c.section("erfolgreicher pull")
    s = Session(200, OK_STREAM); a = api(s)
    c("liefert True", await a.pull_image_update(2, "cid"), True)
    c("stream VOLLSTAENDIG gelesen - sonst bricht docker ab", len(s.consumed), len(OK_STREAM))
    c("name und tag getrennt", s.params, {"fromImage": "portainer/portainer-ce", "tag": "2.21.4"})

    c.section("fehler IM stream bei HTTP 200 - der alte blinde fleck")
    s = Session(200, ERR_STREAM); a = api(s)
    try:
        await a.pull_image_update(2, "cid"); c("wirft fehler", False, True)
    except pa.PortainerError as err:
        c.true("wirft fehler mit grund", "manifest unknown" in str(err))
    c("stream trotzdem ganz gelesen", len(s.consumed), len(ERR_STREAM))

    c.section("HTTP-fehler nennt portainers begruendung")
    s = Session(404, [], text=json.dumps({"message": "repository does not exist"})); a = api(s)
    try:
        await a.pull_image_update(2, "cid"); c("wirft fehler", False, True)
    except pa.PortainerError as err:
        c.true("grund enthalten", "repository does not exist" in str(err))

    c.section("netzwerkfehler wird nicht verschluckt")
    a = pa.PortainerAPI.__new__(pa.PortainerAPI)
    a.base_url, a.headers = "http://p:9000", {}
    a.session = type("S", (), {"post": lambda *x, **k: (_ for _ in ()).throw(OSError("connection refused"))})()
    try:
        await a.recreate_container(2, "cid"); c("wirft fehler", False, True)
    except pa.PortainerError as err:
        c.true("ursache genannt", "connection refused" in str(err))

    c.section("fehlermeldungen lesbar machen")
    c("portainer-format", pa.describe_error(409, json.dumps({"message": "container is running"})),
      "HTTP 409: container is running")
    c("details angehaengt", pa.describe_error(409, json.dumps({"message": "a", "details": "b"})), "HTTP 409: a - b")
    c("dopplung entfernt", pa.describe_error(500, json.dumps({"message": "x", "details": "x"})), "HTTP 500: x")
    c("nur text", pa.describe_error(502, "bad gateway"), "HTTP 502: bad gateway")
    c("leer", pa.describe_error(500, ""), "HTTP 500")

asyncio.run(main())
sys.exit(c.done())
