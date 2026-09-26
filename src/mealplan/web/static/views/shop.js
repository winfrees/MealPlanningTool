// Shopping list (UI-5, SHP-1..5): store order, tick off as you shop, export.
import { html, useState, useEffect, useApi, ErrorNote, Loading } from "../lib.js";

function useTicks(week) {
  const key = `mealplan:ticks:${week}`;
  const [ticks, setTicks] = useState({});
  useEffect(() => {
    if (!week) return;
    try { setTicks(JSON.parse(localStorage.getItem(key) || "{}")); } catch { setTicks({}); }
  }, [key]);
  const toggle = (name) => {
    const next = { ...ticks, [name]: !ticks[name] };
    setTicks(next);
    try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* private mode */ }
  };
  return [ticks, toggle];
}

export function ShopView() {
  const [calendar] = useApi("/api/calendar");
  const start = calendar?.week_start;
  const [list, error] = useApi(start ? `/api/list?start=${start}` : null);
  const [ticks, toggle] = useTicks(list?.week_start);
  if (error) return html`<h1>Shopping</h1><${ErrorNote} error=${error} /><p><a href="#/week">Plan the week first.</a></p>`;
  if (!list) return html`<${Loading} />`;
  const sections = [];
  for (const line of list.lines) {
    const last = sections[sections.length - 1];
    if (!last || last.name !== line.section) sections.push({ name: line.section, lines: [line] });
    else last.lines.push(line);
  }
  const left = list.lines.filter((l) => !ticks[l.name]).length;
  return html`
    <div class="toolbar">
      <h1>Shopping</h1>
      <span class="muted">${left} of ${list.lines.length} left</span>
    </div>
    <p class="actions">
      <a class="button secondary" href=${`/api/list/export?start=${list.week_start}&format=text`}>Text</a>
      <a class="button secondary" href=${`/api/list/export?start=${list.week_start}&format=pdf`}>PDF</a>
    </p>
    ${sections.map((s) => html`
      <section class="card">
        <h2>${s.name[0].toUpperCase() + s.name.slice(1).replace("-", " ")}</h2>
        <ul class="checklist">
          ${s.lines.map((l) => html`
            <li class=${ticks[l.name] ? "done" : ""}>
              <label><input type="checkbox" checked=${!!ticks[l.name]} onChange=${() => toggle(l.name)} />
                <span>${l.text}</span></label>
            </li>`)}
        </ul>
      </section>`)}
    ${list.have.length ? html`<section class="card"><h2>Already have</h2>
      <ul>${list.have.map((l) => html`<li>${l.text}</li>`)}</ul></section>` : null}
    ${list.staples.length ? html`<p class="muted">Staples assumed on hand: ${list.staples.join(", ")}</p>` : null}
    ${list.checks.length ? html`<section class="card warn"><h2>Check these</h2>
      <ul>${list.checks.map((c) => html`<li>${c}</li>`)}</ul></section>` : null}`;
}
