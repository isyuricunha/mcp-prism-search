"use strict";
const $ = (id) => document.getElementById(id);
let state = null;
function status(message = "", error = false, target = "status") {
  $(target).textContent = message;
  $(target).classList.toggle("error", error);
}
async function api(path, method = "GET", data) {
  const response = await fetch("/api/" + path, {
    method, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Prism-Admin": "1" },
    ...(data === undefined ? {} : { body: JSON.stringify(data) })
  });
  const body = await response.json();
  if (response.status === 401 && path !== "login") showLogin();
  if (!response.ok) throw new Error(body.error || "Request failed");
  return body;
}
function element(tag, value = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}
function showLogin() {
  state = null;
  $("provider-list").replaceChildren();
  $("choices").replaceChildren();
  $("results").replaceChildren();
  $("dashboard").hidden = true;
  $("login").hidden = false;
  $("password").focus();
}
function showDashboard() {
  $("login").hidden = true;
  $("dashboard").hidden = false;
}
function applyTheme(theme) {
  const name = theme === "yu-daylight" ? theme : "yu-dark";
  document.documentElement.dataset.theme = name;
  $("theme").textContent = name === "yu-dark" ? "Switch to White" : "Switch to Dark";
  try { localStorage.setItem("prism-theme", name); } catch {}
}
try { applyTheme(localStorage.getItem("prism-theme")); } catch { applyTheme("yu-dark"); }
$("theme").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "yu-dark" ? "yu-daylight" : "yu-dark"));
function page(id) {
  const button = document.querySelector('[data-page="' + id + '"]');
  if (!button) return;
  document.querySelectorAll(".page").forEach(node => { node.hidden = node.id !== id; });
  document.querySelectorAll("[data-page]").forEach(node => node.setAttribute("aria-current", node === button ? "page" : "false"));
  $("page-title").textContent = button.textContent;
  status();
}
document.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => page(button.dataset.page)));
function metric(label, value) {
  const div = element("div", "", "metric");
  div.append(element("span", label, "muted"), element("strong", String(value)));
  return div;
}
function render() {
  const { providers, metrics, cache, settings, counters } = state;
  $("summary").replaceChildren(
    metric("Available engines", providers.filter(p => p.available).length + "/" + providers.length),
    metric("Upstream requests", metrics.reduce((n, m) => n + m.requests, 0)),
    metric("Cache hits", counters.cache_hits || 0),
    metric("Rate limits", metrics.reduce((n, m) => n + m.limited, 0))
  );
  $("metrics").replaceChildren();
  if (!metrics.length) {
    const tr = element("tr"), td = element("td", "No requests recorded yet.", "muted");
    td.colSpan = 7; tr.append(td); $("metrics").append(tr);
  }
  for (const m of metrics) {
    const tr = element("tr");
    [m.name, m.requests, m.ok, m.empty, m.errors, m.limited, (m.seconds / Math.max(m.requests, 1)).toFixed(2) + " s"].forEach(v => tr.append(element("td", String(v))));
    $("metrics").append(tr);
  }
  $("cache-stats").textContent = JSON.stringify(cache, null, 2);
  for (const key of ["timeout", "search_ttl", "fetch_ttl"]) {
    $(key).value = settings[key]; $(key).disabled = settings.locked.includes(key);
  }
  $("preferred").replaceChildren(new Option("Default order", ""));
  for (const p of providers.filter(p => p.kind === "general")) $("preferred").append(new Option(p.id, p.id));
  $("preferred").value = settings.preferred || "";
  $("preferred").disabled = settings.locked.includes("preferred");
  const list = $("provider-list"), choices = $("choices");
  list.replaceChildren(); choices.replaceChildren();
  for (const p of providers) {
    const article = element("article", "", "card");
    const head = element("div", "", "provider-head");
    head.append(element("h3", p.id), element("span", p.available ? "Available" : p.enabled ? "Setup required" : "Disabled", "badge" + (p.available ? " ready" : "")));
    article.append(head, element("p", p.description, "muted"));
    const form = element("form", "", "provider-actions");
    const label = element("label", "Enabled"), toggle = document.createElement("input");
    toggle.type = "checkbox"; toggle.checked = p.enabled; label.prepend(toggle); form.append(label);
    let input = null;
    if (p.editable && !p.managed) {
      input = document.createElement("input"); input.type = "password"; input.autocomplete = "new-password"; input.maxLength = 4096;
      input.placeholder = p.credential ? "Replace stored key" : "Enter API key";
      input.setAttribute("aria-label", p.id + " API key"); form.append(input);
    }
    const save = element("button", "Save", "primary"); save.type = "submit"; form.append(save);
    async function update(remove = false) {
      save.disabled = true;
      try {
        const data = { enabled: toggle.checked };
        if (remove) data.remove = true;
        else if (input && input.value) data.credential = input.value;
        await api("providers/" + encodeURIComponent(p.id), "PUT", data);
        if (input) input.value = "";
        await refresh(); status(p.id + " updated.");
      } catch (error) { status(error.message, true); } finally { save.disabled = false; }
    }
    form.addEventListener("submit", event => { event.preventDefault(); update(); });
    if (p.editable && p.credential && !p.managed) {
      const remove = element("button", "Remove key"); remove.type = "button";
      remove.addEventListener("click", () => { if (confirm("Remove this stored API key?")) update(true); });
      form.append(remove);
    }
    if (p.managed || (p.env && !p.editable)) article.append(element("p", "Configured via " + p.env + " (environment only).", "muted"));
    article.append(form); list.append(article);
    if (p.available && p.kind === "general") {
      const label = element("label", p.id), check = document.createElement("input");
      check.type = "checkbox"; check.value = p.id; check.checked = ["keenable", "freeserp"].includes(p.id); label.prepend(check); choices.append(label);
    }
  }
}
async function refresh() { state = await api("overview"); render(); showDashboard(); }
$("login-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button");
  button.disabled = true; status("", false, "login-status");
  try { await api("login", "POST", { password: $("password").value }); $("password").value = ""; await refresh(); page("overview"); }
  catch (error) { status(error.message, true, "login-status"); }
  finally { button.disabled = false; }
});
$("refresh").addEventListener("click", async () => { try { await refresh(); status("Updated."); } catch (error) { status(error.message, true); } });
$("logout").addEventListener("click", async () => { try { await api("logout", "POST"); state = null; $("results").replaceChildren(); showLogin(); } catch (error) { status(error.message, true); } });
$("search-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const providers = Array.from($("choices").querySelectorAll("input:checked"), node => node.value);
  if (!providers.length) { status("Select at least one provider.", true); return; }
  button.disabled = true; status("Searching…"); $("results").replaceChildren(); $("search-attempts").textContent = "";
  try {
    const data = await api("search", "POST", { query: $("query").value, providers, max_results: 5 });
    $("search-attempts").textContent = (data.cached ? "Cached · " : "") + data.provider_attempts.join(" · ");
    for (const row of data.results) {
      const article = element("article", "", "card result");
      const url = new URL(row.url);
      if (["https:", "http:"].includes(url.protocol)) {
        const link = element("a", row.title); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; article.append(link);
      }
      article.append(element("p", row.url, "muted"), element("p", row.snippet)); $("results").append(article);
    }
    status(data.results.length + " results.");
  } catch (error) { status(error.message, true); } finally { button.disabled = false; }
});
$("prune").addEventListener("click", async () => {
  if (!confirm("Prune expired and corrupt cache entries?")) return;
  try { await api("cache/prune", "POST"); await refresh(); status("Cache pruned."); }
  catch (error) { status(error.message, true); }
});
$("clear-cache").addEventListener("click", async () => {
  if (!confirm("Clear all search and fetch cache entries?")) return;
  try { await api("cache/clear", "POST", { namespace: "all", confirm: true }); await refresh(); status("Cache cleared."); }
  catch (error) { status(error.message, true); }
});
$("settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const data = {};
  for (const key of ["timeout", "search_ttl", "fetch_ttl"]) {
    if (!$(key).disabled) data[key] = Number($(key).value);
  }
  if (!$("preferred").disabled) data.preferred = $("preferred").value || null;
  try { await api("settings", "PUT", data); await refresh(); status("Settings saved."); }
  catch (error) { status(error.message, true); }
});
function connection() {
  $("connection-json").textContent = JSON.stringify({mcp:{"prism-search":{type:"remote",url:$("endpoint").value,enabled:true,oauth:false,headers:{Authorization:"Bearer <your-private-token>"}}}},null,2);
}
$("endpoint").addEventListener("input", connection); connection();
page("overview");
refresh().catch(error => { showLogin(); if (error.message !== "Authentication required") status(error.message, true, "login-status"); });
