import { useEffect, useState } from "react";
import { api } from "../api";
import { Chip, Empty, fmt, Modal, PATHS, useAction, useApp, useLoad } from "../ui";

export function Practices() {
  const { me, nameOf } = useApp();
  const { data } = useLoad<any[]>("/practices");
  const { data: packs } = useLoad<any[]>("/packs");
  const plabel: Record<string, string> = Object.fromEntries((packs ?? []).map((k) => [k.key, k.label]));
  const { run } = useAction();
  const [edit, setEdit] = useState<any | null>(null);
  const admin = !!me?.isPracticeAdmin;
  const domains = Array.from(new Set((data ?? []).map((p) => p.domain)));
  return (
    <>
      <div className="spread">
        <p className="muted">Datatonic's own practices. Recommendations apply the ones that fit the engagement's shape first; where none applies they fall back to best practice, and any clash with a client rule goes to the solution owner. {admin ? "You maintain this page." : "Only practice maintainers can change it."}</p>
        {admin && <button className="btn primary" onClick={() => setEdit({ domain: PATHS[0], title: "", statement: "", rationale: "", status: "active", appliesTo: [] })}>Add a practice</button>}
      </div>
      {data && !data.length && <Empty>No practices recorded yet.</Empty>}
      {domains.map((d) => (
        <section key={d} className="card">
          <h2>{d}</h2>
          {(data ?? []).filter((p) => p.domain === d).map((p) => (
            <div key={p.id} className="spread" style={{ borderTop: "1px solid var(--line)", paddingTop: 10, alignItems: "flex-start" }}>
              <div className="stack" style={{ gap: 4, flex: 1 }}>
                <div className="row"><p className="statement">{p.title}</p><Chip tone={p.status === "active" ? "ok" : p.status === "retired" ? "crit" : undefined}>{p.status}</Chip>{p.updatedBy === "seed" && <Chip tone="warn">seeded: needs review</Chip>}</div>
                <div className="row small">{p.appliesTo?.length ? p.appliesTo.map((k: string) => <Chip key={k}>{plabel[k] ?? k}</Chip>) : <Chip>every engagement</Chip>}</div>
                <p className="small">{p.statement}</p>
                {p.rationale && <p className="small muted">{p.rationale}</p>}
                <p className="prov">v{p.version} · {p.updatedBy === "seed" ? "seed" : nameOf(p.updatedBy)} · {fmt(p.updatedAt)}</p>
              </div>
              {admin && <button className="btn" onClick={() => setEdit({ ...p })}>Edit</button>}
            </div>
          ))}
        </section>
      ))}
      {edit && (
        <Modal title={edit.id ? "Edit practice" : "Add a practice"} onClose={() => setEdit(null)}
          footer={<button className="btn primary" disabled={!edit.title || !edit.statement} onClick={async () => {
            const body = { domain: edit.domain, title: edit.title, statement: edit.statement, rationale: edit.rationale ?? "", status: edit.status, appliesTo: edit.appliesTo ?? [] };
            await run("p", () => edit.id ? api(`/practices/${edit.id}`, { method: "PATCH", body }) : api("/practices", { body }), "Saved as a new version.");
            setEdit(null);
          }}>Save</button>}>
          <label>Domain<input id="pr-d" list="pr-domains" value={edit.domain} onChange={(e) => setEdit({ ...edit, domain: e.target.value })} /></label>
          <datalist id="pr-domains">{PATHS.map((x) => <option key={x} value={x} />)}</datalist>
          <label>Title<input id="pr-t" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></label>
          <label>Practice<textarea id="pr-s" value={edit.statement} onChange={(e) => setEdit({ ...edit, statement: e.target.value })} /></label>
          <label>Why<textarea id="pr-r" value={edit.rationale ?? ""} onChange={(e) => setEdit({ ...edit, rationale: e.target.value })} style={{ minHeight: 70 }} /></label>
          <fieldset className="stack" style={{ gap: 6, border: 0, padding: 0 }}>
            <legend className="small"><strong>Applies to</strong> (none ticked = every engagement)</legend>
            {(packs ?? []).filter((k) => k.key !== "core").map((k) => (
              <label key={k.key} className="row small" style={{ gap: 8 }}>
                <input type="checkbox" checked={(edit.appliesTo ?? []).includes(k.key)}
                  onChange={(e) => setEdit({ ...edit, appliesTo: e.target.checked ? [...(edit.appliesTo ?? []), k.key] : (edit.appliesTo ?? []).filter((x: string) => x !== k.key) })} />
                {k.label}
              </label>
            ))}
          </fieldset>
          <label>Status<select id="pr-st" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}><option value="active">Active: engines use it</option><option value="draft">Draft: not used yet</option><option value="retired">Retired</option></select></label>
        </Modal>
      )}
    </>
  );
}

const OUTPUTS = [
  { key: "discovery-summary", label: "Discovery summary", ext: "md" },
  { key: "open-items", label: "Open items register", ext: "md" },
  { key: "decision-log", label: "Decision log", ext: "md" },
  { key: "divergence-log", label: "Divergence log", ext: "md" },
  { key: "recommendations", label: "Recommendations", ext: "md" },
  { key: "claims-register", label: "Claims register with provenance", ext: "csv" },
];

