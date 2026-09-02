# Handoff: Docker-Hub-Rate-Limit durch Registry-Checks

Analysiert am 2026-09-02. Symptom beim Nutzer: beim `docker compose up -d` auf
`heatherthink` immer wieder `toomanyrequests: You have reached your unauthenticated
pull rate limit`. Verdacht war diese Integration — bestätigt.

## Kurzfassung

Das konfigurierte Update-Check-Intervall (Default 6 h) greift nach einem
Home-Assistant-Neustart nicht, weil der Zeitstempel des letzten Registry-Checks nur
im RAM liegt. Jeder HA-Neustart löst deshalb sofort einen vollständigen
Registry-Sweep über alle Container aus. Alle Anfragen laufen anonym, landen also im
striktesten Docker-Hub-Kontingent, das zusätzlich pro öffentlicher IP zählt (im LAN
des Nutzers teilen sich mehrere Docker-Hosts diese IP).

## Belege im Code

| Fundstelle | Bedeutung |
| --- | --- |
| `const.py:25` | `DEFAULT_UPDATE_CHECK_INTERVAL = 360` (Minuten) — Intervall ist korrekt gesetzt |
| `coordinator.py:66` | `self._last_registry_check = 0.0` — nur Instanz-State, nicht persistiert |
| `coordinator.py:204` | `include_registry = ... and (now - self._last_registry_check >= max(interval, 60))` — mit `0.0` beim Start immer wahr |
| `image_api.py:25-30` | `_cache_duration = 6*3600`, aber `_update_cache` / `_version_cache` / `_digest_cache` sind reine Dicts auf der Instanz — nach Neustart leer |
| `image_api.py:225-234`, `257-266` | Bei Multi-Arch-Images wird nach dem Index noch das Child-Manifest geholt → bis zu 2 Requests pro Image |
| `image_api.py:109-134` | `_get_registry_auth_token()` holt das Bearer-Token ohne Credentials → anonymes Kontingent |

Wichtig: Die `CONF_USERNAME` / `CONF_PASSWORD` aus der Config gehören zu Portainer
(`auth.py`, `portainer_api.py`), nicht zur Registry. Registry-Credentials gibt es
aktuell nirgends.

Docker Hub zählt Manifest-Abfragen als Pulls — ein reiner "gibt es was Neueres?"-Check
kostet also genauso viel wie ein echter Pull.

## Vorgeschlagene Änderungen

### 1. Registry-Check-Zeitstempel persistieren (höchste Priorität)

`self._last_registry_check` über HA-Neustarts hinweg halten, z. B. mit
`homeassistant.helpers.storage.Store` (eigener Storage-Key pro Config-Entry).

- Beim Setup laden, nach jedem Sweep speichern.
- `time.monotonic()` ist dafür ungeeignet (Referenzpunkt überlebt den Neustart nicht) —
  auf `time.time()` umstellen oder Wall-Clock separat persistieren.
- Sinnvoll gleich mit: `_digest_cache` und `_version_cache` mitpersistieren, damit nach
  dem Neustart nicht nur der Sweep ausbleibt, sondern die Sensoren auch sofort
  wieder Werte haben.

Damit verschwindet die eigentliche Ursache: Neustarts lösen dann keinen Sweep mehr aus.

### 2. Optionale Registry-Credentials

Im Config-Flow optionale Felder für Docker-Hub-Zugangsdaten ergänzen und in
`_get_registry_auth_token()` beim Abruf der Realm-URL als Basic Auth mitgeben.
Authentifiziert zählt das Limit pro Account statt pro IP und liegt deutlich höher.
Felder klar von den Portainer-Credentials trennen (Namensgebung, Beschreibungstext).

### 3. Ersten Check nach dem Start entzerren

Auch mit Punkt 1 sollte der erste fällige Sweep nach dem Start nicht synchron im Setup
laufen. Kleinen zufälligen Versatz einbauen, damit nach einem Neustart nicht alles
gleichzeitig gegen die Registry läuft.

## Validierung

- HA neu starten und im Debug-Log prüfen, dass **kein** Registry-Sweep startet, solange
  das Intervall noch nicht abgelaufen ist.
- Verbleibendes Kontingent gegenprüfen:

```sh
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:ratelimitpreview/test:pull" | jq -r .token)
curl -s -I -H "Authorization: Bearer $TOKEN" https://registry-1.docker.io/v2/ratelimitpreview/test/manifests/latest | grep -i ratelimit
```

- Zählen, wie viele Manifest-Requests ein Sweep tatsächlich auslöst (Debug-Logging um die
  Registry-Aufrufe), und gegen die Containeranzahl gegenrechnen.

## Nicht Teil dieser Änderung

Die Fehlermeldung beim `docker compose up -d` kommt vom Docker-Daemon selbst, nicht von
dieser Integration — sie teilt sich nur dasselbe IP-Kontingent. Dagegen hilft
`docker login` auf den Hosts bzw. Docker-Hub-Credentials in Portainer unter Registries.
Das ist Infrastruktur-Konfiguration und gehört nicht ins Repo.
