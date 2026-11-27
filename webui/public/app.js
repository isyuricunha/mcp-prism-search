"use strict";
// Browser-local UI state. Credentials are never persisted in browser storage.
const $ = id => document.getElementById(id);
const pages = {
  overview: "Monitor the running server and understand search activity.",
  providers: "Configure search engines, credentials and availability.",
  playground: "Try a search before connecting an MCP client.",
  cache: "Inspect stored results and safely reclaim disk space.",
  settings: "Tune search behavior and personalize this browser.",
  connection: "Generate connection settings for your MCP client."
};
const catalog = {
  keenable: ["Keenable", "Public web search with extracted text snippets.", "https://docs.keenable.ai/"],
  freeserp: ["FreeSerp", "Web results from the FreeSerp search index.", "https://freeserp.ai/serp-scraper-api"],
  bing_rss: ["Bing RSS", "Web results from Bing's RSS feed. Availability depends on the upstream service.", "https://www.bing.com/"],
  "ddgs:yahoo": ["Yahoo via DDGS", "Yahoo web results through the DDGS search library.", "https://search.yahoo.com/"],
  mwmbl: ["Mwmbl", "Community-maintained search index. Anonymous requests have limits.", "https://developer.mwmbl.org/"],
  youcom_keyless: ["You.com (public MCP)", "Public MCP search with a limited daily allowance.", "https://you.com/docs/agents/mcp-server"],
  ddg_html: ["DuckDuckGo HTML", "Keyless HTML search. Network reputation and upstream protections can affect results.", "https://duckduckgo.com/"],
  tinyfish: ["TinyFish TinySearch", "Authenticated web search API. Check allowance and wallet settings.", "https://docs.tinyfish.ai/tinysearch/reference", "https://agent.tinyfish.ai/sign-up"],
  tavily: ["Tavily", "Search API for research and AI applications.", "https://docs.tavily.com/", "https://app.tavily.com/"],
  exa: ["Exa", "Semantic and keyword web search API.", "https://docs.exa.ai/", "https://dashboard.exa.ai/"],
  brave: ["Brave Search", "Web search through Brave's independent search index.", "https://api-dashboard.search.brave.com/documentation", "https://api-dashboard.search.brave.com/"],
  youcom: ["You.com (API)", "Authenticated You.com web search API.", "https://you.com/docs/guides/search", "https://you.com/"],
  serper: ["Serper", "Google search results through the Serper API.", "https://serper.dev/", "https://serper.dev/"],
  jina: ["Jina Search", "Web search with text content prepared for research.", "https://api.jina.ai/docs", "https://jina.ai/api-dashboard/key-manager"],
  searxng: ["SearXNG", "Your own metasearch instance. Set its URL in the server environment.", "https://docs.searxng.org/dev/search_api.html"],
  wikipedia: ["Wikipedia", "Encyclopedic search through MediaWiki. Used by the specialized Wikipedia MCP tool.", "https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy"],
  hackernews: ["Hacker News", "Search technology discussions and submissions. Used by the specialized Hacker News MCP tool.", "https://hn.algolia.com/api"]
};
let state = null;
let activePage = "overview";
let refreshPromise = null;
let authEpoch = 0;
let busyCount = 0;
const providerDrafts = new Map();
const providerPending = new Set();
const providerMessages = new Map();
const selected = new Set(["keenable", "freeserp"]);
let settingsDirty = false;
let searchPending = false;
let settingsPending = false;
let cachePending = false;
let nativePending = false;

