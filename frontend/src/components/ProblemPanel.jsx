import { useEffect, useRef } from "react";

// Left panel: problem name, metadata, progress line, and the rich HTML statement
// (with MathJax typesetting the $$$…$$$ math after each update).
export default function ProblemPanel({ problem, progress, onNew, loadingNew, onSummarize, busy }) {
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
    <aside>
      <div className="head">
        <h1>{problem ? problem.name : "loading…"}</h1>
        <div className="head-btns">
          <button className="new-btn" onClick={onSummarize} disabled={busy} title="Recap your work on this problem">
            📝 Summarize
          </button>
          <button className="new-btn" onClick={onNew} disabled={loadingNew}>
            {loadingNew ? "…" : "🎲 New problem"}
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
    </aside>
  );
}
