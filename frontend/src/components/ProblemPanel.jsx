import { useEffect, useRef } from "react";

// Problem name, metadata, progress line, and the rich HTML statement (with
// MathJax typesetting the $$$…$$$ math after each update). Rendered inside the
// left panel's "Problem" tab — the <aside> itself lives in LeftPanel.
export default function ProblemPanel({ problem, progress, onNew, loadingNew, onSummarize, onReset, focus, onToggleFocus, busy }) {
  const stmtRef = useRef(null);

  useEffect(() => {
    const el = stmtRef.current;
    if (el && window.MathJax && window.MathJax.typesetPromise) {
      window.MathJax.typesetPromise([el]).catch(() => {});
    }
  }, [problem?.statement_html]);

  const meta = [];
  if (problem?.rating) meta.push(`rating ${problem.rating}`);
  if (problem?.source) meta.push(problem.source);
  if (typeof problem?.num_sample_tests === "number")
    meta.push(`${problem.num_sample_tests} sample test(s)`);

  return (
    <>
      <div className="head">
        <h1>{problem ? problem.name : "loading…"}</h1>
        <div className="head-btns">
          <button className="new-btn" onClick={onToggleFocus}
                  title={focus ? "Show the chat again" : "Hide the chat and read full-width"}>
            {focus ? "✕ Exit focus" : "🔎 Focus"}
          </button>
          <button className="new-btn" onClick={onSummarize} disabled={busy} title="Recap your work on this problem">
            📝 Summarize
          </button>
          <button className="new-btn" onClick={onNew} disabled={loadingNew}>
            {loadingNew ? "…" : "🎲 New problem"}
          </button>
          <button className="new-btn reset-btn" onClick={onReset} disabled={busy || loadingNew}
                  title="Wipe this session and start over">
            ↺ Start over
          </button>
        </div>
      </div>

      <div className="pmeta">
        {meta.join(" · ")}
        {problem?.url ? (
          <>
            {"  "}
            <a href={problem.url} target="_blank" rel="noreferrer">
              open on Codeforces ↗
            </a>
          </>
        ) : null}
      </div>

      <div className="progress">
        {progress
          ? `progress: solved ${progress.solved}/${progress.seen} seen · ${progress.attempts} attempts` +
            (progress.by_verdict && Object.keys(progress.by_verdict).length
              ? " · " +
                Object.keys(progress.by_verdict)
                  .sort()
                  .map((k) => `${k}:${progress.by_verdict[k]}`)
                  .join(" ")
              : "")
          : "progress: —"}
      </div>

      {problem?.statement_html ? (
        <div
          className="statement"
          ref={stmtRef}
          dangerouslySetInnerHTML={{ __html: problem.statement_html }}
        />
      ) : (
        <pre className="statement-text">{problem?.statement || ""}</pre>
      )}
    </>
  );
}
