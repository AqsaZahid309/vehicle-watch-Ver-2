"""
Gemini LLM integration for human-readable anomaly summaries.

We call Gemini asynchronously using run_in_executor to wrap the synchronous
google-generativeai SDK — the SDK does not have native async support.
Failures are caught and logged rather than raised, so an LLM outage
never blocks alert creation.
"""

import asyncio
import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Per-fault action playbooks injected into the Gemini prompt.
# Gives the model grounded, operator-facing guidance rather than generic advice.
_FAULT_PLAYBOOKS: dict[str, str] = {
    "COOLANT_LEAK": (
        "Inspect coolant reservoir level and top up if low. "
        "Check all coolant hoses and the radiator for cracks, leaks, or loose clamps. "
        "Test the radiator cap pressure rating. "
        "Do not operate the vehicle until the cooling system is verified intact."
    ),
    "BATTERY_FAILURE": (
        "Test battery voltage under load with a multimeter (healthy: 12.4 V+ at rest, 13.8–14.4 V running). "
        "Inspect battery terminals for corrosion and connections for looseness. "
        "Test alternator output — if below 13.5 V at idle the alternator is suspect. "
        "Replace battery or alternator as indicated."
    ),
    "TRANSMISSION_STRESS": (
        "Check transmission fluid level and condition (should be bright red, not dark/burnt). "
        "Inspect for slipping by observing if RPM rises without proportional speed increase. "
        "Avoid high-load driving until inspected. "
        "Schedule transmission service — fluid flush or band adjustment may be required."
    ),
    "BRAKE_WEAR": (
        "Inspect all brake pads and rotors for wear thickness (minimum: 3 mm pad, 20 mm rotor). "
        "Check wheel bearings for play by lifting each wheel and testing for wobble. "
        "Listen for grinding or humming noise at speed — indicates metal-on-metal contact. "
        "Do not operate at highway speed until brakes and bearings are inspected."
    ),
    "ENGINE_STRESS": (
        "Check engine oil level and condition immediately — low or degraded oil causes thermal runaway. "
        "Inspect air filter for blockage reducing engine breathing. "
        "Check for exhaust restriction or catalytic converter blockage. "
        "Reduce load and limit RPM to under 2500 until the root cause is identified."
    ),
    "UNKNOWN_ANOMALY": (
        "Run a full diagnostic scan (OBD-II) to retrieve fault codes. "
        "Review the last 24 hours of telemetry for gradual trend changes. "
        "Inspect the highest z-score sensor readings identified in this alert. "
        "Schedule a preventive inspection — anomaly pattern does not match a known fault signature."
    ),
}


