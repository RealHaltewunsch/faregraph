from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
import airportsdata
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .providers import fetch_offers, fetch_provider_airports


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://faregraph:faregraph-local@localhost:5432/faregraph")
CACHE_TTL_HOURS = max(1, int(os.getenv("CACHE_TTL_HOURS", "6")))
AIRPORT_CATALOG_TTL_DAYS = max(1, int(os.getenv("AIRPORT_CATALOG_TTL_DAYS", "7")))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="FareGraph", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
AIRPORTS = airportsdata.load("IATA")
LOGGER = logging.getLogger("faregraph")
ACTIVE_WORKERS: set[uuid.UUID] = set()
ACTIVE_WORKERS_LOCK = threading.Lock()
CACHE_KEY_LOCKS: dict[tuple, threading.Lock] = {}
CACHE_KEY_LOCKS_LOCK = threading.Lock()
PROVIDER_CATALOG_LOCKS: dict[str, threading.Lock] = {}
PROVIDER_CATALOG_LOCKS_LOCK = threading.Lock()


def airport_info(code: str) -> dict:
    code = code.strip().upper()
    raw = AIRPORTS.get(code, {})
    return {
        "code": code,
        "name": raw.get("name") or code,
        "city": raw.get("city") or "",
        "country": raw.get("country") or "",
        "timezone": raw.get("tz") or "",
        "latitude": raw.get("lat"),
        "longitude": raw.get("lon"),
    }


