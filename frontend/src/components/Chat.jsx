import { useEffect, useRef, useState } from "react";
import Message from "./Message.jsx";

// Right panel: scrolling message list + composer.
export default function Chat({ messages, onSend, sending }) {
  const [text, setText] = useState("");
  const logRef = useRef(null);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  function submit(e) {
    e.preventDefault();
    const t = text.trim();
    if (!t || sending) return;
    setText("");
    onSend(t);
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(e);
    }
  }

  return (
    <main>
      <div className="log" ref={logRef}>
        {messages.map((m, i) => (
          <Message key={i} role={m.role} content={m.content} tag={m.tag} code={m.code} />
        ))}
        {sending ? (
          <div className="msg bot">
            <span className="tag">thinking</span>
            <span className="dots">…</span>
          </div>
        ) : null}
      </div>

      <form className="composer" onSubmit={submit}>
        <textarea
          rows={2}
          value={text}
          placeholder="Describe your approach, ask a concept question, or say 'new problem'…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button className="send" type="submit" disabled={sending || !text.trim()}>
          Send
        </button>
      </form>
    </main>
  );
}
