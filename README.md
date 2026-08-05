# FareGraph

FareGraph findet günstige Rundreisen und mehrteilige Flugrouten, die klassische
Flugsuchmaschinen oft nicht sinnvoll zusammensetzen. Statt nur nach
`A → B → A` zu suchen, sammelt FareGraph verfügbare Einzelflüge und verbindet
sie anschließend als zeitabhängigen Graphen.

Beispiele:

- `CGN → PMI → STN → BGY → NRN`
- Start in Köln, Rückkehr über einen anderen Flughafen in der Umgebung
- möglichst günstige Route mit zwei bis fünf Flugsegmenten
- zwei Freundesgruppen starten an verschiedenen Orten und suchen ein gemeinsames Ziel
- Weiterreise über einen nahe gelegenen Flughafen, zum Beispiel `NRN ⇢ EIN`

FareGraph läuft vollständig als Docker-Anwendung mit Weboberfläche und
PostgreSQL-Datenbank. Es wurde für Unraid entwickelt, funktioniert aber auch
auf anderen Docker-Systemen.

> [!IMPORTANT]
> Der Ryanair-Zugriff verwendet öffentliche, aber nicht offiziell dokumentierte
> Web-Endpunkte. Diese können sich ändern oder Anfragen begrenzen. FareGraph ist
> weder mit Ryanair noch mit einer anderen Fluggesellschaft verbunden. Preise,
> Flugzeiten und Verfügbarkeit vor einer Buchung immer auf der Airline-Webseite prüfen.

## Funktionen

- bedarfsgesteuertes Sammeln statt permanentem Crawling
- Ryanair als voreingestellte Live-Datenquelle
- PostgreSQL-Speicherung aller Suchaufträge und Angebote
- frei wählbares Datumsfenster, Preislimit und maximale Suchtiefe
- korrekte lokale Abflug- und Ankunftszeiten
- getrennte Anzeige von Reisezeit und tatsächlicher Flugzeit
- Routensuche mit einstellbarem Mindestaufenthalt ab Ankunft
- Flughafenwechsel am Boden innerhalb eines einstellbaren Radius
- nahe aktive Ryanair-Flughäfen automatisch zu den Startflughäfen hinzufügen
- Routendarstellung auf einer interaktiven Karte
- gemeinsame Direktziele aus zwei gesammelten Datensätzen finden
- deutscher und englischer Bedienmodus
- Live-Anzeige von Suchdauer, Abfragen, Cache-Treffern und Angeboten
- Suchaufträge pausieren, fortsetzen und vollständig löschen
- Cache für identische Airline-Abfragen
- `Retry-After`, exponentielle Wiederholungen und optionale Drosselung
- Demo-Datenquelle für gefahrlose Funktionstests

## Voraussetzungen

- Docker Engine mit Docker Compose v2
- ungefähr 1 GB freier Arbeitsspeicher
- ausreichend Speicherplatz für PostgreSQL; der tatsächliche Bedarf hängt von
  Anzahl und Tiefe der Suchaufträge ab
- Internetzugriff des Containers für Ryanair-Abfragen und Kartenkacheln

Für Unraid wird außerdem ein Share wie `appdata` für die dauerhaften
PostgreSQL-Daten empfohlen.

## Schnellstart mit Docker

### 1. Projekt herunterladen

Nach Veröffentlichung des Repositorys:

```bash
git clone https://github.com/RealHaltewunsch/faregraph.git
cd faregraph
```

Alternativ kann das Repository bei GitHub als ZIP-Datei heruntergeladen und
entpackt werden.

### 2. Konfiguration anlegen

```bash
cp .env.example .env
```

Öffne anschließend `.env` und ersetze `change-me` in beiden folgenden Zeilen
durch dasselbe sichere Passwort:

```dotenv
POSTGRES_PASSWORD=ein-langes-zufaelliges-passwort
DATABASE_URL=postgresql://faregraph:ein-langes-zufaelliges-passwort@db:5432/faregraph
```

Wichtig: Sonderzeichen in `DATABASE_URL` müssen URL-kodiert sein. Für eine
unkomplizierte Einrichtung eignet sich ein langes Passwort aus Buchstaben und
Ziffern.

Die Standardkonfiguration speichert die Daten in `./data/postgres`. Für einen
anderen Ordner kann diese Zeile geändert werden:

```dotenv
POSTGRES_DATA_PATH=./data/postgres
```

### 3. FareGraph starten

```bash
docker compose up -d --build
```