def connect():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def init_db():
    deadline = time.time() + 60
    while True:
        try:
            with connect() as con, con.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS search_jobs (
                        id uuid PRIMARY KEY,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        status text NOT NULL,
                        settings jsonb NOT NULL,
                        current_depth integer NOT NULL DEFAULT 0,
                        queries_done integer NOT NULL DEFAULT 0,
                        offers_found integer NOT NULL DEFAULT 0,
                        error text
                    );
                    CREATE TABLE IF NOT EXISTS flight_offers (
                        id bigserial PRIMARY KEY,
                        search_job_id uuid NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
                        provider text NOT NULL,
                        airline text NOT NULL,
                        flight_number text,
                        origin char(3) NOT NULL,
                        destination char(3) NOT NULL,
                        departure_time timestamptz NOT NULL,
                        arrival_time timestamptz NOT NULL,
                        price numeric(10,2) NOT NULL,
                        currency char(3) NOT NULL,
                        booking_url text,
                        fetched_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE(search_job_id, provider, origin, destination, departure_time, price)
                    );
                    CREATE INDEX IF NOT EXISTS flight_job_origin_departure
                      ON flight_offers(search_job_id, origin, departure_time);
                    ALTER TABLE search_jobs ADD COLUMN IF NOT EXISTS cache_hits integer NOT NULL DEFAULT 0;
                    ALTER TABLE search_jobs ADD COLUMN IF NOT EXISTS external_queries integer NOT NULL DEFAULT 0;
                    ALTER TABLE search_jobs ADD COLUMN IF NOT EXISTS started_at timestamptz;
                    ALTER TABLE search_jobs ADD COLUMN IF NOT EXISTS run_started_at timestamptz;
                    ALTER TABLE search_jobs ADD COLUMN IF NOT EXISTS active_seconds double precision NOT NULL DEFAULT 0;
                    ALTER TABLE search_jobs ADD COLUMN IF NOT EXISTS data_fetched_from timestamptz;
                    ALTER TABLE search_jobs ADD COLUMN IF NOT EXISTS data_fetched_to timestamptz;
                    UPDATE search_jobs SET started_at=created_at,
                      active_seconds=GREATEST(0,EXTRACT(epoch FROM (updated_at-created_at)))
                    WHERE started_at IS NULL AND queries_done > 0;
                    CREATE TABLE IF NOT EXISTS search_job_nodes (
                        id bigserial PRIMARY KEY,
                        search_job_id uuid NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
                        origin char(3) NOT NULL,
                        window_start date NOT NULL,
                        window_end date NOT NULL,
                        depth integer NOT NULL,
                        status text NOT NULL DEFAULT 'queued',
                        attempts integer NOT NULL DEFAULT 0,
                        error text,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE(search_job_id, origin, window_start, window_end, depth)
                    );
                    CREATE INDEX IF NOT EXISTS search_job_nodes_next
                      ON search_job_nodes(search_job_id, status, depth, id);
                    CREATE TABLE IF NOT EXISTS provider_query_cache (
                        id bigserial PRIMARY KEY,
                        provider text NOT NULL,
                        origin char(3) NOT NULL,
                        date_from date NOT NULL,
                        date_to date NOT NULL,
                        max_price numeric(10,2) NOT NULL,
                        offers jsonb NOT NULL,
                        fetched_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE(provider, origin, date_from, date_to, max_price)
                    );
                    CREATE TABLE IF NOT EXISTS provider_airports (
                        provider text NOT NULL,
                        code char(3) NOT NULL,
                        name text NOT NULL,
                        city text NOT NULL DEFAULT '',
                        country text NOT NULL DEFAULT '',
                        timezone text NOT NULL DEFAULT '',
                        latitude double precision NOT NULL,
                        longitude double precision NOT NULL,
                        fetched_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY(provider, code)
                    );
                    CREATE TABLE IF NOT EXISTS provider_airport_distances (
                        provider text NOT NULL,
                        origin char(3) NOT NULL,
                        destination char(3) NOT NULL,
                        distance_km numeric(8,1) NOT NULL,
                        calculated_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY(provider, origin, destination)
                    );
                    CREATE INDEX IF NOT EXISTS provider_airport_distances_nearby
                      ON provider_airport_distances(provider, origin, distance_km);
                    UPDATE search_jobs
                    SET error='Die Verbindung zur Datenquelle wurde unerwartet beendet.'
                    WHERE error LIKE '%Errno 104%' OR error ILIKE '%Connection reset by peer%';
                """)
            return
        except psycopg.OperationalError:
            if time.time() >= deadline:
                raise
            time.sleep(2)


@app.on_event("startup")
def startup():
    init_db()
    with connect() as con, con.cursor() as cur:
        cur.execute("UPDATE search_job_nodes SET status='queued', updated_at=now() WHERE status='running'")
        cur.execute("""
            UPDATE search_jobs SET status='paused',
              active_seconds=active_seconds+CASE WHEN run_started_at IS NULL THEN 0
                ELSE GREATEST(0,EXTRACT(epoch FROM (updated_at-run_started_at))) END,
              run_started_at=NULL,
              error='App wurde während der Suche neu gestartet – der Auftrag kann fortgesetzt werden',
              updated_at=now() WHERE status='running'
        """)
        cur.execute("SELECT id FROM search_jobs WHERE status='queued'")
        queued_jobs = [row[0] for row in cur.fetchall()]
    for job_id in queued_jobs:
        start_job_thread(job_id)


class JobCreate(BaseModel):
    start_airports: list[str] = Field(min_length=1, max_length=50)
    target_airports: list[str] = Field(default_factory=list, max_length=50)
    search_direction: Literal["any", "to_target", "from_target", "round_trip"] = "any"
    min_target_stay_hours: int = Field(default=24, ge=0, le=336)
    start_date: date
    end_date: date
    min_trip_days: int = Field(default=1, ge=1, le=14)
    max_trip_days: int = Field(default=5, ge=1, le=14)
    max_depth: int = Field(default=3, ge=1, le=6)
    min_connection_hours: int = Field(default=0, ge=0, le=168)
    max_price_per_leg: float = Field(default=80, gt=0, le=1000)
    max_destinations_per_node: int = Field(default=12, ge=1, le=50)
    max_airport_transfer_km: float = Field(default=0, ge=0, le=5000)
    provider: Literal["demo", "ryanair"] = "ryanair"

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("Enddatum muss nach dem Startdatum liegen")
        if (self.end_date - self.start_date).days > 92:
            raise ValueError("Das Startfenster darf höchstens 92 Tage umfassen")
        if self.min_trip_days > self.max_trip_days:
            raise ValueError("Min. Reisetage darf Max. Reisetage nicht überschreiten")
        self.start_airports = sorted({code.strip().upper() for code in self.start_airports if code.strip()})
        self.target_airports = sorted({code.strip().upper() for code in self.target_airports if code.strip()})
        if not self.start_airports or any(len(code) != 3 for code in self.start_airports):
            raise ValueError("Flughäfen müssen dreistellige IATA-Codes sein")
        if any(len(code) != 3 for code in self.target_airports):
            raise ValueError("Wunschziele müssen dreistellige IATA-Codes sein")
        if self.search_direction != "any" and not self.target_airports:
            raise ValueError("Für diese Suchrichtung ist mindestens ein Wunschziel erforderlich")
        return self


class RouteQuery(BaseModel):
    job_id: uuid.UUID | None = None
    job_ids: list[uuid.UUID] = Field(default_factory=list, max_length=12)
    end_airports: list[str] = Field(min_length=1)
    required_visit_airports: list[str] = Field(default_factory=list)
    min_segments: int = Field(default=2, ge=1, le=6)
    max_segments: int = Field(default=4, ge=1, le=6)
    min_connection_hours: int = Field(default=8, ge=0, le=168)
    min_target_stay_hours: int = Field(default=0, ge=0, le=336)
    max_airport_transfer_km: float = Field(default=0, ge=0, le=5000)
    max_total_price: float = Field(default=200, gt=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_jobs(self):
        selected = self.job_ids or ([self.job_id] if self.job_id else [])
        self.job_ids = list(dict.fromkeys(selected))
        if not self.job_ids:
            raise ValueError("Mindestens ein Datensatz muss ausgewählt werden")
        return self


class MeetupQuery(BaseModel):
    job_a_id: uuid.UUID
    job_b_id: uuid.UUID
    departure_from: date
    departure_to: date
    max_arrival_difference_hours: float = Field(default=24, ge=0, le=168)
    min_stay_days: int = Field(default=3, ge=0, le=14)
    max_stay_days: int = Field(default=5, ge=0, le=14)
    min_outbound_segments: int = Field(default=1, ge=1, le=6)
    max_outbound_segments: int = Field(default=1, ge=1, le=6)
    min_return_segments: int = Field(default=1, ge=1, le=6)
    max_return_segments: int = Field(default=2, ge=1, le=6)
    max_route_duration_hours: float = Field(default=24, ge=1, le=336)
    max_return_difference_days: int = Field(default=0, ge=0, le=14)
    max_total_price: float = Field(default=300, gt=0, le=5000)
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def validate_meetup(self):
        if self.job_a_id == self.job_b_id:
            raise ValueError("Bitte zwei unterschiedliche Datensätze auswählen")
        if self.departure_to < self.departure_from:
            raise ValueError("Das Enddatum muss nach dem Startdatum liegen")
        if self.min_stay_days > self.max_stay_days:
            raise ValueError("Der minimale Aufenthalt darf den maximalen Aufenthalt nicht überschreiten")
        if self.min_outbound_segments > self.max_outbound_segments:
            raise ValueError("Min. Hinflüge darf Max. Hinflüge nicht überschreiten")
        if self.min_return_segments > self.max_return_segments:
            raise ValueError("Min. Rückflüge darf Max. Rückflüge nicht überschreiten")
        return self


def _job_row(row):
    return {
        "id": str(row[0]), "created_at": row[1], "updated_at": row[2], "status": row[3],
        "settings": row[4], "current_depth": row[5], "queries_done": row[6],
        "offers_found": row[7], "error": row[8], "cache_hits": row[9],
        "external_queries": row[10], "elapsed_seconds": round(float(row[11] or 0)),
        "data_fetched_from": row[12], "data_fetched_to": row[13],
    }


def _public_error(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, ConnectionResetError) or "connection reset" in text or "errno 104" in text:
        return "Die Verbindung zur Datenquelle wurde unerwartet beendet."
    if isinstance(exc, httpx.TimeoutException):
        return "Die Datenquelle hat nicht rechtzeitig geantwortet."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Die Datenquelle hat die Anfrage abgelehnt (HTTP {exc.response.status_code})."
    return "Bei der Verarbeitung ist ein interner Fehler aufgetreten. Details stehen im Serverprotokoll."


def _cache_key(provider: str, origin: str, date_from: date, date_to: date, max_price: float) -> tuple:
    return provider, origin, date_from, date_to, round(max_price, 2)


def _cache_lock(key: tuple) -> threading.Lock:
    with CACHE_KEY_LOCKS_LOCK:
        return CACHE_KEY_LOCKS.setdefault(key, threading.Lock())


def _catalog_lock(provider: str) -> threading.Lock:
    with PROVIDER_CATALOG_LOCKS_LOCK:
        return PROVIDER_CATALOG_LOCKS.setdefault(provider, threading.Lock())


def _distance_km(a: dict, b: dict) -> float:
    """Great-circle distance between two airport catalogue entries."""
    lat1, lon1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lon2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(value))


def ensure_provider_airport_catalog(provider: str) -> None:
    """Refresh the provider airport/distance cache only when it is missing or stale."""
    with _catalog_lock(provider):
        with connect() as con, con.cursor() as cur:
            cur.execute("SELECT count(*),max(fetched_at) FROM provider_airports WHERE provider=%s", (provider,))
            count, fetched_at = cur.fetchone()
        if count and fetched_at and fetched_at >= datetime.now(timezone.utc) - timedelta(days=AIRPORT_CATALOG_TTL_DAYS):
            return

        airports = fetch_provider_airports(provider)
        if not airports:
            raise RuntimeError(f"Der Flughafenkatalog für {provider} ist leer")
        distances = []
        for origin in airports:
            for destination in airports:
                if origin["code"] == destination["code"]:
                    continue
                distances.append((provider, origin["code"], destination["code"], round(_distance_km(origin, destination), 1)))

        with connect() as con, con.transaction(), con.cursor() as cur:
            cur.execute("DELETE FROM provider_airport_distances WHERE provider=%s", (provider,))
            cur.execute("DELETE FROM provider_airports WHERE provider=%s", (provider,))
            cur.executemany("""
                INSERT INTO provider_airports
                  (provider,code,name,city,country,timezone,latitude,longitude,fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
            """, [(provider, a["code"], a["name"], a["city"], a["country"], a["timezone"],
                    a["latitude"], a["longitude"]) for a in airports])
            cur.executemany("""
                INSERT INTO provider_airport_distances
                  (provider,origin,destination,distance_km,calculated_at)
                VALUES (%s,%s,%s,%s,now())
            """, distances)


def load_transfer_neighbors(provider: str, max_distance_km: float) -> dict[str, dict[str, float]]:
    if max_distance_km <= 0:
        return {}
    ensure_provider_airport_catalog(provider)
    with connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT origin,destination,distance_km
            FROM provider_airport_distances
            WHERE provider=%s AND distance_km<=%s
            ORDER BY origin,distance_km
        """, (provider, max_distance_km))
        rows = cur.fetchall()
    neighbors: dict[str, dict[str, float]] = {}
    for origin, destination, distance in rows:
        neighbors.setdefault(origin.strip(), {})[destination.strip()] = float(distance)
    return neighbors