class GeminiService:
    """
    Instantiate once at application startup (e.g. in the anomaly worker) and
    reuse across all detection cycles. Constructing a new instance on every
    60-second cycle wastes resources re-configuring the SDK client.
    """

    def __init__(self) -> None:
        self._client = None
        if settings.gemini_api_key:
            try:
                import google.generativeai as genai

                genai.configure(api_key=settings.gemini_api_key)
                self._client = genai.GenerativeModel("gemini-1.5-flash")
                logger.info("Gemini client initialized (model: gemini-1.5-flash)")
            except ImportError:
                logger.warning("google-generativeai not installed; LLM summaries disabled")
        else:
            logger.info("GEMINI_API_KEY not set; LLM summaries will use fallback text")

    def _build_prompt(
        self,
        device_type: str,
        device_name: str,
        anomaly_score: float,
        affected_metrics: dict[str, Any],
        fault_type: str | None = None,
        fault_confidence: str | None = None,
    ) -> str:
        # ── Sensor deviation summary ─────────────────────────────────────────
        # affected_metrics structure from AnomalyService:
        # {
        #   "top_contributors": [
        #       {"feature": str, "z_score": float, "value": float,
        #        "train_mean": float, "train_std": float, "direction": str}, ...
        #   ],
        #   "ensemble": {"isolation_forest_score": float, "lof_confirmed": bool,
        #                "confidence": str, "n_features": int, "n_train_samples": int}
        # }
        top_contributors: list[dict[str, Any]] = affected_metrics.get("top_contributors", [])

        if top_contributors:
            parts = []
            for contrib in top_contributors[:4]:
                feature   = contrib["feature"].replace("_", " ")
                value     = contrib["value"]
                mean      = contrib["train_mean"]
                std       = contrib["train_std"]
                z         = contrib["z_score"]
                direction = contrib["direction"]
                lo        = round(mean - 2 * std, 2)
                hi        = round(mean + 2 * std, 2)
                parts.append(
                    f"  • {feature}: {value:.3g} "
                    f"({direction} normal range {lo}–{hi}, deviation {z:+.1f}\u03c3)"
                )
            metrics_desc = "\n".join(parts)
        else:
            metrics_desc = "  • Multiple sensor readings outside expected ranges"

        # ── Severity label ────────────────────────────────────────────────────
        severity = (
            "CRITICAL"
            if anomaly_score < settings.anomaly_score_critical
            else "MEDIUM"
            if anomaly_score < settings.anomaly_score_medium
            else "LOW"
        )

        # ── Ensemble metadata ─────────────────────────────────────────────────
        ensemble: dict[str, Any] = affected_metrics.get("ensemble", {})
        ml_confidence = ensemble.get("confidence", "MEDIUM")
        lof_line = (
            " The Local Outlier Factor model independently confirmed this as an outlier,"
            " increasing detection confidence."
            if ensemble.get("lof_confirmed")
            else ""
        )

        # ── Fault classification section ──────────────────────────────────────
        effective_fault = fault_type or "UNKNOWN_ANOMALY"
        effective_conf  = fault_confidence or "MEDIUM"
        playbook        = _FAULT_PLAYBOOKS.get(effective_fault, _FAULT_PLAYBOOKS["UNKNOWN_ANOMALY"])

        if effective_fault != "UNKNOWN_ANOMALY":
            fault_section = (
                f"Rule-based fault classification: {effective_fault} "
                f"(confidence: {effective_conf}).\n"
                f"Recommended maintenance actions for this fault type:\n"
                f"  {playbook}\n"
            )
        else:
            fault_section = (
                f"Fault pattern: UNKNOWN — does not match a named fault signature.\n"
                f"Diagnostic starting point: {playbook}\n"
            )

        # ── Full prompt ───────────────────────────────────────────────────────
        return (
            f"VEHICLE ANOMALY ALERT\n"
            f"{'=' * 50}\n"
            f"Vehicle:        {device_name} ({device_type})\n"
            f"Alert severity: {severity}\n"
            f"IsoForest score:{anomaly_score:.4f} (more negative = further from normal)\n"
            f"ML confidence:  {ml_confidence}{lof_line}\n"
            f"\n"
            f"SENSOR DEVIATIONS:\n"
            f"{metrics_desc}\n"
            f"\n"
            f"FAULT CLASSIFICATION:\n"
            f"{fault_section}\n"
            f"{'=' * 50}\n"
            f"Write a diagnostic summary in exactly 3 sentences (no numbering, as a paragraph):\n"
            f"\n"
            f"Sentence 1: State that {device_name} is showing signs of "
            f"{effective_fault.replace('_', ' ')} with {effective_conf} confidence, "
            f"then name the specific vehicle component most likely failing.\n"
            f"\n"
            f"Sentence 2: Reference the specific sensor values and their normal ranges "
            f"to explain physically what the fault means and what will happen to the "
            f"vehicle if the issue is not addressed promptly.\n"
            f"\n"
            f"Sentence 3: State the exact immediate action the fleet operator must take "
            f"right now — name specific parts to inspect, fluids to check, or "
            f"operations to restrict. Be direct and specific.\n"
            f"\n"
            f"Tone: professional fleet maintenance, direct and actionable. "
            f"Use the fault type name and sensor values in your response."
        )

    async def generate_alert_summary(
        self,
        device_type: str,
        device_name: str,
        anomaly_score: float,
        affected_metrics: dict[str, Any],
        fault_type: str | None = None,
        fault_confidence: str | None = None,
    ) -> str | None:
        if not self._client:
            return self._fallback_summary(
                device_type, affected_metrics, fault_type, fault_confidence
            )

        prompt = self._build_prompt(
            device_type, device_name, anomaly_score, affected_metrics,
            fault_type, fault_confidence,
        )

        try:
            # Use get_running_loop() — get_event_loop() is deprecated in Python 3.10+
            # and raises DeprecationWarning when called inside a running coroutine.
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, lambda: self._client.generate_content(prompt)
            )
            return response.text.strip()
        except Exception as exc:
            logger.warning("Gemini API call failed: %s — using fallback summary", exc)
            return self._fallback_summary(
                device_type, affected_metrics, fault_type, fault_confidence
            )

    def _fallback_summary(
        self,
        device_type: str,
        affected_metrics: dict[str, Any],
        fault_type: str | None = None,
        fault_confidence: str | None = None,
    ) -> str:
        """Rule-based fallback when Gemini is unavailable or not configured."""
        top_contributors: list[dict[str, Any]] = affected_metrics.get("top_contributors", [])
        effective_fault = fault_type or "UNKNOWN_ANOMALY"
        effective_conf  = fault_confidence or "MEDIUM"

        if effective_fault != "UNKNOWN_ANOMALY":
            fault_line = (
                f" Fault classification: {effective_fault} "
                f"({effective_conf} confidence)."
            )
        else:
            fault_line = ""

        if not top_contributors:
            return (
                f"An anomaly was detected on this {device_type} with multiple sensor "
                f"readings outside normal operating ranges.{fault_line} "
                f"Immediate inspection is recommended."
            )

        metric_names = [c["feature"].replace("_", " ") for c in top_contributors[:3]]
        metrics_str  = ", ".join(metric_names)
        playbook     = _FAULT_PLAYBOOKS.get(effective_fault, _FAULT_PLAYBOOKS["UNKNOWN_ANOMALY"])

        return (
            f"Abnormal readings detected for {metrics_str} on this {device_type}.{fault_line} "
            f"These deviations suggest: {playbook}"
        )
