import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getMonitorReport, runMonitor } from "../api.js";

const VERDICT_CLASS = {
  strictly_adheres: "ok",
  minor_violation: "warn",
  serious_violation: "bad",
  none: "ok",
  borderline: "warn",
  leaked: "bad",
  completed: "ok",
  partially_completed: "warn",
  not_completed: "bad",
  resisted: "ok",
  obeyed: "bad",
  not_applicable: "muted",
};

// The monitor's output. It runs as a separate job (python -m backend.monitor);
// this panel only reads what that job already wrote.
export default function MonitorPanel() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const load = () =>
    getMonitorReport()
      .then(setData)
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    load();
  }, []);

  const trigger = async () => {
    setBusy(true);
    try {
      await runMonitor();
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel-body">
      <p className="panel-hint">
        A separate job grades past runs on named values — never a 1-to-10 score — and every
        violation carries a rationale. It is not part of the chat loop and cannot change what
        you were told. Real entry point: <code>python -m backend.monitor --watch 300</code>.
      </p>
      <button className="new-btn" onClick={trigger} disabled={busy}>
        {busy ? "grading…" : "▶ Run a pass now"}
      </button>

      {err ? <p className="panel-err">{err}</p> : null}

      {data?.judgments?.length ? (
        <>
          <h3 className="panel-h">Verdicts</h3>
          {data.judgments.map((j) => (
            <div className="judgment" key={j.run_id}>
              <div className="chips">
                <span className={`chip ${VERDICT_CLASS[j.prompt_adherence] || ""}`}>
                  {j.prompt_adherence}
                </span>
                <span className={`chip ${VERDICT_CLASS[j.hint_leakage] || ""}`}>
                  leakage: {j.hint_leakage}
                </span>
                <span className={`chip ${VERDICT_CLASS[j.task_completion] || ""}`}>
                  {j.task_completion}
                </span>
                {j.injection_resisted !== "not_applicable" ? (
                  <span className={`chip ${VERDICT_CLASS[j.injection_resisted] || ""}`}>
                    injection: {j.injection_resisted}
                  </span>
                ) : null}
              </div>
              <div className="memdoc-meta">
                {j.user_id} · {j.intent} · {j.cited_rules.join(", ") || "no rules cited"}
              </div>
              <div className="judgment-q">“{(j.user_message || "").slice(0, 180)}”</div>
              <div className="judgment-r">{j.rationale}</div>
            </div>
          ))}
        </>
      ) : (
        <p className="panel-hint">Nothing graded yet — chat a little, then run a pass.</p>
      )}

      {data?.report ? (
        <>
          <h3 className="panel-h">Latest report · {data.file}</h3>
          <div className="markdown report">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.report}</ReactMarkdown>
          </div>
        </>
      ) : null}
    </div>
  );
}