def _serialize_offers(offers: list[dict]) -> str:
    payload = []
    for offer in offers:
        item = offer.copy()
        item["departure_time"] = offer["departure_time"].isoformat()
        item["arrival_time"] = offer["arrival_time"].isoformat()
        payload.append(item)
    return json.dumps(payload)


def _deserialize_offers(payload: list[dict]) -> list[dict]:
    offers = []
    for cached in payload:
        item = cached.copy()
        item["departure_time"] = datetime.fromisoformat(item["departure_time"])
        item["arrival_time"] = datetime.fromisoformat(item["arrival_time"])
        offers.append(item)
    return offers


def cached_fetch_offers(
    provider: str, origin: str, date_from: date, date_to: date, max_price: float
) -> tuple[list[dict], bool, datetime]:
    key = _cache_key(provider, origin, date_from, date_to, max_price)
    with _cache_lock(key):
        with connect() as con, con.cursor() as cur:
            cur.execute("""
                SELECT offers,fetched_at FROM provider_query_cache
                WHERE provider=%s AND origin=%s AND date_from=%s AND date_to=%s AND max_price=%s
                  AND fetched_at >= now() - (%s * interval '1 hour')
            """, (*key, CACHE_TTL_HOURS))
            row = cur.fetchone()
        if row:
            return _deserialize_offers(row[0]), True, row[1]

        offers = fetch_offers(provider, origin, date_from, date_to, max_price)
        fetched_at = datetime.now(timezone.utc)
        with connect() as con, con.cursor() as cur:
            cur.execute("""
                INSERT INTO provider_query_cache(provider,origin,date_from,date_to,max_price,offers,fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,now())
                ON CONFLICT (provider,origin,date_from,date_to,max_price)
                DO UPDATE SET offers=excluded.offers, fetched_at=now()
            """, (*key, _serialize_offers(offers)))
        return offers, False, fetched_at


def _start_worker(job_id: uuid.UUID) -> bool:
    with ACTIVE_WORKERS_LOCK:
        if job_id in ACTIVE_WORKERS:
            return False
        ACTIVE_WORKERS.add(job_id)
    threading.Thread(target=_worker_entry, args=(job_id,), daemon=True).start()
    return True


def start_job_thread(job_id: uuid.UUID) -> None:
    _start_worker(job_id)


def _worker_entry(job_id: uuid.UUID) -> None:
    try:
        run_job(job_id)
    finally:
        with ACTIVE_WORKERS_LOCK:
            ACTIVE_WORKERS.discard(job_id)
        try:
            with connect() as con, con.cursor() as cur:
                cur.execute("SELECT status FROM search_jobs WHERE id=%s", (job_id,))
                row = cur.fetchone()
            if row and row[0] == "queued":
                start_job_thread(job_id)
        except psycopg.Error:
            pass


