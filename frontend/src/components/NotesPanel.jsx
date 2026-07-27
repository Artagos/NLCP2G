import { useEffect, useState } from "react";
import { addNote, deleteNote, getNotes } from "../api.js";

// Shared memory, visible: notes written by any learner on this problem. Whoever
// is signed in sees all of them, and the tutor can pull them mid-answer.
export default function NotesPanel({ user }) {
  const [notes, setNotes] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const load = () =>
    getNotes()
      .then((d) => setNotes(d.notes))
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    load();
  }, [user]);

  const submit = async () => {
    if (!draft.trim()) return;
    setBusy(true);
    try {
      const d = await addNote(draft.trim());
      setNotes(d.notes);
      setDraft("");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    await deleteNote(id);
    load();
  };

  return (
    <div className="panel-body">
      <p className="panel-hint">
        Notes are <strong>shared</strong>. Everyone working on this problem sees them, and
        their tutor can read them mid-answer. They arrive as quoted, untrusted text — the
        agent will not follow instructions written inside one.
      </p>

      {err ? <p className="panel-err">{err}</p> : null}

      <div className="note-composer">
        <textarea
          rows={3}
          value={draft}
          placeholder="Something useful for whoever tries this next — a wording trap, a sample that's easy to misread…"
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="new-btn" onClick={submit} disabled={busy || !draft.trim()}>
          {busy ? "…" : `Post as ${user}`}
        </button>
      </div>

      {notes.length === 0 ? (
        <p className="panel-hint">No notes on this problem yet.</p>
      ) : (
        notes.map((n) => (
          <div className="note" key={n.id}>
            <div className="note-head">
              <span className="note-author">{n.author}</span>
              <button className="link-btn" onClick={() => remove(n.id)} title="Delete note">
                ✕
              </button>
            </div>
            <div className="note-body">{n.body}</div>
          </div>
        ))
      )}
    </div>
  );
}
