"""
VehicleWatch Device Simulator — 5 Unique Fault Personalities

Each vehicle is a stateful class. Degradation accumulates across readings,
not randomly per reading. All 5 vehicles run concurrently via asyncio.

Usage:
    python simulator/device_simulator.py
    python simulator/device_simulator.py --host http://localhost:8000 --devices 3
    python simulator/device_simulator.py --email admin@example.com --password secret

Vehicles (in order):
    1. Truck-Alpha  — healthy baseline (control vehicle, no faults)
    2. Truck-Beta   — developing coolant leak (engine_temp rising curve)
    3. Truck-Gamma  — battery / alternator degradation (voltage drop + erratic RPM)
    4. Truck-Delta  — transmission stress (RPM spikes at high speed)
    5. Truck-Echo   — brake wear / wheel bearing (vibration scales with speed)
"""

import abc
import argparse
import asyncio
import logging
import random
import sys
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | SIMULATOR | %(message)s",
)
logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rand(lo: float, hi: float, decimals: int = 3) -> float:
    return round(random.uniform(lo, hi), decimals)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _gps() -> tuple[float, float]:
    """Random US-continental GPS coordinate."""
    return _rand(30.0, 48.0, 6), _rand(-120.0, -75.0, 6)


# ── Base vehicle ─────────────────────────────────────────────────────────────

class BaseVehicle(abc.ABC):
    """
    Abstract base for all simulated vehicles.

    Subclasses implement `_build_telemetry()` which returns all non-GPS fields.
    State (degradation counters, baselines) lives in the instance and persists
    across every reading for the lifetime of the simulation run.

    `generate_reading()` is the public API — it increments the counter,
    attaches GPS, and returns the full payload dict ready to POST.
    """

    name:        str = "BaseVehicle"
    device_type: str = "truck"

    def __init__(self) -> None:
        self.reading_count: int = 0

    def generate_reading(self) -> dict[str, Any]:
        """Increment counter, build telemetry, attach GPS. Returns full payload."""
        self.reading_count += 1
        lat, lon = _gps()
        payload = self._build_telemetry()
        payload["gps_lat"] = lat
        payload["gps_lon"] = lon
        return payload

    @abc.abstractmethod
    def _build_telemetry(self) -> dict[str, Any]:
        """Return dict of all non-GPS sensor fields for this reading."""
        ...


# ── Vehicle 1 — Truck-Alpha (healthy baseline / control vehicle) ─────────────

class TruckAlpha(BaseVehicle):
    """
    All sensor readings stay within normal ranges for the entire run.
    No anomalies, no degradation. Provides the ML model with clean baseline
    data and acts as the control vehicle for anomaly comparison.

    Normal ranges:
        engine_temp:     75–95 °C
        rpm:             800–2500
        fuel_level:      30–95 %
        battery_voltage: 12.8–14.2 V
        speed:           0–100 km/h
        vibration:       0.5–2.5
    """

    name        = "Truck-Alpha"
    device_type = "truck"

    def _build_telemetry(self) -> dict[str, Any]:
        return {
            "engine_temp":     _rand(75.0,  95.0,  2),
            "rpm":             _rand(800.0, 2500.0, 1),
            "fuel_level":      _rand(30.0,  95.0,  2),
            "battery_voltage": _rand(12.8,  14.2,  3),
            "speed":           _rand(0.0,   100.0, 2),
            "vibration":       _rand(0.5,   2.5,   3),
        }


# ── Vehicle 2 — Truck-Beta (developing coolant leak) ─────────────────────────