def run_job(job_id: uuid.UUID) -> None:
    try:
        with connect() as con, con.cursor() as cur:
            cur.execute("SELECT settings,status FROM search_jobs WHERE id=%s", (job_id,))
            job = cur.fetchone()
            if not job or job[1] not in ("queued", "running"):
                return
            settings = job[0]
            cur.execute("""
                UPDATE search_jobs SET status='running', error=NULL,
                  started_at=COALESCE(started_at,now()),
                  run_started_at=COALESCE(run_started_at,now()), updated_at=now()
                WHERE id=%s
            """, (job_id,))

        last_date = date.fromisoformat(settings["end_date"]) + timedelta(days=settings["max_trip_days"])
        search_direction = settings.get("search_direction", "any")
        start_airports = set(settings["start_airports"])
        target_airports = set(settings.get("target_airports", []))
        priority_destinations = target_airports | (start_airports if search_direction in ("from_target", "round_trip") else set())
        transfer_neighbors = load_transfer_neighbors(
            settings["provider"], float(settings.get("max_airport_transfer_km", 0))
        )

        while True:
            with connect() as con, con.cursor() as cur:
                cur.execute("SELECT status FROM search_jobs WHERE id=%s", (job_id,))
                status_row = cur.fetchone()
                if not status_row or status_row[0] in ("paused", "cancelled"):
                    return
                cur.execute("""
                    SELECT id,origin,window_start,window_end,depth FROM search_job_nodes
                    WHERE search_job_id=%s AND status='queued'
                    ORDER BY depth,id LIMIT 1
                """, (job_id,))
                node = cur.fetchone()
                if not node:
                    cur.execute("SELECT count(*) FROM search_job_nodes WHERE search_job_id=%s AND status='skipped'", (job_id,))
                    skipped = cur.fetchone()[0]
                    message = None if not skipped else f"Abgeschlossen mit {skipped} übersprungenen Abfrage(n); gespeicherte Angebote sind nutzbar"
                    cur.execute("""
                        UPDATE search_jobs SET status='completed', error=%s,
                          active_seconds=active_seconds+CASE WHEN run_started_at IS NULL THEN 0
                            ELSE EXTRACT(epoch FROM (now()-run_started_at)) END,
                          run_started_at=NULL, updated_at=now() WHERE id=%s
                    """, (message, job_id))
                    return
                node_id, origin, window_start, window_end, depth = node
                origin = origin.strip()
                cur.execute("UPDATE search_job_nodes SET status='running', attempts=attempts+1, updated_at=now() WHERE id=%s", (node_id,))

            effective_end = min(window_end, last_date)
            try:
                offers, cache_hit, source_fetched_at = cached_fetch_offers(
                    settings["provider"], origin, window_start, effective_end,
                    settings["max_price_per_leg"],
                )
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                with connect() as con, con.cursor() as cur:
                    cur.execute("UPDATE search_job_nodes SET status='skipped', error=%s, updated_at=now() WHERE id=%s", (str(exc)[:1000], node_id))
                    cur.execute("""
                        UPDATE search_jobs SET queries_done=queries_done+1,
                          external_queries=external_queries+1,
                          error='Datenquelle vorübergehend nicht erreichbar; einzelne Abfrage übersprungen',
                          updated_at=now() WHERE id=%s
                    """, (job_id,))
                continue

            offers.sort(key=lambda item: item["price"])
            offer_limit = settings["max_destinations_per_node"] * max(1, (window_end - window_start).days + 1)
            prioritized = [offer for offer in offers if offer["destination"] in priority_destinations]
            selected_keys = {
                (offer["origin"], offer["destination"], offer["departure_time"], offer["price"])
                for offer in prioritized
            }
            offers = prioritized + [
                offer for offer in offers[:offer_limit]
                if (offer["origin"], offer["destination"], offer["departure_time"], offer["price"]) not in selected_keys
            ]

            with connect() as con, con.cursor() as cur:
                for offer in offers:
                    cur.execute("""
                        INSERT INTO flight_offers
                          (search_job_id, provider, airline, flight_number, origin, destination,
                           departure_time, arrival_time, price, currency, booking_url)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING
                    """, (job_id, offer["provider"], offer["airline"], offer["flight_number"],
                          offer["origin"], offer["destination"], offer["departure_time"], offer["arrival_time"],
                          offer["price"], offer["currency"], offer["booking_url"]))
                    next_start = (offer["arrival_time"] + timedelta(hours=settings["min_connection_hours"])).date()
                    reached_final_region = (
                        search_direction == "to_target" and offer["destination"] in target_airports
                    ) or (
                        search_direction == "from_target" and offer["destination"] in start_airports
                    )
                    if not reached_final_region and next_start <= last_date and depth + 1 < settings["max_depth"]:
                        next_origins = {offer["destination"]}
                        next_origins.update(transfer_neighbors.get(offer["destination"], {}))
                        for next_origin in next_origins:
                            cur.execute("""
                                INSERT INTO search_job_nodes(search_job_id,origin,window_start,window_end,depth)
                                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
                            """, (job_id, next_origin, next_start, last_date, depth + 1))
                cur.execute("UPDATE search_job_nodes SET status='completed', error=NULL, updated_at=now() WHERE id=%s", (node_id,))
                cur.execute("""
                    UPDATE search_jobs SET current_depth=GREATEST(current_depth,%s),
                      queries_done=queries_done+1,
                      cache_hits=cache_hits+%s,
                      external_queries=external_queries+%s,
                      offers_found=(SELECT count(*) FROM flight_offers WHERE search_job_id=%s),
                      data_fetched_from=CASE WHEN data_fetched_from IS NULL THEN %s
                        ELSE LEAST(data_fetched_from,%s) END,
                      data_fetched_to=CASE WHEN data_fetched_to IS NULL THEN %s
                        ELSE GREATEST(data_fetched_to,%s) END,
                      updated_at=now() WHERE id=%s
                """, (depth + 1, 1 if cache_hit else 0, 0 if cache_hit else 1, job_id,
                      source_fetched_at, source_fetched_at, source_fetched_at, source_fetched_at, job_id))
    except Exception as exc:
        LOGGER.exception("Suchauftrag %s ist fehlgeschlagen", job_id)
        with connect() as con, con.cursor() as cur:
            cur.execute("UPDATE search_job_nodes SET status='queued', updated_at=now() WHERE search_job_id=%s AND status='running'", (job_id,))
            cur.execute("""
                UPDATE search_jobs SET status='failed', error=%s,
                  active_seconds=active_seconds+CASE WHEN run_started_at IS NULL THEN 0
                    ELSE EXTRACT(epoch FROM (now()-run_started_at)) END,
                  run_started_at=NULL, updated_at=now() WHERE id=%s
            """, (_public_error(exc), job_id))


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    with connect() as con, con.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"status": "ok", "time": datetime.now(timezone.utc)}


