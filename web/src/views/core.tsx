import { useState } from "react";
import { api } from "../api";
import { can, Chip, Empty, fmt, Modal, OUTCOMES, useAction, useApp, useLoad } from "../ui";

export function Engagements({ go }: { go: (h: string) => void }) {
  const app = useApp();
  const { data } = useLoad<any[]>("/engagements");
  const { run, busy } = useAction();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", client: "", sector: "", description: "", aiBudgetUsd: 25 });
  const create = async () => {
    const e = await run("create", () => api("/engagements", { body: f }), "Engagement opened. You are its solution owner.");
    if (e) { app.setEid((e as any).id); setOpen(false); go("home"); }
  };
  return (
    <>
      <div className="spread">
        <p className="muted">Engagements you take part in. Opening one makes you its solution owner.</p>
        <button className="btn primary" onClick={() => setOpen(true)}>New engagement</button>
      </div>
      {data && !data.length && <Empty>No engagements yet. Open one to start discovery.</Empty>}
      <div className="grid">
        {(data ?? []).map((e) => (
          <button key={e.id} className="card tile" onClick={() => { app.setEid(e.id); go("home"); }}>
            <h2>{e.name}</h2>
            <p className="muted small">{e.client}{e.sector ? ` · ${e.sector}` : ""}</p>
            <div className="row"><Chip tone="accent">{e.myRole === "owner" ? "solution owner" : e.myRole}</Chip><span className="small muted">opened {fmt(e.createdAt)}</span></div>
          </button>
        ))}
      </div>
      {open && (
        <Modal title="New engagement" onClose={() => setOpen(false)}
          footer={<button className="btn primary" disabled={!f.name || !!busy} onClick={create}>Open engagement</button>}>
          <label>Name<input id="eng-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Data platform modernisation" /></label>
          <label>Client<input id="eng-client" value={f.client} onChange={(e) => setF({ ...f, client: e.target.value })} /></label>
          <label>Sector<input id="eng-sector" value={f.sector} onChange={(e) => setF({ ...f, sector: e.target.value })} /></label>
          <label>What we know so far<textarea id="eng-desc" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
          <label>Model spend cap (USD)<input id="eng-budget" type="number" min={1} value={f.aiBudgetUsd} onChange={(e) => setF({ ...f, aiBudgetUsd: Number(e.target.value) })} /></label>
        </Modal>
      )}
    </>
  );
}

const QUESTIONS = [
  { key: "why", label: "WHY", hint: "Business drivers and pain points" },
  { key: "what", label: "WHAT", hint: "Outcomes and use cases" },
  { key: "rules", label: "RULES", hint: "Guardrails we must follow" },
  { key: "from", label: "FROM", hint: "The current estate" },
  { key: "how", label: "HOW", hint: "Paths, design, effort" },
];

