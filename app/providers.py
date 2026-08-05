from __future__ import annotations

import random
import os
import threading
import time as time_module
from email.utils import parsedate_to_datetime
from functools import lru_cache
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import airportsdata
import httpx


RYANAIR_FARES = "https://www.ryanair.com/api/farfnd/v4/oneWayFares"
RYANAIR_TIMETABLE = "https://www.ryanair.com/api/timtbl/3/schedules"
RYANAIR_AIRPORTS = "https://www.ryanair.com/api/views/locate/5/airports/en/active"
AIRPORTS = airportsdata.load("IATA")
RYANAIR_MIN_DELAY = float(os.getenv("RYANAIR_MIN_DELAY", "0"))
RYANAIR_MAX_DELAY = float(os.getenv("RYANAIR_MAX_DELAY", "0"))
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def _throttle_ryanair() -> None:
    """Apply an optional operator-configured delay; disabled by default."""
    global _LAST_REQUEST_AT
    if max(RYANAIR_MIN_DELAY, RYANAIR_MAX_DELAY) <= 0:
        return
    with _REQUEST_LOCK:
        interval = random.uniform(
            min(RYANAIR_MIN_DELAY, RYANAIR_MAX_DELAY),
            max(RYANAIR_MIN_DELAY, RYANAIR_MAX_DELAY),
        )
        remaining = interval - (time_module.monotonic() - _LAST_REQUEST_AT)
        if remaining > 0:
            time_module.sleep(remaining)
        _LAST_REQUEST_AT = time_module.monotonic()


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                requested_at = parsedate_to_datetime(retry_after)
                if requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=timezone.utc)
                return max(0.0, (requested_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return min(1.5 * (2**attempt) * random.uniform(0.85, 1.2), 30.0)


def _get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    attempts: int = 5,
) -> httpx.Response:
    """Retry transient airline/API failures with bounded exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            _throttle_ryanair()
            response = client.get(url, params=params)
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            retryable = isinstance(exc, httpx.TransportError) or (
                isinstance(exc, httpx.HTTPStatusError)
                and (exc.response.status_code == 429 or exc.response.status_code >= 500)
            )
            if not retryable or attempt == attempts - 1:
                raise
            response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
            time_module.sleep(_retry_delay(response, attempt))
    raise RuntimeError("Ryanair-Anfrage fehlgeschlagen") from last_error


def _local_datetime(value: str, airport: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return parsed
    timezone_name = AIRPORTS.get(airport, {}).get("tz") or "UTC"
    return parsed.replace(tzinfo=ZoneInfo(timezone_name))


def _parse_ryanair_fare(raw: dict[str, Any]) -> dict[str, Any] | None:
    outbound = raw.get("outbound") or {}
    departure = outbound.get("departureDate")
    arrival = outbound.get("arrivalDate")
    price = raw.get("summary", {}).get("price", {})
    origin = outbound.get("departureAirport", {}).get("iataCode")
    destination = outbound.get("arrivalAirport", {}).get("iataCode")
    if not all((departure, arrival, origin, destination, price.get("value") is not None)):
        return None
    return {
        "provider": "ryanair",
        "airline": "Ryanair",
        "flight_number": outbound.get("flightNumber"),
        "origin": origin,
        "destination": destination,
        "departure_time": _local_datetime(departure, origin),
        "arrival_time": _local_datetime(arrival, destination),
        "price": float(price["value"]),
        "currency": price.get("currencyCode", "EUR"),
        "booking_url": "https://www.ryanair.com/",
    }


@lru_cache(maxsize=4096)
def _ryanair_schedule(origin: str, destination: str, year: int, month: int) -> dict[tuple[int, str], tuple[str, str]]:
    url = f"{RYANAIR_TIMETABLE}/{origin}/{destination}/years/{year}/months/{month}"
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "FareGraph/0.3"}) as client:
        response = _get_with_retry(client, url)
    schedule: dict[tuple[int, str], tuple[str, str]] = {}
    for day in response.json().get("days", []):
        for flight in day.get("flights", []):
            number = f"{flight.get('carrierCode', '')}{flight.get('number', '')}".replace(" ", "")
            schedule[(int(day["day"]), number)] = (flight["departureTime"], flight["arrivalTime"])
    return schedule


def _matches_ryanair_timetable(offer: dict[str, Any]) -> bool:
    departure = offer["departure_time"]
    schedule = _ryanair_schedule(
        offer["origin"], offer["destination"], departure.year, departure.month
    )
    expected = schedule.get((departure.day, (offer["flight_number"] or "").replace(" ", "")))
    if not expected:
        return False
    departure_local = departure.astimezone(ZoneInfo(AIRPORTS.get(offer["origin"], {}).get("tz") or "UTC"))
    arrival_local = offer["arrival_time"].astimezone(ZoneInfo(AIRPORTS.get(offer["destination"], {}).get("tz") or "UTC"))
    return departure_local.strftime("%H:%M") == expected[0] and arrival_local.strftime("%H:%M") == expected[1]


def fetch_ryanair(origin: str, date_from: date, date_to: date, max_price: float) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "FareGraph/0.1"}) as client:
        chunk_from = date_from
        while chunk_from <= date_to:
            chunk_to = min(chunk_from + timedelta(days=30), date_to)
            params = {
                "departureAirportIataCode": origin.upper(),
                "outboundDepartureDateFrom": chunk_from.isoformat(),
                "outboundDepartureDateTo": chunk_to.isoformat(),
                "currency": "EUR",
                "language": "de",
                "market": "de-de",
                "offset": 0,
            }
            for _ in range(10):
                response = _get_with_retry(client, RYANAIR_FARES, params=params)
                payload = response.json()
                rows = payload.get("fares", [])
                for row in rows:
                    parsed = _parse_ryanair_fare(row)
                    if parsed and parsed["price"] <= max_price and _matches_ryanair_timetable(parsed):
                        offers.append(parsed)
                if not payload.get("nextPage") or not rows:
                    break
                params["offset"] = int(params["offset"]) + len(rows)
            chunk_from = chunk_to + timedelta(days=1)
    return offers


def fetch_ryanair_airports() -> list[dict[str, Any]]:
    """Return Ryanair's active airport catalogue with canonical coordinates."""
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "FareGraph/0.4"}) as client:
        response = _get_with_retry(client, RYANAIR_AIRPORTS)
    airports: list[dict[str, Any]] = []
    for raw in response.json():
        coordinates = raw.get("coordinates") or {}
        code = (raw.get("code") or "").strip().upper()
        latitude = coordinates.get("latitude")
        longitude = coordinates.get("longitude")
        if len(code) != 3 or latitude is None or longitude is None:
            continue
        airports.append({
            "code": code,
            "name": raw.get("name") or code,
            "city": (raw.get("city") or {}).get("name") or "",
            "country": (raw.get("country") or {}).get("name") or "",
            "timezone": raw.get("timeZone") or "",
            "latitude": float(latitude),
            "longitude": float(longitude),
        })
    return airports


