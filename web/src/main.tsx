import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, ApiError, devUser } from "./api";
import { AppCtx, useApp, useLoad, type Ctx } from "./ui";
import { Engagements, Overview, People } from "./views/core";
import { Evidence, Questions, Rules } from "./views/evidence";
import { Decisions, Divergences, How, OpenItems } from "./views/work";
import { Governance, Outputs, Practices } from "./views/admin";
import { Lineage } from "./views/lineage";
import "./styles.css";

const ENGAGEMENT_ROUTES: { key: string; label: string; badge?: string }[] = [
  { key: "home", label: "Overview" },
  { key: "people", label: "People" },
  { key: "evidence", label: "Evidence", badge: "proposedClaims" },
  { key: "questions", label: "Five questions" },
  { key: "rules", label: "Rules", badge: "rulesNeedingGapDecision" },
  { key: "divergences", label: "Divergences", badge: "openDivergences" },
  { key: "openitems", label: "Open items", badge: "overdueItems" },
  { key: "how", label: "How" },
  { key: "decisions", label: "Decisions", badge: "escalatedDecisions" },
  { key: "outputs", label: "Outputs" },
];
const GLOBAL_ROUTES = [
  { key: "practices", label: "Datatonic practices" },
  { key: "governance", label: "Governance" },
  { key: "engagements", label: "All engagements" },
];

function useHash(): [string, (h: string) => void] {
  const read = () => (location.hash.replace("#", "") || "home");
  const [h, setH] = useState(read);
  useEffect(() => {
    const f = () => setH(read());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return [h, (x: string) => { location.hash = x; }];
}

function SignIn({ onDone, authMode }: { onDone: () => void; authMode?: string }) {
  const [users, setUsers] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api("/session/dev-users").then(setUsers).catch(() => setErr("Sign in through your company login to continue."));
  }, []);
  return (
    <div className="signin">
      <div className="modal">
        <h1>Discovery Guru</h1>
        <p className="muted">From the first conversation to a priced, defensible solution, traced at every step.</p>
        {err && <p>{err}</p>}
        {users && <>
          <p className="small muted">Local sign-in{authMode ? ` (${authMode})` : ""}. Choose who you are:</p>
          <div className="stack">
            {users.map((u) => (
              <button key={u.id} className="btn" style={{ textAlign: "left" }} onClick={() => { devUser.set(u.id); onDone(); }}>
                <strong>{u.name}</strong> <span className="muted small">{u.email}{u.isPracticeAdmin ? " · maintains practices" : ""}</span>
              </button>
            ))}
          </div>
        </>}
      </div>
    </div>
  );
}

