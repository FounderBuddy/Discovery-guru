import { useMemo, useState } from "react";
import { api } from "../api";
import { can, Chip, Empty, fmt, Modal, OUTCOMES, PATHS, useAction, useApp, useLoad } from "../ui";

function useClaims() {
  const { eid } = useApp();
  const { data } = useLoad<any[]>(`/engagements/${eid}/claims`);
  const { data: sources } = useLoad<any[]>(`/engagements/${eid}/sources`);
  const smap = useMemo(() => Object.fromEntries((sources ?? []).map((s) => [s.id, s.title])), [sources]);
  return { map: useMemo(() => Object.fromEntries((data ?? []).map((c) => [c.id, c])), [data]), smap };
}

export function Divergences() {
  const { eid, role, nameOf, openLineage } = useApp();
  const { data } = useLoad<any[]>(`/engagements/${eid}/divergences`);
  const { map, smap } = useClaims();
  const { run, busy } = useAction();
  const open = (data ?? []).filter((d) => d.status === "open");
  const done = (data ?? []).filter((d) => d.status !== "open");
  const side = (id: string, label: string) => {
    const c = map[id];
    if (!c) return null;
    return (
      <div className="card" style={{ gap: 6 }}>
        <div className="spread"><Chip>{label}</Chip><button className="btn link" onClick={() => openLineage("claim", id)}>Lineage</button></div>
        <p className="statement">{c.statement}</p>
        {c.quote && <blockquote className="quote" style={{ margin: 0 }}>“{c.quote}”</blockquote>}
        <p className="prov">{c.speaker ? `${c.speaker} · ` : ""}{smap[c.sourceId] ?? "entered by a person"}{c.status === "superseded" ? " · superseded" : ""}</p>
      </div>
    );
  };
  const resolve = (d: any, resolution: string, note = "") =>
    run("res", () => api(`/engagements/${eid}/divergences/${d.id}/resolve`, { body: { resolution, note } }),
      (r: any) => r.openItemId ? "Sent to the client as a pending decision." : r.flaggedForReview ? "Resolved. The losing claim is superseded and its dependents flagged." : "Resolved.");
  return (
    <>
      <div className="spread">
        <p className="muted">Where what was said, what was written and what we assumed disagree. Only the solution owner resolves them; the losing claim is superseded, never deleted.</p>
        {can.contribute(role) && <button className="btn primary" disabled={!!busy} onClick={() => run("run", () => api(`/engagements/${eid}/run/divergence`, { method: "POST" }), (r: any) => r.summary)}>{busy === "run" ? "Comparing…" : "Check for contradictions"}</button>}
      </div>
      {data && !open.length && <Empty>No open contradictions.</Empty>}
      {open.map((d) => (
        <section key={d.id} className={`card ${d.severity === "high" ? "crit" : "warn"}`}>
          <div className="row"><Chip tone={d.severity === "high" ? "crit" : "warn"}>{d.severity}</Chip><Chip>{d.type.replace("_", " ")}</Chip>{d.origin === "agent" && <span className="prov">found by the divergence engine</span>}</div>
          <p className="statement">{d.explanation}</p>
          <div className="vs">{side(d.claimA, "A")}{side(d.claimB, "B")}</div>
          {can.own(role) ? (
            <div className="row">
              <button className="btn" onClick={() => resolve(d, "a_correct")}>A is right</button>
              <button className="btn" onClick={() => resolve(d, "b_correct")}>B is right</button>
              <button className="btn" onClick={() => resolve(d, "both_stand", "Not a real conflict.")}>Both stand</button>
              <button className="btn primary" onClick={() => resolve(d, "ask_client")}>Ask the client</button>
            </div>
          ) : <p className="small muted">Waiting for the solution owner.</p>}
        </section>
      ))}
      {!!done.length && <details><summary className="muted">{done.length} resolved</summary>
        <div className="stack" style={{ marginTop: 12 }}>{done.map((d) => (
          <p key={d.id} className="small"><Chip tone={d.status === "with_client" ? "accent" : "ok"}>{d.status === "with_client" ? "with the client" : d.resolution.replace("_", " ")}</Chip> {d.explanation} <span className="prov">· {nameOf(d.resolvedBy)} · {fmt(d.resolvedAt)}</span></p>
        ))}</div></details>}
    </>
  );
}

