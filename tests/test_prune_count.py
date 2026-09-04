import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import load, Checker

b = load("const", "entity")[1]

fails=[]
def check(l, got, want):
    ok = got==want
    print(f"{'PASS' if ok else 'FAIL'}  {l}\n       -> {got!r}")
    if not ok: print(f"       want={want!r}"); fails.append(l)

print("=== dein realer fall: EIN image, 12 Deleted-eintraege (11 layer) ===")
viele = {"ImagesDeleted": [{"Untagged": "alt:tag"}] + [{"Deleted": f"sha256:{i:02d}"} for i in range(12)],
         "SpaceReclaimed": 175_900_000}
check("rohzaehlung meldet 12 - das sind layer, keine images", b.count_pruned(viele), (12, 1))
print("   -> die image-zahl kommt NICHT von hier, sondern aus vorher/nachher\n")

print("=== weiterer fall: EIN image, drei antwort-eintraege ===")
real = {"ImagesDeleted": [
    {"Untagged": "dangling-demo:x"},
    {"Untagged": "dangling-demo@sha256:83b2b6703a62aaaa"},
    {"Deleted": "sha256:83b2b6703a62aaaa"},
], "SpaceReclaimed": 7398000}
check("frueher wurde '3' gemeldet, richtig ist", b.count_pruned(real), (1, 2))

print("\n=== weitere faelle ===")
check("leer", b.count_pruned({"ImagesDeleted": [], "SpaceReclaimed": 0}), (0, 0))
check("feld fehlt ganz", b.count_pruned({}), (0, 0))
check("zwei images", b.count_pruned({"ImagesDeleted": [
    {"Untagged": "a:1"}, {"Deleted": "sha256:a"},
    {"Untagged": "b:1"}, {"Deleted": "sha256:b"}]}), (2, 2))
check("nur untagged (image noch referenziert)",
      b.count_pruned({"ImagesDeleted": [{"Untagged": "a:1"}]}), (0, 1))
check("mehrere layer eines images",
      b.count_pruned({"ImagesDeleted": [{"Deleted": "sha256:a"}, {"Deleted": "sha256:b"}]}), (2, 0))
check("muell im payload wird ignoriert",
      b.count_pruned({"ImagesDeleted": ["kaputt", None, {"Deleted": "sha256:a"}]}), (1, 0))

print("\n=== groessenformat ===")
check("7398000 bytes", b.format_bytes(7398000), "7.1 MB")

print("\n" + ("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
