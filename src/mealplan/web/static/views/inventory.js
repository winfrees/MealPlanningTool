// Kitchen inventory and staples (UI-5, INV-1..5).
import { html, useState, api, useApi, ErrorNote, Loading } from "../lib.js";

const LOCATIONS = ["fridge", "freezer", "pantry"];
const UNITS = ["", "oz", "lb", "g", "kg", "cup", "tbsp", "tsp", "ml", "l", "can", "jar", "package", "bag", "bunch", "clove"];

function AddItem({ onAdded }) {
  const [catalog] = useApi("/api/catalog");
  const [form, setForm] = useState({ name: "", qty: "1", unit: "", location: "fridge", best_by: "" });
  const [error, setError] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      await api("/api/inventory", { method: "POST", body: {
        name: form.name, qty: Number(form.qty), unit: form.unit || null,
        location: form.location, best_by: form.best_by || null } });
      onAdded(`Added ${form.name}.`);
      setForm({ ...form, name: "", qty: "1", best_by: "" });
    } catch (err) { setError(err); }
  };
  return html`
    <form class="card add" onSubmit=${submit}>
      <h2>Add to the kitchen</h2>
      <input list="catalog" placeholder="ingredient" value=${form.name} onInput=${set("name")} required aria-label="Ingredient" />
      <datalist id="catalog">${(catalog || []).map((n) => html`<option value=${n} />`)}</datalist>
      <div class="row">
        <input type="number" min="0" step="any" value=${form.qty} onInput=${set("qty")} required aria-label="Quantity" />
        <select value=${form.unit} onChange=${set("unit")} aria-label="Unit">
          ${UNITS.map((u) => html`<option value=${u}>${u || "count"}</option>`)}
        </select>
        <select value=${form.location} onChange=${set("location")} aria-label="Location">
          ${LOCATIONS.map((l) => html`<option value=${l}>${l}</option>`)}
        </select>
      </div>
      <label>Best by (optional) <input type="date" value=${form.best_by} onInput=${set("best_by")} /></label>
      <button type="submit">Add</button>
      <${ErrorNote} error=${error} />
    </form>`;
}

function Item({ item, onChanged, onError }) {
  const [qty, setQty] = useState(String(item.qty));
  const save = async () => {
    try { await api(`/api/inventory/${item.id}`, { method: "PATCH", body: { qty: Number(qty) } }); onChanged(`Updated ${item.name}.`); }
    catch (e) { onError(e); }
  };
  const remove = async () => {
    try { await api(`/api/inventory/${item.id}`, { method: "DELETE" }); onChanged(`Removed ${item.name}.`); }
    catch (e) { onError(e); }
  };
  return html`
    <li class=${item.expiring ? "expiring" : ""}>
      <span class="grow">${item.name}${item.best_by ? html` <span class="muted small">best by ${item.best_by}</span>` : null}
        ${item.expiring ? html` <span class="badge">use soon</span>` : null}</span>
      <input class="qty" type="number" min="0" step="any" value=${qty} onInput=${(e) => setQty(e.target.value)} aria-label=${`${item.name} quantity`} />
      <span class="unit">${item.unit === "each" ? "" : item.unit}</span>
      <button class="secondary small" onClick=${save}>Save</button>
      <button class="danger small" aria-label=${`Remove ${item.name}`} onClick=${remove}>×</button>
    </li>`;
}

function Staples({ staples, onChanged, onError }) {
  const [out, setOut] = useState(new Set(staples.out));
  const toggle = (n) => { const next = new Set(out); next.has(n) ? next.delete(n) : next.add(n); setOut(next); };
  const save = async () => {
    try { await api("/api/staples", { method: "POST", body: { out: [...out] } }); onChanged("Staples check recorded."); }
    catch (e) { onError(e); }
  };
  return html`
    <section class="card">
      <h2>Staples ${staples.check_due ? html`<span class="badge">check due</span>` : null}</h2>
      <p class="muted small">Assumed on hand. Tick what has run out; it goes on the next list.
        Last checked: ${staples.last_checked || "never"}.</p>
      <div class="chips">
        ${staples.all.map((n) => html`
          <label class=${out.has(n) ? "chip on" : "chip"}><input type="checkbox" checked=${out.has(n)} onChange=${() => toggle(n)} />${n}</label>`)}
      </div>
      <button onClick=${save}>${out.size ? `Save: ${out.size} out` : "Nothing is out"}</button>
    </section>`;
}

export function InventoryView() {
  const [data, error, reload] = useApi("/api/inventory");
  const [message, setMessage] = useState(null);
  const [actionError, setActionError] = useState(null);
  const changed = (text) => { setMessage(text); setActionError(null); reload(); };
  if (error) return html`<${ErrorNote} error=${error} />`;
  if (!data) return html`<${Loading} />`;
  return html`
    <h1>Kitchen</h1>
    ${message ? html`<p class="note" role="status">${message}</p>` : null}
    <${ErrorNote} error=${actionError} />
    <${AddItem} onAdded=${changed} />
    ${LOCATIONS.map((loc) => {
      const items = data.items.filter((i) => i.location === loc);
      return html`
        <section class="card">
          <h2>${loc[0].toUpperCase() + loc.slice(1)}</h2>
          ${items.length ? html`<ul class="stock">${items.map((i) => html`
            <${Item} key=${`${i.id}:${i.qty}`} item=${i} onChanged=${changed} onError=${setActionError} />`)}</ul>`
            : html`<p class="muted">Empty.</p>`}
        </section>`;
    })}
    <${Staples} key=${data.staples.out.join(",")} staples=${data.staples} onChanged=${changed} onError=${setActionError} />`;
}
