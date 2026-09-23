import { useState, type FormEvent } from "react";
import { Car } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { ErrorBox, Field } from "../components/ui";

export default function AuthPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ email: "", password: "", full_name: "", organization_name: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(form.email, form.password);
      else await register(form);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-hero">
        <div className="row" style={{ marginBottom: 28, fontWeight: 750, fontSize: 18 }}>
          <span className="brand-mark" style={{ background: "white", color: "#2563eb" }}><Car size={18} /></span> VehicleWatch
        </div>
        <h1>Catch the £200 fix before it becomes the £12,000 rebuild.</h1>
        <p style={{ opacity: 0.9, maxWidth: 520 }}>
          Fleet telemetry, predictive maintenance and operations in one place.
        </p>
        <ul style={{ paddingLeft: 18, maxWidth: 520 }}>
          <li>ML anomaly detection with plain-English diagnoses</li>
          <li>Failure forecasts: see which vehicle breaks down next, and when</li>
          <li>Work orders that close the loop and measure detector accuracy</li>
          <li>Live map, trips, driver scorecards, geofences and fuel-theft detection</li>
          <li>Slack, Teams, email, SMS and webhook alerts with escalation</li>
        </ul>
      </div>
      <div className="auth-form">
        <form className="card" onSubmit={submit}>
          <div className="card-head">
            <h2>{mode === "login" ? "Sign in" : "Create your fleet workspace"}</h2>
          </div>
          <div className="card-body stack">
            {mode === "register" && (
              <>
                <Field label="Your name"><input value={form.full_name} onChange={set("full_name")} autoComplete="name" /></Field>
                <Field label="Company / fleet name"><input value={form.organization_name} onChange={set("organization_name")} placeholder="Acme Logistics" /></Field>
              </>
            )}
            <Field label="Email"><input type="email" required value={form.email} onChange={set("email")} autoComplete="email" /></Field>
            <Field label="Password" help={mode === "register" ? "At least 8 characters." : undefined}>
              <input type="password" required minLength={mode === "register" ? 8 : undefined} value={form.password} onChange={set("password")}
                autoComplete={mode === "login" ? "current-password" : "new-password"} />
            </Field>
            <ErrorBox error={error} />
            <button className="btn primary" disabled={busy} type="submit">
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create workspace"}
            </button>
            <div className="small muted" style={{ textAlign: "center" }}>
              {mode === "login" ? "New to VehicleWatch? " : "Already have an account? "}
              <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); setError(null); }}>
                {mode === "login" ? "Create a workspace" : "Sign in"}
              </a>
            </div>
            {mode === "login" && (
              <div className="info-box small">
                Running the simulator? Sign in with <code>demo@vehiclewatch.io</code> / <code>demo-fleet-2026</code>.
              </div>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
