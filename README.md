# Reel

Kleine Mac-App zum Herunterladen und Sortieren von Instagram-Videos (z. B. Übungs-Reels). Die Oberfläche läuft im Browser, alle Videos und Notizen bleiben lokal auf deinem Rechner, in einem Ordner deiner Wahl.

## Installieren

1. Unter **[Releases](../../releases/latest)** die Datei `Reel-mac.zip` herunterladen und entpacken.
2. `Reel.app` in den Programme-Ordner ziehen.
3. Reel per Doppelklick öffnen. macOS blockiert die App beim ersten Mal, weil sie nicht bei Apple registriert ist – Meldung mit «Fertig» schliessen.
4. **Systemeinstellungen → Datenschutz & Sicherheit**, ganz nach unten scrollen: dort steht «Reel wurde blockiert» mit dem Knopf **«Trotzdem öffnen»**. Klicken, mit Passwort oder Touch ID bestätigen. Das ist nur einmal nötig.

Läuft auf Macs mit Apple-Chip (M1 und neuer).

## Benutzen

Reel öffnen – die Oberfläche erscheint im Browser unter http://localhost:5055. Die App hat kein Dock-Symbol. Öffnest du sie nochmals, kommt einfach wieder der Tab.

- **Hinzufügen** – Instagram-Link(s) einfügen, Tags dazu (Enter oder Komma trennt), «Hinzufügen»
- **Filtern** – oben auf Tags klicken; mehrere Tags zeigen nur Videos, die alle davon haben
- **Blockansicht** – Klick auf ein Video: gross abspielen, Tags und Notiz ändern, im Finder zeigen, löschen (landet im Papierkorb)
- **Beenden** – oben rechts

Alle Videos liegen in einem Ordner, zuerst `~/Sportuebungen`. Unten auf der Seite lässt er sich mit **«Ordner wählen …»** ändern, z. B. auf einen Ordner in iCloud Drive. Neben jedem Video liegt eine `.json` mit Link, Account, Caption, Tags und Notiz.

Ordner aus früheren Versionen (eine Kategorie pro Ordner) werden beim Start automatisch aufgelöst: Die Videos wandern in den Hauptordner, der Ordnername wird zum Tag.

## Wenn ein Download scheitert

- **Login-Fehler:** Unten auf der Seite bei «Login über Browser» den Browser wählen, in dem du bei Instagram eingeloggt bist. Bei Safari braucht Reel dafür unter Systemeinstellungen → Datenschutz & Sicherheit → Festplattenvollzugriff die Berechtigung.
- **Andere Fehler:** Instagram ändert ab und zu etwas. Die App wird jeden Monatsersten automatisch mit dem neusten yt-dlp neu gebaut. Lade dann einfach die neuste Version unter Releases. Sofort neu bauen geht über **Actions → Mac-App bauen → Run workflow**.

Einstellungen und Logdatei liegen unter `~/Library/Application Support/Reel/` (im Finder: Gehe zu → Gehe zu Ordner …).

## Technik

`app.py` (Flask + [yt-dlp](https://github.com/yt-dlp/yt-dlp)), verpackt mit PyInstaller über `.github/workflows/build.yml`. Auch direkt startbar mit `pip install -r requirements.txt && python3 app.py`.
