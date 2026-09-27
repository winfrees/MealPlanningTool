// Getting started (UI-8): add the recipe collection, review it, plan the first week.
import { html, useState, useEffect, api, useApi, ErrorNote, Loading, count } from "../lib.js";

function Progress({ job }) {
  if (job.state === "running") {
    return html`<p role="status">Importing recipes… ${job.done} of ${job.total}
      <progress max=${job.total} value=${job.done}></progress></p>`;
  }
  if (job.state === "idle") return null;
  return html`
    <div role="status">
      ${job.message ? html`<p class="warn">${job.message}</p>` : null}
      <p>Imported ${count(job.created, "recipe")}${job.skipped ? ` (${job.skipped} were already here)` : ""}.</p>
      ${job.needs_agent ? html`<p class="muted">${count(job.needs_agent, "recipe")} (scans or photos)
        need Claude to read them. Add your Anthropic API key below, then import again;
        recipes already imported are skipped.</p>` : null}
      ${job.failed ? html`<p class="muted">${job.failed} could not be read; see <code>mealctl import failures</code>.</p>` : null}
    </div>`;
}

// The Anthropic API key, for the pages only Claude can read. Saved on the server in .env.
function ClaudeKey({ status, onChange }) {
  const key = status.api_key;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async (path, body, done) => {
    setBusy(true); setError(null); setNote(null);
    try { await api(path, { method: "POST", body }); done(); onChange(); }
    catch (e) { setError(e); }
    finally { setBusy(false); }
  };
  const save = (e) => {
    e.preventDefault();
    run("/api/setup/api-key", { key: value }, () => {
      setValue(""); setEditing(false); setNote("Key checked and saved. Import again to add the scanned recipes.");
    });
  };
  const check = () => run("/api/setup/api-key/check", undefined, () => setNote("The key works."));
  return html`
    <div class="claude-key">
      <h4>Claude (for scanned pages)</h4>
      ${key.set && !editing ? html`
        <p>API key saved (ends in <code>${key.ends_with}</code>).</p>
        <div class="actions">
          <button class="secondary small" disabled=${busy} onClick=${check}>${busy ? "Checking…" : "Check it works"}</button>
          <button class="link" onClick=${() => { setEditing(true); setNote(null); setError(null); }}>Use a different key</button>
        </div>` : html`
        <p class="muted">Paste an API key from <a href="https://console.anthropic.com/settings/keys"
          target="_blank" rel="noopener noreferrer">console.anthropic.com</a>.
          It starts with <code>sk-ant-</code>; the model name isn't needed.</p>
        <form class="row" onSubmit=${save}>
          <input type="password" autocomplete="off" spellcheck="false" placeholder="sk-ant-…"
            value=${value} onInput=${(e) => setValue(e.target.value)} aria-label="Anthropic API key" />
          <button type="submit" disabled=${busy || !value.trim()}>${busy ? "Checking…" : "Save key"}</button>
        </form>
        ${key.set ? html`<button class="link" onClick=${() => setEditing(false)}>Cancel</button>` : null}`}
      ${note ? html`<p class="note" role="status">${note}</p>` : null}
      <${ErrorNote} error=${error} />
    </div>`;
}

function AddRecipes({ status, onChange }) {
  const [error, setError] = useState(null);
  const [uploading, setUploading] = useState(false);
  const job = status.import;
  const busy = uploading || job.state === "running";
  const importSaved = async () => {
    setError(null);
    try { await api("/api/setup/import", { method: "POST" }); onChange(); } catch (e) { setError(e); }
  };
  const upload = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    setError(null); setUploading(true);
    try { await api("/api/setup/pdf", { method: "POST", raw: file }); onChange(); }
    catch (err) { setError(err); }
    finally { setUploading(false); }
  };
  return html`
    <p>Import your recipe collection, <strong>${status.pdf_name}</strong>.</p>
    <div class="actions">
      ${status.pdf_on_disk ? html`
        <button disabled=${busy} onClick=${importSaved}>Import ${status.pdf_name}</button>` : null}
      <label class=${`button ${status.pdf_on_disk ? "secondary" : ""} ${busy ? "disabled" : ""}`}>
        ${uploading ? "Uploading…" : status.pdf_on_disk ? "Upload a copy" : "Choose the PDF…"}
        <input type="file" accept="application/pdf,.pdf" class="visually-hidden"
          disabled=${busy} onChange=${upload} aria-label="Recipe PDF" />
      </label>
    </div>
    <${Progress} job=${job} />
    <${ErrorNote} error=${error} />
    <${ClaudeKey} status=${status} onChange=${onChange} />`;
}

export function GetStarted({ status, onChange, onPlan, busy }) {
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const running = status.import.state === "running";
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(onChange, 1500);
    return () => clearInterval(timer);
  }, [running]);
  const approveReady = async () => {
    setError(null);
    try {
      const r = await api("/api/review/approve-ready", { method: "POST" });
      setMessage(r.approved.length
        ? `Approved ${count(r.approved.length, "recipe")}. Any others need a quick look in Review.`
        : "Every draft needs a quick look: open Review.");
      onChange();
    } catch (e) { setError(e); }
  };
  const added = status.approved + status.drafts > 0;
  return html`
    <section class="card start">
      <h2>Get started</h2>
      <ol class="steps">
        <li class=${added ? "done" : ""}>
          <h3>Add your recipes</h3>
          <${AddRecipes} status=${status} onChange=${onChange} />
        </li>
        <li class=${status.approved ? "done" : ""}>
          <h3>Check them</h3>
          ${status.drafts ? html`
            <p>${count(status.drafts, "recipe")} waiting for a check before the planner uses them.</p>
            <div class="actions">
              <button disabled=${running} onClick=${approveReady}>Approve the ones that look fine</button>
              <a class="button secondary" href="#/review">Open Review</a>
            </div>` : html`<p class="muted">Imported recipes wait here for you to approve.</p>`}
          ${message ? html`<p class="note" role="status">${message}</p>` : null}
          <${ErrorNote} error=${error} />
        </li>
        <li>
          <h3>Plan your week</h3>
          <p class="muted">The planner fills dinners and lunches from your approved recipes,
            around the standing meals of your base week.</p>
          <button disabled=${busy || running || !status.approved} onClick=${onPlan}>Plan this week</button>
        </li>
      </ol>
    </section>`;
}

// Always reachable (#/start): import more recipes or change the API key after the first week.
export function StartView() {
  const [status, error, reload] = useApi("/api/setup");
  const [planned, setPlanned] = useState(null);
  const plan = async () => {
    await api("/api/week/plan", { method: "POST", body: {} });
    setPlanned(true);
  };
  if (!status) return html`<${ErrorNote} error=${error} /><${Loading} />`;
  return html`
    <h1>Import and setup</h1>
    ${planned ? html`<p class="note" role="status">Planned. <a href="#/week">Open the week</a>.</p>` : null}
    <${GetStarted} status=${status} onChange=${reload} onPlan=${plan} busy=${false} />`;
}
