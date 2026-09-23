import { useEffect, type ReactNode } from "react";
import { Inbox, X } from "lucide-react";
import { healthColor, humanize } from "../lib/format";

export function Card({ title, sub, actions, children, flush, className }: {
  title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {sub && <div className="sub">{sub}</div>}
          </div>
          {actions && <div className="row wrap">{actions}</div>}
        </div>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, icon, color }: {
  label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; color?: string;
}) {
  return (
    <div className="card stat">
      <div className="label">{icon}{label}</div>
      <div className="value" style={color ? { color } : undefined}>{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="row wrap no-print">{actions}</div>}
    </div>
  );
}

const SEV: Record<string, string> = { CRITICAL: "red", HIGH: "orange", MEDIUM: "orange", LOW: "yellow", NONE: "" };
export function SeverityBadge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  return <span className={`badge ${SEV[value] ?? ""}`}>{value}</span>;
}

const STATUS: Record<string, string> = {
  OPEN: "blue", IN_PROGRESS: "purple", ON_HOLD: "yellow", RESOLVED: "green", CANCELLED: "",
  URGENT: "red", HIGH: "orange", MEDIUM: "yellow", LOW: "",
  TRUE_POSITIVE: "green", FALSE_POSITIVE: "red", REFUEL: "green", THEFT_SUSPECTED: "red",
  ENTER: "blue", EXIT: "", SPEEDING: "orange", DEPOT: "blue", CUSTOMER: "green", SERVICE: "purple", RESTRICTED: "red",
};
export function Badge({ value, color }: { value: string | null | undefined; color?: string }) {
  if (!value) return null;
  return <span className={`badge ${color ?? STATUS[value] ?? ""}`}>{humanize(value)}</span>;
}

export function FaultBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="badge">Unclassified</span>;
  return <span className="badge purple">{humanize(value)}</span>;
}

export function Health({ score }: { score: number }) {
  return (
    <span className="health" title={`Health ${score}/100`}>
      <span className="health-bar"><span style={{ width: `${score}%`, background: healthColor(score) }} /></span>
      <span className="small" style={{ color: healthColor(score), fontWeight: 650 }}>{score}</span>
    </span>
  );
}

export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return <div className="empty">{icon ?? <Inbox size={28} />}<div>{children}</div></div>;
}

export function Loading() {
  return <div className="center"><div className="spinner" /></div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error-box">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Modal({ title, onClose, children, footer, wide }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEscape(onClose);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true">
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn ghost icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn ghost icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="modal-body">{children}</div>
      </aside>
    </div>
  );
}

function useEscape(fn: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && fn();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [fn]);
}

export function Field({ label, help, children, full }: { label: string; help?: ReactNode; children: ReactNode; full?: boolean }) {
  return (
    <div className={`field ${full ? "full" : ""}`}>
      <label>{label}</label>
      {children}
      {help && <div className="help">{help}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "active" : ""} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  return (
    <div className="row" style={{ justifyContent: "flex-end", padding: 12 }}>
      <button className="btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span className="small muted">Page {page} of {pages}</span>
      <button className="btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}