@app.get("/api/airports/nearby")
def nearby_provider_airports(codes: str, radius_km: float = 150, provider: str = "ryanair"):
    origins = sorted({code.strip().upper() for code in codes.split(",") if code.strip()})
    if not origins or any(len(code) != 3 for code in origins):
        raise HTTPException(400, "Bitte mindestens einen dreistelligen IATA-Code eingeben")
    if radius_km <= 0 or radius_km > 1000:
        raise HTTPException(400, "Der Radius muss zwischen 1 und 1000 km liegen")
    if provider != "ryanair":
        raise HTTPException(400, "Nahe Startflughäfen werden derzeit für Ryanair angeboten")

    origin_info = []
    for code in origins:
        info = airport_info(code)
        if not isinstance(info["latitude"], (int, float)) or not isinstance(info["longitude"], (int, float)):
            raise HTTPException(404, f"Flughafen {code} wurde nicht gefunden")
        origin_info.append(info)

    try:
        ensure_provider_airport_catalog(provider)
    except Exception as exc:
        LOGGER.exception("Provider airport catalogue failed")
        raise HTTPException(502, _public_error(exc)) from exc

    with connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT code,name,city,country,timezone,latitude,longitude
            FROM provider_airports WHERE provider=%s ORDER BY code
        """, (provider,))
        provider_airports = cur.fetchall()

    nearby = []
    for row in provider_airports:
        destination = {
            "code": row[0].strip(), "name": row[1], "city": row[2], "country": row[3],
            "timezone": row[4], "latitude": row[5], "longitude": row[6],
        }
        distance = min(_distance_km(origin, destination) for origin in origin_info)
        if distance <= radius_km and destination["code"] not in origins:
            nearby.append({**destination, "distance_km": round(distance, 1)})
    nearby.sort(key=lambda airport: (airport["distance_km"], airport["code"]))
    return nearby


def _create_job_from_settings(settings: dict) -> uuid.UUID:
    job_id = uuid.uuid4()
    seed_airports = (
        settings.get("target_airports", [])
        if settings.get("search_direction") == "from_target"
        else settings["start_airports"]
    )
    with connect() as con, con.cursor() as cur:
        cur.execute("INSERT INTO search_jobs(id,status,settings) VALUES (%s,'queued',%s::jsonb)", (job_id, json.dumps(settings)))
        for airport in seed_airports:
            cur.execute("""
                INSERT INTO search_job_nodes(search_job_id,origin,window_start,window_end,depth)
                VALUES (%s,%s,%s,%s,0) ON CONFLICT DO NOTHING
            """, (job_id, airport, settings["start_date"], settings["end_date"]))
    start_job_thread(job_id)
    return job_id


@app.post("/api/jobs", status_code=202)
def create_job(payload: JobCreate):
    settings = json.loads(payload.model_dump_json())
    job_id = _create_job_from_settings(settings)
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    with connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT id,created_at,updated_at,status,settings,current_depth,queries_done,offers_found,error,
              cache_hits,external_queries,
              active_seconds+CASE WHEN status='running' AND run_started_at IS NOT NULL
                THEN EXTRACT(epoch FROM (now()-run_started_at)) ELSE 0 END AS elapsed_seconds,
              data_fetched_from,data_fetched_to
            FROM search_jobs ORDER BY created_at DESC LIMIT 50
        """)
        return [_job_row(row) for row in cur.fetchall()]


