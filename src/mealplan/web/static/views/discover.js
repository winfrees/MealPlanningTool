// Discover (M5): new recipes from the web. Links are fetched and checked by the app itself;
// the scout (API key) or a Claude chat (no key) only proposes links.
import { html, useState, useEffect, api, useApi, ErrorNote, Loading, count } from "../lib.js";

const VERDICT = {
  ok: "fits",
  duplicate: "looks familiar",
  blocked: "breaks a rule",
  exists: "already yours",
  unreadable: "couldn't read",
};

function facts(f) {
  if (!f || !f.ingredients) return "";
  return [
    f.role,
    f.protein,
    f.active_minutes != null ? `${f.active_minutes} min hands-on` : null,
    f.weeknight_ok === false ? "not a weeknight" : null,
    f.spice ? `spice ${f.spice}/3` : null,
    f.servings ? `serves ${f.servings}` : null,
    count(f.ingredients, "ingredient"),
  ].filter(Boolean).join(" · ");
}

function Candidate({ c, role }) {
  const [added, setAdded] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const add = async (variantOf) => {
    setBusy(true); setError(null);
    try {
      setAdded(await api("/api/discover/add", {
        method: "POST", body: { url: c.url, role: role || null, variant_of: variantOf || null },
      }));
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const similar = c.similar[0];
  return html`
    <li class=${`card candidate ${c.verdict}`}>
      <div class="toolbar">
        <h3><a href=${c.url} target="_blank" rel="noopener noreferrer">${c.title || c.url}</a></h3>
        <span class=${`badge ${c.verdict}`}>${VERDICT[c.verdict]}</span>
      </div>
      <p class="muted small">${[c.site, facts(c.facts)].filter(Boolean).join(" · ")}</p>
      ${c.note ? html`<p class="small">Suggested because: ${c.note}</p>` : null}
      ${c.reasons.length ? html`<p class="small warn-text">${c.reasons.join("; ")}</p>` : null}
      ${added ? html`<p class="note" role="status">Added as ${added.ref}${added.family ? ` in the ${added.family} family` : ""}.
          <a href="#/review">Check it in Review</a></p>`
        : c.verdict === "ok" ? html`
          <button disabled=${busy} onClick=${() => add()}>Add to Review</button>`
        : c.verdict === "duplicate" && similar ? html`
          <div class="actions">
            <button disabled=${busy} onClick=${() => add(similar.ref)}>Add as a version of ${similar.title}</button>
            <button class="secondary" disabled=${busy} onClick=${() => add()}>Add as its own recipe</button>
          </div>` : null}
      <${ErrorNote} error=${error} />
    </li>`;
}

function Candidates({ list, role }) {
  if (!list) return null;
  const good = list.filter((c) => c.verdict === "ok" || c.verdict === "duplicate");
  const rest = list.filter((c) => !good.includes(c));
  return html`
    ${good.length ? html`<ul class="plain">${good.map((c) => html`<${Candidate} key=${c.url} c=${c} role=${role} />`)}</ul>`
      : html`<p class="muted">None of these fit.</p>`}
    ${rest.length ? html`
      <details><summary>${count(rest.length, "link")} left out</summary>
        <ul class="plain">${rest.map((c) => html`<${Candidate} key=${c.url} c=${c} role=${role} />`)}</ul>
      </details>` : null}`;
}

function Links() {
  const [urls, setUrls] = useState("");
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const check = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null); setList(null);
    try { setList(await api("/api/discover/check", { method: "POST", body: { urls } })); }
    catch (err) { setError(err); } finally { setBusy(false); }
  };
  return html`
    <section class="card">
      <h2>Add from a link</h2>
      <form onSubmit=${check}>
        <label>Recipe links <span class="muted small">(one or more; no AI involved)</span>
          <textarea rows="3" value=${urls} placeholder="https://…"
            onInput=${(e) => setUrls(e.target.value)}></textarea></label>
        <button type="submit" disabled=${busy || !urls.trim()}>${busy ? "Checking…" : "Check links"}</button>
      </form>
      <${ErrorNote} error=${error} />
      <${Candidates} list=${list} />
    </section>`;
}

