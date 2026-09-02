# Tests

Standalone scripts. No pytest, no Home Assistant installation: `_harness.py`
stubs every Home Assistant module the integration imports, so what a test
exercises is always the integration's own code.

```sh
for t in tests/test_*.py; do python3 "$t" || echo "FAILED $t"; done
```

Each script prints one line per check and exits non-zero on the first failure it
collected — it runs every check first, so one break does not hide the rest.

| Datei | deckt ab |
| --- | --- |
| `test_imageref.py` | Zerlegen von Image-Referenzen, Abgleich gegen `RepoDigests` |
| `test_pullsplit.py` | Name/Tag-Trennung vor dem Pull |
| `test_digests.py` | Index- gegen Config-Digest, Plattformauswahl, Anzeige |
| `test_devices.py` | Geräte-Identitäten, Benennung, Aufräumen nach Options-Wechsel |
| `test_health.py` | Healthcheck-Erkennung und Zählung |
| `test_instance.py` | Instanz-Aggregate, löschbare Images, Build-Datum |
| `test_pull.py` | Stream-Verarbeitung, Fehlermeldungen |
| `test_install.py` | Recreate beim Installieren, Schutzfälle |
| `test_manualcheck.py` | erzwungener Registry-Check |
| `test_ratelimit.py` | Anfragezahl pro Image, Token-Wiederverwendung, Persistenz |
| `test_options.py` | jede Option im Formular und ausgewertet |
| `test_prune_count.py`, `test_prunereport.py`, `test_buttonresult.py` | Prune-Zählung und Button-Rückmeldung |

Sie liegen hier statt in einem temporären Verzeichnis, weil eine frühere Fassung
genau dort verloren ging.
