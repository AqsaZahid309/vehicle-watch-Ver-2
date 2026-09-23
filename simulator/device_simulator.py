"""
VehicleWatch Fleet Simulator

Drives five trucks around London with realistic physics (smooth acceleration,
stops at customer sites, fuel burn proportional to distance) and five distinct
fault personalities. Each vehicle authenticates with its own device API key,
and one of them periodically loses coverage, buffers readings and uploads them
as a batch — exercising the offline backfill path.

On first run the simulator also sets up the organization: drivers, geofences
(depot, customers, a restricted zone with a speed limit) and a preventive
maintenance schedule.

Usage:
    python simulator/device_simulator.py
    python simulator/device_simulator.py --host http://localhost:8000 --devices 3 --interval 1

Vehicles:
    1. Truck-Alpha  — healthy baseline (control), careful driver
    2. Truck-Beta   — developing coolant leak: temperature climbs, coolant level falls, DTC P0217
    3. Truck-Gamma  — battery/alternator degradation: voltage falls steadily, DTC P0562, erratic RPM
    4. Truck-Delta  — transmission slip at speed (DTC P0730), aggressive driver, fuel-theft stops,
                      route crosses the restricted zone
    5. Truck-Echo   — wheel bearing: vibration rises with speed (DTC C0040); drives through
                      dead zones and backfills buffered readings
"""

import argparse
import asyncio
import logging
import math
import random
import sys
from datetime import datetime, timezone
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | SIM | %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "/api/v1"
DEPOT = (51.5150, -0.2000)
CUSTOMERS = {
    "Canary Wharf DC": (51.5054, -0.0235),
    "Heathrow Cargo": (51.4730, -0.4540),
    "Wembley Retail": (51.5560, -0.2795),
    "Croydon Hub": (51.3762, -0.0982),
    "Stratford Yard": (51.5430, -0.0030),
}
RESTRICTED = ("City Low-Speed Zone", (51.5155, -0.0920), 1800.0, 30.0)  # name, centre, radius m, limit km/h
KM_PER_DEG_LAT = 111.32


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat = (b[0] - a[0]) * KM_PER_DEG_LAT
    dlon = (b[1] - a[1]) * KM_PER_DEG_LAT * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlon)


