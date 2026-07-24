// Thin wrappers over the FastAPI endpoints. Cookies (the session `sid`) ride
// along automatically since everything is same-origin (or proxied in dev).
const json = (r) => {
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
};

export const getProblem = () => fetch("/problem").then(json);
export const newProblem = () => fetch("/new-problem", { method: "POST" }).then(json);
export const getHistory = () => fetch("/history").then(json);
export const getProgress = () => fetch("/progress").then(json);
export const sendChat = (message) =>
  fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  }).then(json);
