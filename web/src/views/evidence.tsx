import { useMemo, useState } from "react";
import { api } from "../api";
import { can, Chip, Empty, fmt, KIND_LABEL, Modal, Provenance, Q_LABEL, useAction, useApp, useLoad } from "../ui";

const KINDS = ["transcript", "architecture", "policy", "inventory", "proposal", "other"];
const RULE_CATS: Record<string, string> = {
  regulatory: "Regulatory and compliance", security: "Security policy", strategic: "Strategic positions",
  architecture: "Architectural standards", organisational: "Organisational and governance", commercial: "Commercial", operational: "Operational",
};

function useSources() {
  const { eid } = useApp();
  const { data } = useLoad<any[]>(`/engagements/${eid}/sources`);
  const map = useMemo(() => Object.fromEntries((data ?? []).map((s) => [s.id, s.title])), [data]);
  return { list: data, map };
}

function ClaimCard({ c, sources, actions }: { c: any; sources: Record<string, string>; actions?: React.ReactNode }) {
  const { openLineage } = useApp();
  return (
    <article className={`card${c.needsReview ? " warn" : ""}`}>
      <div className="spread">
        <div className="row">
          <Chip q>{Q_LABEL[c.question] ?? c.question}</Chip>
          <Chip>{KIND_LABEL[c.kind] ?? c.kind}</Chip>
          {c.number && <Chip tone="accent">{c.number.value} {c.number.unit}</Chip>}
          {c.strength && <Chip tone={c.strength === "hard" ? "crit" : undefined}>{c.strength} rule</Chip>}
          {c.status === "proposed" && <Chip tone={c.confidence === "high" ? "ok" : c.confidence === "low" ? "warn" : undefined}>{c.confidence} confidence</Chip>}
          {c.evidence === "none" && <Chip tone="warn">Assumption: no client evidence</Chip>}
          {c.question === "why" && ["driver", "pain_point"].includes(c.kind) && !c.number && <Chip tone="warn">No baseline number yet</Chip>}
        </div>
        <button className="btn link" onClick={() => openLineage("claim", c.id)}>Lineage</button>
      </div>
      <p className="statement">{c.statement}</p>
      {c.quote && <blockquote className="quote" style={{ margin: 0 }}>“{c.quote}” {c.status === "proposed" && <Chip tone="ok">citation verified</Chip>}</blockquote>}
      <Provenance c={c} sources={sources} />
      {actions && <div className="row">{actions}</div>}
    </article>
  );
}

