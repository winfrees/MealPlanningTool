// Recipe editor (UI-5): fix an imported recipe before approving it, or tidy one later.
// Ingredients are one per line and are matched to the catalog again on save.
import { html, useState, useEffect, api, ErrorNote, Loading } from "../lib.js";

const EQUIPMENT = ["oven", "stove", "slow cooker", "pressure cooker", "grill", "microwave", "mixer"];
const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

function StepRow({ step, index, count, onChange, onMove, onRemove }) {
  const set = (field, value) => onChange({ ...step, [field]: value });
  const toggle = (item) => set("equipment", step.equipment.includes(item)
    ? step.equipment.filter((e) => e !== item) : [...step.equipment, item]);
  return html`
    <li class="step-edit">
      <textarea rows="2" aria-label=${`Step ${index + 1}`} value=${step.text}
        onInput=${(e) => set("text", e.target.value)}></textarea>
      <div class="row small">
        <label>Hands-on min
          <input type="number" min="0" inputmode="numeric" value=${step.active_minutes ?? 0}
            onInput=${(e) => set("active_minutes", num(e.target.value) ?? 0)} /></label>
        <label>Waiting min
          <input type="number" min="0" inputmode="numeric" value=${step.passive_minutes ?? 0}
            onInput=${(e) => set("passive_minutes", num(e.target.value) ?? 0)} /></label>
      </div>
      <div class="chips">
        ${EQUIPMENT.map((item) => html`
          <label class="chip"><input type="checkbox" checked=${step.equipment.includes(item)}
            onChange=${() => toggle(item)} /> ${item}</label>`)}
      </div>
      <div class="actions">
        <button type="button" class="secondary small" disabled=${index === 0}
          onClick=${() => onMove(-1)} aria-label=${`Move step ${index + 1} up`}>↑</button>
        <button type="button" class="secondary small" disabled=${index === count - 1}
          onClick=${() => onMove(1)} aria-label=${`Move step ${index + 1} down`}>↓</button>
        <button type="button" class="danger small" onClick=${onRemove}
          aria-label=${`Remove step ${index + 1}`}>Remove</button>
      </div>
    </li>`;
}

export function EditView({ ref_ }) {
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api(`/api/recipes/${encodeURIComponent(ref_)}/edit`).then(
      (r) => setForm({ ...r, ingredientsText: r.ingredients.join("\n"), tagsText: r.tags.join(", ") }),
      setError);
  }, [ref_]);

  if (!form) return html`<${ErrorNote} error=${error} /><${Loading} />`;
  const set = (field, value) => setForm({ ...form, [field]: value });
  const setStep = (i, step) => set("steps", form.steps.map((s, j) => (j === i ? step : s)));
  const moveStep = (i, by) => {
    const steps = [...form.steps];
    [steps[i], steps[i + by]] = [steps[i + by], steps[i]];
    set("steps", steps);
  };

  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null); setSaved(null);
    try {
      const result = await api(`/api/recipes/${encodeURIComponent(ref_)}`, {
        method: "PUT",
        body: {
          title: form.title,
          servings: num(form.servings),
          prep_minutes: num(form.prep_minutes),
          cook_minutes: num(form.cook_minutes),
          total_minutes: num(form.total_minutes),
          meal_role: form.meal_role || null,
          tags: form.tagsText.split(",").map((t) => t.trim()).filter(Boolean),
          household_notes: form.household_notes,
          ingredients: form.ingredientsText.split("\n"),
          steps: form.steps.filter((s) => s.text.trim()),
        },
      });
      setSaved(result);
      setForm({ ...form, issues: result.issues });
    } catch (err) { setError(err); } finally { setBusy(false); }
  };

  const back = form.status === "draft" ? "#/review" : `#/recipe/${encodeURIComponent(ref_)}`;
  return html`
    <p><a href=${back}>‹ Back to ${form.status === "draft" ? "Review" : "the recipe"}</a></p>
    <h1>Edit ${form.ref}</h1>
    ${form.sources.map((s) => html`
      <p class="muted small">From ${s.title || s.file}${s.pages ? `, page ${s.pages}` : ""}
        ${s.confidence < 1 ? ` · read with ${Math.round(s.confidence * 100)}% confidence, check it against the page` : ""}</p>`)}
    ${form.issues.length ? html`<section class="card warn"><h2>Check these</h2>
      <ul>${form.issues.map((i) => html`<li>${i}</li>`)}</ul>
      <p class="small">An unmatched ingredient usually needs its name written plainly
        ("1 cup long-grain rice" rather than "1 c. rice (see note)").</p></section>` : null}
    <form class="edit" onSubmit=${save}>
      <label>Title <input value=${form.title} required maxlength="200"
        onInput=${(e) => set("title", e.target.value)} /></label>
      <div class="row">
        <label>Servings <input type="number" min="0" step="any" inputmode="decimal"
          value=${form.servings ?? ""} onInput=${(e) => set("servings", e.target.value)} /></label>
        <label>Meal
          <select value=${form.meal_role ?? ""} onChange=${(e) => set("meal_role", e.target.value)}>
            <option value="">—</option>
            ${form.roles.map((r) => html`<option value=${r}>${r}</option>`)}
          </select></label>
      </div>
      <div class="row">
        <label>Prep min <input type="number" min="0" inputmode="numeric" value=${form.prep_minutes ?? ""}
          onInput=${(e) => set("prep_minutes", e.target.value)} /></label>
        <label>Cook min <input type="number" min="0" inputmode="numeric" value=${form.cook_minutes ?? ""}
          onInput=${(e) => set("cook_minutes", e.target.value)} /></label>
      </div>
      <label>Ingredients <span class="muted small">(one per line, as written: "2 cups rice, rinsed")</span>
        <textarea rows="10" value=${form.ingredientsText}
          onInput=${(e) => set("ingredientsText", e.target.value)}></textarea></label>
      <fieldset>
        <legend>Steps</legend>
        <ol class="plain">
          ${form.steps.map((step, i) => html`
            <${StepRow} key=${i} step=${step} index=${i} count=${form.steps.length}
              onChange=${(s) => setStep(i, s)} onMove=${(by) => moveStep(i, by)}
              onRemove=${() => set("steps", form.steps.filter((_, j) => j !== i))} />`)}
        </ol>
        <button type="button" class="secondary" onClick=${() => set("steps", [...form.steps,
          { text: "", equipment: [], active_minutes: 0, passive_minutes: 0 }])}>Add a step</button>
      </fieldset>
      <label>Tags <span class="muted small">(comma separated)</span>
        <input value=${form.tagsText} onInput=${(e) => set("tagsText", e.target.value)} /></label>
      <label>Household notes
        <textarea rows="2" value=${form.household_notes}
          onInput=${(e) => set("household_notes", e.target.value)}></textarea></label>
      <div class="actions">
        <button type="submit" disabled=${busy}>${busy ? "Saving…" : "Save"}</button>
        <a class="button secondary" href=${back}>Done</a>
      </div>
    </form>
    <${ErrorNote} error=${error} />
    ${saved ? html`<p class="note" role="status">Saved.${saved.issues.length ? " A few things still need a look (listed at the top)." : " No issues left."}</p>` : null}
`;
}
