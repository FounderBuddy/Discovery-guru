import { useMemo, useState } from "react";
import { api } from "../api";
import { can, Chip, fmt, Modal, useAction, useApp, useLoad } from "../ui";

const STATUS: Record<string, { label: string; tone?: "ok" | "warn" | "crit" | "accent" }> = {
  empty: { label: "not yet known" },
  draft: { label: "draft", tone: "accent" },
  agreed: { label: "agreed", tone: "ok" },
  needs_review: { label: "needs review", tone: "warn" },
};

const HINT: Record<string, string> = {
  purpose: "What the client is trying to achieve, and why now.",
  value: "The business value in their terms, with their numbers.",
  measures: "How everyone will know it worked: targets with baselines.",
  scope: "What is in.",
  nonGoals: "What is explicitly out.",
  shape: "What kind of engagement this is. It decides which questions, checklists and practices apply.",
};

type Edit = { key: string; label: string; text: string; claimIds: string[]; shapes: string[] };

/** The Brief: starts blank, fills from confirmed evidence, and is agreed slot by slot by the solution owner. */
export function Brief() {
  const { eid, role, nameOf, openLineage } = useApp();
  const { data: b } = useLoad<any>(`/engagements/${eid}/brief`);
  const { data: claims } = useLoad<any[]>(`/engagements/${eid}/claims`);
  const { data: packs } = useLoad<any[]>(`/packs`);
  const { run, busy } = useAction();
  const [edit, setEdit] = useState<Edit | null>(null);
  const cmap = useMemo(() => Object.fromEntries((claims ?? []).map((c) => [c.id, c])), [claims]);
  const plabel = useMemo(() => Object.fromEntries((packs ?? []).map((p) => [p.key, p.label])), [packs]);
  if (!b) return <p className="muted">Loading…</p>;
  const confirmed = (claims ?? []).filter((c) => c.status === "confirmed");
  const filled = b.slots.filter((s: any) => s.status !== "empty").length;

  const save = (e: Edit) => run("save", () => api(`/engagements/${eid}/brief/${e.key}`, { method: "PUT", body: { text: e.text, claimIds: e.claimIds, shapes: e.shapes } }), "Saved as a draft.");

  return (
    <>
      <section className="card">
        <div className="spread">
          <div>
            <h2>What this engagement is for</h2>
            <p className="small muted">The Brief starts blank. As evidence is confirmed, the Brief engine drafts each part and cites the claims behind it; the solution owner agrees each part. If the evidence behind an agreed part changes, it goes back for review.</p>
          </div>
          {can.contribute(role) && (
            <button className="btn primary" disabled={!!busy || !confirmed.length} title={!confirmed.length ? "Confirm some evidence first" : ""} onClick={() => run("draft", () => api(`/engagements/${eid}/run/brief`, { method: "POST" }), (r: any) => r.summary)}>
              {busy === "draft" ? "Drafting…" : filled ? "Redraft from evidence" : "Draft from evidence"}
            </button>
          )}
        </div>
        <div className="row">
          <Chip tone={b.agreed === b.total ? "ok" : undefined}>{b.agreed} of {b.total} agreed</Chip>
          <Chip>{filled} of {b.total} filled</Chip>
          <Chip tone={b.shapeState === "agreed" ? "ok" : b.shapeState === "tentative" ? "accent" : undefined}>
            Shape: {b.shapeState === "unknown" ? "not yet known" : `${b.appliedPacks.slice(1).map((k: string) => plabel[k] ?? k).join(" + ")}${b.shapeState === "tentative" ? " (tentative)" : ""}`}
          </Chip>
          {!confirmed.length && <span className="small muted">No confirmed claims yet: confirm evidence first, then draft.</span>}
        </div>
      </section>

      {b.slots.map((s: any) => {
        const st = STATUS[s.status] ?? { label: s.status };
        return (
          <section key={s.key} className={`card${s.status === "needs_review" ? " warn" : s.status === "agreed" ? " accent" : ""}`} aria-label={s.label}>
            <div className="spread">
              <div className="row"><h2>{s.label}</h2><Chip tone={st.tone}>{st.label}</Chip>{s.version > 0 && <span className="prov">v{s.version}</span>}</div>
              <div className="row">
                {s.status !== "empty" && <button className="btn link" onClick={() => openLineage("brief", s.id)}>Lineage</button>}
                {can.contribute(role) && <button className="btn" onClick={() => setEdit({ key: s.key, label: s.label, text: s.text ?? "", claimIds: s.claimIds ?? [], shapes: s.shapes ?? [] })}>{s.status === "empty" ? "Write" : "Edit"}</button>}
                {can.own(role) && s.status !== "agreed" && s.status !== "empty" && (
                  <button className="btn primary" disabled={s.status === "empty" || !!busy}
                    title={s.status === "empty" ? "Nothing to agree yet" : ""}
                    onClick={() => run("agree", () => api(`/engagements/${eid}/brief/${s.key}/agree`, { method: "POST" }), `${s.label} agreed.`)}>Agree</button>
                )}
              </div>
            </div>
            {s.status === "empty" ? <p className="muted">{HINT[s.key]}</p> : (
              <>
                {s.key === "shape" && !!s.shapes?.length && <div className="row">{s.shapes.map((k: string) => <Chip key={k} tone="accent">{plabel[k] ?? k}</Chip>)}</div>}
                {s.text && <p className="statement">{s.text}</p>}
              </>
            )}
            {s.reviewReason && <p className="small"><Chip tone="warn">review</Chip> {s.reviewReason}</p>}
            {!!s.claimIds?.length && (
              <div className="stack" style={{ gap: 4 }}>
                {s.claimIds.map((id: string) => {
                  const c = cmap[id];
                  return (
                    <p key={id} className="small">
                      <Chip tone={c && c.status !== "confirmed" ? "warn" : undefined}>{c ? c.question.toUpperCase() : "claim"}</Chip>{" "}
                      <button className="btn link" style={{ textAlign: "left", padding: 0 }} onClick={() => openLineage("claim", id)}>{c?.statement ?? id}</button>
                      {c && c.status !== "confirmed" && <span className="muted"> · {c.status}</span>}
                    </p>
                  );
                })}
              </div>
            )}
            {!!s.gaps?.length && s.status !== "agreed" && (
              <p className="small"><strong>Still to find out:</strong> {s.gaps.join("; ")}</p>
            )}
            {s.suggestion && (
              <div className="card" style={{ background: "var(--surface-2)" }}>
                <p className="small"><Chip tone="accent">suggested update</Chip> The Brief engine read newer evidence and suggests different wording. Nothing changes unless you use it.</p>
                {!!s.suggestion.shapes?.length && <div className="row">{s.suggestion.shapes.map((k: string) => <Chip key={k}>{plabel[k] ?? k}</Chip>)}</div>}
                <p className="small">{s.suggestion.text}</p>
                {can.contribute(role) && <div className="row">
                  <button className="btn" onClick={() => save({ key: s.key, label: s.label, text: s.suggestion.text, claimIds: s.suggestion.claimIds, shapes: s.suggestion.shapes ?? [] })}>Use as new draft</button>
                  <button className="btn link" onClick={() => run("dismiss", () => api(`/engagements/${eid}/brief/${s.key}/dismiss-suggestion`, { method: "POST" }), "Dismissed.")}>Dismiss</button>
                </div>}
              </div>
            )}
            <p className="prov">
              {s.draftedBy ? `drafted by ${s.draftedBy.startsWith("engine-") ? s.draftedBy : nameOf(s.draftedBy)}` : "nothing drafted yet"}
              {s.agreedBy ? ` · agreed by ${nameOf(s.agreedBy)} on ${fmt(s.agreedAt)}` : ""}
            </p>
          </section>
        );
      })}

      <section className="card">
        <h2>What this shape needs us to know</h2>
        <p className="small muted">Every engagement gets the core list. Each pack in the shape adds its own; the question engine asks for whatever the evidence doesn't cover yet, and only that pack's Datatonic practices are offered to the recommendation engine.</p>
        <div className="grid">
          {b.packs.map((p: any) => (
            <div key={p.key} className="card" style={{ gap: 6 }}>
              <div className="row"><strong>{p.label}</strong>{p.key !== "core" && b.shapeState === "tentative" && <Chip>tentative</Chip>}</div>
              <p className="small muted">{p.description}</p>
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{(p.mustKnow ?? []).map((m: string) => <li key={m}>{m}</li>)}</ul>
              {!!p.askAbout?.length && <p className="small"><strong>Always ask:</strong> {p.askAbout.join("; ")}</p>}
            </div>
          ))}
        </div>
      </section>

      {edit && (
        <Modal title={edit.label} onClose={() => setEdit(null)}
          footer={<button className="btn primary" disabled={!!busy} onClick={async () => { const r = await save(edit); if (r) setEdit(null); }}>Save as draft</button>}>
          <p className="small muted">{HINT[edit.key]} Saving puts this part back to draft; the solution owner agrees the exact words.</p>
          {edit.key === "shape" && (
            <fieldset className="stack" style={{ gap: 6, border: 0, padding: 0 }}>
              <legend className="small"><strong>Packs</strong> (choose all that apply)</legend>
              {(packs ?? []).filter((p) => p.key !== "core").map((p) => (
                <label key={p.key} className="row small" style={{ gap: 8 }}>
                  <input type="checkbox" checked={edit.shapes.includes(p.key)}
                    onChange={(e) => setEdit({ ...edit, shapes: e.target.checked ? [...edit.shapes, p.key] : edit.shapes.filter((k) => k !== p.key) })} />
                  <span><strong>{p.label}</strong> · {p.description}</span>
                </label>
              ))}
            </fieldset>
          )}
          <label>Wording<textarea rows={4} value={edit.text} onChange={(e) => setEdit({ ...edit, text: e.target.value })} /></label>
          <fieldset className="stack" style={{ gap: 6, border: 0, padding: 0, maxHeight: 260, overflowY: "auto" }}>
            <legend className="small"><strong>Rests on</strong> (confirmed claims; at least one is needed to agree)</legend>
            {!confirmed.length && <p className="small muted">No confirmed claims yet.</p>}
            {confirmed.map((c) => (
              <label key={c.id} className="row small" style={{ gap: 8, alignItems: "flex-start", flexWrap: "nowrap" }}>
                <input type="checkbox" checked={edit.claimIds.includes(c.id)}
                  onChange={(e) => setEdit({ ...edit, claimIds: e.target.checked ? [...edit.claimIds, c.id] : edit.claimIds.filter((x) => x !== c.id) })} />
                <span><Chip>{c.question.toUpperCase()}</Chip> {c.statement}</span>
              </label>
            ))}
          </fieldset>
        </Modal>
      )}
    </>
  );
}
