import { useCallback, useEffect, useState } from "react";
import LeftPanel from "./components/LeftPanel.jsx";
import Chat from "./components/Chat.jsx";
import {
  getHistory, getProblem, getProgress, newProblem, resetSession, sendChat,
  setUser as setUserApi, whoami,
} from "./api.js";

const WELCOME =
  "Read the problem on the left, then describe how you'd solve it and I'll build and run your approach. I won't tell you how to solve it — that's yours. Ask me general concepts anytime, or say \"give me another problem\" to switch.";

function tagFor(d) {
  let tag = d.intent;
  if (d.meta?.verdict) tag += ` · ${d.meta.verdict}`;
  if (d.meta?.attempt_number) tag += ` · attempt ${d.meta.attempt_number}`;
  // surface the second agent when it actually did something
  if (d.meta?.critic_rounds > 1) tag += ` · critic: ${d.meta.critic_rounds} rounds`;
  if (d.meta?.critic_status === "escalate") tag += " · escalated";
  if (d.meta?.memory_saved) tag += ` · remembered a ${d.meta.memory_saved.type}`;
  return tag;
}

const MIN_LEFT = 340;

export default function App() {
  const [problem, setProblem] = useState(null);
  const [progress, setProgress] = useState(null);
  const [messages, setMessages] = useState([]);
  const [sending, setSending] = useState(false);
  const [loadingNew, setLoadingNew] = useState(false);
  const [user, setUser] = useState("guest");
  const [leftW, setLeftW] = useState(() => Number(localStorage.getItem("cp_leftw")) || 560);
  const [focus, setFocus] = useState(() => localStorage.getItem("cp_focus") === "1");

  useEffect(() => {
    localStorage.setItem("cp_leftw", String(leftW));
  }, [leftW]);
  useEffect(() => {
    localStorage.setItem("cp_focus", focus ? "1" : "0");
  }, [focus]);

  const startDrag = useCallback((e) => {
    e.preventDefault();
    const onMove = (ev) => {
      const max = Math.max(MIN_LEFT, window.innerWidth - 360);
      setLeftW(Math.min(Math.max(ev.clientX, MIN_LEFT), max));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, []);

  // Load (or reload) everything scoped to the current learner. Switching user
  // switches the whole scope, so this runs again on every switch.
  const loadAll = useCallback(async () => {
    const [who, p, pr, h] = await Promise.all([
      whoami(), getProblem(), getProgress(), getHistory(),
    ]);
    setUser(who.user);
    setProblem(p);
    setProgress(pr);
    setMessages(
      h.messages.length
        ? h.messages.map((m) => ({ role: m.role, content: m.content, tag: m.intent || "" }))
        : [{ role: "assistant", content: WELCOME, tag: "tutor" }],
    );
  }, []);

  useEffect(() => {
    loadAll().catch(() => {
      setMessages([{ role: "assistant", content: "Couldn't reach the server.", tag: "error" }]);
    });
  }, [loadAll]);

  const handleSwitchUser = useCallback(async (name) => {
    setLoadingNew(true);
    try {
      const { user: uid } = await setUserApi(name);
      setUser(uid);
      await loadAll();
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: `Couldn't switch user: ${e}`, tag: "error" }]);
    } finally {
      setLoadingNew(false);
    }
  }, [loadAll]);

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
    <div className={`app${focus ? " focus" : ""}`} style={{ "--left-w": `${leftW}px` }}>
      <LeftPanel
        user={user}
        onSwitchUser={handleSwitchUser}
        problem={problem}
        progress={progress}
        onNew={handleNew}
        loadingNew={loadingNew}
        onSummarize={() => handleSend("summarize")}
        onReset={handleReset}
        focus={focus}
        onToggleFocus={() => setFocus((f) => !f)}
        busy={sending}
      />
      <div className="divider" onMouseDown={startDrag} title="Drag to resize" />
      <Chat messages={messages} onSend={handleSend} sending={sending} />
    </div>
  );
}
