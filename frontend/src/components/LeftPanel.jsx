import { useState } from "react";
import MemoryPanel from "./MemoryPanel.jsx";
import MonitorPanel from "./MonitorPanel.jsx";
import NotesPanel from "./NotesPanel.jsx";
import ProblemPanel from "./ProblemPanel.jsx";

const TABS = [
  ["problem", "Problem"],
  ["notes", "Notes"],
  ["memory", "Memory"],
  ["monitor", "Monitor"],
];

// The left column: who you are, then one of four views. Identity sits at the
// top because switching it switches every private thing below.
export default function LeftPanel({ user, onSwitchUser, ...problemProps }) {
  const [tab, setTab] = useState("problem");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(user);

  const commit = () => {
    setEditing(false);
    const name = draft.trim();
    if (name && name !== user) onSwitchUser(name);
    else setDraft(user);
  };

  return (
    <aside>
      <div className="idbar">
        <span className="idlabel">signed in as</span>
        {editing ? (
          <input
            className="idinput"
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setDraft(user);
                setEditing(false);
              }
            }}
          />
        ) : (
          <button
            className="idname"
            onClick={() => {
              setDraft(user);
              setEditing(true);
            }}
            title="Switch learner — private memory follows the name"
          >
            {user} ✎
          </button>
        )}
        <span className="idhint">no password — the name scopes memory</span>
      </div>

      <div className="tabs">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            className={`tab${tab === id ? " active" : ""}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "problem" ? <ProblemPanel {...problemProps} /> : null}
      {tab === "notes" ? <NotesPanel user={user} /> : null}
      {tab === "memory" ? <MemoryPanel user={user} /> : null}
      {tab === "monitor" ? <MonitorPanel /> : null}
    </aside>
  );
}