export function Outputs() {
  const { eid, eng, toast } = useApp();
  const [kind, setKind] = useState(OUTPUTS[0]);
  const [text, setText] = useState<string>("");
  useEffect(() => {
    let live = true;
    setText("");
    api<string>(`/engagements/${eid}/outputs/${kind.key}`).then((t) => live && setText(t)).catch((e) => live && setText(e.message));
    return () => { live = false; };
  }, [eid, kind]);
  const download = () => {
    const blob = new Blob([text], { type: kind.ext === "csv" ? "text/csv" : "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(eng?.name ?? "engagement").replace(/[^\w-]+/g, "-")}-${kind.key}.${kind.ext}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <>
      <div className="spread">
        <div className="tabs">{OUTPUTS.map((o) => <button key={o.key} className={kind.key === o.key ? "active" : ""} onClick={() => setKind(o)}>{o.label}</button>)}</div>
        <div className="row">
          <button className="btn" onClick={() => navigator.clipboard.writeText(text).then(() => toast("Copied."), () => toast("Copy was blocked. Select the text instead."))}>Copy</button>
          <button className="btn primary" onClick={download}>Download .{kind.ext}</button>
        </div>
      </div>
      <p className="small muted">Built from the ledger by the artefacts engine, with no model involved, so the same ledger always gives the same output. Every claim carries its source.</p>
      <pre className="out">{text || "Building…"}</pre>
    </>
  );
}

export function Governance() {
  const { eid, nameOf } = useApp();
  const { data: providers } = useLoad<any>("/system/providers");
  const { data: runs } = useLoad<any[]>(eid ? `/engagements/${eid}/runs?limit=100` : null);
  const { data: events } = useLoad<any[]>(eid ? `/engagements/${eid}/events` : null);
  return (
    <>
      <section className="card">
        <h2>Rules every agent follows</h2>
        <ul className="small">
          <li><strong>Agents propose, people confirm.</strong> Engines hold proposal rights only; the ledger refuses an engine that tries to confirm, resolve, decide, approve or delete.</li>
          <li><strong>No citation, no claim.</strong> Every extracted claim quotes its source verbatim. The engine checks, and the ledger checks again.</li>
          <li><strong>Least privilege.</strong> Each service has its own credential and a fixed permission set. No engine can act as a person.</li>
          <li><strong>Escalate, never resolve.</strong> A clash between a Datatonic practice and a client rule goes to the solution owner.</li>
          <li><strong>Full audit.</strong> Every engine run records its prompt version, provider, model, tokens and cost; every change is kept in an append-only history.</li>
          <li><strong>Spend cap.</strong> The model gateway refuses calls once an engagement's budget is spent.</li>
        </ul>
      </section>
      {providers && <section className="card">
        <h2>Model gateway</h2>
        <p className="small">Default provider: <Chip tone="accent">{providers.default}</Chip> Switch providers in config/models.yaml; no engine changes.</p>
        <div className="table-wrap"><table>
          <thead><tr><th>Provider</th><th>Type</th><th>Models by tier</th><th>Credentials</th></tr></thead>
          <tbody>{providers.providers.map((p: any) => (
            <tr key={p.name}><td className="statement">{p.name}</td><td><Chip>{p.type}</Chip></td>
              <td className="prov">{Object.entries(p.models).map(([t, m]) => `${t}: ${m}`).join(" · ")}</td>
              <td>{p.credentialsPresent ? <Chip tone="ok">present</Chip> : <Chip>not configured</Chip>}</td></tr>))}
          </tbody></table></div>
      </section>}
      {eid && <section className="card">
        <h2>Engine runs</h2>
        {runs && !runs.length && <p className="muted">No runs yet on this engagement.</p>}
        {!!runs?.length && <div className="table-wrap"><table>
          <thead><tr><th>When</th><th>Engine</th><th>Prompt</th><th>Model</th><th>Asked by</th><th>Kept</th><th>Discarded</th><th>Tokens</th><th>Cost</th></tr></thead>
          <tbody>{runs.map((r) => (
            <tr key={r.id}><td className="small">{fmt(r.startedAt)}</td><td>{r.engine.replace("engine-", "")}</td><td className="prov">{r.promptVersion}</td>
              <td className="prov">{r.provider ? `${r.provider} / ${r.model}` : "none"}</td><td className="small">{nameOf(r.actorId)}</td>
              <td>{r.accepted}</td><td>{r.rejected ? <Chip tone="warn">{r.rejected}</Chip> : 0}</td>
              <td className="prov">{r.tokensIn + r.tokensOut}</td><td className="prov">${r.costUsd.toFixed(4)}</td></tr>))}
          </tbody></table></div>}
      </section>}
      {eid && !!events?.length && <section className="card">
        <h2>Event stream</h2>
        <p className="small muted">CloudEvents 1.0 on the bus for this engagement, newest first.</p>
        {events.slice(0, 20).map((e) => <p key={e.id} className="prov">{e.time.slice(0, 19).replace("T", " ")} · {e.type} · from {e.source}{e.sdpactorid ? ` · by ${e.sdpactorid}` : ""}</p>)}
      </section>}
    </>
  );
}
