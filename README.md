# FareGraph

[English](#english) · [Deutsch](#deutsch)

<a id="english"></a>
## English

FareGraph finds affordable round trips and multi-leg flight routes that
traditional flight search engines often cannot combine usefully. Instead of
only searching for `A → B → A`, FareGraph collects available one-way flights
and connects them as a time-dependent graph.

Examples:

- `CGN → PMI → STN → BGY → NRN`
- depart from Cologne and return through another nearby airport
- find the cheapest possible route with two to six flight segments
- let two groups of friends depart from different places and find a common destination
- continue from a nearby airport, for example `NRN ⇢ EIN`

FareGraph runs entirely as a Docker application with a web interface and a
PostgreSQL database. It was designed for Unraid but also works on other Docker
systems.

> [!IMPORTANT]
> Ryanair access uses public but unofficial and undocumented web endpoints.
> These endpoints can change or rate-limit requests. FareGraph is not affiliated
> with Ryanair or any other airline. Always verify prices, flight times and
> availability on the airline's website before booking.

### Features

- on-demand collection instead of permanent crawling
- Ryanair as the default live data source
- PostgreSQL storage for all search jobs and offers
- configurable date window, price limit and maximum search depth
- correct local departure and arrival times
- separate display of total trip duration and actual flight time
- route search with a configurable minimum stay after arrival
- ground transfers between airports within a configurable radius
- automatically add nearby active Ryanair airports as departure airports
- optional destination region with nearby airports and searches to, from or
  round-trip through that region
- price-data timestamps, relative age and one-click recollection of a dataset
- interactive map for selected routes
- find common direct destinations from two collected datasets
- German and English interface
- live display of search time, requests, cache hits and found offers
- pause, resume and permanently delete search jobs
- cache for identical airline requests
- support for `Retry-After`, exponential retries and optional throttling
- demo data source for safe functional testing

### Requirements

- Docker Engine with Docker Compose v2
- approximately 1 GB of free memory
- sufficient storage for PostgreSQL; actual usage depends on the number and
  depth of search jobs
- internet access from the container for Ryanair requests and map tiles

On Unraid, a share such as `appdata` is recommended for persistent PostgreSQL
data.

### Quick start with Docker

#### 1. Download the project

```bash
git clone https://github.com/RealHaltewunsch/faregraph.git
cd faregraph
```

Alternatively, download the repository as a ZIP file from GitHub and extract it.

#### 2. Create the configuration

```bash
cp .env.example .env
```

Open `.env` and replace `change-me` in both of the following lines with the
same secure password:

```dotenv
POSTGRES_PASSWORD=a-long-random-password
DATABASE_URL=postgresql://faregraph:a-long-random-password@db:5432/faregraph
```

Important: Special characters in `DATABASE_URL` must be URL-encoded. A long
password consisting of letters and numbers is easiest to configure correctly.

By default, database data is stored in `./data/postgres`. Change this line to
use another directory:

```dotenv
POSTGRES_DATA_PATH=./data/postgres
```

#### 3. Start FareGraph

```bash
docker compose up -d --build
```

On the first start, Docker builds the app image, downloads PostgreSQL and
creates the database tables automatically.

#### 4. Open the web interface

```text
http://SERVER-IP:8787
```

On the same computer, this is normally:

```text
http://localhost:8787
```

The port can be changed in `.env`:

```dotenv
APP_PORT=8787
```

### Installation on Unraid

The simplest installation uses the Unraid terminal and Docker Compose.

#### 1. Create the project directory and clone the repository

```bash
mkdir -p /mnt/user/appdata/faregraph
cd /mnt/user/appdata/faregraph
git clone https://github.com/RealHaltewunsch/faregraph.git src
cd src
```

If `git` is unavailable on the server, download the GitHub ZIP file, copy it to
`/mnt/user/appdata/faregraph/src` through an Unraid share and extract it there.

#### 2. Create the Unraid configuration

```bash
cp .env.example .env
```

At minimum, change the password and storage path in `.env`:

```dotenv
POSTGRES_PASSWORD=a-long-random-password
DATABASE_URL=postgresql://faregraph:a-long-random-password@db:5432/faregraph
POSTGRES_DATA_PATH=/mnt/user/appdata/faregraph/postgres
APP_PORT=8787
```

#### 3. Start the containers

```bash
docker compose up -d --build
```

FareGraph is then available at:

```text
http://UNRAID-IP:8787
```

#### 4. Check the installation

```bash
docker compose ps
curl http://127.0.0.1:8787/health
```

The health endpoint should return a response containing `"status":"ok"`.

### Using FareGraph

#### 1. Collect data

Open the **Collect data** tab.

1. Choose the search type: **Flexible: anywhere**, **One-way trip to a desired
   destination** or **Round trip to a desired destination**. Destination fields
   only appear for the latter two choices.
2. Enter one or more departure airports as IATA codes, for example `NRN` or
   `CGN,DUS,NRN`.
3. Optional: Set the radius to, for example, `150 km` and click
   **Add nearby Ryanair airports**. FareGraph adds active Ryanair airports
   within the radius and displays their distances.
4. For a targeted search, enter one or more comma-separated destination
   airports. You can expand
   this region independently with **Add nearby Ryanair airports to destination**.
5. Select the earliest and latest possible departure dates.
6. Set the trip duration, maximum number of flights and price limit.
7. Choose **Ryanair live** or use **Demo data** for a safe test.
8. Click **Start search**.

The job runs in the background. The interface displays the elapsed search time,
current level, number of requests, cache hits and found offers live.

##### Collection parameters

| Setting | Meaning |
| --- | --- |
| Departure airports | Airports from which the initial search starts |
| Nearby-airport radius | Adds active Ryanair departure airports; it does not change ground transfers within a route |
| Desired destination airports | Comma-separated target region shown for one-way and round-trip searches; each listed airport is accepted as a target |
| Destination radius | Adds active Ryanair airports around the desired destination |
| Search type | Collect flexibly anywhere, one-way to a target, or there and back |
| Minimum stay at destination | For round trips, earliest return departure after actual arrival at a target |
| Earliest/latest departure | Date window for the first flights, up to 92 days (about three months) |
| Min./max. trip days | Shortest and longest permitted time from the first departure to the final arrival |
| Max. flights per connection | Maximum graph depth or number of individual flight segments |
| Earliest onward departure after arrival | Earliest permitted next flight, calculated from the actual arrival time |
| Max. price per flight | More expensive individual offers are not stored |
| Max. distance between ground airports | Allows onward searches from another airport within this radius; `0` disables it |

A large depth, many departure airports and a wide date window can significantly
increase the number of airline requests. Start with one to three departure
airports and a depth of two or three. FareGraph splits long Ryanair date
windows into smaller request windows internally so a three-month collection is
not truncated by one oversized provider response.

#### 2. Find routes

After a collection job has completed:

1. Open **Find routes**.
2. Select one or more collected datasets. FareGraph combines their flights for
   this query without copying the datasets. Selecting adjacent periods allows,
   for example, a departure on 30 September from one dataset and a return on
   3 October from another.
3. Enter the permitted final airports and, when needed, a list of airports of
   which at least one must be visited. FareGraph prefills these fields for
   targeted collection jobs.
4. Set the minimum and maximum number of flights.
5. Set the minimum stay after arrival and the total budget.
6. Click **Find cheapest routes**.

`Max. flights = 2` does not automatically mean a return trip. It permits two
individual segments, so it can produce either `A → B → A` or `A → B → C` when
the final airport is permitted.

Each result displays these values separately:

- **Trip duration:** time from the first departure to the final arrival, including stays
- **Flight time:** sum of the actual flight time of every segment

Click **Show on map** to display only the selected route on the map.

#### 3. Ground transfers between airports

During collection, you can permit transfers between airports within a maximum
distance. For example, after arriving at `NRN`, a route may continue from `EIN`
when the configured radius is large enough.

- `0 km`: no airport transfer
- `100 km`: transfer to Ryanair airports within 100 km
- the radius used for route finding cannot exceed the radius used for collection
- ground transfers are displayed separately and do not count as flight segments
- ground-transfer time and cost are not calculated automatically yet

#### 4. Find a common destination

For two groups departing from different regions:

1. Collect one dataset for departure region A.
2. Collect another dataset for departure region B.
3. Open **Common destination**.
4. Select both datasets and the outbound departure window.
5. Set the minimum and maximum stay in calendar days, the permitted number of
   flights in each return route, the maximum arrival difference, the permitted
   difference between the two return-departure dates and the total budget for
   both round trips. A return-flight difference of `0` requires both groups to
   return on the same calendar day.
6. Click **Find common destinations**.

FareGraph combines a direct outbound flight for each group with a return route
to that group's departure region. The stay is counted from the calendar date
on which both groups have arrived. Results include all outbound and return
segments. Return routes are optimized as a pair, so the cheapest return for one
group cannot silently extend that group's stay beyond the permitted difference.

### Managing search jobs

- **Pause:** stops the job safely after the current request
- **Resume:** continues a paused job using its stored queue
- **Collect again:** starts a fresh collection with the same settings
- **Delete:** permanently removes the job and all associated flight offers

Each job shows the timestamp of the price data and its relative age. When a
job contains responses collected at different times, FareGraph shows the full
time range. Older jobs created before this feature show their creation time.

If the app container restarts during a search, the job is stored as paused and
can be resumed afterward.

### Configuration

All configuration values are stored in `.env`:

| Variable | Default | Description |
| --- | ---: | --- |
| `APP_PORT` | `8787` | Web interface port on the Docker host |
| `POSTGRES_DB` | `faregraph` | Database name |
| `POSTGRES_USER` | `faregraph` | Database user |
| `POSTGRES_PASSWORD` | `change-me` | Database password; always change this |
| `DATABASE_URL` | – | App connection to the database; the password must match |
| `POSTGRES_DATA_PATH` | `./data/postgres` | Persistent storage location for PostgreSQL data |
| `CACHE_TTL_HOURS` | `6` | Cache lifetime for identical fare requests |
| `AIRPORT_CATALOG_TTL_DAYS` | `7` | Cache lifetime for the Ryanair airport catalogue |
| `RYANAIR_MIN_DELAY` | `0` | Optional minimum delay between Ryanair requests |
| `RYANAIR_MAX_DELAY` | `0` | Optional maximum delay between Ryanair requests |

The fixed delay is disabled by default. FareGraph still respects `Retry-After`
for HTTP `429` responses. Temporary network and server errors are retried with
exponential backoff.

### Updating

Run these commands inside the project directory:

```bash
git pull
docker compose up -d --build
```

PostgreSQL data remains intact as long as `POSTGRES_DATA_PATH` is not deleted or
changed.

### Stopping and restarting

```bash
docker compose stop
docker compose start
```

Remove the containers while keeping the data:

```bash
docker compose down
```

> [!CAUTION]
> `docker compose down -v` or deleting `POSTGRES_DATA_PATH` removes stored
> search jobs and flight data.

### Backup

For a file-based backup, stop FareGraph briefly and back up the directory
configured as `POSTGRES_DATA_PATH`. Alternatively, create a PostgreSQL dump:

```bash
docker compose exec -T db pg_dump -U faregraph faregraph > faregraph-backup.sql
```

### Troubleshooting

#### The web interface is unavailable

```bash
docker compose ps
docker compose logs --tail=100 app
docker compose logs --tail=100 db
```

Also check whether another container is already using `APP_PORT`.

#### The database does not start

Make sure the directory configured as `POSTGRES_DATA_PATH` exists and is
writable by Docker. `POSTGRES_PASSWORD` and the password inside `DATABASE_URL`
must be identical.

#### Ryanair rate-limits or resets requests

FareGraph waits according to the response from the data source and retries
temporary failures. A stopped job can be resumed through the web interface.
Split very large searches into smaller date windows or lower search depths.

#### Times or prices differ

FareGraph validates Ryanair offers against the timetable and displays times in
the local timezone of each airport. Airline data may still change at short
notice. The airline's booking page is always authoritative.

### Public access

FareGraph currently has no user accounts and should not be exposed directly to
the internet without additional access protection. Use a reverse proxy with
HTTPS and authentication, or access FareGraph through a private VPN.

The [`proxy`](proxy/) directory contains the Nginx structure used by the
original Unraid installation as an example. It contains installation-specific
IP addresses that must be adapted. The corresponding Compose service uses the
`public-proxy` profile and is not started by a normal `docker compose up -d`.

### Current limitations

- Ryanair is currently the only live source; Wizz Air, easyJet and Eurowings are planned
- unofficial airline data is not guaranteed to be complete
- no automatic booking
- no automatic calculation of train, bus or taxi time for ground transfers
- no ground-transfer costs
- common destinations currently compare direct flights
- no integrated login or user management

### Privacy and security

- FareGraph stores search settings and found flight data in the local database.
- It does not require personal travel data or payment information.
- Never commit `.env`, database files, certificates or password files to Git.
- Run the interface inside a home network, behind a VPN or behind a protected reverse proxy.

### Project structure

```text
faregraph/
├── app/                  FastAPI backend, search and data sources
├── static/               Web interface and translations
├── proxy/                installation-specific reverse-proxy example
├── .env.example          documented example configuration
├── docker-compose.yml    app, PostgreSQL and optional proxy
├── Dockerfile            application container image
└── requirements.txt      Python dependencies
```

### Legal notice

Use this software at your own risk. Operators are responsible for checking the
terms of the data sources they use and all applicable laws and regulations.
FareGraph is an independent project and not an official airline application.

---

<a id="deutsch"></a>
## Deutsch

FareGraph findet günstige Rundreisen und mehrteilige Flugrouten, die klassische
Flugsuchmaschinen oft nicht sinnvoll zusammensetzen. Statt nur nach
`A → B → A` zu suchen, sammelt FareGraph verfügbare Einzelflüge und verbindet
sie anschließend als zeitabhängigen Graphen.

Beispiele:

- `CGN → PMI → STN → BGY → NRN`
- Start in Köln, Rückkehr über einen anderen Flughafen in der Umgebung
- möglichst günstige Route mit zwei bis sechs Flugsegmenten
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

1. Wähle die Suchart: **Flexibel: egal wohin**, **Einfache Reise zum
   Wunschziel** oder **Hin- und Rückreise zum Wunschziel**. Die Ziel-Felder
   erscheinen nur bei den beiden zielgerichteten Sucharten.
2. Trage einen oder mehrere Startflughäfen als IATA-Codes ein, beispielsweise
   `NRN` oder `CGN,DUS,NRN`.
3. Optional: Stelle den Radius auf beispielsweise `150 km` und klicke auf
   **Nahe Ryanair-Flughäfen hinzufügen**. FareGraph ergänzt aktive
   Ryanair-Flughäfen im Umkreis und zeigt deren Entfernung an.
4. Trage bei einer zielgerichteten Suche einen oder mehrere komma-getrennte
   Wunschziel-Flughäfen ein. Mit **Nahe
   Ryanair-Flughäfen zum Ziel hinzufügen** lässt sich diese Zielregion separat
   erweitern.
5. Wähle das früheste und späteste mögliche Startdatum.
6. Lege Reisedauer, maximale Zahl der Flüge und Preislimit fest.
7. Wähle **Ryanair live** oder für einen ungefährlichen Test **Demo-Daten**.
8. Klicke auf **Suche starten**.

Der Auftrag läuft im Hintergrund. Die Oberfläche zeigt Suchzeit, aktuelle
Ebene, Zahl der Abfragen, Cache-Treffer und gefundene Angebote live an.

#### Bedeutung der Sammelparameter

| Einstellung | Bedeutung |
| --- | --- |
| Startflughäfen | Flughäfen, von denen die erste Suche beginnt |
| Radius für nahe Flughäfen | Ergänzt weitere aktive Ryanair-Startflughäfen; verändert nicht den Bodenwechsel einer Route |
| Wunschziel-Flughäfen | Komma-getrennte Zielregion für einfache Hinreise oder Hin- und Rückreise; jeder eingetragene Flughafen gilt als passendes Ziel |
| Zielradius | Ergänzt aktive Ryanair-Flughäfen rund um das Wunschziel |
| Suchart | Sammelt flexibel egal wohin, als einfache Hinreise oder hin und zurück |
| Mindestaufenthalt am Wunschziel | Bei Hin und zurück frühester Rückflug ab tatsächlicher Ankunft am Ziel |
| Frühester/spätester Start | Datumsfenster für die ersten Abflüge, maximal 92 Tage (etwa drei Monate) |
| Min./max. Reisetage | Kürzester und längster erlaubter Zeitraum vom ersten Abflug bis zur letzten Ankunft |
| Max. Flüge je Verbindung | Maximale Graphentiefe beziehungsweise Zahl einzelner Flugsegmente |
| Früheste Weiterreise ab Ankunft | Frühester erlaubter Folgeflug, gerechnet ab der tatsächlichen Ankunft |
| Max. Preis je Flug | Einzelne teurere Flugangebote werden nicht gespeichert |
| Entfernung zwischen Bodenflughäfen | Erlaubt Folgesuchen von einem anderen Flughafen im angegebenen Radius; `0` deaktiviert dies |

Eine große Suchtiefe, viele Startflughäfen und ein großes Datumsfenster können
die Zahl der Airline-Abfragen stark erhöhen. Für den Einstieg sind ein bis drei
Startflughäfen und eine Tiefe von zwei oder drei sinnvoll. FareGraph teilt lange
Ryanair-Zeiträume intern in kleinere Abfragefenster, damit eine dreimonatige
Sammlung nicht durch eine einzelne zu große Anbieterantwort abgeschnitten wird.

### 2. Routen finden

Nach Abschluss eines Sammelauftrags:

1. Öffne **Routen finden**.
2. Wähle einen oder mehrere gesammelte Datensätze. FareGraph kombiniert deren
   Flüge für diese Abfrage, ohne die Datensätze zu kopieren. Mit angrenzenden
   Zeiträumen kann eine Route beispielsweise am 30. September aus einem
   Datensatz starten und am 3. Oktober über einen anderen zurückkehren.
3. Trage die erlaubten Endflughäfen ein und bei Bedarf eine Liste, von der
   mindestens ein Flughafen besucht werden muss. Bei zielgerichteten
   Sammelaufträgen füllt FareGraph diese Felder automatisch passend vor.
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
4. Wähle beide Datensätze und das Abflugfenster für die Hinflüge.
5. Lege minimalen und maximalen Aufenthalt in Kalendertagen, die erlaubte Zahl
   der Flüge je Rückroute, die maximale Ankunftsdifferenz, den erlaubten
   Unterschied zwischen den beiden Rückflugtagen und das Gesamtbudget für beide
   Rundreisen fest. Eine Rückflug-Differenz von `0` verlangt denselben
   Kalendertag für beide Gruppen.
6. Klicke auf **Gemeinsame Ziele finden**.

FareGraph kombiniert für jede Gruppe einen direkten Hinflug mit einer
Rückroute in ihre Startregion. Der Aufenthalt zählt ab dem Kalendertag, an dem
beide Gruppen angekommen sind. Die Ergebnisse enthalten alle Hin- und
Rückflugsegmente. Die Rückrouten werden als Paar optimiert, damit ein billigerer
Rückflug für nur eine Gruppe deren Aufenthalt nicht unbemerkt verlängert.

## Suchaufträge verwalten

- **Pausieren:** beendet den Auftrag kontrolliert nach der aktuellen Abfrage
- **Fortsetzen:** setzt einen pausierten Auftrag mit seiner gespeicherten Warteschlange fort
- **Neu sammeln:** startet einen frischen Sammelauftrag mit denselben Einstellungen
- **Löschen:** entfernt den Auftrag und alle zugehörigen Flugangebote dauerhaft

Jeder Auftrag zeigt den Zeitstempel des Preisstands und dessen relatives Alter.
Wurden Antworten zu unterschiedlichen Zeitpunkten gesammelt, erscheint der
gesamte Zeitraum. Ältere Aufträge zeigen ersatzweise ihren Erstellzeitpunkt.

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