export function Overview({ go }: { go: (h: string) => void }) {
  const { eid, eng, nameOf } = useApp();
  const { data: s } = useLoad<any>(`/engagements/${eid}/summary`);
  const { data: runs } = useLoad<any[]>(`/engagements/${eid}/runs?limit=6`);
  const { data: budget } = useLoad<any>(`/engagements/${eid}/budget`);
  if (!s) return <p className="muted">Loading…</p>;
  const attention = [
    { n: s.brief?.needs_review ?? 0, text: "parts of the Brief need review because their evidence changed", to: "brief", tone: "warn" as const },
    { n: s.brief?.draft ?? 0, text: "parts of the Brief drafted and waiting for the solution owner", to: "brief", tone: "accent" as const },
    { n: s.proposedClaims, text: "proposed claims waiting for review", to: "evidence", tone: "accent" as const },
    { n: s.openDivergences, text: "contradictions for the solution owner to resolve", to: "divergences", tone: "crit" as const },
    { n: s.overdueItems, text: "open items past their deadline", to: "openitems", tone: "crit" as const },
    { n: s.escalatedDecisions, text: "practice-versus-client conflicts escalated for a decision", to: "decisions", tone: "warn" as const },
    { n: s.rulesNeedingGapDecision, text: "absent guardrails needing a decision: establish it, or accept the gap", to: "rules", tone: "warn" as const },
    { n: s.rulesUnknown, text: "guardrail categories not yet answered", to: "rules", tone: "warn" as const },
    { n: s.whyWithoutBaseline, text: "drivers or pain points with no baseline number", to: "questions", tone: "warn" as const },
    { n: s.recommendationsNeedingReview, text: "recommendations blocked or needing review", to: "how", tone: "warn" as const },
    { n: s.needsReview, text: "items resting on a superseded claim", to: "openitems", tone: "warn" as const },
  ].filter((a) => a.n > 0);
  return (
    <>
      <section className="grid-5" aria-label="The five questions">
        {QUESTIONS.map((q) => (
          <button key={q.key} className="card tile" onClick={() => go(q.key === "rules" ? "rules" : q.key === "how" ? "how" : "questions")}>
            <span className="q">{q.label}</span>
            <span className="big">{s.questions[q.key]}</span>
            <span className="small muted">confirmed · {q.hint}</span>
          </button>
        ))}
      </section>
      <div className="vs">
        <section className="card">
          <h2>Needs attention</h2>
          {!attention.length && <p className="muted">Nothing waiting. Add evidence or run an engine.</p>}
          {attention.map((a) => (
            <button key={a.text} className="btn link" style={{ textAlign: "left" }} onClick={() => go(a.to)}>
              <Chip tone={a.tone}>{a.n}</Chip> {a.text}
            </button>
          ))}
          {s.assumptions > 0 && <p className="small muted">{s.assumptions} confirmed items have no client evidence and are carried as assumptions.</p>}
        </section>
        <section className="card">
          <h2>Open items by resolution</h2>
          <div className="row">
            {OUTCOMES.map((o) => <Chip key={o.key}>{o.label}: {s.openItemsByOutcome[o.key]}</Chip>)}
          </div>
          <h2 style={{ marginTop: 8 }}>Team</h2>
          <div className="row">
            {(eng?.members ?? []).map((m: any) => <Chip key={m.userId} tone={m.role === "owner" ? "accent" : undefined}>{nameOf(m.userId)} · {m.role === "owner" ? "solution owner" : m.role}</Chip>)}
          </div>
          {budget && <p className="small muted">Model spend ${budget.spentUsd.toFixed(2)} of ${budget.budgetUsd} cap</p>}
        </section>
      </div>
      <section className="card">
        <h2>Recent engine runs</h2>
        {!runs?.length && <p className="muted">No engine has run yet.</p>}
        {(runs ?? []).map((r) => (
          <p key={r.id} className="small"><Chip>{r.engine.replace("engine-", "")}</Chip> {r.outputSummary} <span className="prov">· {r.promptVersion} · {r.model || "no model"} · {fmt(r.startedAt)}</span></p>
        ))}
      </section>
    </>
  );
}

const STAKE_TYPES = ["sponsor", "owner", "champion", "approver", "blocker", "influencer"];