export function Evidence() {
  const { eid, role } = useApp();
  const { list: sources, map } = useSources();
  const { data: proposed } = useLoad<any[]>(`/engagements/${eid}/claims?status=proposed`);
  const { data: runs } = useLoad<any[]>(`/engagements/${eid}/runs?limit=20`);
  const { run, busy } = useAction();
  const [adding, setAdding] = useState<null | "paste" | "upload">(null);
  const [f, setF] = useState<any>({ title: "", kind: "transcript", ruleCategory: "", text: "" });
  const [file, setFile] = useState<File | null>(null);
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<any | null>(null);
  const contributor = can.contribute(role);

  const add = async () => {
    const r = adding === "upload" && file
      ? await run("add", () => { const fd = new FormData(); fd.append("file", file); fd.append("title", f.title || file.name); fd.append("kind", f.kind); fd.append("ruleCategory", f.ruleCategory); return api(`/engagements/${eid}/sources/upload`, { form: fd }); }, "Source stored. Run extraction when ready.")
      : await run("add", () => api(`/engagements/${eid}/sources/text`, { body: { title: f.title, kind: f.kind, ruleCategory: f.ruleCategory || null, text: f.text } }), "Source stored. Run extraction when ready.");
    if (r) { setAdding(null); setF({ title: "", kind: "transcript", ruleCategory: "", text: "" }); setFile(null); }
  };
  const queue = (proposed ?? []).filter((c) => !filter || c.sourceId === filter);
  const discarded = (runs ?? []).filter((r) => r.engine === "engine-extraction" && r.rejectedSamples?.length);

  return (
    <>
      <section className="card">
        <div className="spread">
          <h2>Sources</h2>
          {contributor && <div className="row"><button className="btn" onClick={() => setAdding("paste")}>Paste text</button><button className="btn primary" onClick={() => setAdding("upload")}>Upload a file</button></div>}
        </div>
        <p className="small muted">Transcripts, architecture documents, policies and inventories. Text is read from .txt, .md, .vtt, .srt, .csv, .pdf and .docx.</p>
        {sources && !sources.length && <Empty>No evidence yet. Add a transcript or a document to start.</Empty>}
        {!!sources?.length && <div className="table-wrap"><table>
          <thead><tr><th>Source</th><th>Type</th><th>Size</th><th>Status</th><th>Added</th><th /></tr></thead>
          <tbody>{sources.map((s) => (
            <tr key={s.id}>
              <td className="statement">{s.title}{s.ruleCategory && <div className="small muted">for {RULE_CATS[s.ruleCategory]}</div>}</td>
              <td><Chip>{s.kind}</Chip></td>
              <td className="small">{s.chars.toLocaleString()} chars</td>
              <td>{s.status === "extracted" ? <Chip tone="ok">{s.proposedClaims} proposed · {s.rejectedClaims} discarded</Chip> : s.status === "failed" ? <Chip tone="crit">extraction failed</Chip> : s.status === "extracting" ? <Chip tone="accent">extracting…</Chip> : <Chip>not extracted</Chip>}</td>
              <td className="small">{fmt(s.createdAt)}</td>
              <td className="row">
                {contributor && <button className="btn" disabled={!!busy} onClick={() => run(`x${s.id}`, () => api(`/engagements/${eid}/run/extraction`, { body: { sourceId: s.id } }), (r: any) => r.summary)}>{busy === `x${s.id}` ? "Extracting…" : s.status === "extracted" ? "Extract again" : "Extract claims"}</button>}
                {can.own(role) && <button className="btn danger" onClick={() => run("del", () => api(`/engagements/${eid}/sources/${s.id}`, { method: "DELETE" }), "Source deleted.")}>Delete</button>}
              </td>
            </tr>))}
          </tbody>
        </table></div>}
      </section>

      <section className="stack">
        <div className="spread">
          <h2>Review queue · {queue.length} proposed</h2>
          <div className="row">
            <select aria-label="Filter by source" value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">All sources</option>{(sources ?? []).map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
            {contributor && !!queue.length && <button className="btn primary" onClick={() => run("bulk", () => api(`/engagements/${eid}/claims/confirm-high${filter ? `?sourceId=${filter}` : ""}`, { method: "POST" }), (r: any) => `${r.confirmed} high-confidence claims confirmed.`)}>Confirm all high confidence</button>}
          </div>
        </div>
        <p className="small muted">Agents propose; people confirm. Every proposal below quotes its source word for word, checked by the engine and again by the ledger.</p>
        {proposed && !queue.length && <Empty>Nothing waiting for review.</Empty>}
        {queue.map((c) => (
          <ClaimCard key={c.id} c={c} sources={map} actions={contributor && <>
            <button className="btn primary" onClick={() => run("c", () => api(`/engagements/${eid}/claims/${c.id}/confirm`, { body: {} }))}>Confirm</button>
            <button className="btn" onClick={() => setEditing({ ...c })}>Edit, then confirm</button>
            <button className="btn danger" onClick={() => run("r", () => api(`/engagements/${eid}/claims/${c.id}/reject`, { body: { reason: "Not relevant" } }))}>Reject</button>
          </>} />
        ))}
      </section>

      {!!discarded.length && <section className="card">
        <h2>Discarded by the citation check</h2>
        <p className="small muted">The engine proposed these, but the quote was not in the source, so they never reached the ledger.</p>
        {discarded.slice(0, 3).flatMap((r) => r.rejectedSamples.slice(0, 5).map((x: any, i: number) => (
          <p key={r.id + i} className="small"><Chip tone="crit">discarded</Chip> {x.statement} <span className="prov">· {x.reason}</span></p>
        )))}
      </section>}

      {adding && (
        <Modal title={adding === "upload" ? "Upload a source" : "Paste a source"} onClose={() => setAdding(null)}
          footer={<button className="btn primary" disabled={!!busy || (adding === "upload" ? !file : !f.text || !f.title)} onClick={add}>{busy ? "Storing…" : "Store source"}</button>}>
          {adding === "upload" && <label>File<input id="src-file" type="file" accept=".txt,.md,.vtt,.srt,.csv,.json,.pdf,.docx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>}
          <label>Title<input id="src-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder={adding === "upload" ? "Defaults to the file name" : "e.g. Discovery call 1"} /></label>
          <div className="row">
            <label style={{ flex: 1 }}>Type<select id="src-kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select></label>
            <label style={{ flex: 1 }}>Evidence for a guardrail<select id="src-rule" value={f.ruleCategory} onChange={(e) => setF({ ...f, ruleCategory: e.target.value })}><option value="">None</option>{Object.entries(RULE_CATS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          </div>
          {adding === "paste" && <label>Text<textarea id="src-text" value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} style={{ minHeight: 220 }} /></label>}
        </Modal>
      )}
      {editing && <ClaimEditor claim={editing} onClose={() => setEditing(null)} confirmAfter />}
    </>
  );
}

function ClaimEditor({ claim, onClose, confirmAfter }: { claim: any; onClose: () => void; confirmAfter?: boolean }) {
  const { eid } = useApp();
  const { run } = useAction();
  const [c, setC] = useState(claim);
  const save = async () => {
    const body: any = { statement: c.statement, question: c.question, kind: c.kind };
    if (c.kind === "guardrail") { body.ruleCategory = c.ruleCategory || "architecture"; if (c.strength) body.strength = c.strength; }
    await run("edit", async () => {
      await api(`/engagements/${eid}/claims/${c.id}`, { method: "PATCH", body });
      if (confirmAfter) await api(`/engagements/${eid}/claims/${c.id}/confirm`, { body: {} });
    }, confirmAfter ? "Edited and confirmed." : "Saved. The edit is in the claim's history.");
    onClose();
  };
  return (
    <Modal title={confirmAfter ? "Edit, then confirm" : "Edit claim"} onClose={onClose} footer={<button className="btn primary" onClick={save}>{confirmAfter ? "Save and confirm" : "Save"}</button>}>
      <label>Statement<textarea id="ce-st" value={c.statement} onChange={(e) => setC({ ...c, statement: e.target.value })} /></label>
      <div className="row">
        <label style={{ flex: 1 }}>Question<select id="ce-q" value={c.question} onChange={(e) => setC({ ...c, question: e.target.value })}>{Object.entries(Q_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label style={{ flex: 1 }}>Kind<select id="ce-k" value={c.kind} onChange={(e) => setC({ ...c, kind: e.target.value })}>{Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      </div>
      {c.kind === "guardrail" && <div className="row">
        <label style={{ flex: 1 }}>Category<select id="ce-cat" value={c.ruleCategory ?? ""} onChange={(e) => setC({ ...c, ruleCategory: e.target.value })}>{Object.entries(RULE_CATS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label style={{ flex: 1 }}>Strength<select id="ce-str" value={c.strength ?? "soft"} onChange={(e) => setC({ ...c, strength: e.target.value })}><option value="soft">Soft: can be challenged</option><option value="hard">Hard: non-negotiable</option></select></label>
      </div>}
      {c.quote && <blockquote className="quote" style={{ margin: 0 }}>“{c.quote}”</blockquote>}
      <p className="small muted">The quote can't be edited: it is the evidence.</p>
    </Modal>
  );
}

export function Questions() {
  const { eid, role } = useApp();
  const { map } = useSources();
  const { data: claims } = useLoad<any[]>(`/engagements/${eid}/claims?status=confirmed,superseded`);
  const { run } = useAction();
  const [tab, setTab] = useState("why");
  const [adding, setAdding] = useState<any | null>(null);
  const [editing, setEditing] = useState<any | null>(null);
  const [superseding, setSuperseding] = useState<any | null>(null);
  const live = (claims ?? []).filter((c) => c.question === tab && c.status === "confirmed");
  const old = (claims ?? []).filter((c) => c.question === tab && c.status === "superseded");
  const confirmed = (claims ?? []).filter((c) => c.status === "confirmed");
  return (
    <>
      <div className="tabs" role="tablist">
        {Object.entries(Q_LABEL).map(([k, v]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
            {v} <span className="muted">{(claims ?? []).filter((c) => c.question === k && c.status === "confirmed").length}</span>
          </button>
        ))}
      </div>
      <div className="spread">
        <p className="muted">{{ why: "Why the client is doing this: drivers and pain points, each pushed for a baseline number.", what: "What gets built or transformed: outcomes with targets, and use cases.", rules: "Guardrails. The Rules page organises them by category, with hard or soft strength.", from: "Facts about today's estate. Effort is estimated from these.", how: "Approaches and designs the client has stated. Our recommendations live on the How page." }[tab]}</p>
        {can.contribute(role) && <button className="btn" onClick={() => setAdding({ statement: "", question: tab, kind: tab === "why" ? "pain_point" : tab === "what" ? "outcome" : tab === "rules" ? "guardrail" : tab === "from" ? "estate_fact" : "preference", value: "", unit: "" })}>Add an item</button>}
      </div>
      {claims && !live.length && <Empty>Nothing confirmed under {Q_LABEL[tab]} yet.</Empty>}
      {live.map((c) => (
        <ClaimCard key={c.id} c={c} sources={map} actions={<>
          {can.contribute(role) && <button className="btn" onClick={() => setEditing(c)}>Edit</button>}
          {can.own(role) && <button className="btn" onClick={() => setSuperseding({ claim: c, by: "", reason: "" })}>Supersede</button>}
        </>} />
      ))}
      {!!old.length && <details><summary className="muted">{old.length} superseded, kept for lineage</summary>
        <div className="stack" style={{ marginTop: 12 }}>{old.map((c) => <ClaimCard key={c.id} c={c} sources={map} />)}</div></details>}
      {adding && (
        <Modal title={`Add to ${Q_LABEL[adding.question]}`} onClose={() => setAdding(null)}
          footer={<button className="btn primary" disabled={!adding.statement} onClick={async () => {
            const body: any = { statement: adding.statement, question: adding.question, kind: adding.kind };
            if (adding.value) body.number = { value: Number(adding.value), unit: adding.unit };
            if (adding.kind === "guardrail") body.ruleCategory = adding.ruleCategory || "architecture";
            await run("add", () => api(`/engagements/${eid}/claims`, { body }), "Added. With no source cited, it is carried as an assumption.");
            setAdding(null);
          }}>Add</button>}>
          <label>Statement<textarea id="add-st" value={adding.statement} onChange={(e) => setAdding({ ...adding, statement: e.target.value })} /></label>
          <label>Kind<select id="add-k" value={adding.kind} onChange={(e) => setAdding({ ...adding, kind: e.target.value })}>{Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <div className="row">
            <label style={{ flex: 1 }}>Number (optional)<input id="add-v" type="number" value={adding.value} onChange={(e) => setAdding({ ...adding, value: e.target.value })} /></label>
            <label style={{ flex: 1 }}>Unit<input id="add-u" value={adding.unit} onChange={(e) => setAdding({ ...adding, unit: e.target.value })} placeholder="weeks, TB, reports…" /></label>
          </div>
          <p className="small muted">Items entered by hand carry no client evidence, so they are flagged as assumptions until a source backs them.</p>
        </Modal>
      )}
      {editing && <ClaimEditor claim={editing} onClose={() => setEditing(null)} />}
      {superseding && (
        <Modal title="Supersede a claim" onClose={() => setSuperseding(null)}
          footer={<button className="btn primary" disabled={!superseding.reason} onClick={async () => {
            await run("sup", () => api(`/engagements/${eid}/claims/${superseding.claim.id}/supersede`, { body: { byClaimId: superseding.by || null, reason: superseding.reason } }),
              (r: any) => `Superseded. ${r.flaggedForReview.openItems.length + r.flaggedForReview.recommendations.length + r.flaggedForReview.decisions.length} dependent items flagged for review.`);
            setSuperseding(null);
          }}>Supersede</button>}>
          <p className="statement">{superseding.claim.statement}</p>
          <label>Replaced by (optional)<select id="sup-by" value={superseding.by} onChange={(e) => setSuperseding({ ...superseding, by: e.target.value })}>
            <option value="">No replacement</option>{confirmed.filter((c) => c.id !== superseding.claim.id).map((c) => <option key={c.id} value={c.id}>{c.statement.slice(0, 90)}</option>)}</select></label>
          <label>Why<input id="sup-why" value={superseding.reason} onChange={(e) => setSuperseding({ ...superseding, reason: e.target.value })} placeholder="e.g. The client corrected this figure on 12 March" /></label>
          <p className="small muted">Nothing is deleted. Everything that rested on this claim is flagged for review.</p>
        </Modal>
      )}
    </>
  );
}

export function Rules() {
  const { eid, role } = useApp();
  const { data: rules } = useLoad<any[]>(`/engagements/${eid}/rules`);
  const { list: sources, map } = useSources();
  const { run } = useAction();
  const [notes, setNotes] = useState<any | null>(null);
  const [adding, setAdding] = useState<any | null>(null);
  const contributor = can.contribute(role);
  const put = (r: any, patch: any) => run("rule", () => api(`/engagements/${eid}/rules/${r.category}`, { method: "PUT", body: { status: r.status, gapDecision: r.gapDecision, notes: r.notes ?? "", sourceIds: r.sourceIds ?? [], ...patch } }));
  return (
    <>
      <p className="muted">The rules the solution must adhere to. Each category needs an answer and, ideally, a document. A hard rule is non-negotiable; a soft one can be challenged in a decision. An absent guardrail is a finding: either we establish it, which is scope, or the client accepts the gap, which is a risk.</p>
      {(rules ?? []).map((r) => (
        <section key={r.category} className={`card${r.needsGapDecision ? " warn" : ""}`}>
          <div className="spread">
            <h2>{r.label}</h2>
            <div className="row">
              <label className="row" style={{ flexDirection: "row", alignItems: "center" }}>Do they have it?
                <select aria-label={`${r.label} status`} disabled={!contributor} value={r.status} onChange={(e) => put(r, { status: e.target.value })}>
                  <option value="unknown">Not known yet</option><option value="yes">Yes</option><option value="partial">Partly</option><option value="no">No</option>
                </select></label>
              {(r.status === "no" || r.status === "partial") && (
                <select aria-label="Gap decision" disabled={!contributor} value={r.gapDecision ?? ""} onChange={(e) => put(r, { gapDecision: e.target.value || null })}>
                  <option value="">Gap decision needed</option><option value="establish">We establish it (scope)</option><option value="accept">Client accepts the gap (risk)</option>
                </select>)}
            </div>
          </div>
          {r.needsGapDecision && <p className="small"><Chip tone="warn">Needs a decision</Chip> This guardrail is missing or partial. Decide whether it becomes scope or an accepted risk.</p>}
          {r.notes && <p className="small">{r.notes}</p>}
          <div className="row small muted">Evidence: {(r.sourceIds ?? []).length ? r.sourceIds.map((s: string) => <Chip key={s}>{map[s] ?? s}</Chip>) : "no document linked. Without one, rules here stay soft."}</div>
          {r.rules.map((c: any) => (
            <div key={c.id} className="spread" style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
              <div className="stack" style={{ gap: 4, flex: 1 }}><p className="statement">{c.statement}</p><Provenance c={c} sources={map} /></div>
              <select aria-label="Strength" disabled={!contributor} value={c.strength ?? "soft"} onChange={(e) => run("str", () => api(`/engagements/${eid}/claims/${c.id}`, { method: "PATCH", body: { strength: e.target.value } }))}>
                <option value="soft">Soft</option><option value="hard">Hard</option></select>
            </div>
          ))}
          {contributor && <div className="row">
            <button className="btn" onClick={() => setAdding({ category: r.category, statement: "", strength: "soft" })}>Add a rule</button>
            <button className="btn" onClick={() => setNotes({ ...r })}>Notes and evidence</button>
          </div>}
        </section>
      ))}
      {adding && (
        <Modal title={`Add a rule: ${RULE_CATS[adding.category]}`} onClose={() => setAdding(null)}
          footer={<button className="btn primary" disabled={!adding.statement} onClick={async () => {
            await run("add", () => api(`/engagements/${eid}/claims`, { body: { statement: adding.statement, question: "rules", kind: "guardrail", ruleCategory: adding.category, strength: adding.strength } }), "Rule added.");
            setAdding(null);
          }}>Add</button>}>
          <label>Rule<textarea id="r-st" value={adding.statement} onChange={(e) => setAdding({ ...adding, statement: e.target.value })} /></label>
          <label>Strength<select id="r-str" value={adding.strength} onChange={(e) => setAdding({ ...adding, strength: e.target.value })}><option value="soft">Soft: can be challenged</option><option value="hard">Hard: non-negotiable</option></select></label>
        </Modal>
      )}
      {notes && (
        <Modal title={`${notes.label}: notes and evidence`} onClose={() => setNotes(null)}
          footer={<button className="btn primary" onClick={async () => { await put(notes, { notes: notes.notes, sourceIds: notes.sourceIds }); setNotes(null); }}>Save</button>}>
          <label>Notes<textarea id="n-notes" value={notes.notes ?? ""} onChange={(e) => setNotes({ ...notes, notes: e.target.value })} /></label>
          <fieldset className="stack" style={{ border: 0, padding: 0 }}><legend className="small">Evidence documents</legend>
            {(sources ?? []).map((s) => (
              <label key={s.id} className="row" style={{ flexDirection: "row", fontWeight: 400 }}>
                <input type="checkbox" checked={(notes.sourceIds ?? []).includes(s.id)} onChange={(e) => setNotes({ ...notes, sourceIds: e.target.checked ? [...(notes.sourceIds ?? []), s.id] : notes.sourceIds.filter((x: string) => x !== s.id) })} />{s.title}
              </label>))}
          </fieldset>
        </Modal>
      )}
    </>
  );
}
