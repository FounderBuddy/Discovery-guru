import { Chip, fmt, useApp, useLoad } from "../ui";

const ACTION_LABEL: Record<string, string> = {
  proposed: "proposed", confirmed: "confirmed", rejected: "rejected", edited: "edited", superseded: "superseded",
  entered: "entered by hand", drafted: "drafted", approved: "approved", escalated: "escalated", decided: "decided",
  needs_review: "flagged for review", unblocked: "unblocked", created: "created", updated: "updated", resolved: "resolved",
};

function ClaimRef({ c }: { c: any }) {
  const { openLineage } = useApp();
  return (
    <div className="card" style={{ gap: 6 }}>
      <div className="spread"><p className="statement small">{c.statement}</p><button className="btn link" onClick={() => openLineage("claim", c.id)}>Open</button></div>
      {c.quote && <blockquote className="quote small" style={{ margin: 0 }}>“{c.quote}”</blockquote>}
      <p className="prov">{c.speaker ? `${c.speaker} · ` : ""}{c.source?.title ?? (c.evidence === "none" ? "no client evidence" : "")} · {c.status}</p>
    </div>
  );
}

/** Up to the evidence, down to everything resting on it, and every change in between. */
export function Lineage({ entity, id, onClose }: { entity: string; id: string; onClose: () => void }) {
  const { eid, nameOf, openLineage } = useApp();
  const { data, error } = useLoad<any>(`/engagements/${eid}/lineage/${entity}/${id}`);
  const up = data?.upstream ?? {};
  const down = data?.downstream ?? {};
  const item = data?.item;
  return (
    <aside className="drawer" aria-label="Lineage">
      <div className="spread"><h2>Lineage</h2><button className="btn link" onClick={onClose}>Close</button></div>
      {error && <p>{error}</p>}
      {item && <>
        <div className="card accent">
          <Chip>{entity}</Chip>
          <p className="statement">{item.statement ?? item.recommendation ?? item.question ?? item.title ?? item.explanation}</p>
          {item.quote && <blockquote className="quote" style={{ margin: 0 }}>“{item.quote}”</blockquote>}
          <p className="prov">status: {item.status}</p>
        </div>
        <h3>Rests on</h3>
        {up.source && <p className="small"><Chip>source</Chip> {up.source.title} · {up.source.kind} · added {fmt(up.source.createdAt)}</p>}
        {up.run && <p className="small"><Chip>engine run</Chip> {up.run.engine} · {up.run.promptVersion} · {up.run.provider}/{up.run.model} · asked by {nameOf(up.run.actorId)}</p>}
        {up.practice && <p className="small"><Chip>practice</Chip> {up.practice.title} (v{up.practice.version})</p>}
        {(up.practices ?? []).map((p: any) => <p key={p.id} className="small"><Chip>practice</Chip> {p.title} (v{p.version})</p>)}
        {up.recommendation && <p className="small"><Chip>recommendation</Chip> <button className="btn link" onClick={() => openLineage("recommendation", up.recommendation.id)}>{up.recommendation.path}</button></p>}
        {(up.claims ?? []).map((c: any) => <ClaimRef key={c.id} c={c} />)}
        {!up.source && !up.run && !(up.claims ?? []).length && !up.practice && !(up.practices ?? []).length && <p className="small muted">{item.evidence === "none" ? "Nothing: entered by a person without a source, so it is an assumption." : "No upstream links."}</p>}
        <h3>What rests on it</h3>
        {down.supersededBy && <><p className="small"><Chip tone="warn">superseded by</Chip></p><ClaimRef c={down.supersededBy} /></>}
        {(down.openItems ?? []).map((o: any) => <p key={o.id} className="small"><Chip>open item</Chip> <button className="btn link" onClick={() => openLineage("openitem", o.id)}>{o.question}</button></p>)}
        {(down.recommendations ?? []).map((r: any) => <p key={r.id} className="small"><Chip>recommendation</Chip> <button className="btn link" onClick={() => openLineage("recommendation", r.id)}>{r.path}</button> · {r.status}</p>)}
        {(down.decisions ?? []).map((d: any) => <p key={d.id} className="small"><Chip>decision</Chip> <button className="btn link" onClick={() => openLineage("decision", d.id)}>ADR-{String(d.number).padStart(3, "0")} {d.title}</button></p>)}
        {(down.divergences ?? []).map((d: any) => <p key={d.id} className="small"><Chip tone="crit">divergence</Chip> {d.explanation}</p>)}
        {!Object.values(down).some((v: any) => (Array.isArray(v) ? v.length : v)) && <p className="small muted">Nothing depends on this yet.</p>}
        <h3>History</h3>
        {(data.history ?? []).map((h: any) => (
          <p key={h.id} className="prov">{h.at.slice(0, 16).replace("T", " ")} · {ACTION_LABEL[h.action] ?? h.action} by {h.actorType === "user" ? nameOf(h.actorId) : `${h.actorId} (${h.actorType})`}{h.viaService !== "api-gateway" ? ` via ${h.viaService}` : ""}</p>
        ))}
      </>}
    </aside>
  );
}