function Shell() {
  const app = useApp();
  const [route, go] = useHash();
  const { data: engagements } = useLoad<any[]>("/engagements");
  const { data: summary } = useLoad<any>(app.eid ? `/engagements/${app.eid}/summary` : null);
  const isGlobal = GLOBAL_ROUTES.some((r) => r.key === route) || !app.eid;
  const view = !app.eid && !GLOBAL_ROUTES.some((r) => r.key === route) ? "engagements" : route;

  const V: Record<string, JSX.Element> = {
    home: <Overview go={go} />, people: <People />, evidence: <Evidence />, questions: <Questions />, rules: <Rules />,
    divergences: <Divergences />, openitems: <OpenItems />, how: <How />, decisions: <Decisions />, outputs: <Outputs />,
    practices: <Practices />, governance: <Governance />, engagements: <Engagements go={go} />,
  };
  const title = [...ENGAGEMENT_ROUTES, ...GLOBAL_ROUTES].find((r) => r.key === view)?.label ?? "";
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">Discovery Guru<small>Datatonic pre-sales</small></div>
        <label style={{ color: "var(--rail-ink)" }}>
          <span className="small">Engagement</span>
          <select value={app.eid ?? ""} onChange={(e) => { app.setEid(e.target.value || null); go("home"); }}>
            <option value="">Choose…</option>
            {(engagements ?? []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
        </label>
        {app.eid && <nav className="nav" aria-label="Engagement">
          {ENGAGEMENT_ROUTES.map((r) => (
            <a key={r.key} href={`#${r.key}`} className={view === r.key ? "active" : ""}>
              {r.label}
              {r.badge && summary?.[r.badge] > 0 && <span className="count" aria-label={`${summary[r.badge]} need attention`}>{summary[r.badge]}</span>}
            </a>
          ))}
        </nav>}
        <nav className="nav" aria-label="Workspace">
          <div className="nav-label">Workspace</div>
          {GLOBAL_ROUTES.map((r) => <a key={r.key} href={`#${r.key}`} className={view === r.key ? "active" : ""}>{r.label}</a>)}
        </nav>
        <div className="me">
          Signed in as <strong style={{ color: "var(--rail-active)" }}>{app.me?.name}</strong><br />
          {app.me?.authMode === "dev" && <button onClick={() => { devUser.set(null); location.reload(); }}>Switch person</button>}
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <h1>{isGlobal ? title : app.eng?.name ?? title}</h1>
            {!isGlobal && app.eng && <p className="sub">{title} · {app.eng.client}{app.eng.sector ? ` · ${app.eng.sector}` : ""}</p>}
          </div>
          {!isGlobal && app.role && <span className="chip accent">Your role: {app.role === "owner" ? "solution owner" : app.role}</span>}
        </header>
        <div className="content">{V[view] ?? V.home}</div>
      </main>
    </div>
  );
}

function Root() {
  const [me, setMe] = useState<any>(null);
  const [needSignIn, setNeedSignIn] = useState(false);
  const [eid, setEidState] = useState<string | null>(() => { try { return localStorage.getItem("sdp.eid"); } catch { return null; } });
  const [refreshKey, setRefreshKey] = useState(0);
  const [toastMsg, setToast] = useState<string | null>(null);
  const [lineage, setLineage] = useState<{ entity: string; id: string } | null>(null);
  const [eng, setEng] = useState<any>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const timer = useRef<number>();

  const loadMe = useCallback(() => {
    api("/me").then((m) => { setMe(m); setNeedSignIn(false); })
      .catch((e) => { if (e instanceof ApiError && (e.status === 401 || e.status === 404)) { devUser.set(null); setNeedSignIn(true); } });
  }, []);
  useEffect(loadMe, [loadMe]);

  useEffect(() => {
    if (!eid || !me) { setEng(null); return; }
    api(`/engagements/${eid}`).then(setEng).catch(() => { setEng(null); setEidState(null); });
  }, [eid, me, refreshKey]);

  const ctx: Ctx = useMemo(() => ({
    me, eid, eng, role: eng?.myRole ?? null,
    setEid: (id) => { setEidState(id); try { id ? localStorage.setItem("sdp.eid", id) : localStorage.removeItem("sdp.eid"); } catch { /* */ } },
    refreshKey, refresh: () => setRefreshKey((k) => k + 1),
    toast: (m) => { setToast(m); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setToast(null), 4500); },
    openLineage: (entity, id) => setLineage({ entity, id }),
    nameOf: (id) => {
      if (!id) return "Unassigned";
      if (id === me?.id) return "you";
      if (names[id] !== undefined) return names[id] || "Someone";
      if (id.startsWith("engine-")) return id.replace("engine-", "") + " engine";
      if (!pending.current.has(id)) {
        pending.current.add(id);
        api(`/users/${id}`).then((u) => setNames((n) => ({ ...n, [id]: u.name }))).catch(() => setNames((n) => ({ ...n, [id]: "" })));
      }
      return "…";
    },
  }), [me, eid, eng, refreshKey, names]);

  if (needSignIn) return <SignIn onDone={loadMe} />;
  if (!me) return <div className="signin"><p style={{ color: "var(--rail-ink)" }}>Loading…</p></div>;
  return (
    <AppCtx.Provider value={ctx}>
      <Shell />
      {lineage && eid && <Lineage entity={lineage.entity} id={lineage.id} onClose={() => setLineage(null)} />}
      {toastMsg && <div className="toast" role="status">{toastMsg}</div>}
    </AppCtx.Provider>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><Root /></StrictMode>);