export function People() {
  const { eid, eng, role, nameOf, me } = useApp();
  const { data: people } = useLoad<any[]>(`/engagements/${eid}/stakeholders`);
  const { run, busy } = useAction();
  const [nominate, setNominate] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  const [pick, setPick] = useState<{ id: string; role: string } | null>(null);
  const [edit, setEdit] = useState<any | null>(null);
  const search = async (v: string) => { setQ(v); setHits(await api(`/directory?q=${encodeURIComponent(v)}`)); };
  const saveStake = async () => {
    const body = { name: edit.name, title: edit.title ?? "", organisation: edit.organisation ?? "", type: edit.type ?? "", decides: edit.decides ?? "", status: "confirmed" };
    await run("stake", () => edit.id ? api(`/engagements/${eid}/stakeholders/${edit.id}`, { method: "PATCH", body }) : api(`/engagements/${eid}/stakeholders`, { body }), "Saved.");
    setEdit(null);
  };
  return (
    <>
      <section className="card">
        <div className="spread">
          <h2>Our team</h2>
          {can.own(role) && <button className="btn primary" onClick={() => { setNominate(true); search(""); }}>Nominate a participant</button>}
        </div>
        <div className="table-wrap"><table>
          <thead><tr><th>Person</th><th>Role</th><th>What they can do</th><th /></tr></thead>
          <tbody>
            {(eng?.members ?? []).map((m: any) => (
              <tr key={m.userId}>
                <td>{nameOf(m.userId)}</td>
                <td>
                  {can.own(role) && m.role !== "owner" ? (
                    <select aria-label="Role" value={m.role} onChange={(e) => run("role", () => api(`/engagements/${eid}/members/${m.userId}`, { method: "PUT", body: { role: e.target.value } }), "Role changed.")}>
                      <option value="contributor">contributor</option><option value="reviewer">reviewer</option><option value="observer">observer</option><option value="owner">solution owner (hand over)</option>
                    </select>
                  ) : <Chip tone={m.role === "owner" ? "accent" : undefined}>{m.role === "owner" ? "solution owner" : m.role}</Chip>}
                </td>
                <td className="small muted">{{ owner: "Everything: nominates people, resolves contradictions, decides escalations, signs off", contributor: "Adds evidence, runs engines, confirms claims, drafts recommendations", reviewer: "Approves recommendations, comments through decisions and open items", observer: "Reads everything, changes nothing" }[m.role as string]}</td>
                <td>{can.own(role) && m.userId !== me?.id && m.role !== "owner" && <button className="btn danger" onClick={() => run("rm", () => api(`/engagements/${eid}/members/${m.userId}`, { method: "DELETE" }), "Removed.")}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </section>
      <section className="card">
        <div className="spread">
          <h2>Client stakeholders</h2>
          {can.contribute(role) && <button className="btn" onClick={() => setEdit({ name: "" })}>Add stakeholder</button>}
        </div>
        {people && !people.length && <Empty>No stakeholders yet. Extraction proposes the people named in your sources.</Empty>}
        {!!people?.length && <div className="table-wrap"><table>
          <thead><tr><th>Name</th><th>Title</th><th>Type</th><th>Decides</th><th>Status</th><th /></tr></thead>
          <tbody>{people.map((p) => (
            <tr key={p.id}>
              <td className="statement">{p.name}</td><td>{p.title}{p.organisation ? `, ${p.organisation}` : ""}</td>
              <td>{p.type && <Chip>{p.type}</Chip>}</td><td className="small">{p.decides}</td>
              <td>{p.status === "proposed" ? <Chip tone="accent">proposed by extraction</Chip> : <Chip tone="ok">confirmed</Chip>}</td>
              <td>{can.contribute(role) && <button className="btn" onClick={() => setEdit(p)}>{p.status === "proposed" ? "Review" : "Edit"}</button>}</td>
            </tr>))}
          </tbody>
        </table></div>}
      </section>
      {nominate && (
        <Modal title="Nominate a participant" onClose={() => { setNominate(false); setPick(null); }}
          footer={<button className="btn primary" disabled={!pick || !!busy} onClick={async () => { await run("nom", () => api(`/engagements/${eid}/members/${pick!.id}`, { method: "PUT", body: { role: pick!.role } }), "Nominated."); setNominate(false); setPick(null); }}>Nominate</button>}>
          <label>Search your organisation<input id="nom-q" value={q} onChange={(e) => search(e.target.value)} placeholder="Name or email" /></label>
          <div className="stack">{hits.map((h) => (
            <button key={h.id} className={`btn${pick?.id === h.id ? " primary" : ""}`} style={{ textAlign: "left" }} onClick={() => setPick({ id: h.id, role: pick?.role ?? "contributor" })}>{h.name} <span className="small">{h.email}</span></button>
          ))}</div>
          {pick && <label>Role<select id="nom-role" value={pick.role} onChange={(e) => setPick({ ...pick, role: e.target.value })}>
            <option value="contributor">Contributor</option><option value="reviewer">Reviewer</option><option value="observer">Observer</option></select></label>}
        </Modal>
      )}
      {edit && (
        <Modal title={edit.id ? "Stakeholder" : "Add stakeholder"} onClose={() => setEdit(null)}
          footer={<>{edit.id && <button className="btn danger" onClick={async () => { await run("del", () => api(`/engagements/${eid}/stakeholders/${edit.id}`, { method: "DELETE" }), "Removed."); setEdit(null); }}>Delete</button>}
            <button className="btn primary" disabled={!edit.name} onClick={saveStake}>{edit.status === "proposed" ? "Confirm" : "Save"}</button></>}>
          <label>Name<input id="st-name" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
          <label>Title<input id="st-title" value={edit.title ?? ""} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></label>
          <label>Organisation<input id="st-org" value={edit.organisation ?? ""} onChange={(e) => setEdit({ ...edit, organisation: e.target.value })} /></label>
          <label>Type<select id="st-type" value={edit.type ?? ""} onChange={(e) => setEdit({ ...edit, type: e.target.value })}><option value="">Not known</option>{STAKE_TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
          <label>What they decide<input id="st-decides" value={edit.decides ?? ""} onChange={(e) => setEdit({ ...edit, decides: e.target.value })} /></label>
        </Modal>
      )}
    </>
  );
}