DEMO_CONNECTIONS = {
    "CGN": ["IBZ", "PMI", "DUB", "BGY"],
    "DUS": ["DUB", "PMI", "BGY"],
    "NRN": ["BGY", "DUB", "SOF"],
    "IBZ": ["DUB", "MAN", "BGY"],
    "PMI": ["DUB", "BCN", "CGN"],
    "DUB": ["NRN", "CGN", "SOF"],
    "EIN": ["DUB", "BGY", "SOF"],
    "BGY": ["SOF", "CGN", "NRN"],
    "SOF": ["CGN", "DUS", "NRN"],
    "MAN": ["CGN", "DUS"],
    "BCN": ["CGN", "DUS"],
}


def fetch_provider_airports(provider: str) -> list[dict[str, Any]]:
    if provider == "ryanair":
        return fetch_ryanair_airports()
    if provider == "demo":
        codes = sorted(set(DEMO_CONNECTIONS) | {code for destinations in DEMO_CONNECTIONS.values() for code in destinations})
        result = []
        for code in codes:
            raw = AIRPORTS.get(code, {})
            if raw.get("lat") is None or raw.get("lon") is None:
                continue
            result.append({
                "code": code,
                "name": raw.get("name") or code,
                "city": raw.get("city") or "",
                "country": raw.get("country") or "",
                "timezone": raw.get("tz") or "",
                "latitude": float(raw["lat"]),
                "longitude": float(raw["lon"]),
            })
        return result
    raise ValueError(f"Unbekannter Provider: {provider}")


def fetch_demo(origin: str, date_from: date, date_to: date, max_price: float) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    destinations = DEMO_CONNECTIONS.get(origin.upper(), [])
    current = date_from
    while current <= date_to:
        for destination in destinations:
            seed = f"{origin}-{destination}-{current.isoformat()}"
            rng = random.Random(seed)
            if rng.random() < 0.72:
                hour = rng.choice([6, 8, 10, 13, 16, 19, 21])
                departure = datetime.combine(current, time(hour, rng.choice([0, 15, 30, 45])), timezone.utc)
                arrival = departure + timedelta(minutes=rng.randint(85, 190))
                price = round(rng.uniform(12, 58), 2)
                if price <= max_price:
                    offers.append({
                        "provider": "demo",
                        "airline": "Demo Air",
                        "flight_number": f"DE{rng.randint(100, 999)}",
                        "origin": origin.upper(),
                        "destination": destination,
                        "departure_time": departure,
                        "arrival_time": arrival,
                        "price": price,
                        "currency": "EUR",
                        "booking_url": None,
                    })
        current += timedelta(days=1)
    return offers


def fetch_offers(provider: str, origin: str, date_from: date, date_to: date, max_price: float):
    if provider == "demo":
        return fetch_demo(origin, date_from, date_to, max_price)
    if provider == "ryanair":
        return fetch_ryanair(origin, date_from, date_to, max_price)
    raise ValueError(f"Unbekannter Provider: {provider}")
