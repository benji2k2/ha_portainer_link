"""Jede Option muss im Formular stehen und ausgewertet werden.

Faengt den Fehler aus 0.6.4: ein Optionsschluessel war als zweites
Positionsargument von vol.Required gelandet, wo voluptuous ihn als
Fehlermeldung liest. Gueltiges Python, stille Wirkungslosigkeit.
"""
import sys, ast, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _harness import Checker, SRC

c = Checker("options-verdrahtung")
trees = {f.name: ast.parse(f.read_text()) for f in SRC.glob("*.py")}

conf = {n.targets[0].id: n.value.value for n in trees["const.py"].body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id.startswith("CONF_") and isinstance(n.value, ast.Constant)}
defaults = set()
for n in trees["const.py"].body:
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DEFAULT_OPTIONS":
        defaults = {k.id for k in n.value.keys if isinstance(k, ast.Name)}

form, multiarg = set(), []
for node in ast.walk(trees["config_flow.py"]):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("Required", "Optional")):
        if node.args and isinstance(node.args[0], ast.Name):
            form.add(node.args[0].id)
        if len(node.args) > 1:
            multiarg.append([getattr(a, "id", "?") for a in node.args])

c.section(f"{len(defaults)} optionen im formular?")
for key in sorted(defaults):
    c(conf.get(key, key), key in form, True)

c.section("kein zweites positionsargument (die panne aus 0.6.4)")
c("vol.Required/Optional mit genau einem key", multiarg, [])

c.section("jede option wird auch ausgewertet")
consumed = "".join((SRC / f).read_text() for f in ("coordinator.py", "__init__.py"))
for key in sorted(defaults):
    if key != "CONF_NOTIFY_SERVICE":
        c(conf.get(key, key), key in consumed, True)

sys.exit(c.done())
