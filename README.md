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

- **Links einfügen** – mehrere gleichzeitig möglich, einer pro Zeile
- **Kategorie** – bestehende wählen oder neue eintippen, sie wird als Ordner angelegt
- **Notiz** – z. B. Sätze und Wiederholungen, später in der Galerie änderbar
- **Umsortieren** – das Dropdown unter dem Video verschiebt die Datei samt Notiz
- **↗** – zeigt die Datei im Finder
- **Beenden** – oben rechts

Der Ablage-Ordner ist zuerst `~/Sportuebungen`. Unten auf der Seite lässt er sich mit **«Ordner wählen …»** ändern, z. B. auf einen Ordner in iCloud Drive oder auf einer externen Festplatte. Unterordner darin werden zu Kategorien.

Neben jedem Video liegt eine `.json` mit Link, Account, Caption und Notiz. Die Ordner kannst du auch direkt im Finder umbenennen oder aufräumen.

## Wenn ein Download scheitert

- **Login-Fehler:** Unten auf der Seite bei «Login über Browser» den Browser wählen, in dem du bei Instagram eingeloggt bist. Bei Safari braucht Reel dafür unter Systemeinstellungen → Datenschutz & Sicherheit → Festplattenvollzugriff die Berechtigung.
- **Andere Fehler:** Instagram ändert ab und zu etwas. Die App wird jeden Monatsersten automatisch mit dem neusten yt-dlp neu gebaut. Lade dann einfach die neuste Version unter Releases. Sofort neu bauen geht über **Actions → Mac-App bauen → Run workflow**.

Einstellungen und Logdatei liegen unter `~/Library/Application Support/Reel/` (im Finder: Gehe zu → Gehe zu Ordner …).

## Technik

`app.py` (Flask + [yt-dlp](https://github.com/yt-dlp/yt-dlp)), verpackt mit PyInstaller über `.github/workflows/build.yml`. Auch direkt startbar mit `pip install -r requirements.txt && python3 app.py`.