class TruckBeta(BaseVehicle):
    """
    Engine temperature rises on a deterministic degradation curve, simulating
    a slow coolant leak that causes progressive overheating.

    Degradation schedule:
        Every 20 readings  → _temp_offset += 4 °C
        After 100 readings → _temp_offset = +20 °C
        engine_temp base (95 °C) + 20 °C offset = 115 °C consistently

    Vibration rises in proportion to the thermal offset because an overheating
    engine produces additional mechanical vibration (expansion, knock).

    Fault label logged: COOLANT_LEAK
    """

    name        = "Truck-Beta"
    device_type = "truck"

    _STEP_EVERY: int   = 20   # readings between degradation increments
    _STEP_DEG:   float = 4.0  # °C gained per step
    _FAULT_ABOVE: float = 105.0  # °C threshold for logging the fault

    def __init__(self) -> None:
        super().__init__()
        self._temp_offset: float = 0.0  # cumulative °C added to baseline

    def _build_telemetry(self) -> dict[str, Any]:
        # Advance degradation: fires at readings 20, 40, 60, 80, 100, …
        if self.reading_count % self._STEP_EVERY == 0:
            self._temp_offset += self._STEP_DEG

        engine_temp = round(
            _clamp(95.0 + self._temp_offset + random.uniform(-3.0, 3.0), 40.0, 160.0), 2
        )

        # Vibration coupled to thermal stress: +0.05 per degree of offset
        vibration = round(
            _clamp(0.8 + self._temp_offset * 0.05 + random.uniform(-0.2, 0.4), 0.1, 10.0), 3
        )

        if engine_temp > self._FAULT_ABOVE:
            logger.warning(
                "[%s] COOLANT_LEAK anomaly injected — engine_temp: %.1f°C "
                "(offset: +%.1f°C, reading #%d)",
                self.name, engine_temp, self._temp_offset, self.reading_count,
            )

        return {
            "engine_temp":     engine_temp,
            "rpm":             _rand(800.0, 2500.0, 1),
            "fuel_level":      _rand(30.0,  90.0,  2),
            "battery_voltage": _rand(12.8,  14.2,  3),
            "speed":           _rand(0.0,   100.0, 2),
            "vibration":       vibration,
        }


# ── Vehicle 3 — Truck-Gamma (battery / alternator degradation) ───────────────

class TruckGamma(BaseVehicle):
    """
    Battery voltage drops steadily, simulating a failing alternator or cell
    degradation. Once voltage falls below 11.8 V, the engine control module
    receives unstable power causing erratic RPM fluctuations.

    Degradation schedule:
        Every 15 readings  → _voltage_base -= 0.05 V
        Start: 13.8 V  →  erratic threshold (11.8 V) reached after ~600 readings
        At 2-second intervals that is ~20 minutes — realistic for a slow drain.

    Below 11.8 V:
        RPM base ± 500 RPM noise (simulates ECM instability under low voltage)

    Fault label logged: BATTERY_DEGRADATION
    """

    name        = "Truck-Gamma"
    device_type = "truck"

    _STEP_EVERY:        int   = 15
    _STEP_VOLT:         float = 0.05
    _ERRATIC_THRESHOLD: float = 11.8   # V — below this, RPM becomes unstable
    _RPM_ERRATIC_SWING: float = 500.0  # ± RPM noise
    _VOLTAGE_FLOOR:     float = 8.0    # V — dead battery floor

    def __init__(self) -> None:
        super().__init__()
        self._voltage_base: float = 13.8

    def _build_telemetry(self) -> dict[str, Any]:
        # Advance degradation: first decrement at reading 15
        if self.reading_count % self._STEP_EVERY == 0:
            self._voltage_base = round(
                max(self._VOLTAGE_FLOOR, self._voltage_base - self._STEP_VOLT), 3
            )

        voltage = round(
            _clamp(self._voltage_base + random.uniform(-0.1, 0.1), self._VOLTAGE_FLOOR, 15.0),
            3,
        )

        erratic = self._voltage_base < self._ERRATIC_THRESHOLD
        base_rpm = random.uniform(800.0, 2500.0)

        if erratic:
            rpm = round(
                _clamp(
                    base_rpm + random.uniform(-self._RPM_ERRATIC_SWING, self._RPM_ERRATIC_SWING),
                    300.0,
                    5000.0,
                ),
                1,
            )
            logger.warning(
                "[%s] BATTERY_DEGRADATION anomaly injected — voltage: %.3f V "
                "(base: %.3f V, erratic RPM: %.0f, reading #%d)",
                self.name, voltage, self._voltage_base, rpm, self.reading_count,
            )
        else:
            rpm = round(base_rpm, 1)

        return {
            "engine_temp":     _rand(75.0, 95.0,  2),
            "rpm":             rpm,
            "fuel_level":      _rand(30.0, 90.0,  2),
            "battery_voltage": voltage,
            "speed":           _rand(0.0,  100.0, 2),
            "vibration":       _rand(0.5,  2.5,   3),
        }


# ── Vehicle 4 — Truck-Delta (transmission stress / slip) ─────────────────────