class Vehicle:
    name = "Vehicle"
    device_type = "truck"
    driver = "Driver"
    cruise_kmh = (55.0, 80.0)
    accel_kmh_per_s = 1.5          # gentle
    harsh_probability = 0.0        # chance per reading of a harsh accel/brake
    speeding_probability = 0.0
    tank_liters = 400.0
    l_per_100km = 32.0

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.n = 0
        self.pos = (DEPOT[0] + random.uniform(-0.003, 0.003), DEPOT[1] + random.uniform(-0.003, 0.003))
        self.speed = 0.0
        self.target_speed = random.uniform(*self.cruise_kmh)
        self.fuel = random.uniform(55, 90)
        self.engine_temp = 20.0
        self.dwell = random.randint(5, 20)       # readings to wait before departing
        self.waypoints = self._route()
        self.dtc: list[str] = []

    def _route(self) -> list[tuple[float, float]]:
        stops = random.sample(list(CUSTOMERS.values()), 3)
        return stops + [DEPOT]

    # ── physics ──────────────────────────────────────────────────────────────
    def _drive(self) -> None:
        if self.dwell > 0:
            self.dwell -= 1
            self.speed = max(0.0, self.speed - 8 * self.interval)
            self.on_stop()
            return
        target = self.waypoints[0]
        dist = _km_between(self.pos, target)
        if dist < 0.15:
            self.waypoints.pop(0)
            if not self.waypoints:
                self.waypoints = self._route()
            self.dwell = random.randint(15, 45)
            if self.fuel < 25:
                self.fuel = random.uniform(88, 97)   # refuel at the stop
            return

        if random.random() < 0.02:
            self.target_speed = random.uniform(*self.cruise_kmh)
        wanted = self.target_speed
        if random.random() < self.speeding_probability:
            wanted = random.uniform(105, 118)
        if dist < 0.8:
            wanted = min(wanted, 25 + dist * 40)   # slow down approaching the stop

        step = self.accel_kmh_per_s * self.interval
        if random.random() < self.harsh_probability:
            step = random.uniform(8, 12) * self.interval   # ≥ 3 m/s² — a harsh event
            wanted = 0 if random.random() < 0.5 else wanted + 40
        if self.speed < wanted:
            self.speed = min(wanted, self.speed + step)
        else:
            self.speed = max(wanted, self.speed - step * 1.5)
        self.speed = max(0.0, self.speed)

        km = self.speed * self.interval / 3600.0
        if km > 0:
            frac = min(1.0, km / dist)
            self.pos = (self.pos[0] + (target[0] - self.pos[0]) * frac,
                        self.pos[1] + (target[1] - self.pos[1]) * frac)
            self.fuel = max(3.0, self.fuel - km * self.l_per_100km / 100.0 / self.tank_liters * 100.0)

    def on_stop(self) -> None:
        """Hook for events that happen while parked (e.g. fuel theft)."""

    # ── sensors ──────────────────────────────────────────────────────────────
    def base(self) -> dict[str, Any]:
        self.engine_temp += (88.0 - self.engine_temp) * 0.05 + random.uniform(-0.4, 0.4)
        rpm = 750 + self.speed * 18 + random.uniform(-80, 80) if self.speed > 1 else random.uniform(650, 800)
        vibration = 0.6 + self.speed * 0.012 + random.uniform(-0.15, 0.25)
        return {
            "engine_temp": round(self.engine_temp, 2),
            "rpm": round(rpm, 1),
            "battery_voltage": round(random.uniform(13.6, 14.2), 3),
            "vibration": round(max(0.1, vibration), 3),
            "oil_pressure": round(random.uniform(35, 48) if self.speed > 1 else random.uniform(22, 30), 1),
            "coolant_level": round(random.uniform(93, 97), 1),
            "tire_pressure": round(random.uniform(102, 108), 1),
            "ambient_temp": round(14 + 4 * math.sin(self.n / 900.0), 1),
        }

    def apply_fault(self, r: dict[str, Any]) -> None:
        """Override in subclasses."""

    def reading(self) -> dict[str, Any]:
        self.n += 1
        self._drive()
        r = self.base()
        self.dtc = []
        self.apply_fault(r)
        r.update({
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "gps_lat": round(self.pos[0], 6),
            "gps_lon": round(self.pos[1], 6),
            "speed": round(self.speed, 2),
            "fuel_level": round(_clamp(self.fuel, 0, 100), 2),
            "dtc_codes": self.dtc or None,
        })
        return r


class TruckAlpha(Vehicle):
    """Healthy control vehicle with a careful driver."""
    name, driver = "Truck-Alpha", "Priya Shah"


