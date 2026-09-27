// Recipe search and detail (UI-5): filter, open, scale, rate, tag.
import { html, useState, api, useApi, ErrorNote, Loading } from "../lib.js";

const ROLES = ["", "dinner", "lunch", "soup", "side", "bread", "dessert", "breakfast", "condiment", "snack"];
const QUICK_TAGS = ["mild", "very-spicy", "weeknight-friendly", "no-leftovers", "freezer-friendly"];

export function RecipesView() {
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [tag, setTag] = useState("");
  const [minRating, setMinRating] = useState("");
  const params = new URLSearchParams({ q });
  if (role) params.set("role", role);
  if (tag) params.set("tag", tag);
  if (minRating) params.set("min_rating", minRating);
  const [recipes, error, , loading] = useApi(`/api/recipes?${params}`);
  return html`
    <h1>Recipes</h1>
    <div class="filters">
      <input type="search" placeholder="Search titles and ingredients" value=${q}
        onInput=${(e) => setQ(e.target.value)} aria-label="Search" />
      <select value=${role} onChange=${(e) => setRole(e.target.value)} aria-label="Role">
        ${ROLES.map((r) => html`<option value=${r}>${r || "any role"}</option>`)}
      </select>
      <input placeholder="tag" value=${tag} onInput=${(e) => setTag(e.target.value.trim())} aria-label="Tag" />
      <select value=${minRating} onChange=${(e) => setMinRating(e.target.value)} aria-label="Minimum rating">
        <option value="">any rating</option>
        ${[3, 4, 5].map((n) => html`<option value=${n}>${n}+ stars</option>`)}
      </select>
    </div>
    <${ErrorNote} error=${error} />
    ${loading && !recipes ? html`<${Loading} />` : null}
    <p class="muted">${recipes ? `${recipes.length} recipes` : ""}</p>
    <ul class="results">
      ${(recipes || []).map((r) => html`
        <li>
          <a href=${`#/recipe/${r.ref}`}>${r.title}</a>
          <div class="muted small">
            ${[r.role, r.protein, r.active_minutes != null ? `${r.active_minutes} min` : null,
               r.rating != null ? `${"★".repeat(Math.round(r.rating))} ${r.rating}` : null,
               r.family ? `family: ${r.family}` : null].filter(Boolean).join(" · ")}
          </div>
          ${r.tags.length ? html`<div>${r.tags.map((t) => html`<span class="tag">${t}</span>`)}</div>` : null}
        </li>`)}
    </ul>`;
}

export function RecipeView({ ref_ }) {
  const [servings, setServings] = useState(null);
  const query = servings ? `?servings=${servings}` : "";
  const [recipe, error, reload] = useApi(`/api/recipes/${encodeURIComponent(ref_)}${query}`);
  const [message, setMessage] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [repeat, setRepeat] = useState(true);

  const post = async (path, body, done) => {
    setActionError(null);
    try { await api(path, { method: "POST", body }); setMessage(done); reload(); }
    catch (e) { setActionError(e); }
  };
  if (error) return html`<${ErrorNote} error=${error} />`;
  if (!recipe) return html`<${Loading} />`;
  const shown = recipe.shown_servings;
  return html`
    <p><a href="#/recipes">‹ Recipes</a></p>
    <h1>${recipe.title}</h1>
    <p><a class="button secondary small" href=${`#/edit/${encodeURIComponent(recipe.ref)}`}>Edit</a></p>
    <p class="muted">${recipe.ref} · ${recipe.status} · ${recipe.collection}${recipe.role ? ` · ${recipe.role}` : ""}
      ${recipe.family ? html` · family ${recipe.family}` : null}</p>
    ${recipe.variants.length ? html`<p>Other versions: ${recipe.variants.map((v) => html`<a href=${`#/recipe/${v}`}>${v}</a> `)}</p>` : null}
    ${recipe.notes ? html`<p class="note">${recipe.notes}</p>` : null}
    <${ErrorNote} error=${actionError} />
    ${message ? html`<p class="note" role="status">${message}</p>` : null}

    <section class="card">
      <div class="toolbar">
        <h2>Ingredients</h2>
        ${shown ? html`
          <div class="stepper" aria-label="Servings">
            <button class="secondary small" aria-label="Fewer servings" disabled=${shown <= 1}
              onClick=${() => setServings(Math.max(1, shown - 1))}>−</button>
            <span>${shown} servings</span>
            <button class="secondary small" aria-label="More servings" onClick=${() => setServings(shown + 1)}>+</button>
          </div>` : null}
      </div>
      <ul>
        ${recipe.ingredients.map((i) => html`
          <li class=${i.matched ? "" : "warn"}>
            ${recipe.scaled && i.qty != null && i.name
              ? `${i.qty}${i.unit ? ` ${i.unit}` : ""} ${i.name}${i.prep_note ? `, ${i.prep_note}` : ""}`
              : i.raw}
          </li>`)}
      </ul>
    </section>
    <section class="card">
      <h2>Steps</h2>
      <ol>${recipe.steps.map((s) => html`<li>${s}</li>`)}</ol>
    </section>
    <section class="card">
      <h2>Rate it</h2>
      <div class="actions">
        ${[1, 2, 3, 4, 5].map((n) => html`
          <button class="secondary" aria-label=${`${n} stars`}
            onClick=${() => post(`/api/recipes/${recipe.ref}/rate`, { score: n, repeat }, `Rated ${n}/5.`)}>${"★".repeat(n)}</button>`)}
      </div>
      <label class="inline"><input type="checkbox" checked=${repeat} onChange=${(e) => setRepeat(e.target.checked)} /> Would make again</label>
      ${recipe.ratings.length ? html`<p class="muted">Past ratings: ${recipe.ratings.map((r) => `${r.score}/5 (${r.date})`).join(", ")}</p>` : null}
    </section>
    <section class="card">
      <h2>Tags</h2>
      <div class="actions">
        ${QUICK_TAGS.map((t) => {
          const on = recipe.tags.includes(t);
          return html`<button class=${on ? "" : "secondary"} aria-pressed=${on}
            onClick=${() => post(`/api/recipes/${recipe.ref}/tags`, on ? { remove: [t] } : { add: [t] }, `${on ? "Removed" : "Added"} ${t}.`)}>${t}</button>`;
        })}
      </div>
      <p class="muted small">${recipe.tags.join(", ")}</p>
    </section>
    <p class="muted small">Source: ${recipe.sources.map((s) => [s.title, s.url || (s.file && `${s.file} p${s.pages}`)].filter(Boolean).join(" ")).join("; ")}</p>`;
}
