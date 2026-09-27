// Week plan screen (UI-5): plan, re-plan, swap, lock, cooked, day cards, prep checklist.
import { html, useState, useEffect, api, useApi, ErrorNote, Loading, fmtMinutes, addDays, count } from "../lib.js";
import { GetStarted } from "./start.js";

function SwapPicker({ day, meal, onPick, onCancel }) {
  const [q, setQ] = useState("");
  const role = meal === "dinner" ? "dinner" : "";
  const [recipes, error] = useApi(`/api/recipes?role=${role}&q=${encodeURIComponent(q)}`);
  return html`
    <div class="picker">
      <input type="search" placeholder="Search recipes" value=${q}
        onInput=${(e) => setQ(e.target.value)} aria-label="Search recipes to swap in" />
      <${ErrorNote} error=${error} />
      <ul class="plain">
        ${(recipes || []).slice(0, 12).map((r) => html`
          <li><button class="link" onClick=${() => onPick(r.ref)}>${r.title}</button>
            <span class="muted"> ${r.active_minutes != null ? `${r.active_minutes} min` : ""}</span></li>`)}
      </ul>
      <button class="secondary" onClick=${onCancel}>Cancel</button>
    </div>`;
}

function MealLine({ label, meal, day, onSwap, onCooked, busy, locked }) {
  const [picking, setPicking] = useState(false);
  if (!meal) return null;
  const title = meal.ref && !meal.components.length
    ? html`<a href=${`#/recipe/${meal.ref}`}>${meal.title}</a>` : meal.title;
  return html`
    <div class="meal">
      <div>
        <span class="tag">${label}</span> ${title}
        ${meal.leftover_of ? html` <span class="badge">leftovers</span>` : null}
        ${meal.locked ? html` <span class="badge">swapped</span>` : null}
        <span class="muted"> · ${meal.servings} servings</span>
      </div>
      <div class="actions">
        <button class="secondary small" disabled=${busy || locked} onClick=${() => setPicking(!picking)}>Swap</button>
        ${label === "Dinner" && meal.ref
          ? html`<button class="secondary small" disabled=${busy} onClick=${() => onCooked(day, meal.meal)}>Cooked</button>`
          : null}
      </div>
      ${picking ? html`<${SwapPicker} day=${day} meal=${meal.meal}
          onPick=${(ref) => { setPicking(false); onSwap(day, meal.meal, ref); }}
          onCancel=${() => setPicking(false)} />` : null}
    </div>`;
}

function Prep({ prep, onDone, busy }) {
  if (!prep.tasks.length) return html`<p class="muted">Nothing to prep this week.</p>`;
  return html`
    <section class="card">
      <h2>Prep day · about ${fmtMinutes(prep.est_minutes)}</h2>
      <ol>
        ${prep.tasks.map((t) => html`
          <li><span class="muted">[${t.start ? fmtMinutes(t.start) : "0 min"}]</span> ${t.name}:
            ${t.active} min hands-on${t.passive ? `, then ${t.passive} min${t.equipment.length ? ` on the ${t.equipment.join(", ")}` : ""}` : ""}
            ${t.note ? html` <span class="muted">(${t.note})</span>` : null}</li>`)}
      </ol>
      ${prep.storage.length ? html`<h3>Storage</h3><ul>${prep.storage.map((s) => html`<li>${s}</li>`)}</ul>` : null}
      ${prep.conflicts.map((c) => html`<p class="warn">${c}</p>`)}
      <button disabled=${busy} onClick=${onDone}>Prep done</button>
    </section>`;
}

export function WeekView() {
  const [calendar] = useApi("/api/calendar");
  const [setup, , reloadSetup] = useApi("/api/setup");
  const [start, setStart] = useState(null);
  const [week, setWeek] = useState(undefined);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (calendar && !start) setStart(calendar.week_start); }, [calendar]);
  useEffect(() => {
    if (!start) return;
    setWeek(undefined);
    api(`/api/week?start=${start}`).then(setWeek, setError);
  }, [start]);

  const act = async (path, body, keep = true) => {
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await api(path, { method: "POST", body: { start, ...body } });
      if (keep && result.days) setWeek(result);
      return result;
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const cooked = async (day, meal) => {
    const r = await act("/api/week/cooked", { day, meal }, false);
    if (r) setMessage(r.not_in_inventory.length
      ? `Marked cooked. Not in inventory: ${r.not_in_inventory.join(", ")}` : "Marked cooked.");
  };
  const prepDone = async () => {
    const r = await act("/api/prep/done", {}, false);
    if (r) setMessage(r.not_in_inventory.length
      ? `Prep recorded. Not in inventory: ${r.not_in_inventory.join(", ")}` : "Prep recorded.");
  };

  if (!start || !setup) return html`<${Loading} />`;
  const locked = week && week.status === "locked";
  const starting = setup.approved === 0 || setup.import.state === "running";
  const plan = () => act("/api/week/plan", {});
  return html`
    <div class="toolbar">
      <button class="secondary" aria-label="Previous week" onClick=${() => setStart(addDays(start, -7))}>‹</button>
      <h1>Week of ${new Date(`${start}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</h1>
      <button class="secondary" aria-label="Next week" onClick=${() => setStart(addDays(start, 7))}>›</button>
    </div>
    <${ErrorNote} error=${error} />
    ${message ? html`<p class="note" role="status">${message}</p>` : null}
    ${!starting && setup.drafts ? html`
      <p class="note"><a href="#/review">${count(setup.drafts, "imported recipe")}</a> waiting for a check.</p>` : null}
    ${week === undefined ? html`<${Loading} />` : week === null ? starting ? html`
      <${GetStarted} status=${setup} onChange=${reloadSetup} onPlan=${plan} busy=${busy} />` : html`
      <section class="card">
        <p>No plan for this week yet.</p>
        <button disabled=${busy} onClick=${plan}>Plan this week</button>
      </section>` : html`
      <div class="actions">
        <span class="badge">${week.status}</span>
        <button class="secondary" disabled=${busy || locked} onClick=${() => act("/api/week/plan", {})}>Re-plan</button>
        <button class="secondary" disabled=${busy || locked}
          onClick=${() => act("/api/week/plan", { seed: Math.floor(Math.random() * 1e6) })}>Shuffle</button>
        <button class="secondary" disabled=${busy} onClick=${() => act("/api/week/lock", { locked: !locked })}>
          ${locked ? "Unlock" : "Lock"}</button>
      </div>
      ${week.conflicts.length ? html`<section class="card warn"><h2>Check these</h2>
        <ul>${week.conflicts.map((c) => html`<li>${c}</li>`)}</ul></section>` : null}
      ${week.days.map((d) => html`
        <section class="card day">
          <h2>${d.label}</h2>
          <${MealLine} label="Lunch" meal=${d.lunch} day=${d.date} busy=${busy} locked=${locked}
            onSwap=${(day, meal, ref) => act("/api/week/swap", { day, meal, ref })} onCooked=${cooked} />
          <${MealLine} label="Dinner" meal=${d.dinner} day=${d.date} busy=${busy} locked=${locked}
            onSwap=${(day, meal, ref) => act("/api/week/swap", { day, meal, ref })} onCooked=${cooked} />
          <details><summary>Day card</summary>
            <ul class="plain">${d.card.map((line) => html`<li>${line.replace(/^\s*- /, "")}</li>`)}</ul>
          </details>
        </section>`)}
      <${Prep} prep=${week.prep} onDone=${prepDone} busy=${busy} />`}`;
}