class TruckDelta(BaseVehicle):
    """
    RPM spikes occur at high speed, simulating a slipping transmission that
    over-revs under load. The drivetrain resonance during slip also increases
    vibration measurably.

    Fault pattern:
        speed ≤ 70 km/h → normal RPM (800–2500), normal vibration (0.5–2.5)
        speed >  70 km/h → slip RPM (3500–4500), elevated vibration (3.5–6.0)

    Engine temperature and battery stay in normal range — this is a purely
    mechanical drivetrain fault, not thermal or electrical.

    Fault label logged: TRANSMISSION_SLIP
    """

    name        = "Truck-Delta"
    device_type = "truck"

    _SPEED_THRESHOLD: float = 70.0
    _SLIP_RPM_LO:     float = 3500.0
    _SLIP_RPM_HI:     float = 4500.0
    _SLIP_VIB_LO:     float = 3.5
    _SLIP_VIB_HI:     float = 6.0

    def _build_telemetry(self) -> dict[str, Any]:
        speed = _rand(0.0, 110.0, 2)
        slipping = speed > self._SPEED_THRESHOLD

        if slipping:
            rpm       = _rand(self._SLIP_RPM_LO, self._SLIP_RPM_HI, 1)
            vibration = _rand(self._SLIP_VIB_LO, self._SLIP_VIB_HI, 3)
            logger.warning(
                "[%s] TRANSMISSION_SLIP anomaly injected — speed: %.1f km/h, "
                "rpm: %.0f, vibration: %.2f (reading #%d)",
                self.name, speed, rpm, vibration, self.reading_count,
            )
        else:
            rpm       = _rand(800.0, 2500.0, 1)
            vibration = _rand(0.5,   2.5,    3)

        return {
            "engine_temp":     _rand(75.0, 95.0,  2),
            "rpm":             rpm,
            "fuel_level":      _rand(30.0, 90.0,  2),
            "battery_voltage": _rand(12.8, 14.2,  3),
            "speed":           speed,
            "vibration":       vibration,
        }


# ── Vehicle 5 — Truck-Echo (brake wear / wheel bearing failure) ──────────────

class TruckEcho(BaseVehicle):
    """
    Vibration amplifies with speed, simulating a worn wheel bearing or brake
    pad that has reached metal-on-metal contact. The fault is invisible at low
    speeds (bearing load is minimal) but escalates sharply on highway runs.

    This is particularly interesting for the ML ensemble: engine temp and RPM
    stay completely normal, so only the vibration-speed cross-sensor ratio
    (vib_per_speed engineered feature) can catch it.

    Fault pattern:
        speed < 60 km/h  → normal  vibration: 0.5–3.0
        60 ≤ speed < 90  → elevated vibration: 5.0–8.0  (WHEEL_BEARING_ELEVATED)
        speed ≥ 90 km/h  → severe  vibration: 7.0–10.0  (WHEEL_BEARING_SEVERE)
    """

    name        = "Truck-Echo"
    device_type = "truck"

    _THRESH_MED: float = 60.0
    _THRESH_HI:  float = 90.0

    def _build_telemetry(self) -> dict[str, Any]:
        speed = _rand(0.0, 110.0, 2)

        if speed >= self._THRESH_HI:
            vibration   = _rand(7.0, 10.0, 3)
            fault_label = "WHEEL_BEARING_SEVERE"
        elif speed >= self._THRESH_MED:
            vibration   = _rand(5.0, 8.0, 3)
            fault_label = "WHEEL_BEARING_ELEVATED"
        else:
            vibration   = _rand(0.5, 3.0, 3)
            fault_label = None

        if fault_label:
            logger.warning(
                "[%s] %s anomaly injected — speed: %.1f km/h, "
                "vibration: %.2f (reading #%d)",
                self.name, fault_label, speed, vibration, self.reading_count,
            )

        return {
            "engine_temp":     _rand(75.0,  95.0,  2),
            "rpm":             _rand(800.0, 2500.0, 1),
            "fuel_level":      _rand(30.0,  90.0,  2),
            "battery_voltage": _rand(12.8,  14.2,  3),
            "speed":           speed,
            "vibration":       vibration,
        }


# ── Fleet definition ─────────────────────────────────────────────────────────

FLEET: list[BaseVehicle] = [
    TruckAlpha(),   # 1 — control
    TruckBeta(),    # 2 — coolant leak
    TruckGamma(),   # 3 — battery degradation
    TruckDelta(),   # 4 — transmission slip
    TruckEcho(),    # 5 — wheel bearing
]


# ── API helpers ───────────────────────────────────────────────────────────────

