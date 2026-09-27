// Meal planner web app (UI-4, UI-5): login gate, navigation, and hash routing.
import { html, render, useEffect, useState, api, ErrorNote } from "./lib.js";
import { WeekView } from "./views/week.js";
import { RecipesView, RecipeView } from "./views/recipes.js";
import { ReviewView } from "./views/review.js";
import { ShopView } from "./views/shop.js";
import { InventoryView } from "./views/inventory.js";
import { StartView } from "./views/start.js";

const TABS = [
  ["#/week", "Week"],
  ["#/recipes", "Recipes"],
  ["#/review", "Review"],
  ["#/shop", "Shop"],
  ["#/inventory", "Kitchen"],
];

function useHash() {
  const [hash, setHash] = useState(location.hash || "#/week");
  useEffect(() => {
    const onChange = () => setHash(location.hash || "#/week");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

function Login({ onLogin }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/login", { method: "POST", body: { password } });
      onLogin();
    } catch (err) {
      setError(err);
    }
  };
  return html`
    <main class="login">
      <h1>Meal planner</h1>
      <form onSubmit=${submit}>
        <label>Household password
          <input type="password" autocomplete="current-password" value=${password}
            onInput=${(e) => setPassword(e.target.value)} autofocus />
        </label>
        <button type="submit">Log in</button>
      </form>
      <${ErrorNote} error=${error} />
    </main>`;
}

function route(hash) {
  const [, name, arg] = hash.split("/");
  switch (name) {
    case "recipes": return html`<${RecipesView} />`;
    case "recipe": return html`<${RecipeView} key=${arg} ref_=${decodeURIComponent(arg || "")} />`;
    case "review": return html`<${ReviewView} />`;
    case "shop": return html`<${ShopView} />`;
    case "inventory": return html`<${InventoryView} />`;
    case "start": return html`<${StartView} />`;
    default: return html`<${WeekView} />`;
  }
}

function App() {
  const [authed, setAuthed] = useState(null);
  const hash = useHash();
  useEffect(() => {
    api("/api/session").then((s) => setAuthed(s.authenticated), () => setAuthed(false));
    const onLogout = () => setAuthed(false);
    window.addEventListener("mealplan:logged-out", onLogout);
    return () => window.removeEventListener("mealplan:logged-out", onLogout);
  }, []);
  if (authed === null) return null;
  if (!authed) return html`<${Login} onLogin=${() => setAuthed(true)} />`;
  const logout = async () => {
    await api("/api/logout", { method: "POST" });
    setAuthed(false);
  };
  const active = "#/" + (hash.split("/")[1] === "recipe" ? "recipes" : hash.split("/")[1] || "week");
  return html`
    <header>
      <nav>
        ${TABS.map(([href, label]) => html`
          <a href=${href} class=${active === href ? "active" : ""}
             aria-current=${active === href ? "page" : undefined}>${label}</a>`)}
        <a href="#/start" class=${active === "#/start" ? "active" : ""} title="Import recipes and settings">Setup</a>
        <button class="link" onClick=${logout}>Log out</button>
      </nav>
    </header>
    <main>${route(hash)}</main>`;
}

render(html`<${App} />`, document.getElementById("app"));
