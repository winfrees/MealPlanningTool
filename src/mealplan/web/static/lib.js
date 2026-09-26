// Shared UI helpers: Preact + htm, the API client, and small components.
import { h, render } from "./vendor/preact.module.js";
import { useEffect, useState, useCallback } from "./vendor/hooks.module.js";
import htm from "./vendor/htm.module.js";

export const html = htm.bind(h);
export { render, useEffect, useState, useCallback };

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Every call sends the X-Mealplan header the server requires on writes (CSRF, UI-6).
export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "X-Mealplan": "1", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
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
export function useApi(path) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const reload = useCallback(() => {
    if (!path) return;
    setState((s) => ({ ...s, loading: true }));
    api(path).then(
      (data) => setState({ data, error: null, loading: false }),
      (error) => setState({ data: null, error, loading: false }),
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

export const iso = (d) => d.toISOString().slice(0, 10);

export function addDays(isoDate, n) {
  const d = new Date(`${isoDate}T12:00:00`);
  d.setDate(d.getDate() + n);
  return iso(d);
}
