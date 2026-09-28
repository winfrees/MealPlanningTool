// Review queue (UI-5, ING-3): approve, reject, merge a copy, or group a variant.
import { html, useState, api, useApi, ErrorNote, Loading, count } from "../lib.js";

function Draft({ item, onDone }) {
  const [family, setFamily] = useState("");
  const [error, setError] = useState(null);
  const act = async (path, body, label) => {
    setError(null);
    try { await api(path, { method: "POST", body }); onDone(`${item.title}: ${label}`); }
    catch (e) { setError(e); }
  };
  const base = `/api/review/${encodeURIComponent(item.ref)}`;
  return html`
    <section class="card">
      <h2><a href=${`#/recipe/${item.ref}`}>${item.title}</a> <span class="muted small">${item.ref}</span></h2>
      ${item.issues.length ? html`<ul class="issues">${item.issues.map((i) => html`<li>${i}</li>`)}</ul>`
        : html`<p class="muted">No issues found.</p>`}
      ${item.similar.length ? html`
        <p>Looks like:</p>
        <ul class="plain">${item.similar.map((m) => html`
          <li><a href=${`#/recipe/${m.ref}`}>${m.title}</a> <span class="muted">(${Math.round(m.score * 100)}% similar)</span>
            <button class="secondary small" onClick=${() => act(`${base}/merge`, { into: m.ref }, `merged into ${m.ref}`)}>Same recipe: merge</button></li>`)}
        </ul>` : null}
      <div class="actions">
        <a class="button secondary" href=${`#/edit/${encodeURIComponent(item.ref)}`}>Edit</a>
        <button onClick=${() => act(`${base}/approve`, undefined, "approved")}>Approve</button>
        <button class="danger" onClick=${() => act(`${base}/reject`, undefined, "rejected")}>Reject</button>
      </div>
      <form class="actions" onSubmit=${(e) => { e.preventDefault(); if (family.trim()) act(`${base}/family`, { name: family.trim() }, `in family ${family.trim()}`); }}>
        <input placeholder="variant family, e.g. injera" value=${family} onInput=${(e) => setFamily(e.target.value)} aria-label="Variant family" />
        <button class="secondary" type="submit">Group as variant</button>
      </form>
      <${ErrorNote} error=${error} />
    </section>`;
}

export function ReviewView() {
  const [items, error, reload] = useApi("/api/review");
  const [message, setMessage] = useState(null);
  const [actionError, setActionError] = useState(null);
  const done = (text) => { setMessage(text); reload(); };
  const approveReady = async () => {
    setActionError(null);
    try {
      const r = await api("/api/review/approve-ready", { method: "POST" });
      done(r.approved.length ? `Approved ${count(r.approved.length, "recipe")} with no issues.`
        : "Every recipe here needs a look first.");
    } catch (e) { setActionError(e); }
  };
  return html`
    <h1>Review queue</h1>
    <${ErrorNote} error=${error || actionError} />
    ${items && items.length ? html`
      <div class="actions">
        <button onClick=${approveReady}>Approve the ones that look fine</button>
        <span class="muted small">No issues and no look-alike already in the library.</span>
      </div>` : null}
    ${message ? html`<p class="note" role="status">${message}</p>` : null}
    ${!items ? html`<${Loading} />` : items.length === 0 ? html`<p class="muted">Nothing waiting for review.</p>`
      : items.map((item) => html`<${Draft} key=${item.ref} item=${item} onDone=${done} />`)}`;
}