export function OpenItems() {
  const { eid, eng, role, nameOf, openLineage } = useApp();
  const { data } = useLoad<any[]>(`/engagements/${eid}/open-items`);
  const { run, busy } = useAction();
  const [edit, setEdit] = useState<any | null>(null);
  const open = (data ?? []).filter((o) => o.status === "open");
  const closed = (data ?? []).filter((o) => o.status !== "open");
  const members = eng?.members ?? [];
  const save = async () => {
    const body: any = { question: edit.question, kind: edit.kind, outcome: edit.outcome, ownerId: edit.ownerId || null, dueDate: edit.dueDate || null,
      impact: edit.impact ?? "", defaultIfSilent: edit.defaultIfSilent ?? "", askRole: edit.askRole ?? "", status: edit.status, answer: edit.answer ?? "", needsReview: false };
    if (!edit.id) { delete body.status; delete body.answer; delete body.needsReview; }
    await run("save", () => edit.id ? api(`/engagements/${eid}/open-items/${edit.id}`, { method: "PATCH", body }) : api(`/engagements/${eid}/open-items`, { body }), "Saved.");
    setEdit(null);
  };
  return (
    <>
      <div className="spread">
        <p className="muted">Every unanswered question resolves one of five ways, and each has an owner and a deadline. Past the deadline it escalates: item owner, then solution owner, then the client sponsor.</p>
        <div className="row">
          {can.contribute(role) && <button className="btn" onClick={() => setEdit({ question: "", kind: "number", outcome: "unclassified", status: "open" })}>Add an item</button>}
          {can.contribute(role) && <button className="btn primary" disabled={!!busy} onClick={() => run("q", () => api(`/engagements/${eid}/run/questions`, { method: "POST" }), (r: any) => r.summary)}>{busy === "q" ? "Thinking…" : "Suggest questions"}</button>}
          {can.own(role) && <button className="btn" onClick={() => run("age", () => api(`/engagements/${eid}/run/resolution`, { method: "POST" }), (r: any) => r.summary)}>Escalate overdue</button>}
        </div>
      </div>
      {data && !open.length && <Empty>No open questions. Suggest some from what's known, or add your own.</Empty>}
      <div className="board">
        {OUTCOMES.map((o) => (
          <div key={o.key} className="col">
            <h3>{o.label}</h3><p className="hint">{o.hint}</p>
            {open.filter((x) => x.outcome === o.key).map((x) => (
              <button key={x.id} className={`card${x.overdue ? " crit" : x.needsReview ? " warn" : ""}`} style={{ textAlign: "left", cursor: "pointer" }} onClick={() => setEdit({ ...x })}>
                <p className="statement small">{x.question}</p>
                <div className="row">
                  <Chip>{x.kind}</Chip>
                  {x.overdue && <Chip tone="crit">overdue</Chip>}
                  {x.escalationLevel > 0 && <Chip tone="warn">escalated to {["", "solution owner", "client sponsor"][x.escalationLevel]}</Chip>}
                  {x.needsReview && <Chip tone="warn">rests on a superseded claim</Chip>}
                </div>
                <p className="prov">{nameOf(x.ownerId)}{x.dueDate ? ` · due ${x.dueDate}` : ""}{x.origin === "agent" ? " · suggested" : ""}</p>
              </button>
            ))}
          </div>
        ))}
      </div>
      {!!closed.length && <details><summary className="muted">{closed.length} answered or closed</summary>
        <div className="stack" style={{ marginTop: 12 }}>{closed.map((x) => <p key={x.id} className="small"><Chip tone="ok">{x.status}</Chip> {x.question} <span className="muted">{x.answer}</span></p>)}</div></details>}
      {edit && (
        <Modal title={edit.id ? "Open item" : "Add an open item"} onClose={() => setEdit(null)}
          footer={<>{edit.id && <button className="btn link" onClick={() => openLineage("openitem", edit.id)}>Lineage</button>}
            {edit.id && can.own(role) && <button className="btn danger" onClick={async () => { await run("d", () => api(`/engagements/${eid}/open-items/${edit.id}`, { method: "DELETE" }), "Deleted."); setEdit(null); }}>Delete</button>}
            {can.contribute(role) || can.review(role) ? <button className="btn primary" disabled={!edit.question} onClick={save}>Save</button> : null}</>}>
          <label>Question<textarea id="oi-q" value={edit.question} onChange={(e) => setEdit({ ...edit, question: e.target.value })} style={{ minHeight: 70 }} /></label>
          <div className="row">
            <label style={{ flex: 1 }}>It demands<select id="oi-k" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}>{["number", "constraint", "owner", "decision", "artefact"].map((k) => <option key={k}>{k}</option>)}</select></label>
            <label style={{ flex: 1 }}>If unanswered<select id="oi-o" value={edit.outcome} onChange={(e) => setEdit({ ...edit, outcome: e.target.value })}>{OUTCOMES.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select></label>
          </div>
          <div className="row">
            <label style={{ flex: 1 }}>Owner<select id="oi-own" value={edit.ownerId ?? ""} onChange={(e) => setEdit({ ...edit, ownerId: e.target.value })}><option value="">Unassigned</option>{members.map((m: any) => <option key={m.userId} value={m.userId}>{nameOf(m.userId)}</option>)}</select></label>
            <label style={{ flex: 1 }}>Deadline<input id="oi-due" type="date" value={edit.dueDate ?? ""} onChange={(e) => setEdit({ ...edit, dueDate: e.target.value })} /></label>
          </div>
          <label>Who should answer at the client<input id="oi-ask" value={edit.askRole ?? ""} onChange={(e) => setEdit({ ...edit, askRole: e.target.value })} /></label>
          <label>What it affects if unanswered<input id="oi-imp" value={edit.impact ?? ""} onChange={(e) => setEdit({ ...edit, impact: e.target.value })} /></label>
          {edit.outcome === "pending" && <label>Default if the client stays silent<input id="oi-def" value={edit.defaultIfSilent ?? ""} onChange={(e) => setEdit({ ...edit, defaultIfSilent: e.target.value })} /></label>}
          {edit.id && <><label>Answer<textarea id="oi-ans" value={edit.answer ?? ""} onChange={(e) => setEdit({ ...edit, answer: e.target.value })} style={{ minHeight: 70 }} /></label>
            <label>Status<select id="oi-st" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}><option value="open">Open</option><option value="answered">Answered</option><option value="closed">Closed</option></select></label></>}
        </Modal>
      )}
    </>
  );
}