@app.post("/api/jobs/{job_id}/repeat", status_code=202)
def repeat_job(job_id: uuid.UUID):
    with connect() as con, con.cursor() as cur:
        cur.execute("SELECT settings FROM search_jobs WHERE id=%s", (job_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Suchauftrag nicht gefunden")
    settings = row[0]
    settings.setdefault("target_airports", [])
    settings.setdefault("search_direction", "any")
    settings.setdefault("min_target_stay_hours", 24)
    settings.setdefault("min_trip_days", 1)
    new_job_id = _create_job_from_settings(settings)
    return {"id": new_job_id, "status": "queued", "repeated_from": job_id}


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: uuid.UUID):
    with connect() as con, con.cursor() as cur:
        cur.execute("""
            UPDATE search_jobs SET status='paused',
              active_seconds=active_seconds+CASE WHEN run_started_at IS NULL THEN 0
                ELSE EXTRACT(epoch FROM (now()-run_started_at)) END,
              run_started_at=NULL,
              error='Pausiert – kann später fortgesetzt werden', updated_at=now()
            WHERE id=%s AND status IN ('queued','running') RETURNING id
        """, (job_id,))
        if not cur.fetchone():
            raise HTTPException(409, "Suchauftrag ist nicht aktiv")
    return {"status": "paused"}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: uuid.UUID):
    with connect() as con, con.cursor() as cur:
        cur.execute("""
            UPDATE search_job_nodes SET status='queued', updated_at=now()
            WHERE search_job_id=%s AND status='running'
        """, (job_id,))
        cur.execute("""
            UPDATE search_jobs SET status='queued', error=NULL, updated_at=now()
            WHERE id=%s AND status='paused' RETURNING id
        """, (job_id,))
        if not cur.fetchone():
            raise HTTPException(409, "Nur pausierte Suchaufträge können fortgesetzt werden")
    start_job_thread(job_id)
    return {"status": "queued"}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID):
    with connect() as con, con.cursor() as cur:
        cur.execute("""
            UPDATE search_jobs SET status='cancelled',
              active_seconds=active_seconds+CASE WHEN run_started_at IS NULL THEN 0
                ELSE EXTRACT(epoch FROM (now()-run_started_at)) END,
              run_started_at=NULL, updated_at=now()
            WHERE id=%s AND status IN ('queued','running','paused') RETURNING id
        """, (job_id,))
        if not cur.fetchone():
            raise HTTPException(409, "Suchauftrag ist nicht aktiv")
    return {"status": "cancelled"}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: uuid.UUID):
    with ACTIVE_WORKERS_LOCK:
        if job_id in ACTIVE_WORKERS:
            raise HTTPException(409, "Die aktuelle Teilabfrage läuft noch; bitte kurz warten")
    with connect() as con, con.cursor() as cur:
        cur.execute("SELECT status FROM search_jobs WHERE id=%s", (job_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Suchauftrag nicht gefunden")
        if row[0] in ("queued", "running"):
            raise HTTPException(409, "Laufenden Suchauftrag zuerst abbrechen")
        cur.execute("DELETE FROM search_jobs WHERE id=%s", (job_id,))
    return {"status": "deleted", "id": job_id}


@app.get("/api/jobs/{job_id}/offers")
def list_offers(job_id: uuid.UUID, limit: int = 200):
    with connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT id,provider,airline,flight_number,origin,destination,departure_time,arrival_time,price,currency,booking_url
            FROM flight_offers WHERE search_job_id=%s ORDER BY departure_time,price LIMIT %s
        """, (job_id, min(limit, 1000)))
        return [{
            "id": row[0], "provider": row[1], "airline": row[2], "flight_number": row[3],
            "origin": row[4].strip(), "destination": row[5].strip(), "departure_time": row[6],
            "arrival_time": row[7], "price": float(row[8]), "currency": row[9].strip(), "booking_url": row[10],
            "origin_airport": airport_info(row[4]), "destination_airport": airport_info(row[5]),
        } for row in cur.fetchall()]


def _stored_offers(job_id: uuid.UUID) -> list[dict]:
    with connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT id,airline,flight_number,origin,destination,departure_time,arrival_time,
              price,currency,booking_url
            FROM flight_offers WHERE search_job_id=%s ORDER BY departure_time,price
        """, (job_id,))
        rows = cur.fetchall()
    offers = []
    for row in rows:
        origin = row[3].strip()
        origin_airport = airport_info(origin)
        destination = row[4].strip()
        offers.append({
            "id": row[0], "airline": row[1], "flight_number": row[2],
            "origin": origin, "destination": destination,
            "departure_time": row[5], "arrival_time": row[6], "price": float(row[7]),
            "currency": row[8].strip(), "booking_url": row[9],
            "origin_airport": origin_airport,
            "destination_airport": airport_info(destination),
        })
    return offers


def _local_date(value: datetime, airport: dict) -> date:
    try:
        return value.astimezone(ZoneInfo(airport.get("timezone") or "UTC")).date()
    except (KeyError, ValueError):
        return value.date()


def _outbound_routes(
    offers: list[dict], home_airports: set[str], departure_from: date, departure_to: date,
    min_segments: int, max_segments: int, min_connection_hours: int,
    max_duration_hours: float, max_price: float,
) -> list[dict]:
    by_origin: dict[str, list[dict]] = {}
    for offer in offers:
        by_origin.setdefault(offer["origin"], []).append(offer)
    for origin_offers in by_origin.values():
        origin_offers.sort(key=lambda offer: (offer["departure_time"], offer["price"]))

    candidates: list[dict] = []
    max_duration = timedelta(hours=max_duration_hours)

    def walk(path: list[dict], airport: str, earliest: datetime | None, visited: set[str], total: float):
        segments = len(path)
        if segments >= min_segments and airport not in home_airports:
            candidates.append({
                "destination": airport,
                "segments": path.copy(),
                "total_price": round(total, 2),
                "arrival_time": path[-1]["arrival_time"],
                "destination_airport": path[-1]["destination_airport"],
            })
        if segments >= max_segments or total >= max_price or len(candidates) > 5000:
            return
        for stored_offer in by_origin.get(airport, []):
            offer = stored_offer.copy()
            if not path:
                local_departure = _local_date(offer["departure_time"], offer["origin_airport"])
                if not departure_from <= local_departure <= departure_to:
                    continue
            elif earliest and offer["departure_time"] < earliest:
                continue
            if offer["destination"] in visited or offer["destination"] in home_airports:
                continue
            if path and offer["arrival_time"] - path[0]["departure_time"] > max_duration:
                continue
            new_total = total + offer["price"]
            if new_total > max_price:
                continue
            walk(
                path + [offer], offer["destination"],
                offer["arrival_time"] + timedelta(hours=min_connection_hours),
                visited | {offer["destination"]}, new_total,
            )

    for start in home_airports:
        walk([], start, None, {start}, 0.0)
    candidates.sort(key=lambda route: (route["total_price"], route["arrival_time"]))
    return candidates


def _return_routes(
    offers: list[dict], destination: str, home_airports: set[str],
    departure_from: date, departure_to: date, min_segments: int, max_segments: int,
    min_connection_hours: int, max_duration_hours: float, max_price: float,
) -> list[dict]:
    by_origin: dict[str, list[dict]] = {}
    for offer in offers:
        by_origin.setdefault(offer["origin"], []).append(offer)
    for origin_offers in by_origin.values():
        origin_offers.sort(key=lambda offer: (offer["departure_time"], offer["price"]))

    candidates: list[dict] = []
    max_duration = timedelta(hours=max_duration_hours)

    def walk(path: list[dict], airport: str, earliest: datetime | None, visited: set[str], total: float):
        segments = len(path)
        if segments and airport in home_airports:
            if segments >= min_segments:
                candidates.append({"segments": path.copy(), "total_price": round(total, 2)})
            return
        if segments >= max_segments or total >= max_price or len(candidates) > 1000:
            return
        for stored_offer in by_origin.get(airport, []):
            offer = stored_offer.copy()
            if not path:
                local_departure = _local_date(offer["departure_time"], offer["origin_airport"])
                if not departure_from <= local_departure <= departure_to:
                    continue
            elif earliest and offer["departure_time"] < earliest:
                continue
            if path and offer["arrival_time"] - path[0]["departure_time"] > max_duration:
                continue
            if offer["destination"] in visited and offer["destination"] not in home_airports:
                continue
            new_total = total + offer["price"]
            if new_total > max_price:
                continue
            walk(
                path + [offer], offer["destination"],
                offer["arrival_time"] + timedelta(hours=min_connection_hours),
                visited | {offer["destination"]}, new_total,
            )

    walk([], destination, None, {destination}, 0.0)
    best_by_departure_date: dict[date, dict] = {}
    for route in candidates:
        first_segment = route["segments"][0]
        departure_date = _local_date(first_segment["departure_time"], first_segment["origin_airport"])
        route["departure_date"] = departure_date
        current = best_by_departure_date.get(departure_date)
        route_score = (route["total_price"], route["segments"][-1]["arrival_time"])
        current_score = (
            (current["total_price"], current["segments"][-1]["arrival_time"])
            if current else None
        )
        if current_score is None or route_score < current_score:
            best_by_departure_date[departure_date] = route
    routes = list(best_by_departure_date.values())
    routes.sort(key=lambda route: (route["total_price"], route["departure_date"]))
    return routes


@app.post("/api/meetups")
def find_common_destinations(query: MeetupQuery):
    with connect() as con, con.cursor() as cur:
        cur.execute("SELECT id,settings,status FROM search_jobs WHERE id IN (%s,%s)", (query.job_a_id, query.job_b_id))
        jobs_by_id = {row[0]: {"settings": row[1], "status": row[2]} for row in cur.fetchall()}
    if query.job_a_id not in jobs_by_id or query.job_b_id not in jobs_by_id:
        raise HTTPException(404, "Mindestens ein Datensatz wurde nicht gefunden")
    if any(jobs_by_id[job_id]["status"] != "completed" for job_id in (query.job_a_id, query.job_b_id)):
        raise HTTPException(409, "Beide Datensätze müssen abgeschlossen sein")

    settings_a = jobs_by_id[query.job_a_id]["settings"]
    settings_b = jobs_by_id[query.job_b_id]["settings"]
    all_offers_a = _stored_offers(query.job_a_id)
    all_offers_b = _stored_offers(query.job_b_id)
    home_a = set(settings_a["start_airports"])
    home_b = set(settings_b["start_airports"])
    outbound_routes_a = _outbound_routes(
        all_offers_a, home_a, query.departure_from, query.departure_to,
        query.min_outbound_segments, query.max_outbound_segments,
        int(settings_a.get("min_connection_hours", 0)), query.max_route_duration_hours,
        query.max_total_price,
    )
    outbound_routes_b = _outbound_routes(
        all_offers_b, home_b, query.departure_from, query.departure_to,
        query.min_outbound_segments, query.max_outbound_segments,
        int(settings_b.get("min_connection_hours", 0)), query.max_route_duration_hours,
        query.max_total_price,
    )
    b_by_destination: dict[str, list[dict]] = {}
    for route in outbound_routes_b:
        b_by_destination.setdefault(route["destination"], []).append(route)
    b_arrivals_by_destination: dict[str, list[datetime]] = {}
    for destination, routes in b_by_destination.items():
        routes.sort(key=lambda route: route["arrival_time"])
        b_arrivals_by_destination[destination] = [route["arrival_time"] for route in routes]

    best_by_destination: dict[str, dict] = {}
    return_cache: dict[tuple, list[dict]] = {}
    for outbound_a in outbound_routes_a:
        destination_routes_b = b_by_destination.get(outbound_a["destination"], [])
        destination_arrivals_b = b_arrivals_by_destination.get(outbound_a["destination"], [])
        arrival_window = timedelta(hours=query.max_arrival_difference_hours)
        first_match = bisect_left(destination_arrivals_b, outbound_a["arrival_time"] - arrival_window)
        last_match = bisect_right(destination_arrivals_b, outbound_a["arrival_time"] + arrival_window)
        for outbound_b in destination_routes_b[first_match:last_match]:
            arrival_difference = abs(
                (outbound_a["arrival_time"] - outbound_b["arrival_time"]).total_seconds()
            ) / 3600
            if arrival_difference > query.max_arrival_difference_hours:
                continue
            outbound_price = outbound_a["total_price"] + outbound_b["total_price"]
            if outbound_price > query.max_total_price:
                continue
            destination = outbound_a["destination"]
            destination_airport = outbound_a["destination_airport"]
            shared_arrival = max(outbound_a["arrival_time"], outbound_b["arrival_time"])
            shared_arrival_date = _local_date(shared_arrival, destination_airport)
            return_from = shared_arrival_date + timedelta(days=query.min_stay_days)
            return_to = shared_arrival_date + timedelta(days=query.max_stay_days)

            def returns_for(label: str, all_offers: list[dict], homes: set[str], settings: dict):
                key = (
                    label, destination, return_from, return_to,
                    query.min_return_segments, query.max_return_segments,
                    query.max_route_duration_hours,
                )
                if key not in return_cache:
                    return_cache[key] = _return_routes(
                        all_offers, destination, homes, return_from, return_to,
                        query.min_return_segments, query.max_return_segments,
                        int(settings.get("min_connection_hours", 0)),
                        query.max_route_duration_hours,
                        query.max_total_price,
                    )
                return return_cache[key]

            returns_a = returns_for("a", all_offers_a, home_a, settings_a)
            returns_b = returns_for("b", all_offers_b, home_b, settings_b)
            paired_returns = []
            for return_a in returns_a:
                for return_b in returns_b:
                    return_difference_days = abs(
                        (return_a["departure_date"] - return_b["departure_date"]).days
                    )
                    if return_difference_days > query.max_return_difference_days:
                        continue
                    combined_price = outbound_price + return_a["total_price"] + return_b["total_price"]
                    if combined_price <= query.max_total_price:
                        paired_returns.append((
                            combined_price, return_difference_days,
                            max(return_a["segments"][-1]["arrival_time"], return_b["segments"][-1]["arrival_time"]),
                            return_a, return_b,
                        ))
            if not paired_returns:
                continue
            combined_price, return_difference_days, _, return_a, return_b = min(
                paired_returns, key=lambda pair: (pair[0], pair[1], pair[2])
            )

            traveler_a = {
                "outbound": outbound_a["segments"][0],
                "outbound_segments": outbound_a["segments"],
                "return_segments": return_a["segments"],
                "total_price": round(outbound_a["total_price"] + return_a["total_price"], 2),
                "stay_days": (return_a["departure_date"] - shared_arrival_date).days,
            }
            traveler_b = {
                "outbound": outbound_b["segments"][0],
                "outbound_segments": outbound_b["segments"],
                "return_segments": return_b["segments"],
                "total_price": round(outbound_b["total_price"] + return_b["total_price"], 2),
                "stay_days": (return_b["departure_date"] - shared_arrival_date).days,
            }
            candidate = {
                "destination": destination,
                "destination_airport": destination_airport,
                "combined_price": round(combined_price, 2),
                "arrival_difference_hours": round(arrival_difference, 1),
                "return_difference_days": return_difference_days,
                "shared_arrival_date": shared_arrival_date,
                "return_from": return_from,
                "return_to": return_to,
                "traveler_a": traveler_a,
                "traveler_b": traveler_b,
            }
            current = best_by_destination.get(destination)
            candidate_score = (
                candidate["combined_price"], candidate["return_difference_days"],
                candidate["arrival_difference_hours"],
            )
            current_score = (
                (current["combined_price"], current["return_difference_days"], current["arrival_difference_hours"])
                if current else None
            )
            if current_score is None or candidate_score < current_score:
                best_by_destination[destination] = candidate

    results = list(best_by_destination.values())
    results.sort(key=lambda result: (result["combined_price"], result["arrival_difference_hours"], result["destination"]))
    return results[:query.limit]


@app.post("/api/routes")
def find_routes(query: RouteQuery):
    if query.min_segments > query.max_segments:
        raise HTTPException(422, "min_segments darf max_segments nicht überschreiten")
    job_ids = query.job_ids
    with connect() as con, con.cursor() as cur:
        cur.execute("SELECT id,settings,status FROM search_jobs WHERE id = ANY(%s)", (job_ids,))
        jobs_by_id = {row[0]: {"settings": row[1], "status": row[2]} for row in cur.fetchall()}
        if len(jobs_by_id) != len(job_ids):
            raise HTTPException(404, "Mindestens ein Suchauftrag wurde nicht gefunden")
        if any(jobs_by_id[job_id]["status"] != "completed" for job_id in job_ids):
            raise HTTPException(409, "Alle ausgewählten Datensätze müssen abgeschlossen sein")
        settings_list = [jobs_by_id[job_id]["settings"] for job_id in job_ids]
        providers = {settings["provider"] for settings in settings_list}
        if len(providers) != 1:
            raise HTTPException(422, "Kombinierte Datensätze müssen dieselbe Datenquelle verwenden")
        collected_transfer_km = min(
            float(settings.get("max_airport_transfer_km", 0)) for settings in settings_list
        )
        if query.max_airport_transfer_km > collected_transfer_km:
            raise HTTPException(
                422,
                f"Die ausgewählten Datensätze wurden nur mit Flughafenwechseln bis {collected_transfer_km:g} km gesammelt",
            )
        starts: set[str] = set()
        first_departure_windows: list[tuple[date, date]] = []
        round_trip_targets: set[str] = set()
        for settings in settings_list:
            search_direction = settings.get("search_direction", "any")
            starts.update(
                settings.get("target_airports", [])
                if search_direction == "from_target"
                else settings["start_airports"]
            )
            first_departure_windows.append((
                date.fromisoformat(settings["start_date"]),
                date.fromisoformat(settings["end_date"]),
            ))
            if search_direction == "round_trip":
                round_trip_targets.update(settings.get("target_airports", []))
        min_trip_days = max(int(settings.get("min_trip_days", 0)) for settings in settings_list)
        max_trip_days = min(int(settings["max_trip_days"]) for settings in settings_list)
        if min_trip_days > max_trip_days:
            raise HTTPException(422, "Die Reisedauer-Einstellungen der Datensätze überschneiden sich nicht")
        min_trip_duration = timedelta(days=min_trip_days)
        max_trip_duration = timedelta(days=max_trip_days)
        cur.execute("""
            SELECT DISTINCT ON (provider,airline,flight_number,origin,destination,departure_time,arrival_time)
              id,airline,flight_number,origin,destination,departure_time,arrival_time,
              price,currency,booking_url
            FROM flight_offers WHERE search_job_id = ANY(%s)
            ORDER BY provider,airline,flight_number,origin,destination,departure_time,arrival_time,fetched_at DESC
        """, (job_ids,))
        rows = cur.fetchall()

    by_origin: dict[str, list[dict]] = {}
    for row in rows:
        item = {"id": row[0], "airline": row[1], "flight_number": row[2], "origin": row[3].strip(),
                "destination": row[4].strip(), "departure_time": row[5], "arrival_time": row[6],
                "price": float(row[7]), "currency": row[8].strip(), "booking_url": row[9],
                "origin_airport": airport_info(row[3]), "destination_airport": airport_info(row[4])}
        by_origin.setdefault(item["origin"], []).append(item)
    for origin_offers in by_origin.values():
        origin_offers.sort(key=lambda offer: (offer["departure_time"], offer["price"]))

    ends = {code.strip().upper() for code in query.end_airports}
    required_visits = {code.strip().upper() for code in query.required_visit_airports if code.strip()}
    if not required_visits:
        required_visits = round_trip_targets
    target_stay_hours = query.min_target_stay_hours
    if required_visits and not target_stay_hours:
        target_stay_hours = max(int(settings.get("min_target_stay_hours", 0)) for settings in settings_list)
    transfer_neighbors = load_transfer_neighbors(next(iter(providers)), query.max_airport_transfer_km)
    results: list[dict] = []

    def walk(
        path: list[dict], airport: str, earliest: datetime | None, total: float,
        visited: set[str], visited_required: set[str], first_target_arrival: datetime | None,
    ):
        segments = len(path)
        target_requirement_met = not required_visits or bool(required_visits & visited_required)
        trip_duration = path[-1]["arrival_time"] - path[0]["departure_time"] if path else timedelta(0)
        if segments >= query.min_segments and airport in ends and target_requirement_met and trip_duration >= min_trip_duration:
            trip_seconds = (path[-1]["arrival_time"] - path[0]["departure_time"]).total_seconds()
            flight_seconds = sum(
                (segment["arrival_time"] - segment["departure_time"]).total_seconds()
                for segment in path
            )
            results.append({
                "total_price": round(total, 2),
                "segments": path.copy(),
                "duration_hours": round(trip_seconds / 3600, 1),
                "trip_duration_minutes": round(trip_seconds / 60),
                "flight_duration_minutes": round(flight_seconds / 60),
            })
        if segments >= query.max_segments or total >= query.max_total_price or len(results) > 5000:
            return
        candidate_origins = {airport: 0.0}
        if path:
            candidate_origins.update(transfer_neighbors.get(airport, {}))
        for departure_airport, transfer_distance in candidate_origins.items():
            if departure_airport != airport and departure_airport in visited:
                continue
            for stored_offer in by_origin.get(departure_airport, []):
                offer = stored_offer.copy()
                departure_date = offer["departure_time"].date()
                if not path and not any(
                    departure_from <= departure_date <= departure_to
                    for departure_from, departure_to in first_departure_windows
                ):
                    continue
                if earliest and offer["departure_time"] < earliest:
                    continue
                if offer["destination"] in visited and offer["destination"] not in ends:
                    continue
                new_total = total + offer["price"]
                if new_total > query.max_total_price:
                    continue
                if path and offer["arrival_time"] - path[0]["departure_time"] > max_trip_duration:
                    continue
                if departure_airport != airport:
                    offer["ground_transfer_before"] = {
                        "origin": airport,
                        "destination": departure_airport,
                        "distance_km": transfer_distance,
                        "origin_airport": airport_info(airport),
                        "destination_airport": airport_info(departure_airport),
                    }
                new_required = visited_required | ({offer["destination"]} & required_visits)
                target_arrival = first_target_arrival
                next_earliest = offer["arrival_time"] + timedelta(hours=query.min_connection_hours)
                if target_arrival is None and offer["destination"] in required_visits:
                    target_arrival = offer["arrival_time"]
                    next_earliest = max(
                        next_earliest,
                        offer["arrival_time"] + timedelta(hours=target_stay_hours),
                    )
                walk(
                    path + [offer], offer["destination"],
                    next_earliest, new_total,
                    visited | {departure_airport, offer["destination"]},
                    new_required, target_arrival,
                )

    for start in starts:
        walk([], start, None, 0.0, {start}, {start} & required_visits, None)
    results.sort(key=lambda route: (route["total_price"], route["trip_duration_minutes"]))
    return results[: query.limit]