function element(tag, value = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = String(value ?? "");
  if (className) node.className = className;
  return node;
}
function action(label, callback, className = "") {
  const button = element("button", label, className);
  button.type = "button";
  button.addEventListener("click", callback);
  return button;
}
function link(label, url) {
  const a = element("a", label + " ↗");
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}
function feedback(target, message = "", isError = false) {
  const node = typeof target === "string" ? $(target) : target;
  node.textContent = message;
  node.classList.toggle("error", isError);
}
function status(message = "", error = false) { feedback("status", message, error); }
function setPending(button, pending, label) {
  if (pending) {
    button.dataset.idle = button.textContent;
    button.textContent = label;
    button.setAttribute("aria-busy", "true");
  } else {
    if (button.dataset.idle) button.textContent = button.dataset.idle;
    button.removeAttribute("aria-busy");
  }
  button.disabled = pending;
}
async function api(path, method = "GET", data) {
  const epoch = authEpoch;
  let response;
  try {
    response = await fetch("/api/" + path, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Prism-Admin": "1" },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      signal: AbortSignal.timeout(["search","native"].includes(path) ? 180000 : 20000)
    });
  } catch (e) {
    throw new Error(e.name === "TimeoutError" ? (["search","native"].includes(path) ? "The browser stopped waiting. The server may still be searching; check activity before retrying." : "The request timed out. Try again.") : "Cannot reach the server. Check the connection and retry.");
  }
  if (epoch !== authEpoch) throw new Error("Session changed. Please retry.");
  const body = await response.json().catch(() => null);
  if (epoch !== authEpoch) throw new Error("Session changed. Please retry.");
  if (response.status === 401 && path !== "login") showLogin();
  if (!response.ok) {
    const error = new Error(body?.error || ("Server returned HTTP " + response.status + "."));
    error.status = response.status; error.body = body; throw error;
  }
  if (!body) throw new Error("The server returned an invalid response.");
  return body;
}
async function confirmAction(title, description, label, destructive = true) {
  const dialog = $("confirmation");
  if (dialog.open) return false;
  $("confirm-title").textContent = title; $("confirm-description").textContent = description;
  $("confirm-action").textContent = label;
  $("confirm-action").className = destructive ? "danger" : "primary";
  dialog.returnValue = "cancel";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    dialog.showModal();
  });
}
function showLogin() {
  authEpoch++;
  state = null; providerDrafts.clear(); providerMessages.clear(); providerPending.clear();
  settingsDirty = false;
  for (const id of ["provider-list","choices","results","search-attempts","metrics","summary","server-facts","connection-facts","readiness","cache-summary","cache-rows"]) $(id).replaceChildren();
  $("native-query").value = ""; $("native-answer").textContent = ""; $("native-sources").replaceChildren(); $("native-result").hidden=true; feedback("native-feedback");
  $("query").value = ""; $("query-count").textContent = "0 / 1000"; $("search-output").hidden = true;
  $("endpoint").value = ["localhost","127.0.0.1"].includes(location.hostname) ? "http://127.0.0.1:49317/mcp" : ""; connection();
  $("password").value = ""; $("password").type = "password";
  $("password-visibility").textContent = "Show"; $("password-visibility").setAttribute("aria-pressed","false");
  $("dashboard").hidden = true; $("boot").hidden = true; $("login").hidden = false;
  $("password").focus();
}
function showDashboard() {
  $("boot").hidden = true; $("login").hidden = true; $("dashboard").hidden = false;
}
function applyTheme(theme) {
  const name = theme === "yu-daylight" ? theme : "yu-dark";
  document.documentElement.dataset.theme = name;
  for (const id of ["theme", "login-theme", "settings-theme"]) $(id).textContent = name === "yu-dark" ? "Switch to White" : "Switch to Dark";
  try { localStorage.setItem("prism-theme", name); } catch {}
}
try { applyTheme(localStorage.getItem("prism-theme")); } catch { applyTheme("yu-dark"); }
for (const id of ["theme","login-theme","settings-theme"]) $(id).addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "yu-dark" ? "yu-daylight" : "yu-dark"));
function page(id, navigate = true) {
  if (!Object.hasOwn(pages,id)) id = "overview";
  const button = document.querySelector('[data-page="' + id + '"]');
  activePage = id;
  for (const node of document.querySelectorAll(".page[id][aria-labelledby='page-title']")) node.hidden = node.id !== id;
  for (const node of document.querySelectorAll("[data-page]")) {
    if (node === button) node.setAttribute("aria-current","page"); else node.removeAttribute("aria-current");
  }
  $("page-title").textContent = button.querySelector("span").textContent;
  $("page-description").textContent = pages[id];
  document.title = button.querySelector("span").textContent + " · Prism Search";
  if (navigate) {
    if (location.hash !== "#" + id) history.pushState(null,"","#" + id);
    $("page-title").focus({preventScroll:true}); window.scrollTo(0,0);
    status();
  }
}
for (const button of document.querySelectorAll("[data-page]")) button.addEventListener("click", () => page(button.dataset.page));
for (const button of document.querySelectorAll("[data-go]")) button.addEventListener("click", () => page(button.dataset.go));
window.addEventListener("popstate", () => page(location.hash.slice(1),false));
const number = value => Number(value ?? 0).toLocaleString("en-US");
function bytes(value) {
  let n = Number(value || 0), i = 0;
  const units = ["B","KiB","MiB","GiB","TiB"];
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toLocaleString("en-US",{maximumFractionDigits:i ? 1 : 0}) + " " + units[i];
}
function metric(label,value,description) {
  const div = element("div","","metric");
  div.append(element("span",label,"muted"),element("strong",value),element("small",description,"muted"));
  return div;
}
function facts(node, entries) {
  node.replaceChildren();
  for (const [label,value] of entries) {
    const row = element("div"); row.append(element("dt",label),element("dd",value)); node.append(row);
  }
}
function providerName(id) { return catalog[id]?.[0] || id; }
function providerType(p) { return p.kind === "vertical" ? "Specialized" : p.env === "SEARXNG_URL" ? "Self-hosted" : p.env ? "API key" : "Keyless"; }
function renderOverview() {
  const {providers,metrics,counters,server} = state;
  const hits = counters.cache_hits || 0, misses = counters.cache_misses || 0;
  const attempts = metrics.reduce((n,m)=>n+m.requests,0);
  $("summary").replaceChildren(
    metric("Available engines",providers.filter(p=>p.available).length+"/"+providers.length,"Enabled and configured"),
    metric("Upstream attempts",number(attempts),"Cumulative · all providers"),
    metric("Search cache hit rate",hits+misses ? (100*hits/(hits+misses)).toFixed(1)+"%" : "—",number(hits)+" hits · "+number(misses)+" misses"),
    metric("Rate limits",number(metrics.reduce((n,m)=>n+m.limited,0)),"HTTP 429 · included in errors")
  );
  const version = server?.version ? "v"+server.version : "Version unavailable";
  $("sidebar-version").textContent = version;
  $("sidebar-sdk").textContent = server?.sdk_version ? "MCP SDK "+server.sdk_version : "SDK version unavailable";
  const items = [["Prism Search",version],["Python MCP SDK",server?.sdk_version || "Unavailable"],["SDK protocol revision",server?.protocol_version || "Unavailable"],["Transport",server?.transport || "Unavailable"]];
  facts($("server-facts"),items); facts($("connection-facts"),items);
  facts($("readiness"),[
    ["Keyless web engines",providers.filter(p=>p.kind==="general"&&!p.env&&p.available).length],
    ["Configured API / instance",providers.filter(p=>p.kind==="general"&&p.env&&p.available).length],
    ["Specialized engines",providers.filter(p=>p.kind==="vertical"&&p.available).length],
    ["Disabled or missing setup",providers.filter(p=>!p.available).length]
  ]);
  const table = $("metrics"); table.replaceChildren();
  if (!metrics.length) {
    const row = element("tr"), td = element("td","No upstream attempts yet. Run a search in the Playground to get started.","muted");
    td.colSpan=7; row.append(td); table.append(row);
  }
  for (const m of metrics) {
    const row = element("tr"); const heading = element("th",providerName(m.name)); heading.scope="row";
    row.append(heading);
    for (const value of [number(m.requests),number(m.ok),number(m.empty),number(m.errors),number(m.limited),m.requests?(m.seconds/m.requests).toFixed(2)+" s":"—"]) row.append(element("td",value));
    table.append(row);
  }
}
function currentFilter() { return document.querySelector("input[name=provider-filter-kind]:checked").value; }
function filterProviders() {
  const query = $("provider-filter").value.trim().toLowerCase();
  const kind = currentFilter();
  let visible=0;
  for (const row of $("provider-list").children) {
    const p=state?.providers.find(x=>x.id===row.dataset.provider);
    if (!p) continue;
    const match = kind==="all" || (kind==="keyless"&&p.kind==="general"&&!p.env) || (kind==="keyed"&&!!p.env) || (kind==="vertical"&&p.kind==="vertical") || (kind==="available"&&p.available);
    row.hidden = !(match && (p.id+" "+providerName(p.id)).toLowerCase().includes(query));
    if (!row.hidden) visible++;
  }
  $("provider-count").textContent=visible+" / "+(state?.providers.length||0);
  $("provider-empty").hidden=visible!==0; $("provider-list").hidden=visible===0;
}
function draftFor(p) {
  const existing=providerDrafts.get(p.id);
  if (!existing || (!existing.credential && existing.enabled===existing.baseline)) {
    providerDrafts.set(p.id,{enabled:p.enabled,baseline:p.enabled,credential:""});
  }
  const draft = providerDrafts.get(p.id);
  if (!p.editable || p.managed) draft.credential = "";
  return draft;
}
function renderProviders() {
  const list=$("provider-list");
  // Preserve the focused field and cursor when a refresh updates the list.
  const focused=document.activeElement?.id;
  const start=document.activeElement?.selectionStart, end=document.activeElement?.selectionEnd;
  list.replaceChildren();
  const items=[...state.providers].sort((a,b)=>Number(!a.available)-Number(!b.available)||providerName(a.id).localeCompare(providerName(b.id)));
  for (const p of items) {
    const info=catalog[p.id]||[p.id,p.description];
    const draft=draftFor(p);
    const row=element("article","","provider-row"); row.dataset.provider=p.id;
    const head=element("div","","provider-head"), title=element("div","","stack");
    title.append(element("h3",info[0]),element("span",p.id+" · "+providerType(p),"provider-meta"));
    head.append(title,element("span",p.available?"Available":p.enabled?"Setup required":"Disabled","badge"+(p.available?" ready":"")));
    const links=element("div","","actions provider-links");
    if(info[2]) links.append(link("Website / docs",info[2]));
    if(info[3]) links.append(link("Get API key",info[3]));
    if(p.kind==="general"&&p.available) links.append(action("Test in Playground",()=>{selected.clear();selected.add(p.id);document.querySelector('input[name=search-mode][value=selected]').checked=true;renderChoices();page("playground");$("query").focus();},"tonal"));
    row.append(head,element("p",info[1],"muted"),links);
    const native = state.native?.find(x=>x.id===p.id);
    if(native){
      const details=element("div","","stack");
      details.append(element("p","AI capabilities: "+(native.answer?"Answer":"")+(native.research?" · Multi-step research":""),"muted small"),
        element("p",native.pricing,"muted small"),link("AI API pricing",native.pricing_url));
      row.append(details);
    }
    const form=element("form","","provider-actions");
    const toggleLabel=element("label","Enabled","switch-label");
    const toggle=document.createElement("input"); toggle.type="checkbox";toggle.className="switch";toggle.id="enable-"+p.id;toggle.setAttribute("role","switch");toggle.setAttribute("aria-label","Enable "+info[0]);toggle.checked=draft.enabled;
    toggleLabel.prepend(toggle);form.append(toggleLabel);
    let input=null;
    if(p.editable&&!p.managed){
      const field=element("div","","key-field field"), label=element("label",info[0]+" API key");
      input=document.createElement("input");input.id="key-"+p.id;label.htmlFor=input.id;input.type="password";input.autocomplete="new-password";input.maxLength=4096;input.value=draft.credential;
      input.placeholder=p.credential?"New key (leave blank to keep)":"Paste API key";
      const hint=element("span",p.credential?"Stored securely. Existing value is never displayed.":"No stored API key. Get one from the provider.","muted small");hint.id="hint-"+p.id;input.setAttribute("aria-describedby",hint.id);
      field.append(label,input,hint);form.append(field);
      input.addEventListener("input",()=>{draft.credential=input.value;syncDraft();});
    }
    const save=element("button","Save","tonal");save.type="submit";save.id="save-"+p.id;form.append(save);
    const local=element("p","","feedback row-feedback");local.setAttribute("role","status");local.id="provider-feedback-"+p.id;
    const previous=providerMessages.get(p.id);
    if(previous)feedback(local,previous.message,previous.error);
    let remove=null;
    function syncDraft() {
      draft.enabled=toggle.checked;
      const dirty=draft.enabled!==p.enabled||!!draft.credential;
      save.disabled=!dirty||providerPending.has(p.id);
      toggle.disabled=providerPending.has(p.id); if(input)input.disabled=providerPending.has(p.id);
      if(remove)remove.disabled=providerPending.has(p.id);
      row.classList.toggle("dirty",dirty);
    }
    async function update(removeKey=false) {
      if(providerPending.has(p.id))return;
      providerPending.add(p.id);busyCount++;syncDraft();setPending(save,true,"Saving…");
      feedback(local,"");
      try {
        const payload={enabled:draft.enabled};
        if(removeKey)payload.remove=true;else if(draft.credential)payload.credential=draft.credential.trim();
        if(!removeKey&&draft.credential&&!payload.credential)throw new Error("Enter a non-empty API key.");
        await api("providers/"+encodeURIComponent(p.id),"PUT",payload);
        providerDrafts.delete(p.id);providerMessages.set(p.id,{message:"Saved. New searches use these settings.",error:false});
        try{await refresh(true);}catch{providerMessages.set(p.id,{message:"Saved, but refresh failed. Refresh to see current state.",error:true});}
      }catch(error){providerMessages.set(p.id,{message:error.message,error:true});feedback(local,error.message,true);}
      finally{providerPending.delete(p.id);busyCount--;setPending(save,false);syncDraft();if(state)renderProviders();}
    }
    toggle.addEventListener("change",syncDraft);
    form.addEventListener("submit",event=>{event.preventDefault();if(!save.disabled)update();});
    if(p.editable&&p.credential&&!p.managed){
      remove=action("Remove key",async()=>{if(await confirmAction("Remove API key","Remove the stored key for "+info[0]+". Searches using it may become unavailable.","Remove key"))await update(true);},"danger");form.append(remove);
    }
    if(p.managed)row.append(element("p","Managed by "+p.env+". Change this value in the server environment.","notice"));
    else if(p.env&&!p.editable)row.append(element("p","Set "+p.env+" in the server environment. This URL is intentionally not editable here.","notice"));
    row.append(form,local);list.append(row);syncDraft();
  }
  filterProviders();
  if(focused&&document.getElementById(focused)&&focused.startsWith("key-")){
    const field=$(focused); if(!field.hidden&&!field.disabled){field.focus({preventScroll:true});if(start!==null&&start!==undefined)field.setSelectionRange(start,end);}
  }
}
$("provider-filter").addEventListener("input",filterProviders);
for(const input of document.querySelectorAll("input[name=provider-filter-kind]"))input.addEventListener("change",filterProviders);
$("reset-filter").addEventListener("click",()=>{$("provider-filter").value="";document.querySelector('input[name=provider-filter-kind][value=all]').checked=true;filterProviders();$("provider-filter").focus();});
function searchMode(){return document.querySelector("input[name=search-mode]:checked").value;}
function renderChoices(){
  const choices=$("choices");choices.replaceChildren();
  for(const p of state?.providers||[]){
    if(p.kind!=="general"||!p.available)continue;
    const label=element("label","","choice-chip"),check=document.createElement("input");
    check.type="checkbox";check.value=p.id;check.checked=selected.has(p.id);check.disabled=searchPending;
    label.append(check,element("span",providerName(p.id)),element("small",providerType(p),"chip-kind"));
    check.addEventListener("change",()=>{if(check.checked)selected.add(p.id);else selected.delete(p.id);syncSearchMode();});
    choices.append(label);
  }
  if(!choices.children.length)choices.append(element("p","No general engine is available. Enable one in Providers.","muted"));
  syncSearchMode();
}
function chosenProviders(){
  return (state?.providers||[]).filter(p=>p.kind==="general"&&p.available&&(searchMode()!=="selected"||selected.has(p.id)));
}
function syncSearchMode(){
  $("search-provider-field").hidden=searchMode()!=="selected";
  $("search-provider-field").disabled=searchPending;
  const chosen=chosenProviders();
  $("selection-count").textContent=chosen.length+" selected";
  $("search-cost").hidden=!chosen.some(p=>p.env);
  $("run-search").disabled=searchPending||chosen.length===0;
}
for(const node of document.querySelectorAll("input[name=search-mode]"))node.addEventListener("change",syncSearchMode);
$("select-keyless").addEventListener("click",()=>{selected.clear();for(const p of state.providers)if(p.kind==="general"&&p.available&&!p.env)selected.add(p.id);renderChoices();});
$("clear-selection").addEventListener("click",()=>{selected.clear();renderChoices();});
$("query").addEventListener("input",()=>{$("query-count").textContent=$("query").value.length+" / 1000";feedback("search-error");});
function renderSearch(data){
  $("search-output").hidden=false; $("search-source").textContent=data.cached?"Cached result":"Live attempt";
  const rows=Array.isArray(data.results)?data.results:[],attempts=data.provider_attempts||[];
  $("search-summary").textContent=(data.query ? "Query: "+data.query+" · " : "")+(rows.length?rows.length+" result"+(rows.length===1?"":"s")+(attempts.some(a=>a.includes(": error")||a.endsWith(": empty"))?" · Some providers returned no usable results.":""):"No usable results. Review provider attempts or try a different query.");
  $("search-attempts").replaceChildren(...attempts.map(text=>element("li",text)));
  $("results").replaceChildren();
  for(const row of rows){
    const card=element("article","","card result");
    let url;try{url=new URL(row.url);}catch{}
    if(url&&["https:","http:"].includes(url.protocol)) card.append(link(row.title||url.hostname,url.href));
    else card.append(element("h3",row.title||"Result"));
    card.append(element("p",row.url,"muted small"),element("p",row.snippet||"No snippet supplied."));
    $("results").append(card);
  }
}
function renderCache(){
  const c=state.cache;
  $("cache-summary").replaceChildren(metric("Stored entries",number(c.total_files),"Search and fetch"),metric("Disk usage",bytes(c.total_bytes),"Current cache files"));
  $("cache-dir").textContent=c.cache_dir||"Unavailable";
  $("cache-rows").replaceChildren();
  for(const name of ["search","fetch"]){
    const stats=c.namespaces?.[name];
    const tr=element("tr"),heading=element("th",name==="search"?"Search results":"Webpage content");heading.scope="row";
    const cell=element("td");cell.append(action("Clear "+name,()=>cacheAction("clear",name),"danger"));
    tr.append(heading,element("td",stats?number(stats.files):"—"),element("td",stats?bytes(stats.bytes):"—"),cell);$("cache-rows").append(tr);
  }
}
async function cacheAction(kind,namespace="all"){
  if(cachePending)return;
  const clear=kind==="clear";
  if(!await confirmAction(clear?"Clear "+(namespace==="all"?"all cache":namespace+" cache"):"Prune expired entries",clear?"Remove cached results in "+namespace+". This cannot be undone. New searches may make more upstream requests.":"Remove expired and invalid entries. Valid cached results are retained.",clear?"Clear cache":"Prune cache"))return;
  if(cachePending)return;
  cachePending=true;busyCount++;feedback("cache-feedback","Working…");
  for(const b of $("cache").querySelectorAll("button"))b.disabled=true;
  try{
    const result=await api("cache/"+kind,"POST",clear?{namespace,confirm:true}:undefined);
    if(result.error)throw new Error(result.error);
    await refresh(true);
    feedback("cache-feedback","Removed "+number(result.deleted_files)+" entries ("+bytes(result.deleted_bytes)+").");
  }catch(error){feedback("cache-feedback",error.message,true);}
  finally{cachePending=false;busyCount--;for(const b of $("cache").querySelectorAll("button"))b.disabled=false;}
}
$("prune").addEventListener("click",()=>cacheAction("prune"));
$("clear-cache").addEventListener("click",()=>cacheAction("clear"));
function renderSettings(force=false){
  const settings=state.settings;
  if(settingsDirty&&!force){
    for(const key of ["timeout","search_ttl","fetch_ttl"]){
      if(settings.locked.includes(key)){$(key).value=settings[key];$(key).disabled=true;$(key+"-lock").textContent="Managed by server environment.";}
    }
    if(settings.locked.includes("preferred")){$("preferred-field").disabled=true;$("preferred-lock").textContent="Managed by server environment.";}
    return;
  }
  settingsDirty=false;
  for(const key of ["timeout","search_ttl","fetch_ttl"]){
    const field=$(key);field.value=settings[key];field.disabled=settings.locked.includes(key);
    $(key+"-lock").textContent=field.disabled?"Managed by server environment.":"";
  }
  const preferred=$("preferred");preferred.replaceChildren();
  for(const id of ["",...state.providers.filter(p=>p.kind==="general").map(p=>p.id)]){
    const label=element("label","","radio-option"),radio=document.createElement("input");
    radio.type="radio";radio.name="preferred-engine";radio.value=id;radio.checked=id===(settings.preferred||"");
    label.append(radio,element("span",id?providerName(id)+" ("+id+")":"Default order"));
    preferred.append(label);
    radio.addEventListener("change",()=>{$("preferred-label").textContent=id?providerName(id):"Default order";$("preferred-picker").open=false;$("preferred-label").focus();settingsDirty=true;});
  }
  $("preferred-label").textContent=settings.preferred?providerName(settings.preferred):"Default order";
  $("preferred-field").disabled=settings.locked.includes("preferred");
  $("preferred-lock").textContent=settings.locked.includes("preferred")?"Managed by server environment.":"";
  $("preferred-picker").open=false;
}
$("settings-form").addEventListener("input",()=>{settingsDirty=true;feedback("settings-feedback");});
$("reset-settings").addEventListener("click",()=>{if(state){renderSettings(true);feedback("settings-feedback","Unsaved changes reset.");}});
function render(){
  renderOverview();renderProviders();renderChoices();renderCache();renderSettings();renderNativeOptions();
}
async function refresh(fresh=false){
  if(refreshPromise){if(!fresh)return refreshPromise;await refreshPromise.catch(()=>{});return refresh(true);}
  const button=$("refresh");setPending(button,true,"Refreshing…");
  refreshPromise=(async()=>{
    try{
      state=await api("overview");
      render();showDashboard();
      $("last-refresh").textContent="Updated "+new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
      $("server-status").textContent="Connected";$("server-status").classList.add("ready");
    }catch(error){
      if(state){$("server-status").textContent="Refresh failed";$("server-status").classList.remove("ready");$("last-refresh").textContent="Showing last successful data";}
      throw error;
    }finally{setPending(button,false);refreshPromise=null;}
  })();
  return refreshPromise;
}
async function boot(){
  $("boot-retry").hidden=true;$("boot-message").textContent="Connecting to your server…";
  try{await refresh();page(location.hash.slice(1),false);}
  catch(error){if(error.status!==401){$("login").hidden=true;$("boot").hidden=false;$("boot-message").textContent=error.message;$("boot-retry").hidden=false;}}
}
$("boot-retry").addEventListener("click",boot);
$("password-visibility").addEventListener("click",()=>{
  const input=$("password"),show=input.type==="password";input.type=show?"text":"password";$("password-visibility").textContent=show?"Hide":"Show";$("password-visibility").setAttribute("aria-pressed",String(show));input.focus();
});
$("login-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.currentTarget.querySelector('button[type=submit]');
  if(button.disabled)return;
  if(!$("password").value){feedback("login-status","Enter your administrator password.",true);$("password").focus();return;}
  setPending(button,true,"Signing in…");feedback("login-status");
  try{await api("login","POST",{password:$("password").value});$("password").value="";await refresh();page(location.hash.slice(1),false);$("page-title").focus();}
  catch(error){feedback("login-status",error.message,true);}
  finally{setPending(button,false);}
});
$("refresh").addEventListener("click",async()=>{try{await refresh();status("Dashboard refreshed.");}catch(error){status(error.message,true);}});
$("logout").addEventListener("click",async()=>{
  if(busyCount||refreshPromise){status("An operation is in progress. Wait for it to finish before signing out.",true);return;}
  if((settingsDirty||[...providerDrafts.values()].some(d=>d.credential)||state?.providers.some(p=>providerDrafts.get(p.id)?.enabled!==p.enabled))&&!await confirmAction("Sign out","Unsaved settings and credentials will be discarded.","Sign out",false))return;
  const button=$("logout");setPending(button,true,"Signing out…");
  try{await api("logout","POST");showLogin();feedback("login-status","Signed out.");}
  catch(error){status(error.message,true);}
  finally{setPending(button,false);}
});
$("search-form").addEventListener("submit",async event=>{
  event.preventDefault();if(searchPending)return;
  const query=$("query").value.trim(),chosen=chosenProviders(),mode=searchMode();
  if(!query){feedback("search-error","Enter a search query.",true);$("query").focus();return;}
  if(!chosen.length){feedback("search-error","Choose at least one available provider.",true);return;}
  if(chosen.some(p=>p.env)&&!await confirmAction("Use authenticated engines?","This search may consume provider credits or access your private instance. Continue with "+chosen.length+" engine(s)?","Run search",false))return;
  if(searchPending)return;
  searchPending=true;busyCount++;setPending($("run-search"),true,"Searching…");syncSearchMode();feedback("search-error");
  $("search-output").hidden=true;
  for(const n of document.querySelectorAll("input[name=search-mode], #query, #max-results"))n.disabled=true;
  try{
    const data={query,mode,max_results:Number($("max-results").value)};
    if(mode==="selected")data.providers=chosen.map(p=>p.id);
    renderSearch(await api("search","POST",data));
  }catch(error){
    if(Array.isArray(error.body?.results))renderSearch(error.body);
    else feedback("search-error",error.message,true);
  }finally{
    searchPending=false;busyCount--;setPending($("run-search"),false);
    for(const n of document.querySelectorAll("input[name=search-mode], #query, #max-results"))n.disabled=false;
    syncSearchMode();
  }
});
$("settings-form").addEventListener("submit",async event=>{
  event.preventDefault();if(settingsPending)return;
  const data={};
  for(const key of ["timeout","search_ttl","fetch_ttl"])if(!$(key).disabled)data[key]=Number($(key).value);
  if(!$("preferred-field").disabled)data.preferred=$("preferred").querySelector("input:checked")?.value||null;
  const button=event.currentTarget.querySelector('button[type=submit]');
  settingsPending=true;busyCount++;setPending(button,true,"Saving…");
  const editable=[...$("settings-form").querySelectorAll("input,button,fieldset")].filter(n=>n!==button&&!n.disabled);
  for(const n of editable)n.disabled=true;
  try{await api("settings","PUT",data);settingsDirty=false;await refresh(true);feedback("settings-feedback","Settings saved. Changes apply to new searches.");}
  catch(error){feedback("settings-feedback",error.message,true);}
  finally{settingsPending=false;busyCount--;for(const n of editable)n.disabled=false;setPending(button,false);if(state&&!settingsDirty)renderSettings(true);}
});
function nativeMode(){return document.querySelector("input[name=native-mode]:checked").value;}
function renderNativeOptions(){
  const mode=nativeMode(),select=$("native-provider"),old=select.value;
  const caps=(state?.native||[]).filter(p=>p[mode]);
  select.replaceChildren();
  for(const p of caps){
    const option=new Option(providerName(p.id)+(p.available?"":" — setup required"),p.id);
    option.disabled=!p.available; select.append(option);
  }
  if(caps.some(p=>p.id===old&&p.available))select.value=old;
  else if(caps.some(p=>p.available))select.value=caps.find(p=>p.available).id;
  else select.selectedIndex=-1;
  const selectedCap=caps.find(p=>p.id===select.value&&p.available);
  $("native-pricing").textContent=selectedCap?.pricing||"No configured provider for this capability. Add an API key in Providers.";
  $("native-links").replaceChildren();
  if(selectedCap)$("native-links").append(link("API documentation",mode==="research"?(selectedCap.research_docs||selectedCap.docs):selectedCap.docs),link("Pricing",selectedCap.pricing_url));
  const effortSelect=$("native-effort"),previousEffort=effortSelect.value;
  effortSelect.replaceChildren();
  for(const [value,label] of Object.entries(selectedCap?.efforts||{}))effortSelect.append(new Option(label,value));
  if([...effortSelect.options].some(o=>o.value===previousEffort))effortSelect.value=previousEffort;
  effortSelect.disabled=nativePending||!selectedCap;
  $("native-effort-field").hidden=mode!=="research";
  $("native-run").textContent=nativePending?"Working…":mode==="answer"?"Generate answer":"Run research";
  $("native-run").disabled=nativePending||!selectedCap;
  select.disabled=nativePending||!selectedCap;
}
for(const input of document.querySelectorAll("input[name=native-mode]"))input.addEventListener("change",renderNativeOptions);
$("native-provider").addEventListener("change",renderNativeOptions);
$("native-form").addEventListener("submit",async event=>{
  event.preventDefault();if(nativePending)return;
  const query=$("native-query").value.trim(),provider=$("native-provider").value,mode=nativeMode(),effort=$("native-effort").value||"lite";
  const capability=state?.native?.find(p=>p.id===provider&&p.available&&p[mode]);
  if(!query||!capability){feedback("native-feedback","Enter a question and select an available provider.",true);return;}
  if(!await confirmAction("Use provider AI?",providerName(provider)+" may spend API credits. "+capability.pricing,"Continue",false))return;
  if(nativePending)return;
  nativePending=true;busyCount++;
  const inputs=[...$("native-form").querySelectorAll("textarea,select,input,button")].filter(n=>!n.disabled);
  for(const n of inputs)n.disabled=true;
  $("native-run").textContent="Working…";$("native-run").setAttribute("aria-busy","true");
  feedback("native-feedback","Waiting for the provider…");$("native-result").hidden=true;
  try{
    const data=await api("native","POST",{query,provider,mode,effort,allow_cost:true});
    $("native-heading").textContent=providerName(data.provider)+(data.mode==="research"?" · Research":" · Answer");
    $("native-answer").textContent=data.answer;
    $("native-note").textContent=(data.truncated?"Response truncated to limit context. ":"")+"Generated by the provider; not independently verified. "+(data.source_kind==="search_results"?"These are retrieved search results, not verified citations.":data.source_kind==="grounding"?"These are grounding sources returned for the synthesized answer.":"Citation numbering follows the upstream response.");
    $("native-sources").replaceChildren();
    for(const source of data.sources){
      const li=element("li");li.value=source.index;
      let url;try{url=new URL(source.url);}catch{}
      if(source.url&&url&&["http:","https:"].includes(url.protocol))li.append(link(source.title||url.hostname,url.href));else li.append(element("span",source.title+" (URL unavailable)"));
      if(source.excerpt)li.append(element("p",source.excerpt,"muted small"));
      $("native-sources").append(li);
    }
    $("native-result").hidden=false;feedback("native-feedback","Completed.");
  }catch(error){feedback("native-feedback",error.message,true);}
  finally{
    nativePending=false;busyCount--;for(const n of inputs)n.disabled=false;
    $("native-run").removeAttribute("aria-busy");renderNativeOptions();
  }
});
function connection(){
  let url;
  try{url=new URL($("endpoint").value);if(!["https:","http:"].includes(url.protocol)||url.username||url.password||url.hash||url.search)throw new Error();}
  catch{ $("connection-json").textContent="Enter a valid HTTP(S) MCP endpoint without embedded credentials, a query string or a fragment.";$("copy-config").disabled=true;return; }
  const mode=document.querySelector("input[name=client-type]:checked").value;
  $("config-label").textContent=mode==="opencode"?"OpenCode JSON":"Streamable HTTP details";
  const headers={Authorization:"Bearer <your-private-token>"};
  $("connection-json").textContent=JSON.stringify(mode==="opencode"?{mcp:{"prism-search":{type:"remote",url:url.href,enabled:true,oauth:false,headers}}}:{url:url.href,transport:"streamable-http",headers},null,2);
  $("copy-config").disabled=false;
}
$("endpoint").addEventListener("input",()=>{connection();feedback("connection-feedback");});
for(const node of document.querySelectorAll("input[name=client-type]"))node.addEventListener("change",connection);
$("copy-config").addEventListener("click",async()=>{
  try{await navigator.clipboard.writeText($("connection-json").textContent);feedback("connection-feedback","Configuration copied. Replace the placeholder privately.");}
  catch{feedback("connection-feedback","Clipboard unavailable. Select and copy the text manually.",true);$("connection-json").focus();}
});
// Only infer the documented local mapping on local hosts; never assume a remote proxy exposes MCP.
if(["localhost","127.0.0.1"].includes(location.hostname))$("endpoint").value="http://127.0.0.1:49317/mcp";
connection();page(location.hash.slice(1),false);boot();