Beim ersten Start werden das App-Image gebaut, PostgreSQL heruntergeladen und
die Datenbanktabellen automatisch angelegt.

### 4. Weboberfläche öffnen

```text
http://SERVER-IP:8787
```

Auf demselben Rechner ist das normalerweise:

```text
http://localhost:8787
```

Der Port kann in `.env` geändert werden:

```dotenv
APP_PORT=8787
```

## Installation auf Unraid

Die einfachste Variante verwendet das Unraid-Terminal und Docker Compose.

### 1. Projektordner anlegen und Repository klonen

```bash
mkdir -p /mnt/user/appdata/faregraph
cd /mnt/user/appdata/faregraph
git clone https://github.com/RealHaltewunsch/faregraph.git src
cd src
```

Falls `git` auf dem Server nicht verfügbar ist, kann das GitHub-ZIP über die
Unraid-Dateifreigabe nach `/mnt/user/appdata/faregraph/src` kopiert und dort
entpackt werden.

### 2. Unraid-Konfiguration anlegen

```bash
cp .env.example .env
```

In `.env` sollten mindestens Passwort und Datenpfad angepasst werden:

```dotenv
POSTGRES_PASSWORD=ein-langes-zufaelliges-passwort
DATABASE_URL=postgresql://faregraph:ein-langes-zufaelliges-passwort@db:5432/faregraph
POSTGRES_DATA_PATH=/mnt/user/appdata/faregraph/postgres
APP_PORT=8787
```

### 3. Container starten

```bash
docker compose up -d --build
```

Danach ist FareGraph unter folgender Adresse erreichbar:

```text
http://UNRAID-IP:8787
```

### 4. Zustand prüfen

```bash
docker compose ps
curl http://127.0.0.1:8787/health
```

Die Gesundheitsprüfung sollte eine Antwort mit `"status":"ok"` liefern.

## FareGraph benutzen

### 1. Daten sammeln

Öffne den Reiter **Daten sammeln**.

1. Trage einen oder mehrere Startflughäfen als IATA-Codes ein, beispielsweise
   `NRN` oder `CGN,DUS,NRN`.
2. Optional: Stelle den Radius auf beispielsweise `150 km` und klicke auf
   **Nahe Ryanair-Flughäfen hinzufügen**. FareGraph ergänzt aktive
   Ryanair-Flughäfen im Umkreis und zeigt deren Entfernung an.
3. Wähle das früheste und späteste mögliche Startdatum.
4. Lege Reisedauer, maximale Zahl der Flüge und Preislimit fest.
5. Wähle **Ryanair live** oder für einen ungefährlichen Test **Demo-Daten**.
6. Klicke auf **Suche starten**.

Der Auftrag läuft im Hintergrund. Die Oberfläche zeigt Suchzeit, aktuelle
Ebene, Zahl der Abfragen, Cache-Treffer und gefundene Angebote live an.

#### Bedeutung der Sammelparameter

| Einstellung | Bedeutung |
| --- | --- |
| Startflughäfen | Flughäfen, von denen die erste Suche beginnt |
| Radius für nahe Flughäfen | Ergänzt weitere aktive Ryanair-Startflughäfen; verändert nicht den Bodenwechsel einer Route |
| Frühester/spätester Start | Datumsfenster für die ersten Abflüge |
| Max. Reisetage | Maximaler Zeitraum vom ersten Abflug bis zur letzten Ankunft |
| Max. Flüge je Verbindung | Maximale Graphentiefe beziehungsweise Zahl einzelner Flugsegmente |
| Früheste Weiterreise ab Ankunft | Frühester erlaubter Folgeflug, gerechnet ab der tatsächlichen Ankunft |
| Max. Preis je Flug | Einzelne teurere Flugangebote werden nicht gespeichert |
| Entfernung zwischen Bodenflughäfen | Erlaubt Folgesuchen von einem anderen Flughafen im angegebenen Radius; `0` deaktiviert dies |

Eine große Suchtiefe, viele Startflughäfen und ein großes Datumsfenster können
die Zahl der Airline-Abfragen stark erhöhen. Für den Einstieg sind ein bis drei
Startflughäfen und eine Tiefe von zwei oder drei sinnvoll.

### 2. Routen finden

Nach Abschluss eines Sammelauftrags:

1. Öffne **Routen finden**.
2. Wähle den gesammelten Datensatz.
3. Trage die erlaubten Endflughäfen ein. Für eine Rundreise können dies die
   ursprünglichen Startflughäfen sein.