async def _authenticate(
    client: httpx.AsyncClient, host: str, email: str, password: str
) -> str:
    resp = await client.post(
        f"{host}/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    logger.info("Authenticated as %s", email)
    return token


async def register_and_login(
    client: httpx.AsyncClient, host: str, email: str, password: str
) -> str:
    """Attempt registration (idempotent — 409 on duplicate is expected), then login."""
    try:
        await client.post(
            f"{host}/api/v1/auth/register",
            json={"email": email, "password": password, "role": "ADMIN"},
        )
    except httpx.HTTPError:
        pass  # already registered
    return await _authenticate(client, host, email, password)


async def _get_user_id(client: httpx.AsyncClient, host: str, token: str) -> str:
    resp = await client.get(
        f"{host}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


async def _create_device(
    client: httpx.AsyncClient,
    host: str,
    token: str,
    vehicle: BaseVehicle,
    owner_id: str,
) -> str:
    resp = await client.post(
        f"{host}/api/v1/devices",
        json={
            "name":        vehicle.name,
            "device_type": vehicle.device_type,
            "owner_id":    owner_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    device_id = resp.json()["id"]
    logger.info(
        "Registered %-14s  type=%-10s  id=%s",
        vehicle.name, vehicle.device_type, device_id,
    )
    return device_id


# ── Per-vehicle telemetry loop ────────────────────────────────────────────────

async def simulate_vehicle(
    client:    httpx.AsyncClient,
    host:      str,
    token:     str,
    device_id: str,
    vehicle:   BaseVehicle,
    interval:  float = 2.0,
) -> None:
    logger.info("[%s] Telemetry stream started → device_id=%s", vehicle.name, device_id)

    while True:
        reading = vehicle.generate_reading()

        try:
            resp = await client.post(
                f"{host}/api/v1/devices/{device_id}/telemetry",
                json=reading,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code != 201:
                logger.warning(
                    "[%s] Unexpected HTTP %d on reading #%d",
                    vehicle.name, resp.status_code, vehicle.reading_count,
                )
        except httpx.HTTPError as exc:
            logger.error("[%s] HTTP error on reading #%d: %s", vehicle.name, vehicle.reading_count, exc)

        await asyncio.sleep(interval)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(
    host:         str,
    num_vehicles: int,
    email:        str,
    password:     str,
    interval:     float,
) -> None:
    active_fleet = FLEET[:num_vehicles]

    async with httpx.AsyncClient() as client:
        token    = await register_and_login(client, host, email, password)
        owner_id = await _get_user_id(client, host, token)

        pairs: list[tuple[BaseVehicle, str]] = []
        for vehicle in active_fleet:
            try:
                device_id = await _create_device(client, host, token, vehicle, owner_id)
                pairs.append((vehicle, device_id))
            except Exception as exc:
                logger.error("Failed to register %s: %s", vehicle.name, exc)

        if not pairs:
            logger.error("No vehicles registered — cannot start simulation. Exiting.")
            return

        logger.info(
            "Simulation running — %d vehicle(s) | %.1fs interval | Ctrl-C to stop",
            len(pairs), interval,
        )
        for v, did in pairs:
            logger.info("  %-14s  fault=%-24s  device_id=%s", v.name, type(v).__doc__.split("\n")[1].strip(), did)

        tasks = [
            asyncio.create_task(
                simulate_vehicle(client, host, token, device_id, vehicle, interval)
            )
            for vehicle, device_id in pairs
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="VehicleWatch Fleet Simulator — 5 unique fault personalities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Fault personalities (run in order up to --devices limit):
  1  Truck-Alpha   Healthy baseline, no anomalies (control vehicle)
  2  Truck-Beta    Developing coolant leak — engine_temp rises 4°C/20 readings
  3  Truck-Gamma   Battery degradation — voltage drops 0.05V/15 readings → erratic RPM
  4  Truck-Delta   Transmission slip — RPM 3500-4500 when speed > 70 km/h
  5  Truck-Echo    Wheel bearing failure — vibration scales with speed
        """,
    )
    parser.add_argument("--host",     default="http://localhost:8000",
                        help="API base URL (default: http://localhost:8000)")
    parser.add_argument("--devices",  type=int, default=5,
                        help="Number of vehicles to simulate, 1–5 (default: 5)")
    parser.add_argument("--email",    default="simulator@vehiclewatch.io",
                        help="Admin account email")
    parser.add_argument("--password", default="simulator123",
                        help="Admin account password")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="Seconds between readings per vehicle (default: 2.0)")
    args = parser.parse_args()

    num = max(1, min(args.devices, len(FLEET)))
    if num != args.devices:
        logger.warning("--devices=%d out of range — clamped to %d", args.devices, num)

    try:
        asyncio.run(main(args.host, num, args.email, args.password, args.interval))
    except KeyboardInterrupt:
        logger.info("Simulator stopped by user")
        sys.exit(0)
