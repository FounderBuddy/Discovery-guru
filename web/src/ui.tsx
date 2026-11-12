import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";

export type Ctx = {
  me: any;
  eid: string | null;
  eng: any | null;
  role: string | null;
  setEid: (id: string | null) => void;
  refreshKey: number;
  refresh: () => void;
  toast: (msg: string) => void;
  openLineage: (entity: string, id: string) => void;
  nameOf: (id?: string | null) => string;
};

export const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);

export const can = {
  contribute: (r: string | null) => r === "owner" || r === "contributor",
  review: (r: string | null) => r === "owner" || r === "reviewer",
  own: (r: string | null) => r === "owner",
};

/** Load a path from the gateway; reloads on refresh() and every 15 s so teammates' changes appear. */
export function useLoad<T = any>(path: string | null) {
  const { refreshKey } = useApp();
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 15000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!path) return;
    let live = true;
    api<T>(path).then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [path, refreshKey, tick]);
  return { data, error, loading: data === undefined && !error };
}

/** Run a mutation: shows a toast on failure or success and refreshes every view. */
export function useAction() {
  const { toast, refresh } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(async <T,>(label: string, fn: () => Promise<T>, done?: string | ((r: T) => string)) => {
    setBusy(label);
    try {
      const r = await fn();
      if (done) toast(typeof done === "function" ? done(r) : done);
      refresh();
      return r;
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Something went wrong. Try again.");
      return undefined;
    } finally {
      setBusy(null);
    }
  }, [toast, refresh]);
  return { busy, run };
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    ref.current?.querySelector<HTMLElement>("input, textarea, select")?.focus();
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label={title} ref={ref}>
        <div className="spread"><h2>{title}</h2><button className="btn link" onClick={onClose}>Close</button></div>
        {children}
        {footer && <div className="row" style={{ justifyContent: "flex-end" }}>{footer}</div>}
      </div>
    </div>
  );
}

export const Chip = ({ children, tone, q }: { children: ReactNode; tone?: "ok" | "warn" | "crit" | "accent"; q?: boolean }) =>
  <span className={`chip${tone ? " " + tone : ""}${q ? " q" : ""}`}>{children}</span>;

export const Empty = ({ children }: { children: ReactNode }) => <div className="empty">{children}</div>;

export const Q_LABEL: Record<string, string> = { why: "WHY", what: "WHAT", rules: "RULES", from: "FROM", how: "HOW" };
export const KIND_LABEL: Record<string, string> = {
  driver: "Driver", pain_point: "Pain point", baseline: "Baseline", outcome: "Outcome", use_case: "Use case",
  guardrail: "Guardrail", estate_fact: "Estate fact", number: "Number", constraint: "Constraint", decision: "Decision",
  owner: "Owner", risk: "Risk", open_question: "Open question", preference: "Preference",
};
export const OUTCOMES = [
  { key: "unclassified", label: "Unclassified", hint: "Not yet routed" },
  { key: "insist", label: "Insist", hint: "Changes architecture or price: escalate" },
  { key: "estimate", label: "Estimate", hint: "Ambiguous count: range, capped" },
  { key: "risk", label: "Risk", hint: "Not blocking: likelihood, fallback" },
  { key: "pending", label: "Pending decision", hint: "Client's call: default if silent" },
  { key: "design_around", label: "Design around", hint: "Remove the dependency" },
];
export const PATHS = ["Data platform", "Ingestion and ETL", "Analytics and BI", "Integration", "Compute and modernisation",
  "Security and governance", "Network and landing zone", "AI and agents"];
export const fmt = (iso?: string | null) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";

/** One line of provenance under a claim: who said it, where, and how it got here. */
export function Provenance({ c, sources }: { c: any; sources?: Record<string, string> }) {
  const { nameOf } = useApp();
  if (c.evidence === "none") return <p className="prov">Entered by {nameOf(c.createdBy)} · no client evidence: an assumption</p>;
  const src = sources?.[c.sourceId] ?? "source";
  return (
    <p className="prov">
      {c.speaker ? `${c.speaker} · ` : ""}{src}{c.part ? ` · part ${c.part}` : ""}
      {c.origin === "agent" ? ` · proposed by ${c.promptVersion ?? "agent"}` : ""}
      {c.confirmedBy ? ` · confirmed by ${nameOf(c.confirmedBy)}` : ""}
    </p>
  );
}