4. Lege minimale und maximale Zahl der Flüge fest.
5. Stelle den Mindestaufenthalt ab Ankunft und das Gesamtbudget ein.
6. Klicke auf **Günstigste Routen finden**.

`Max. Flüge = 2` bedeutet nicht automatisch Hin- und Rückflug. Es erlaubt zwei
einzelne Segmente, also sowohl `A → B → A` als auch `A → B → C`, sofern der
Endflughafen erlaubt ist.

In jedem Ergebnis werden getrennt angezeigt:

- **Reisedauer:** Zeit vom ersten Abflug bis zur letzten Ankunft einschließlich Aufenthalten
- **Flugzeit:** Summe der tatsächlichen Flugzeiten aller Segmente

Mit **Auf Karte zeigen** erscheint nur die ausgewählte Route auf der Karte.

### 3. Flughafenwechsel am Boden

Beim Sammeln kann ein maximaler Abstand zwischen Flughäfen festgelegt werden.
Beispiel: Nach einer Landung in `NRN` darf eine Route bei passendem Radius ab
`EIN` weitergehen.

- `0 km`: kein Flughafenwechsel
- `100 km`: Wechsel zu Ryanair-Flughäfen in bis zu 100 km Entfernung
- der Wert beim Routenfinden darf nicht größer sein als beim Sammeln
- Bodenwechsel werden separat markiert und zählen nicht als Flugsegment
- Zeit und Kosten des Bodentransfers werden derzeit noch nicht automatisch berechnet

### 4. Gemeinsames Ziel finden

Für zwei Gruppen, die von unterschiedlichen Regionen starten:

1. Sammle einen Datensatz für Startregion A.
2. Sammle einen zweiten Datensatz für Startregion B.
3. Öffne **Gemeinsames Ziel**.
4. Wähle beide Datensätze, das Abflugfenster, die maximale Ankunftsdifferenz
   und das gemeinsame Budget.
5. Klicke auf **Gemeinsame Ziele finden**.

FareGraph vergleicht die Direktflüge beider Datensätze und zeigt gemeinsame
Zielflughäfen nach Gesamtpreis sortiert an.

## Suchaufträge verwalten

- **Pausieren:** beendet den Auftrag kontrolliert nach der aktuellen Abfrage
- **Fortsetzen:** setzt einen pausierten Auftrag mit seiner gespeicherten Warteschlange fort
- **Löschen:** entfernt den Auftrag und alle zugehörigen Flugangebote dauerhaft

Wird der App-Container während einer Suche neu gestartet, wird der Auftrag als
pausiert gespeichert und kann anschließend fortgesetzt werden.

## Konfiguration

Alle Einstellungen liegen in `.env`:

| Variable | Standard | Beschreibung |
| --- | ---: | --- |
| `APP_PORT` | `8787` | Port der Weboberfläche auf dem Docker-Host |
| `POSTGRES_DB` | `faregraph` | Datenbankname |
| `POSTGRES_USER` | `faregraph` | Datenbankbenutzer |
| `POSTGRES_PASSWORD` | `change-me` | Datenbankpasswort; unbedingt ändern |
| `DATABASE_URL` | – | Verbindung der App zur Datenbank; Passwort muss übereinstimmen |
| `POSTGRES_DATA_PATH` | `./data/postgres` | dauerhafter Speicherort der PostgreSQL-Daten |
| `CACHE_TTL_HOURS` | `6` | Lebensdauer identischer Preisabfragen im Cache |
| `AIRPORT_CATALOG_TTL_DAYS` | `7` | Lebensdauer des Ryanair-Flughafenkatalogs |
| `RYANAIR_MIN_DELAY` | `0` | optionale minimale Pause zwischen Ryanair-Anfragen |
| `RYANAIR_MAX_DELAY` | `0` | optionale maximale Pause zwischen Ryanair-Anfragen |

Die feste Pause ist standardmäßig deaktiviert. Bei HTTP `429` berücksichtigt
FareGraph weiterhin `Retry-After`. Vorübergehende Netzwerk- und Serverfehler
werden mit exponentieller Wartezeit erneut versucht.

## Aktualisieren

Im Projektordner:

```bash
git pull
docker compose up -d --build
```

Die PostgreSQL-Daten bleiben erhalten, solange `POSTGRES_DATA_PATH` nicht
gelöscht oder geändert wird.

## Stoppen und neu starten

```bash
docker compose stop
docker compose start
```

Container entfernen, Daten aber behalten:

```bash
docker compose down
```

> [!CAUTION]
> `docker compose down -v` beziehungsweise das Löschen von
> `POSTGRES_DATA_PATH` entfernt gespeicherte Suchaufträge und Flugdaten.

## Datensicherung

FareGraph während einer Dateisicherung am besten kurz stoppen und den in
`POSTGRES_DATA_PATH` angegebenen Ordner sichern. Alternativ kann ein
PostgreSQL-Dump erstellt werden:

```bash
docker compose exec -T db pg_dump -U faregraph faregraph > faregraph-backup.sql
```

## Fehlerbehebung

### Weboberfläche ist nicht erreichbar

```bash
docker compose ps
docker compose logs --tail=100 app
docker compose logs --tail=100 db
```

Prüfe außerdem, ob `APP_PORT` bereits von einem anderen Container verwendet wird.

### Datenbank startet nicht

Kontrolliere, ob der Ordner aus `POSTGRES_DATA_PATH` existiert und von Docker
beschreibbar ist. `POSTGRES_PASSWORD` und das Passwort in `DATABASE_URL` müssen
identisch sein.

### Ryanair begrenzt oder beendet Anfragen

FareGraph pausiert bei Rate-Limits entsprechend der Antwort der Datenquelle und
wiederholt vorübergehende Fehler. Ein abgebrochener Auftrag kann über die
Weboberfläche fortgesetzt werden. Sehr große Suchaufträge sollten in kleinere
Datumsfenster oder geringere Suchtiefen aufgeteilt werden.

### Zeiten oder Preise weichen ab

FareGraph prüft Ryanair-Angebote gegen den Flugplan und zeigt Zeiten in der
lokalen Zeitzone des jeweiligen Flughafens. Airline-Daten können sich dennoch
kurzfristig ändern. Maßgeblich ist immer die Buchungsseite der Airline.

## Öffentlicher Zugriff

FareGraph besitzt derzeit keine Benutzerkonten und sollte nicht ohne
zusätzlichen Zugriffsschutz direkt ins Internet gestellt werden. Empfohlen ist
ein Reverse Proxy mit HTTPS und Authentifizierung oder ein privater VPN-Zugang.

Der Ordner [`proxy`](proxy/) enthält die auf der ursprünglichen Unraid-Installation
verwendete Nginx-Struktur als Beispiel. Sie enthält installationsspezifische
IP-Adressen und muss vor der Verwendung angepasst werden. Der zugehörige
Compose-Dienst ist dem Profil `public-proxy` zugeordnet und wird bei einem
normalen `docker compose up -d` nicht gestartet.

## Aktuelle Grenzen

- Live-Datenquelle ist derzeit nur Ryanair; Wizz Air, easyJet und Eurowings sind geplant
- keine Garantie auf Vollständigkeit der inoffiziellen Airline-Daten
- keine automatische Buchung
- keine automatische Berechnung von Bahn-, Bus- oder Taxizeit bei Bodenwechseln
- keine Transferkosten bei Bodenwechseln
- gemeinsame Ziele vergleichen derzeit Direktflüge
- keine integrierte Anmeldung oder Benutzerverwaltung

## Datenschutz und Sicherheit

- FareGraph speichert Suchparameter und gefundene Flugdaten in der lokalen Datenbank.
- Es werden keine persönlichen Reisedaten oder Zahlungsinformationen benötigt.
- `.env`, Datenbankdateien, Zertifikate und Passwortdateien dürfen nicht in Git eingecheckt werden.
- Die Weboberfläche sollte im Heimnetz, hinter einem VPN oder hinter einem abgesicherten Reverse Proxy betrieben werden.

## Projektstruktur

```text
faregraph/
├── app/                  FastAPI-Backend, Suche und Datenquellen
├── static/               Weboberfläche und Übersetzungen
├── proxy/                installationsspezifisches Reverse-Proxy-Beispiel
├── .env.example          dokumentierte Beispielkonfiguration
├── docker-compose.yml    App, PostgreSQL und optionaler Proxy
├── Dockerfile            Container-Image der Anwendung
└── requirements.txt      Python-Abhängigkeiten
```

## Rechtlicher Hinweis

Die Nutzung erfolgt auf eigene Verantwortung. Betreiber müssen die Bedingungen
der verwendeten Datenquellen und die für sie geltenden gesetzlichen Vorgaben
selbst prüfen. FareGraph ist ein unabhängiges Open-Source-Projekt und keine
offizielle Anwendung einer Fluggesellschaft.