export function How() {
  const { eid, eng, role, nameOf, openLineage } = useApp();
  const { data: recs } = useLoad<any[]>(`/engagements/${eid}/recommendations`);
  const { data: practices } = useLoad<any[]>(`/practices`);
  const { data: brief } = useLoad<any>(`/engagements/${eid}/brief`);
  const paths: string[] = Array.from(new Set((brief?.packs ?? []).flatMap((x: any) => x.paths ?? []))).sort((a, b) => (a as string).localeCompare(b as string)) as string[];
  const { run, busy } = useAction();
  const [profile, setProfile] = useState<any | null>(null);
  const [path, setPath] = useState("");
  const [custom, setCustom] = useState("");
  const pmap = Object.fromEntries((practices ?? []).map((p) => [p.id, p]));
  const p = eng?.profile ?? {};
  const LEVEL: Record<string, string> = { practice: "Datatonic practice", best_practice: "Best practice", client_enforced: "Client-enforced" };
  return (
    <>
      <section className="card">
        <div className="spread"><h2>Audience</h2>{can.contribute(role) && <button className="btn" onClick={() => setProfile({ market: p.market ?? "", audiences: p.audiences ?? "", notes: p.notes ?? "" })}>Edit</button>}</div>
        <div className="row">
          <Chip>{p.market ? { b2b: "B2B", b2c: "B2C", both: "B2B and B2C" }[p.market as string] : "Market: not set"}</Chip>
          {p.audiences && <Chip>Audiences: {p.audiences}</Chip>}
        </div>
        {p.notes && <p className="small">{p.notes}</p>}
      </section>
      <section className="card">
        <h2>Draft a recommendation</h2>
        <p className="small muted">Recommendations serve the Brief. Datatonic practices for this engagement's shape come first, then best practice, with hard client rules respected. Any conflict between a practice and a client rule goes to the solution owner as a decision; the engine never picks a side.</p>
        {can.contribute(role) && <div className="row">
          <select aria-label="Path" value={path || paths[0] || "custom"} onChange={(e) => setPath(e.target.value)}>{paths.map((x) => <option key={x}>{x}</option>)}<option value="custom">Another path…</option></select>
          {(path || paths[0] || "custom") === "custom" && <input aria-label="Custom path" value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Name the path" />}
          <button className="btn primary" disabled={!!busy || ((path || paths[0] || "custom") === "custom" && !custom)} onClick={() => run("rec", () => api(`/engagements/${eid}/run/recommendation`, { body: { path: (path || paths[0] || "custom") === "custom" ? custom : (path || paths[0]) } }), (r: any) => r.summary)}>{busy === "rec" ? "Drafting…" : "Draft"}</button>
        </div>}
      </section>
      {recs && !recs.length && <Empty>No recommendations yet.</Empty>}
      {(recs ?? []).map((r) => (
        <section key={r.id} className={`card${r.status === "blocked" ? " crit" : r.status === "needs_review" ? " warn" : ""}`}>
          <div className="spread">
            <div className="row"><h2>{r.path}</h2><Chip tone="accent">{LEVEL[r.level] ?? r.level}</Chip>
              <Chip tone={r.status === "approved" ? "ok" : r.status === "blocked" ? "crit" : r.status === "needs_review" ? "warn" : undefined}>{r.status.replace("_", " ")}</Chip></div>
            <div className="row">
              <button className="btn link" onClick={() => openLineage("recommendation", r.id)}>Lineage</button>
              {can.review(role) && r.status !== "approved" && <button className="btn primary" disabled={r.status === "blocked"} title={r.status === "blocked" ? "Waiting on escalated decisions" : ""} onClick={() => run("ap", () => api(`/engagements/${eid}/recommendations/${r.id}/approve`, { method: "POST" }), "Approved.")}>Approve</button>}
            </div>
          </div>
          <p className="statement">{r.recommendation}</p>
          {r.reviewReason && <p className="small"><Chip tone="warn">review</Chip> {r.reviewReason}</p>}
          {r.rationale && <p className="small">{r.rationale}</p>}
          {!!r.practiceIds?.length && <div className="row small">Practices: {r.practiceIds.map((id: string) => <Chip key={id}>{pmap[id]?.title ?? id}</Chip>)}</div>}
          {!!r.alternatives?.length && <div className="small"><strong>Considered and not chosen:</strong> {r.alternatives.map((a: any) => `${a.option} (${a.whyNot})`).join("; ")}</div>}
          {!!r.assumptions?.length && <div className="small"><strong>Assumptions:</strong> {r.assumptions.join("; ")}</div>}
          {!!r.conflicts?.length && <div className="small"><Chip tone="crit">{r.conflicts.length} conflict(s) escalated</Chip> {r.conflicts.map((c: any) => c.description).join(" · ")}</div>}
          <p className="prov">{r.promptVersion} · {r.model} · requested by {nameOf(r.requestedBy)} · {fmt(r.createdAt)}{r.approvedBy ? ` · approved by ${nameOf(r.approvedBy)}` : ""}</p>
        </section>
      ))}
      {profile && (
        <Modal title="Audience" onClose={() => setProfile(null)}
          footer={<button className="btn primary" onClick={async () => { await run("p", () => api(`/engagements/${eid}/profile`, { method: "PUT", body: profile }), "Saved."); setProfile(null); }}>Save</button>}>
          <label>Market<select id="pf-m" value={profile.market} onChange={(e) => setProfile({ ...profile, market: e.target.value })}><option value="">Not known</option><option value="b2b">B2B</option><option value="b2c">B2C</option><option value="both">Both</option></select></label>
          <label>Target audiences<input id="pf-a" value={profile.audiences} onChange={(e) => setProfile({ ...profile, audiences: e.target.value })} placeholder="e.g. internal finance and sales; enterprise customers through a portal" /></label>
          <label>Notes<textarea id="pf-n" value={profile.notes} onChange={(e) => setProfile({ ...profile, notes: e.target.value })} /></label>
        </Modal>
      )}
    </>
  );
}

export function Decisions() {
  const { eid, role, nameOf, openLineage } = useApp();
  const { data } = useLoad<any[]>(`/engagements/${eid}/decisions`);
  const { run } = useAction();
  const [deciding, setDeciding] = useState<any | null>(null);
  const [adding, setAdding] = useState<any | null>(null);
  const escalated = (data ?? []).filter((d) => d.status === "escalated");
  const log = (data ?? []).filter((d) => d.status !== "escalated");
  return (
    <>
      <div className="spread">
        <p className="muted">Our decisions and the client's, in one log. Conflicts between a Datatonic practice and a client rule arrive here escalated; only the solution owner decides them.</p>
        {(can.contribute(role) || can.review(role)) && <button className="btn" onClick={() => setAdding({ title: "", source: "client", status: "accepted", context: "", decision: "", rationale: "" })}>Record a decision</button>}
      </div>
      {escalated.map((d) => (
        <section key={d.id} className="card crit">
          <div className="row"><Chip tone="crit">escalated</Chip><h2>ADR-{String(d.number).padStart(3, "0")} {d.title}</h2></div>
          <p className="small" style={{ whiteSpace: "pre-wrap" }}>{d.context}</p>
          <ul className="small">{(d.options ?? []).map((o: string) => <li key={o}>{o}</li>)}</ul>
          <div className="row">
            {can.own(role) ? <button className="btn primary" onClick={() => setDeciding({ d, decision: d.options?.[0] ?? "", rationale: "" })}>Decide</button> : <p className="small muted">Waiting for the solution owner.</p>}
            <button className="btn link" onClick={() => openLineage("decision", d.id)}>Lineage</button>
          </div>
        </section>
      ))}
      {data && !data.length && <Empty>No decisions recorded yet.</Empty>}
      {!!log.length && <section className="card"><div className="table-wrap"><table>
        <thead><tr><th>ADR</th><th>Decision</th><th>Whose</th><th>Status</th><th>Decided</th><th /></tr></thead>
        <tbody>{log.map((d) => (
          <tr key={d.id}>
            <td className="prov">ADR-{String(d.number).padStart(3, "0")}</td>
            <td><p className="statement">{d.title}</p>{d.decision && <p className="small">{d.decision}</p>}{d.needsReview && <Chip tone="warn">rests on a superseded claim</Chip>}</td>
            <td><Chip>{d.source === "client" ? "client" : "ours"}</Chip></td>
            <td><Chip tone={d.status === "accepted" ? "ok" : undefined}>{d.status}</Chip></td>
            <td className="small">{d.decidedBy ? `${nameOf(d.decidedBy)}, ${fmt(d.decidedAt)}` : ""}</td>
            <td><button className="btn link" onClick={() => openLineage("decision", d.id)}>Lineage</button></td>
          </tr>))}
        </tbody></table></div></section>}
      {deciding && (
        <Modal title="Decide" onClose={() => setDeciding(null)}
          footer={<button className="btn primary" disabled={!deciding.rationale} onClick={async () => { await run("dec", () => api(`/engagements/${eid}/decisions/${deciding.d.id}/decide`, { body: { decision: deciding.decision, rationale: deciding.rationale } }), "Decided. The recommendation can now be reviewed."); setDeciding(null); }}>Record decision</button>}>
          <fieldset className="stack" style={{ border: 0, padding: 0 }}><legend className="small">Choose</legend>
            {(deciding.d.options ?? []).map((o: string) => (
              <label key={o} className="row" style={{ flexDirection: "row", fontWeight: 400 }}><input type="radio" name="opt" checked={deciding.decision === o} onChange={() => setDeciding({ ...deciding, decision: o })} />{o}</label>
            ))}
          </fieldset>
          <label>Rationale<textarea id="dec-r" value={deciding.rationale} onChange={(e) => setDeciding({ ...deciding, rationale: e.target.value })} placeholder="Why. This becomes the ADR." /></label>
        </Modal>
      )}
      {adding && (
        <Modal title="Record a decision" onClose={() => setAdding(null)}
          footer={<button className="btn primary" disabled={!adding.title} onClick={async () => { await run("add", () => api(`/engagements/${eid}/decisions`, { body: adding }), "Recorded."); setAdding(null); }}>Record</button>}>
          <label>Title<input id="ad-t" value={adding.title} onChange={(e) => setAdding({ ...adding, title: e.target.value })} placeholder="e.g. VM migration deferred to next year" /></label>
          <div className="row">
            <label style={{ flex: 1 }}>Whose decision<select id="ad-s" value={adding.source} onChange={(e) => setAdding({ ...adding, source: e.target.value })}><option value="client">The client's</option><option value="ours">Ours</option></select></label>
            <label style={{ flex: 1 }}>Status<select id="ad-st" value={adding.status} onChange={(e) => setAdding({ ...adding, status: e.target.value })}><option value="accepted">Accepted</option><option value="proposed">Proposed</option></select></label>
          </div>
          <label>Context<textarea id="ad-c" value={adding.context} onChange={(e) => setAdding({ ...adding, context: e.target.value })} style={{ minHeight: 70 }} /></label>
          <label>Decision<textarea id="ad-d" value={adding.decision} onChange={(e) => setAdding({ ...adding, decision: e.target.value })} style={{ minHeight: 70 }} /></label>
          <label>Rationale<textarea id="ad-r" value={adding.rationale} onChange={(e) => setAdding({ ...adding, rationale: e.target.value })} style={{ minHeight: 70 }} /></label>
        </Modal>
      )}
    </>
  );
}
