// Shared UI helpers: Preact + htm, the API client, and small components.
import { h, render } from "./vendor/preact.module.js";
import { useEffect, useState, useCallback, useRef } from "./vendor/hooks.module.js";
import htm from "./vendor/htm.module.js";

export const html = htm.bind(h);
export { render, useEffect, useState, useCallback, useRef };

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Every call sends the X-Mealplan header the server requires on writes (CSRF, UI-6).
// `body` is sent as JSON; `raw` (a File) is sent as it is.
export async function api(path, { method = "GET", body, raw } = {}) {
  const type = raw ? raw.type || "application/octet-stream" : body ? "application/json" : null;
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "X-Mealplan": "1", ...(type ? { "Content-Type": type } : {}) },
    body: raw || (body ? JSON.stringify(body) : undefined),
  });
  const data = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("mealplan:logged-out"));
    const detail = typeof data === "object" ? data.detail : data;
    const message = Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail;
    throw new ApiError(response.status, message || `Request failed (${response.status})`);
  }
  return data;
}

// Load data for a view; returns [data, error, reload, loading]. A null path waits.
// Only the latest request's answer is used: when a search changes quickly, an older,
// slower response must not overwrite a newer one.
export function useApi(path) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const latest = useRef(0);
  const reload = useCallback(() => {
    if (!path) return;
    const id = ++latest.current;
    setState((s) => ({ ...s, loading: true }));
    api(path).then(
      (data) => id === latest.current && setState({ data, error: null, loading: false }),
      (error) => id === latest.current && setState({ data: null, error, loading: false }),
    );
  }, [path]);
  useEffect(reload, [reload]);
  return [state.data, state.error, reload, state.loading];
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return html`<p class="error" role="alert">${error.message}</p>`;
}

export function Loading() {
  return html`<p class="muted">Loading…</p>`;
}

export const fmtMinutes = (m) =>
  m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}` : `${m} min`;

// "1 recipe", "3 recipes".
export const count = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;

export const iso = (d) => d.toISOString().slice(0, 10);

export function addDays(isoDate, n) {
  const d = new Date(`${isoDate}T12:00:00`);
  d.setDate(d.getDate() + n);
  return iso(d);
}
