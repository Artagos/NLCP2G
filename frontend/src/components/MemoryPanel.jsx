import { useEffect, useState } from "react";
import { forgetDoc, getMemory } from "../api.js";

// All three stores, side by side. Memory you can't look at is memory you can't
// debug, so everything the agent knows about you is listed here and deletable.
export default function MemoryPanel({ user }) {
  const [mem, setMem] = useState(null);
  const [err, setErr] = useState(null);
  const [showRules, setShowRules] = useState(false);

  const load = () =>
    getMemory()
      .then(setMem)
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    load();
  }, [user]);

  if (err) return <div className="panel-body"><p className="panel-err">{err}</p></div>;
  if (!mem) return <div className="panel-body"><p className="panel-hint">loading…</p></div>;

  const forget = async (id) => {
    await forgetDoc(id);
    load();
  };

  const { relational, documents, operating_rules: ops } = mem;

  return (
    <div className="panel-body">
      <h3 className="panel-h">Operating rules · markdown</h3>
      <p className="panel-hint">
        Pushed into every run. Edit <code>rules/operating_rules.md</code> and the next
        message picks it up — no restart.
      </p>
      <div className="chips">
        {ops.ids.map((id) => (
          <span className="chip" key={id} title={ops.titles[id]}>
            {id}
          </span>
        ))}
      </div>
      <button className="link-btn" onClick={() => setShowRules((s) => !s)}>
        {showRules ? "hide the file" : "show the file"}
      </button>
      {showRules ? <pre className="rules-dump">{ops.text}</pre> : null}

      <h3 className="panel-h">Rules the agent was told · documents</h3>
      <p className="panel-hint">Always in force for {mem.user}. Never retrieved — attached every time.</p>
      {documents.rules.length === 0 ? (
        <p className="panel-hint">None yet. Say something like “always give me a tiny worked example”.</p>
      ) : (
        documents.rules.map((d) => (
          <div className="memdoc" key={d.id}>
            <div className="memdoc-text">{d.text}</div>
            <div className="memdoc-meta">
              {d.id} · {d.scope}
              <button className="link-btn" onClick={() => forget(d.id)}>forget</button>
            </div>
          </div>
        ))
      )}

      <h3 className="panel-h">Facts the agent learned · documents</h3>
      <p className="panel-hint">
        Private to {mem.user}, pulled only when the cue matches the request.
      </p>
      {documents.facts.length === 0 ? (
        <p className="panel-hint">Nothing remembered yet.</p>
      ) : (
        documents.facts.map((d) => (
          <div className="memdoc" key={d.id}>
            <div className="memdoc-text">{d.text}</div>
            {d.cue?.keywords?.length ? (
              <div className="chips">
                {d.cue.keywords.map((k) => (
                  <span className="chip cue" key={k}>{k}</span>
                ))}
              </div>
            ) : null}
            {d.cue?.note ? <div className="memdoc-cue">comes back {d.cue.note}</div> : null}
            <div className="memdoc-meta">
              {d.id} · used {d.hits}×
              <button className="link-btn" onClick={() => forget(d.id)}>forget</button>
            </div>
          </div>
        ))
      )}

      <h3 className="panel-h">Progress · SQLite</h3>
      <p className="panel-hint">
        {relational.progress.solved} solved of {relational.progress.seen} seen ·{" "}
        {relational.progress.attempts} attempts
      </p>
      {relational.summaries.slice(0, 4).map((s, i) => (
        <div className="memdoc" key={i}>
          <div className="memdoc-meta">{s.name}</div>
          <div className="memdoc-text">{s.summary}</div>
        </div>
      ))}
    </div>
  );
}