function ChatPath({ prompt, role }) {
  const [reply, setReply] = useState("");
  const [copied, setCopied] = useState(false);
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setCopied(false); setList(null); }, [prompt]);
  const copy = async () => {
    try { await navigator.clipboard.writeText(prompt); setCopied(true); } catch { setCopied(false); }
  };
  const check = async () => {
    setBusy(true); setError(null);
    try { setList(await api("/api/discover/chat", { method: "POST", body: { reply, role } })); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  return html`
    <ol class="small">
      <li>Copy this prompt into a new chat at <a href="https://claude.ai/new" target="_blank" rel="noopener noreferrer">claude.ai</a>.</li>
      <li>Paste Claude's whole reply below and check the links.</li>
    </ol>
    <div class="actions"><button class="secondary small" onClick=${copy}>${copied ? "Prompt copied" : "Copy prompt"}</button></div>
    <textarea readonly rows="5" aria-label="Chat prompt" value=${prompt} onFocus=${(e) => e.target.select()}></textarea>
    <label>Claude's reply
      <textarea rows="4" value=${reply} onInput=${(e) => setReply(e.target.value)}></textarea></label>
    <button disabled=${busy || !reply.trim()} onClick=${check}>${busy ? "Checking…" : "Check the links"}</button>
    <${ErrorNote} error=${error} />
    <${Candidates} list=${list} role=${role} />`;
}

function Scout({ info }) {
  const [request, setRequest] = useState("");
  const [job, setJob] = useState(info.job);
  const [chat, setChat] = useState(null);
  const [role, setRole] = useState(null);
  const [error, setError] = useState(null);
  const running = job && job.state === "running";
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => api("/api/discover/scout").then(setJob, setError), 2000);
    return () => clearInterval(timer);
  }, [running]);
  const go = async (body, presetRole) => {
    setError(null); setRole(presetRole || null);
    try {
      if (info.scout_ready) setJob(await api("/api/discover/scout", { method: "POST", body }));
      else setChat((await api("/api/discover/chat-prompt", { method: "POST", body })).prompt);
    } catch (e) { setError(e); }
  };
  return html`
    <section class="card">
      <h2>Find new recipes</h2>
      <p class="muted small">${info.scout_ready
        ? "Claude searches the web; the app then reads each page and checks it against your rules and recipes."
        : "No API key saved, so this makes a prompt for a Claude chat instead; the app still checks every link."}</p>
      <div class="actions">
        ${info.presets.map((p) => html`
          <button class="secondary" disabled=${running} onClick=${() => go({ preset: p.key }, p.role)}>${p.label}</button>`)}
      </div>
      <form class="row" onSubmit=${(e) => { e.preventDefault(); if (request.trim()) go({ request }); }}>
        <input value=${request} placeholder="Or ask for something: vegetarian curries…"
          onInput=${(e) => setRequest(e.target.value)} aria-label="What are you looking for" />
        <button type="submit" disabled=${running || !request.trim()}>${info.scout_ready ? "Search" : "Make prompt"}</button>
      </form>
      <${ErrorNote} error=${error} />
      ${running ? html`<p role="status">Searching and checking recipes… this takes a minute or two.</p>` : null}
      ${job && job.state === "failed" ? html`<p class="warn">${job.message}</p>` : null}
      ${job && job.state === "done" ? html`<${Candidates} list=${job.candidates} role=${role} />` : null}
      ${chat ? html`<${ChatPath} prompt=${chat} role=${role} />` : null}
    </section>`;
}

export function DiscoverView() {
  const [info, error] = useApi("/api/discover");
  return html`
    <h1>Discover</h1>
    <p class="muted">New finds go to Review first, and the planner adds at most one new dinner a
      week. Rate one 4 or 5 after cooking and it joins your recipes.</p>
    <${ErrorNote} error=${error} />
    ${info ? html`<${Scout} info=${info} />` : html`<${Loading} />`}
    <${Links} />`;
}