class TruckBeta(Vehicle):
    """Coolant leak: +4 °C every 20 readings up to +27 °C; coolant level drains; DTC P0217 when overheating."""
    name, driver = "Truck-Beta", "Tom Walsh"

    def apply_fault(self, r: dict[str, Any]) -> None:
        offset = min(27.0, 4.0 * (self.n // 20))
        r["engine_temp"] = round(r["engine_temp"] + offset + random.uniform(-2, 2), 2)
        r["coolant_level"] = round(max(20.0, 96 - self.n * 0.12 + random.uniform(-1, 1)), 1)
        r["vibration"] = round(r["vibration"] + offset * 0.04, 3)
        if r["engine_temp"] > 112:
            self.dtc.append("P0217")


class TruckGamma(Vehicle):
    """Failing alternator: voltage −0.05 V every 15 readings from 13.8 V; erratic RPM and DTC P0562 below 11.8 V."""
    name, driver = "Truck-Gamma", "Marek Nowak"

    def apply_fault(self, r: dict[str, Any]) -> None:
        base = max(9.0, 13.8 - 0.05 * (self.n // 15))
        r["battery_voltage"] = round(base + random.uniform(-0.08, 0.08), 3)
        if base < 11.8:
            r["rpm"] = round(_clamp(r["rpm"] + random.uniform(-500, 500), 300, 5000), 1)
            self.dtc.append("P0562")


class TruckDelta(Vehicle):
    """Transmission slip above 70 km/h (RPM 3500–4500, DTC P0730). Aggressive driver. Fuel thefts when parked."""
    name, driver = "Truck-Delta", "Jake Miller"
    cruise_kmh = (70.0, 95.0)
    accel_kmh_per_s = 3.0
    harsh_probability = 0.03
    speeding_probability = 0.05

    def _route(self) -> list[tuple[float, float]]:
        # Always cuts through the restricted zone on the way to a customer.
        stops = random.sample(list(CUSTOMERS.values()), 2)
        return [RESTRICTED[1], *stops, DEPOT]

    def on_stop(self) -> None:
        if random.random() < 0.01 and self.fuel > 30:
            self.fuel -= random.uniform(10, 16)      # siphoned
            logger.warning("[%s] fuel theft injected at %.5f, %.5f", self.name, *self.pos)

    def apply_fault(self, r: dict[str, Any]) -> None:
        if self.speed > 70:
            r["rpm"] = round(random.uniform(3500, 4500), 1)
            r["vibration"] = round(random.uniform(3.5, 6.0), 3)
            if random.random() < 0.3:
                self.dtc.append("P0730")


class TruckEcho(Vehicle):
    """Wheel bearing: vibration 5–8 g at 60–90 km/h and 7–10 g above (DTC C0040). Drives through dead zones."""
    name, driver = "Truck-Echo", "Aisha Bello"
    cruise_kmh = (60.0, 100.0)

    def apply_fault(self, r: dict[str, Any]) -> None:
        if self.speed >= 90:
            r["vibration"] = round(random.uniform(7.0, 10.0), 3)
            self.dtc.append("C0040")
        elif self.speed >= 60:
            r["vibration"] = round(random.uniform(5.0, 8.0), 3)

    def offline(self) -> bool:
        # ~60 s without coverage out of every ~10 minutes
        cycle = int(600 / self.interval)
        return (self.n % cycle) > cycle - int(60 / self.interval)


FLEET = [TruckAlpha, TruckBeta, TruckGamma, TruckDelta, TruckEcho]


# ── Setup via the API ─────────────────────────────────────────────────────────

class Setup:
    def __init__(self, client: httpx.AsyncClient, host: str, email: str, password: str, org: str) -> None:
        self.c, self.host, self.email, self.password, self.org = client, host, email, password, org
        self.h: dict[str, str] = {}

    async def auth(self) -> None:
        r = await self.c.post(f"{self.host}{API}/auth/login", json={"email": self.email, "password": self.password})
        if r.status_code == 401:
            reg = await self.c.post(f"{self.host}{API}/auth/register", json={
                "email": self.email, "password": self.password, "full_name": "Fleet Admin",
                "organization_name": self.org,
            })
            reg.raise_for_status()
            logger.info("Created organization '%s' with admin %s", self.org, self.email)
            r = await self.c.post(f"{self.host}{API}/auth/login", json={"email": self.email, "password": self.password})
        r.raise_for_status()
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def get(self, path: str) -> Any:
        r = await self.c.get(f"{self.host}{API}{path}", headers=self.h)
        r.raise_for_status()
        return r.json()

    async def post(self, path: str, body: dict | None = None) -> Any:
        r = await self.c.post(f"{self.host}{API}{path}", json=body, headers=self.h)
        r.raise_for_status()
        return r.json()

    async def patch(self, path: str, body: dict) -> Any:
        r = await self.c.patch(f"{self.host}{API}{path}", json=body, headers=self.h)
        r.raise_for_status()
        return r.json()

    async def org_fixtures(self) -> None:
        fences = {g["name"] for g in await self.get("/geofences")}
        wanted = [
            {"name": "Park Royal Depot", "kind": "DEPOT", "center_lat": DEPOT[0], "center_lon": DEPOT[1],
             "radius_m": 500, "alert_on_exit": False},
            *[{"name": n, "kind": "CUSTOMER", "center_lat": p[0], "center_lon": p[1], "radius_m": 350,
               "alert_on_enter": False} for n, p in CUSTOMERS.items()],
            {"name": RESTRICTED[0], "kind": "RESTRICTED", "center_lat": RESTRICTED[1][0],
             "center_lon": RESTRICTED[1][1], "radius_m": RESTRICTED[2], "speed_limit_kmh": RESTRICTED[3],
             "alert_on_enter": True},
        ]
        for g in wanted:
            if g["name"] not in fences:
                await self.post("/geofences", g)
                logger.info("Created geofence %s", g["name"])

    async def vehicle(self, v: Vehicle) -> tuple[str, str]:
        devices = {d["name"]: d for d in await self.get("/devices")}
        drivers = {d["name"]: d for d in await self.get("/drivers")}
        driver = drivers.get(v.driver) or await self.post("/drivers", {"name": v.driver})
        device = devices.get(v.name)
        if device is None:
            device = await self.post("/devices", {
                "name": v.name, "device_type": v.device_type, "make": "Volvo", "model": "FH",
                "year": random.choice([2019, 2020, 2021, 2022]), "fuel_tank_liters": v.tank_liters,
                "license_plate": f"LX{random.randint(10, 99)} {''.join(random.choices('ABCDEFGHJKLMNPRSTUVWXYZ', k=3))}",
                "odometer_km": round(random.uniform(40_000, 160_000)),
            })
            await self.post("/maintenance/schedules", {
                "device_id": device["id"], "name": "Engine oil & filter", "interval_km": 25_000,
                "interval_days": 180,
            })
            logger.info("Registered %s", v.name)
        if device.get("assigned_driver_id") != driver["id"]:
            await self.patch(f"/devices/{device['id']}", {"assigned_driver_id": driver["id"]})
        key = (await self.post(f"/devices/{device['id']}/api-key"))["api_key"]
        return device["id"], key


# ── Streaming ─────────────────────────────────────────────────────────────────

async def stream(client: httpx.AsyncClient, host: str, key: str, v: Vehicle) -> None:
    headers = {"X-Device-Key": key}
    buffer: list[dict[str, Any]] = []
    while True:
        reading = v.reading()
        offline = isinstance(v, TruckEcho) and v.offline()
        try:
            if offline:
                if not buffer:
                    logger.info("[%s] lost coverage — buffering", v.name)
                buffer.append(reading)
            elif buffer:
                buffer.append(reading)
                r = await client.post(f"{host}{API}/ingest/telemetry/batch", json={"readings": buffer},
                                      headers=headers, timeout=15)
                if r.status_code == 201:
                    logger.info("[%s] back online — uploaded %s", v.name, r.json())
                    buffer.clear()
            else:
                r = await client.post(f"{host}{API}/ingest/telemetry", json=reading, headers=headers, timeout=10)
                if r.status_code not in (201, 409):
                    logger.warning("[%s] HTTP %d: %s", v.name, r.status_code, r.text[:200])
        except httpx.HTTPError as exc:
            logger.error("[%s] %s — keeping reading in buffer", v.name, exc)
            if reading not in buffer:
                buffer.append(reading)
            buffer = buffer[-500:]
        await asyncio.sleep(v.interval)


async def main(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        setup = Setup(client, args.host, args.email, args.password, args.org)
        await setup.auth()
        await setup.org_fixtures()
        vehicles = [cls(args.interval) for cls in FLEET[: args.devices]]
        tasks = []
        for v in vehicles:
            _, key = await setup.vehicle(v)
            tasks.append(asyncio.create_task(stream(client, args.host, key, v)))
            logger.info("%-12s driver=%-12s %s", v.name, v.driver, (type(v).__doc__ or "").strip())
        logger.info("Streaming %d vehicles every %.1fs — open %s and sign in as %s", len(tasks), args.interval,
                    args.host, args.email)
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="VehicleWatch fleet simulator", epilog=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="http://localhost:8000")
    p.add_argument("--devices", type=int, default=5, help="1–5 vehicles")
    p.add_argument("--email", default="demo@vehiclewatch.io")
    p.add_argument("--password", default="demo-fleet-2026")
    p.add_argument("--org", default="Demo Logistics Ltd")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between readings per vehicle")
    a = p.parse_args()
    a.devices = max(1, min(a.devices, len(FLEET)))
    try:
        asyncio.run(main(a))
    except KeyboardInterrupt:
        logger.info("Simulator stopped")
        sys.exit(0)
