import { useCallback, useEffect, useState } from "react";
import ProblemPanel from "./components/ProblemPanel.jsx";
import Chat from "./components/Chat.jsx";
import { getHistory, getProblem, getProgress, newProblem, resetSession, sendChat } from "./api.js";

const WELCOME =
  "Read the problem on the left, then describe how you'd solve it and I'll build and run your approach. I won't tell you how to solve it — that's yours. Ask me general concepts anytime, or say \"give me another problem\" to switch.";

function tagFor(d) {
  let tag = d.intent;
  if (d.meta?.verdict) tag += ` · ${d.meta.verdict}`;
  if (d.meta?.attempt_number) tag += ` · attempt ${d.meta.attempt_number}`;
  return tag;
}

export default function App() {
  const [problem, setProblem] = useState(null);
  const [progress, setProgress] = useState(null);
  const [messages, setMessages] = useState([]);
  const [sending, setSending] = useState(false);
  const [loadingNew, setLoadingNew] = useState(false);

  useEffect(() => {
    (async () => {
      const [p, pr, h] = await Promise.all([getProblem(), getProgress(), getHistory()]);
      setProblem(p);
      setProgress(pr);
      setMessages(
        h.messages.length
          ? h.messages.map((m) => ({ role: m.role, content: m.content, tag: m.intent || "" }))
          : [{ role: "assistant", content: WELCOME, tag: "tutor" }],
      );
    })().catch(() => {
      setMessages([{ role: "assistant", content: "Couldn't reach the server.", tag: "error" }]);
    });
  }, []);

  const handleSend = useCallback(async (text) => {
    setMessages((m) => [...m, { role: "user", content: text }]);
    setSending(true);
    try {
      const d = await sendChat(text);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: d.reply, tag: tagFor(d), code: d.meta?.cpp_source || null },
      ]);
      if (d.intent === "new_problem") setProblem(await getProblem());
      if (d.intent === "solution" || d.intent === "new_problem") setProgress(await getProgress());
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `Something went wrong: ${e}`, tag: "error" }]);
    } finally {
      setSending(false);
    }
  }, []);

  const handleNew = useCallback(async () => {
    setLoadingNew(true);
    try {
      const p = await newProblem();
      setProblem(p);
      setProgress(await getProgress());
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `New problem loaded: ${p.name}. Read it on the left and describe your approach.`,
          tag: "new_problem",
        },
      ]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `Couldn't fetch a new problem: ${e}`, tag: "error" }]);
    } finally {
      setLoadingNew(false);
    }
  }, []);

  const handleReset = useCallback(async () => {
    if (!window.confirm(
      "Start over? This wipes this session's chat, progress, attempts, and " +
      "summaries, and loads a fresh problem. This can't be undone.")) return;
    setLoadingNew(true);
    try {
      const p = await resetSession();
      setProblem(p);
      setProgress(await getProgress());
      setMessages([{ role: "assistant", content: WELCOME, tag: "tutor" }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `Reset failed: ${e}`, tag: "error" }]);
    } finally {
      setLoadingNew(false);
    }
  }, []);

  return (
    <div className="app">
      <ProblemPanel
        problem={problem}
        progress={progress}
        onNew={handleNew}
        loadingNew={loadingNew}
        onSummarize={() => handleSend("summarize")}
        onReset={handleReset}
        busy={sending}
      />
      <Chat messages={messages} onSend={handleSend} sending={sending} />
    </div>
  );
}
