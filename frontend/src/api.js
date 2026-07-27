// Thin wrappers over the FastAPI endpoints. Cookies (the session `uid`) ride
// along automatically since everything is same-origin (or proxied in dev).
const json = (r) => {
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
};

const post = (url, body) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(json);

export const getProblem = () => fetch("/problem").then(json);
export const newProblem = () => post("/new-problem");
export const resetSession = () => post("/reset");
export const getHistory = () => fetch("/history").then(json);
export const getProgress = () => fetch("/progress").then(json);
export const sendChat = (message) => post("/chat", { message });

// identity — no password: the name scopes memory, it doesn't protect anything
export const whoami = () => fetch("/whoami").then(json);
export const setUser = (user) => post("/whoami", { user });

// shared, learner-written notes on the current problem
export const getNotes = () => fetch("/notes").then(json);
export const addNote = (body, kind = "note") => post("/notes", { body, kind });
export const deleteNote = (id) =>
  fetch(`/notes/${id}`, { method: "DELETE" }).then(json);

// everything the system remembers, across all three stores
export const getMemory = () => fetch("/memory").then(json);
export const forgetDoc = (id) =>
  fetch(`/memory/${id}`, { method: "DELETE" }).then(json);

// the out-of-band monitor
export const getMonitorReport = () => fetch("/monitor/report").then(json);
export const runMonitor = () => post("/monitor/run");
