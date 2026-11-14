import { useMemo, useState } from "react";
import { api } from "../api";
import { can, Chip, fmt, useAction, useApp, useLoad } from "../ui";

const STATUS: Record<string, { label: string; tone?: "ok" | "warn" | "accent" }> = {
  draft: { label: "draft", tone: "accent" },
  approved: { label: "approved", tone: "ok" },
  superseded: { label: "superseded" },
  needs_review: { label: "needs review", tone: "warn" },
};

function workday(start: string | null | undefined, day: number): string {
  if (!start) return `Day ${day}`;
  const d = new Date(`${start}T12:00:00`);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  let left = day - 1;
  while (left > 0) { d.setDate(d.getDate() + 1); if (d.getDay() !== 0 && d.getDay() !== 6) left--; }
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

/** Discovery Plan: from whatever we have so far, the sessions to run, who attends, what to ask, how long. */
export function PlanView() {
  const { eid, eng, role, nameOf, toast } = useApp();
  const [version, setVersion] = useState<number | null>(null);
  const { data: res } = useLoad<any>(`/engagements/${eid}/plan${version ? `?version=${version}` : ""}`);
  const { data: planning } = useLoad<any>(`/planning`);
  const { run, busy } = useAction();
  const [format, setFormat] = useState<string>("");
  const [start, setStart] = useState<string>("");
  const p = res?.plan;
  const fmts: any[] = planning?.formats ?? [];
  const croles = planning?.roles?.client ?? {};
  const oroles = planning?.roles?.ours ?? {};
  const byDay = useMemo(() => {
    const m = new Map<number, any[]>();
    for (const s of p?.sessions ?? []) m.set(s.day, [...(m.get(s.day) ?? []), s]);
    return [...m.entries()].sort((a, b) => a[0] - b[0]);
  }, [p]);

  const build = () => run("build", () => api(`/engagements/${eid}/run/plan`, { method: "POST", body: { ...(format ? { format } : {}), ...(start ? { startDate: start } : {}) } }),
    (r: any) => { setVersion(null); return r.summary; });
  const sendable = async () => {
    const text = await api<string>(`/engagements/${eid}/outputs/discovery-plan`);
    const blob = new Blob([text], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(eng?.name ?? "engagement").replace(/[^\w-]+/g, "-")}-discovery-plan.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Downloaded the client-facing plan (no internal hours, risks or nominations).");
  };
  const recommended = p?.recommendedFormat;
  const chosen = format || "";

  return (
    <>
      <section className="card">
        <div className="spread">
          <div>
            <h2>Discovery plan</h2>
            <p className="small muted">Load whatever you have (an RFP, a call transcript, notes, an email) and choose how much time you have. The plan says which sessions to run, who should attend on both sides, what to ask, and how long each takes. Nothing needs to be confirmed first: a plan is a working document.</p>
          </div>
        </div>
        <div className="grid" role="radiogroup" aria-label="How much time do we have?">
          <label className={`card${chosen === "" ? " accent" : ""}`} style={{ cursor: "pointer", gap: 4 }}>
            <span className="row"><input type="radio" name="fmt" checked={chosen === ""} onChange={() => setFormat("")} /><strong>Recommend for me</strong></span>
            <span className="small muted">{recommended ? `Last recommendation: ${fmts.find((f) => f.key === recommended)?.label ?? recommended}. ${p.recommendationReason}` : "The app picks a format from what has been shared so far."}</span>
          </label>
          {fmts.map((f) => (
            <label key={f.key} className={`card${chosen === f.key ? " accent" : ""}`} style={{ cursor: "pointer", gap: 4 }}>
              <span className="row"><input type="radio" name="fmt" checked={chosen === f.key} onChange={() => setFormat(f.key)} /><strong>{f.label}</strong>{recommended === f.key && <Chip tone="accent">recommended</Chip>}</span>
              <span className="small muted">{f.description}</span>
              <span className="prov">{f.days} day{f.days > 1 ? "s" : ""} · up to {f.maxSessions} session{f.maxSessions > 1 ? "s" : ""} · {Math.round(f.maxMinutes / 60 * 10) / 10} h of client time</span>
            </label>
          ))}
        </div>
        <div className="row">
          <label className="row small" style={{ gap: 8 }}>First day <input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
          {can.contribute(role) && <button className="btn primary" disabled={!!busy} onClick={build}>{busy === "build" ? "Planning…" : p ? "Build a new version" : "Build the plan"}</button>}
        </div>
      </section>

      {!p ? <p className="muted">No plan yet.</p> : (
        <>
          <section className={`card${p.status === "needs_review" ? " warn" : p.status === "approved" ? " accent" : ""}`}>
            <div className="spread">
              <div className="row">
                <h2>{p.formatLabel}</h2><Chip tone={STATUS[p.status]?.tone}>{STATUS[p.status]?.label ?? p.status}</Chip>
                <select aria-label="Version" value={p.version} onChange={(e) => setVersion(Number(e.target.value))}>
                  {(res.versions ?? []).map((v: number) => <option key={v} value={v}>v{v}{v === res.approvedVersion ? " (approved)" : ""}</option>)}
                </select>
              </div>
              <div className="row">
                <button className="btn" onClick={sendable}>Download for the client</button>
                {can.own(role) && ["draft", "needs_review"].includes(p.status) && (
                  <button className="btn primary" disabled={!!busy} onClick={() => run("approve", () => api(`/engagements/${eid}/plans/${p.version}/approve`, { method: "POST" }), `Plan v${p.version} approved.`)}>Approve</button>
                )}
              </div>
            </div>
            {p.reviewReason && <p className="small"><Chip tone="warn">review</Chip> {p.reviewReason}</p>}
            {p.summary && <p>{p.summary}</p>}
            <div className="row">
              <Chip>{p.sessions.length} session{p.sessions.length > 1 ? "s" : ""}</Chip>
              <Chip>{Math.round(p.totalMinutes / 6) / 10} h of client time</Chip>
              <Chip>{p.ourTeam.reduce((a: number, t: any) => a + t.hours, 0)} h of our time</Chip>
              <Chip tone="accent">shape: {p.basis?.shape}</Chip>
              <span className="prov">built from {p.basis?.sources} source(s), {p.basis?.claims} claim(s), {p.basis?.stakeholders} stakeholder(s) · {fmt(p.createdAt)}{p.approvedBy ? ` · approved by ${nameOf(p.approvedBy)}` : ""}</span>
            </div>
          </section>

          {!!p.nominations?.length && (
            <section className="card warn">
              <h2>People to find</h2>
              <p className="small muted">No named owner yet for these client functions. Ask for a nomination before the sessions that need them.</p>
              {p.nominations.map((n: any) => (
                <p key={n.role} className="small"><strong>{croles[n.role]?.label ?? n.role}</strong>: ask {n.askWho} · needed for {n.neededFor.map((k: string) => p.sessions.find((s: any) => s.key === k)?.title ?? k).join(", ")}</p>
              ))}
            </section>
          )}

          <div className="grid">
            <section className="card">
              <h2>Our team</h2>
              <table className="small" style={{ width: "100%" }}>
                <thead><tr><th style={{ textAlign: "left" }}>Role</th><th style={{ textAlign: "left" }}>Why</th><th style={{ textAlign: "right" }}>Hours</th></tr></thead>
                <tbody>{p.ourTeam.map((t: any) => <tr key={t.role}><td>{oroles[t.role]?.label ?? t.role}</td><td className="muted">{t.why}</td><td style={{ textAlign: "right" }}>{t.hours}</td></tr>)}</tbody>
              </table>
              <p className="prov">Hours include time in the room plus the same again for preparation and write-up; the solution architect also carries the synthesis.</p>
            </section>
            <section className="card">
              <h2>Ask the client to send</h2>
              {p.requests?.length ? <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{p.requests.map((r: string) => <li key={r}>{r}</li>)}</ul> : <p className="small muted">Nothing yet.</p>}
              {!!p.risks?.length && <><h2>Risks to the discovery</h2><ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{p.risks.map((r: string) => <li key={r}>{r}</li>)}</ul></>}
            </section>
          </div>

          {byDay.map(([day, sessions]) => (
            <section key={day} className="stack" aria-label={`Day ${day}`}>
              <h2>{workday(p.startDate, day)}{p.startDate ? ` · day ${day}` : ""}</h2>
              {sessions.map((s: any) => {
                const untracked = s.questions.filter((q: any) => !q.openItemId).length;
                return (
                  <section key={s.key} className="card">
                    <div className="spread">
                      <div className="row"><strong>{s.title}</strong><Chip>{s.minutes} min</Chip>{s.covers.map((c: string) => <Chip key={c}>{c.toUpperCase()}</Chip>)}</div>
                      {can.contribute(role) && untracked > 0 && (
                        <button className="btn" disabled={!!busy} onClick={() => run("track", () => api(`/engagements/${eid}/plans/${p.version}/track-questions`, { method: "POST", body: { sessionKey: s.key } }), (r: any) => `${r.created.length} question(s) now tracked as open items.`)}>Track {untracked} question{untracked > 1 ? "s" : ""}</button>
                      )}
                    </div>
                    {s.purpose && <p className="small">{s.purpose}</p>}
                    <div className="row small">
                      <span className="muted">Client:</span>
                      {s.clientAttendees.map((a: any, i: number) => a.name
                        ? <Chip key={i} tone="ok">{a.name} · {croles[a.role]?.label ?? a.role}</Chip>
                        : <Chip key={i} tone="warn">{croles[a.role]?.label ?? a.role}: to nominate</Chip>)}
                    </div>
                    <div className="row small"><span className="muted">Us:</span>{s.ourAttendees.map((r: string) => <Chip key={r}>{oroles[r]?.label ?? r}</Chip>)}</div>
                    <div className="grid">
                      <div>
                        <p className="small"><strong>Agenda</strong></p>
                        <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>{s.agenda.map((a: string) => <li key={a}>{a}</li>)}</ol>
                        {!!s.preReads?.length && <><p className="small"><strong>Ask for beforehand</strong></p><ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{s.preReads.map((r: string) => <li key={r}>{r}</li>)}</ul></>}
                      </div>
                      <div>
                        <p className="small"><strong>Questions</strong></p>
                        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                          {s.questions.map((q: any, i: number) => <li key={i}>{q.text}{q.openItemId && <> <Chip tone="ok">tracked</Chip></>}{q.mustKnow && !q.openItemId && <span className="muted"> · checklist</span>}</li>)}
                        </ul>
                      </div>
                    </div>
                  </section>
                );
              })}
            </section>
          ))}
        </>
      )}
    </>
  );
}
