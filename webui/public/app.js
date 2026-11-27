"use strict";
// Browser-local UI state. Credentials are never persisted in browser storage.
const $ = id => document.getElementById(id);
const pages = {
  overview: "Monitor the running server and understand search activity.",
  providers: "Configure search engines, credentials and availability.",
  playground: "Try a search before connecting an MCP client.",
  cache: "Inspect stored results and safely reclaim disk space.",
  logs: "Inspect sanitized operations, provider outcomes and cache activity.",
  settings: "Tune search behavior and personalize this browser.",
  connection: "Generate connection settings for your MCP client."
};
let state = null;
let activePage = "overview";
let refreshPromise = null;
let authEpoch = 0;
let busyCount = 0;
const providerDrafts = new Map();
const providerPending = new Set();
const providerMessages = new Map();
const selected = new Set();
let settingsDirty = false;
let defaultsDirty = false;
let defaultsPending = false;
let searchPending = false;
let settingsPending = false;
let cachePending = false;
let nativePending = false;
let aiSettingsDirty = false;
let aiSettingsPending = false;
let searchAnswerPreference = null;
let logRows=[],logsPending=false,logsRevision=0,logsSearch="";
const sessionRequests=new Set();

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
function safeHttpUrl(value) {
  if(typeof value!=="string"||value.includes(String.fromCharCode(92))||/[\s\x00-\x1f\x7f]/u.test(value))return null;
  if(!/^https?:\/\/[^/?#]/i.test(value)||Array.from(value).length>8192)return null;
  const authority=value.match(/^https?:\/\/([^/?#]+)/i)?.[1];
  if(!authority||/[@%]/.test(authority))return null;
  try{
    const url=new URL(value);
    return ["http:","https:"].includes(url.protocol)&&!url.username&&!url.password&&url.port!=="0"?url:null;
  }catch{return null;}
}
function link(label, url) {
  const target=safeHttpUrl(url);
  if(!target)return element("span",label+" (URL unavailable)");
  const a = element("a", label + " ↗");
  a.href = target.href; a.target = "_blank"; a.rel = "noopener noreferrer";
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
  const controller=new AbortController();
  sessionRequests.add(controller);
  let response,body;
  try {
    response = await fetch("/api/" + path, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Prism-Admin": "1" },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      signal: AbortSignal.any([controller.signal,AbortSignal.timeout(path==="native"&&data?.mode==="research"&&data?.provider==="tinyfish"?2820000:path==="native"&&data?.effort==="exhaustive"?360000:["search","native"].includes(path) ? 180000 : 20000)])
    });
    try{body=await response.json();}
    catch(error){if(error.name!=="SyntaxError")throw error;body=null;}
  } catch (e) {
    if(epoch!==authEpoch)throw new Error("Session changed. Please retry.");
    throw new Error(e.name === "TimeoutError" ? (["search","native"].includes(path) ? "The browser stopped waiting. The server may still be searching; check activity before retrying." : "The request timed out. Try again.") : "Cannot reach the server. Check the connection and retry.");
  } finally {
    sessionRequests.delete(controller);
  }
  if (epoch !== authEpoch) throw new Error("Session changed. Please retry.");
  // The controller remains registered until response body consumption ends.
  if (response.status === 401 && path !== "login") showLogin();
  if (!response.ok) {
    const error = new Error(body?.error || ("Server returned HTTP " + response.status + "."));
    error.status = response.status; error.body = body; throw error;
  }
  if (!body) throw new Error("The server returned an invalid response.");
  return body;
}
async function confirmAction(title, description, label, destructive = true) {
  const dialog = $("confirmation"),epoch=authEpoch;
  if (dialog.open) return false;
  $("confirm-title").textContent = title; $("confirm-description").textContent = description;
  $("confirm-action").textContent = label;
  $("confirm-action").className = destructive ? "danger" : "primary";
  dialog.returnValue = "cancel";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(epoch===authEpoch&&dialog.returnValue === "confirm"), { once: true });
    dialog.showModal();
  });
}
function showLogin() {
  authEpoch++;
  for(const request of sessionRequests)request.abort();
  sessionRequests.clear();
  logsRevision++;logRows=[];$("logs-list").replaceChildren();$("logs-export").disabled=true;
  $("logs-search").value="";logsSearch="";
  $("logs-level").value="";$("logs-provider").value="";$("logs-period").value="all";
  $("logs-health").textContent="Not loaded";$("logs-health").classList.remove("ready");
  feedback("logs-feedback");
  if($("confirmation").open)$("confirmation").close("cancel");
  state = null; providerDrafts.clear(); providerMessages.clear(); providerPending.clear();
  settingsDirty = false; defaultsDirty = false;
  $("default-fields").replaceChildren(); resetSearchOptions(false);
  for (const id of ["provider-list","choices","results","search-attempts","metrics","summary","server-facts","connection-facts","readiness","cache-summary","cache-rows"]) $(id).replaceChildren();
  aiSettingsDirty=false;searchAnswerPreference=null;
  $("search-ai-cards").replaceChildren();$("search-ai-attempts").replaceChildren();$("search-ai-results").hidden=true;
  $("native-provider").value="";$("native-effort").value="";
  $("native-query").value = ""; $("native-answer").textContent = ""; $("native-sources").replaceChildren(); $("native-result").hidden=true; feedback("native-feedback");
  $("query").value = ""; updateQueryCount(); $("search-output").hidden = true;
  for(const id of ["status","search-error","cache-feedback","settings-feedback",
    "defaults-feedback","ai-settings-feedback","connection-feedback"])feedback(id);
  $("endpoint").value = ["localhost","127.0.0.1"].includes(location.hostname) ? "http://127.0.0.1:49317/mcp" : ""; connection();
  $("password").value = ""; $("password").type = "password";
  $("password-visibility").textContent = "Show"; $("password-visibility").setAttribute("aria-pressed","false");
  $("dashboard").hidden = true; $("boot").hidden = true; $("login").hidden = false;
  $("password").focus();
}
function showDashboard() {
  $("boot").hidden = true; $("login").hidden = true; $("dashboard").hidden = false;
  if(activePage==="logs")loadLogs();
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
  if(id==="logs"&&state)loadLogs();
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
function providerName(id) {
  const exact=state?.providers?.find(p=>p.id===id);
  if(exact)return exact.display_name||exact.id;
  const suffix=typeof id==="string"?id.match(/:(answer|research)$/):null;
  if(suffix){
    const base=id.slice(0,-suffix[0].length);
    const provider=state?.providers?.find(p=>p.id===base);
    if(provider)return (provider.display_name||base)+" · "+(suffix[1]==="answer"?"Answer":"Research");
  }
  return id;
}
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
  const version = server?.version || "Version unavailable";
  const kind=server?.build_kind;
  const buildLabel=kind==="release"?"Release version":kind==="development"?"Local development":"Unidentified build";
  const commit=typeof server?.commit==="string"&&/^[0-9a-f]{7,40}$/.test(server.commit)?server.commit:null;
  const versionLink=$("sidebar-version"),buildDetail=$("sidebar-build");
  versionLink.textContent=kind==="release"?"v"+version:buildLabel;
  versionLink.title=version;
  const repository="https://github.com/isyuricunha/mcp-prism-search";
  if(kind==="release"&&/^[0-9]+[.][0-9]+[.][0-9]+$/.test(version))versionLink.href=repository+"/releases/tag/v"+version;
  else if(kind==="development"&&commit)versionLink.href=repository+"/commit/"+commit;
  else versionLink.removeAttribute("href");
  buildDetail.hidden=!commit;buildDetail.textContent=commit?"Commit "+commit.slice(0,9):"";
  $("sidebar-sdk").textContent = server?.sdk_version ? "MCP SDK "+server.sdk_version : "SDK version unavailable";
  const items = [["Prism Search",kind==="release"?"v"+version:version],["Build type",buildLabel],...(commit?[["Source commit",commit]]:[]),["Python MCP SDK",server?.sdk_version || "Unavailable"],["SDK protocol revision",server?.protocol_version || "Unavailable"],["Transport",server?.transport || "Unavailable"]];
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
function providerConfigured(p){
  return p.configured ?? (!p.env||p.credential);
}
function draftFor(p) {
  const existing=providerDrafts.get(p.id);
  if (!existing || (!existing.credential && existing.enabled===existing.baseline)) {
    providerDrafts.set(p.id,{enabled:p.enabled,baseline:p.enabled,credential:""});
  }
  const draft = providerDrafts.get(p.id);
  if(p.enabled_locked){draft.enabled=p.enabled;draft.baseline=p.enabled;}
  if (!p.editable || p.managed) draft.credential = "";
  if(!providerConfigured(p)&&!draft.credential.trim())draft.enabled=false;
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
    const info=[p.display_name||p.id,p.description,p.docs_url,p.signup_url];
    const draft=draftFor(p);
    const row=element("article","","provider-row"); row.dataset.provider=p.id;
    const head=element("div","","provider-head"), title=element("div","","stack");
    title.append(element("h3",info[0]),element("span",p.id+" · "+providerType(p),"provider-meta"));
    head.append(title,element("span",p.available?"Available":p.enabled_locked?"Disabled":!providerConfigured(p)?"Setup required":"Disabled","badge"+(p.available?" ready":"")));
    const links=element("div","","actions provider-links");
    if(info[2]) links.append(link("Website / docs",info[2]));
    if(info[3]) links.append(link("Get API key",info[3]));
    if(p.kind==="general"&&p.available) links.append(action("Test in Playground",()=>{selected.clear();selected.add(p.id);document.querySelector('input[name=search-mode][value=selected]').checked=true;$("search-overrides").open=true;renderChoices();page("playground");$("query").focus();},"tonal"));
    row.append(head,element("p",info[1],"muted"),links);
    if(p.usage_note)row.append(element("p",p.usage_note,"muted small"));
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
      const hint=element("span",p.credential?"Stored securely. Existing value is never displayed.":"No stored API key. Paste a key, then enable the provider and save.","muted small");hint.id="hint-"+p.id;input.setAttribute("aria-describedby",hint.id);
      field.append(label,input,hint);form.append(field);
      input.addEventListener("input",()=>{
        draft.credential=input.value;
        syncDraft();
      });
    }
    const save=element("button","Save","tonal");save.type="submit";save.id="save-"+p.id;form.append(save);
    const local=element("p","","feedback row-feedback");local.setAttribute("role","status");local.id="provider-feedback-"+p.id;
    const previous=providerMessages.get(p.id);
    if(previous)feedback(local,previous.message,previous.error);
    let remove=null;
    function syncDraft() {
      const canEnable=providerConfigured(p)||!!draft.credential.trim();
      if(!canEnable||p.enabled_locked)toggle.checked=false;
      draft.enabled=toggle.checked;
      const dirty=draft.enabled!==p.enabled||!!draft.credential.trim();
      save.disabled=!dirty||providerPending.has(p.id);
      toggle.disabled=providerPending.has(p.id)||!!p.enabled_locked||!canEnable;
      if(input)input.disabled=providerPending.has(p.id);
      if(remove)remove.disabled=providerPending.has(p.id);
      row.classList.toggle("dirty",dirty);
    }
    async function update(removeKey=false) {
      if(providerPending.has(p.id))return;
      const epoch=authEpoch;
      providerPending.add(p.id);busyCount++;syncDraft();setPending(save,true,"Saving…");
      feedback(local,"");
      try {
        const payload={enabled:draft.enabled};
        if(removeKey){payload.remove=true;payload.enabled=false;}else if(draft.credential)payload.credential=draft.credential.trim();
        if(!removeKey&&draft.credential&&!payload.credential)throw new Error("Enter a non-empty API key.");
        await api("providers/"+encodeURIComponent(p.id),"PUT",payload);
        providerDrafts.delete(p.id);providerMessages.set(p.id,{message:"Saved. New searches use these settings.",error:false});
        try{await refresh(true);}catch{if(epoch===authEpoch)providerMessages.set(p.id,{message:"Saved, but refresh failed. Refresh to see current state.",error:true});}
      }catch(error){if(epoch===authEpoch){providerMessages.set(p.id,{message:error.message,error:true});feedback(local,error.message,true);}}
      finally{providerPending.delete(p.id);busyCount--;setPending(save,false);syncDraft();if(state)renderProviders();}
    }
    toggle.addEventListener("change",syncDraft);
    form.addEventListener("submit",event=>{event.preventDefault();if(!save.disabled)update();});
    if(p.editable&&p.credential&&!p.managed){
      remove=action("Remove key",async()=>{if(await confirmAction("Remove API key","Remove the stored key for "+info[0]+". The provider will be disabled until you configure and enable it again.","Remove key"))await update(true);},"danger");form.append(remove);
    }
    if(p.enabled_locked)row.append(element("p","Disabled by MCP_DISABLED_PROVIDERS in the server environment.","notice"));
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
const defaultField = id => state?.defaults?.fields?.find(f=>f.id===id);
const defaultValue = id => defaultField(id)?.value;
const humanChoice = value => ({
  auto:"Automatic fallback", keyless:"Keyless fallback", all:"All available engines",
  selected:"Selected engines", any:"Any time", day:"Past day", week:"Past week",
  month:"Past month", year:"Past year", off:"Off", moderate:"Moderate", strict:"Strict",
  explicit:"Choose explicitly", lite:"Lite", standard:"Standard", deep:"Deep", max:"Max", exhaustive:"Exhaustive"
}[value] || providerName(value));
function searchMode(){return document.querySelector("input[name=search-mode]:checked").value;}
function effectiveSearchMode(){return searchMode()==="default"?(defaultValue("search_mode")||"auto"):searchMode();}
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
  for(const [id,key] of [["search-safe-search","search_safe_search"],["search-recency","search_recency"]]){
    const input=$(id),old=input.value,field=defaultField(key);
    input.replaceChildren(new Option("Server default"+(field?" ("+humanChoice(field.value)+")":""),""));
    for(const value of field?.choices||[])input.append(new Option(humanChoice(value),value));
    if([...input.options].some(o=>o.value===old))input.value=old;
  }
  const resultField=defaultField("search_max_results");
  $("max-results").placeholder=resultField?String(resultField.value):"Server default";
  if(resultField){$("max-results").min=resultField.min;$("max-results").max=resultField.max;}
  syncSearchMode();
}
function chosenProviders(){
  const mode=effectiveSearchMode();
  const ids=searchMode()==="default" ? new Set(defaultValue("search_providers")||[]) : selected;
  return (state?.providers||[]).filter(p=>p.kind==="general"&&p.available&&
    (mode!=="selected"||ids.has(p.id))&&(mode!=="keyless"||!p.env));
}
function syncSearchMode(){
  $("search-provider-field").hidden=searchMode()!=="selected";
  $("search-provider-field").disabled=searchPending;
  const chosen=chosenProviders(),mode=effectiveSearchMode();
  $("selection-count").textContent=chosen.length+" selected";
  const authorized=state?.settings?.ai_search_providers||[];
  const restricted=($("search-safe-search").value||defaultValue("search_safe_search")||"off")!=="off"||
    ($("search-recency").value||defaultValue("search_recency")||"any")!=="any"||
    ["allowed_domains","blocked_domains","preferred_domains"].some(k=>(defaultValue(k)||[]).length>0)||
    Array.from($("query").value.trim()).length>400;
  const available=!!state?.settings?.ai_search_enabled&&chosen.some(p=>authorized.includes(p.id))&&!restricted;
  const toggle=$("search-answers");
  toggle.disabled=searchPending||!available;
  toggle.checked=available&&searchAnswerPreference!==false;
  $("search-answers-hint").textContent=restricted
    ?"Domain, freshness, SafeSearch or query-length constraints disable synthesis."
    :available
    ?"Uses authorized engines only. Turn off for retrieval only."
    :"No authorized AI engine in this selection. Configure AI-enhanced search in Settings.";
  const names=mode==="selected"?" · "+chosen.map(p=>providerName(p.id)).join(", "):"";
  const count=$("max-results").value||defaultValue("search_max_results")||"server default";
  $("search-default-summary").textContent=(searchMode()==="default"?"Server defaults: ":"Overrides: ")+humanChoice(mode)+names+" · Up to "+count+" results · AI "+(toggle.checked?"enabled":"off");
  $("search-cost").hidden=!chosen.some(p=>p.env);
  const limit=$("max-results");
  $("max-results-hint").textContent=limit.validity.valid?"Leave blank to inherit.":
    "Enter a whole number from "+limit.min+" to "+limit.max+", or leave blank.";
  $("run-search").disabled=searchPending||chosen.length===0||!$("query").validity.valid||!limit.validity.valid;
}
function resetSearchOptions(render=true){
  document.querySelector('input[name=search-mode][value=default]').checked=true;
  selected.clear();searchAnswerPreference=null;
  $("max-results").value="";$("search-safe-search").value="";$("search-recency").value="";
  if(render)renderChoices();
}
$("search-answers").addEventListener("change",()=>{searchAnswerPreference=$("search-answers").checked;syncSearchMode();});
for(const node of document.querySelectorAll("input[name=search-mode],#search-safe-search,#search-recency"))node.addEventListener("change",syncSearchMode);
$("max-results").addEventListener("input",syncSearchMode);
$("reset-search-options").addEventListener("click",()=>resetSearchOptions());
$("select-keyless").addEventListener("click",()=>{selected.clear();for(const p of state.providers)if(p.kind==="general"&&p.available&&!p.env)selected.add(p.id);renderChoices();});
$("clear-selection").addEventListener("click",()=>{selected.clear();renderChoices();});
function updateQueryCount(){
  const input=$("query"),limit=state?.limits?.query_max_chars||4000;
  const count=Array.from(input.value).length;
  input.maxLength=limit*2;
  input.setCustomValidity(count>limit?"Use at most "+limit+" characters.":
    count&&!input.value.trim()?"Enter a non-empty search query.":"");
  $("query-count").textContent=count+" / "+limit;
}
$("query").addEventListener("input",()=>{updateQueryCount();feedback("search-error");syncSearchMode();});
function renderSearch(data){
  $("search-output").hidden=false; $("search-source").textContent=data.cached?"Cached result":"Live attempt";
  const rows=Array.isArray(data.results)?data.results:[],attempts=data.provider_attempts||[];
  $("search-summary").textContent=(data.query ? "Query: "+data.query+" · " : "")+(rows.length?rows.length+" result"+(rows.length===1?"":"s")+(attempts.some(a=>a.includes(": error")||a.endsWith(": empty"))?" · Some providers returned no usable results.":""):"No usable results. Review provider attempts or try a different query.");
  $("search-attempts").replaceChildren(...attempts.map(text=>element("li",text)));
  const aiAnswers=data.answers||[],aiAttempts=data.answer_attempts||[];
  $("search-ai-results").hidden=!aiAnswers.length&&!aiAttempts.length;
  $("search-ai-attempts").replaceChildren(...aiAttempts.map(value=>element("li",value)));
  $("search-ai-cards").replaceChildren();
  for(const answer of aiAnswers){
    const card=element("article","","card result stack");
    card.append(element("h3",providerName(answer.provider)+" · "+(answer.mode==="research"?"Research":"Answer")),
      element("p",answer.answer,"native-answer"),
      element("p",(answer.truncated?"Response truncated. ":"")+(answer.source_kind==="search_results"?"Retrieved results, not verified citations.":answer.source_kind==="grounding"?"Provider grounding references.":"Provider-supplied citations."),"muted small"));
    const sources=element("ol","","steps");
    appendNativeSources(sources,answer.sources);
    card.append(sources);$("search-ai-cards").append(card);
  }
  $("results").replaceChildren();
  for(const row of rows){
    const card=element("article","","card result");
    const url=safeHttpUrl(row.url);
    if(url) card.append(link(row.title||url.hostname,url.href));
    else card.append(element("h3",row.title||"Result"));
    card.append(element("p",url?url.href:"URL unavailable","muted small"),element("p",row.snippet||"No snippet supplied."));
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
    const cell=element("td"),button=action("Clear "+name,()=>cacheAction("clear",name),"danger");
    button.disabled=cachePending;cell.append(button);
    tr.append(heading,element("td",stats?number(stats.files):"—"),element("td",stats?bytes(stats.bytes):"—"),cell);$("cache-rows").append(tr);
  }
}
async function cacheAction(kind,namespace="all"){
  if(cachePending)return;
  const clear=kind==="clear";
  if(!await confirmAction(clear?"Clear "+(namespace==="all"?"all cache":namespace+" cache"):"Prune expired entries",clear?"Remove cached results in "+namespace+". This cannot be undone. New searches may make more upstream requests.":"Remove expired entries and stale temporary files. Active cached results are retained.",clear?"Clear cache":"Prune cache"))return;
  if(cachePending)return;
  const epoch=authEpoch;
  cachePending=true;busyCount++;feedback("cache-feedback","Working…");
  for(const b of $("cache").querySelectorAll("button"))b.disabled=true;
  try{
    const result=await api("cache/"+kind,"POST",clear?{namespace,confirm:true}:undefined);
    if(result.error)throw new Error(result.error);
    const message="Removed "+number(result.deleted_files)+" entries ("+bytes(result.deleted_bytes)+").";
    try{await refresh(true);if(epoch===authEpoch)feedback("cache-feedback",message);}
    catch{if(epoch===authEpoch)feedback("cache-feedback",message+" Refresh failed; refresh the dashboard to see current usage.",true);}
  }catch(error){if(epoch===authEpoch)feedback("cache-feedback",error.message,true);}
  finally{cachePending=false;busyCount--;for(const b of $("cache").querySelectorAll("button"))b.disabled=false;}
}
$("prune").addEventListener("click",()=>cacheAction("prune"));
$("clear-cache").addEventListener("click",()=>cacheAction("clear"));
function renderSettings(force=false){
  if(!state||settingsPending)return;
  const settings=state.settings;
  if(settingsDirty&&!force){
    for(const key of ["timeout","search_ttl","fetch_ttl"]){
      const field=$(key),locked=settings.locked.includes(key);
      if(locked)field.value=settings[key];
      field.disabled=locked;$(key+"-lock").textContent=locked?"Managed by server environment.":"";
    }
    const locked=settings.locked.includes("preferred");
    $("preferred-field").disabled=locked;$("preferred-lock").textContent=locked?"Managed by server environment.":"";
    if(locked){
      for(const radio of $("preferred").querySelectorAll("input"))radio.checked=radio.value===(settings.preferred||"");
      $("preferred-label").textContent=settings.preferred?providerName(settings.preferred):"Default order";
    }
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
  updateQueryCount();renderOverview();logFilters();renderProviders();renderChoices();renderCache();renderSettings();renderNativeOptions();renderAiSettings();renderDefaultSettings();
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
  if((settingsDirty||aiSettingsDirty||defaultsDirty||[...providerDrafts.values()].some(d=>d.credential)||state?.providers.some(p=>providerDrafts.get(p.id)?.enabled!==p.enabled))&&!await confirmAction("Sign out","Unsaved settings and credentials will be discarded.","Sign out",false))return;
  const button=$("logout");setPending(button,true,"Signing out…");
  try{await api("logout","POST");showLogin();feedback("login-status","Signed out.");}
  catch(error){status(error.message,true);}
  finally{setPending(button,false);}
});
function searchPayload(query,chosen,mode,useAnswers){
  const data={query,include_answers:useAnswers};
  if(mode!=="default")data.mode=mode;
  if($("max-results").value)data.max_results=Number($("max-results").value);
  if($("search-safe-search").value)data.safe_search=$("search-safe-search").value;
  if($("search-recency").value)data.time_filter=$("search-recency").value;
  if(mode==="selected")data.providers=chosen.map(p=>p.id);
  return data;
}
function searchConsentKey(data,chosen){
  return JSON.stringify([data,chosen.map(p=>[p.id,p.env]),state?.settings?.ai_search_enabled,
    state?.settings?.ai_search_providers,state?.defaults?.fields?.map(f=>[f.id,f.value])]);
}
$("search-form").addEventListener("submit",async event=>{
  event.preventDefault();if(searchPending)return;
  const query=$("query").value.trim(),chosen=chosenProviders(),mode=searchMode();
  if(!query){feedback("search-error","Enter a search query.",true);$("query").focus();return;}
  if(!chosen.length){feedback("search-error","Choose at least one available provider.",true);return;}
  const authorized=state?.settings?.ai_search_providers||[];
  const useAnswers=$("search-answers").checked;
  const data=searchPayload(query,chosen,mode,useAnswers);
  if(chosen.some(p=>p.env&&!(useAnswers&&authorized.includes(p.id)))){
    const consent=searchConsentKey(data,chosen);
    if(!await confirmAction("Use authenticated engines?","This search may consume provider credits or access your private instance. Continue with "+chosen.length+" engine(s)?","Run search",false))return;
    const current=chosenProviders();
    if(consent!==searchConsentKey(searchPayload($("query").value.trim(),current,
        searchMode(),$("search-answers").checked),current)){
      feedback("search-error","Search options or provider availability changed. Review and confirm again.",true);
      return;
    }
  }
  if(searchPending)return;
  const epoch=authEpoch;
  searchPending=true;busyCount++;setPending($("run-search"),true,"Searching…");syncSearchMode();feedback("search-error");
  $("search-output").hidden=true;
  for(const n of document.querySelectorAll("input[name=search-mode], #query, #max-results, #search-safe-search, #search-recency, #reset-search-options"))n.disabled=true;
  try{
    renderSearch(await api("search","POST",data));
  }catch(error){
    if(epoch!==authEpoch)return;
    if(Array.isArray(error.body?.results))renderSearch(error.body);
    else feedback("search-error",error.message,true);
  }finally{
    searchPending=false;busyCount--;setPending($("run-search"),false);
    for(const n of document.querySelectorAll("input[name=search-mode], #query, #max-results, #search-safe-search, #search-recency, #reset-search-options"))n.disabled=false;
    syncSearchMode();
  }
});
$("settings-form").addEventListener("submit",async event=>{
  event.preventDefault();if(settingsPending)return;
  const data={};
  for(const key of ["timeout","search_ttl","fetch_ttl"])if(!$(key).disabled)data[key]=Number($(key).value);
  if(!$("preferred-field").disabled)data.preferred=$("preferred").querySelector("input:checked")?.value||null;
  const button=event.currentTarget.querySelector('button[type=submit]'),epoch=authEpoch;
  settingsPending=true;busyCount++;setPending(button,true,"Saving…");
  const editable=[...$("settings-form").querySelectorAll("input,button,fieldset")].filter(n=>n!==button&&!n.disabled);
  for(const n of editable)n.disabled=true;
  try{
    state.settings=await api("settings","PUT",data);settingsDirty=false;
    try{await refresh(true);if(epoch===authEpoch)feedback("settings-feedback","Settings saved. Changes apply to new searches.");}
    catch{if(epoch===authEpoch&&state)feedback("settings-feedback","Settings saved. Dashboard refresh failed; refresh to see current server information.",true);}
  }
  catch(error){if(epoch===authEpoch)feedback("settings-feedback",error.message,true);}
  finally{settingsPending=false;busyCount--;for(const n of editable)n.disabled=false;setPending(button,false);if(state&&!settingsDirty)renderSettings(true);}
});
function renderDefaultSettings(force=false,useBuiltins=false){
  if(!state)return;
  if(defaultsDirty&&!force){
    for(const field of state.defaults?.fields||[]){
      const input=$("default-"+field.id);
      if(!input)continue;
      if(field.locked){
        if(field.type==="array"&&field.choices){
          for(const node of input.querySelectorAll("input"))node.checked=field.value.includes(node.value);
        }else input.value=field.type==="array"?field.value.join(", "):field.value;
      }
      input.disabled=field.locked||defaultsPending;
      const lock=input.closest(".default-field").querySelector("[data-default-lock]");
      if(lock)lock.textContent=field.locked?"Managed by "+field.env:"";
    }
    syncDefaultSelection();
    $("save-defaults").disabled=defaultsPending||!(state.defaults?.fields||[]).some(f=>!f.locked);
    return;
  }
  defaultsDirty=useBuiltins;
  const fields=state.defaults?.fields||[];
  const root=$("default-fields");
  const opened=new Set([...root.querySelectorAll("details[open]")].map(n=>n.dataset.group));
  root.replaceChildren();
  const groups=new Map();
  for(const field of fields){
    if(!groups.has(field.group)){
      const group=field.group==="Search"?element("section","","default-group"):element("details","","default-group");
      if(group.tagName==="DETAILS"){group.dataset.group=field.group;group.open=opened.has(field.group);group.append(element("summary",field.group));}
      else group.append(element("h3",field.group));
      const content=element("div","","default-grid");group.append(content);root.append(group);groups.set(field.group,content);
    }
    const value=useBuiltins&&!field.locked?field.builtin:field.value;
    const wrapper=element("div","","field default-field");
    const id="default-"+field.id;
    const hint=element("p",field.description,"muted small");hint.id=id+"-help";
    const lock=element("span",field.locked?"Managed by "+field.env:"","muted small");
    lock.dataset.defaultLock="";
    let input;
    if(field.type==="array"&&field.choices){
      input=element("fieldset","","default-provider-picker");input.id=id;
      input.append(element("legend",field.label),hint);
      const choices=element("div","","choices");
      for(const name of field.choices){
        const label=element("label","","choice-chip"),check=document.createElement("input");
        check.type="checkbox";check.value=name;check.checked=value.includes(name);
        const p=state.providers.find(x=>x.id===name);
        label.append(check,element("span",providerName(name)),element("small",p?.available?"Available":"Unavailable","chip-kind"));choices.append(label);
      }
      input.append(choices,lock);wrapper.append(input);
    }else{
      const label=element("label",field.label);label.htmlFor=id;
      if(field.choices){
        input=document.createElement("select");
        for(const choice of field.choices)input.append(new Option(humanChoice(choice),choice));
        input.value=value;
      }else if(field.type==="array"){
        input=document.createElement("textarea");input.rows=2;input.value=value.join(", ");
        input.placeholder="example.org, docs.example.com";
      }else{
        input=document.createElement("input");input.type="number";input.min=field.min;input.max=field.max;input.required=true;input.value=value;
      }
      input.id=id;input.setAttribute("aria-describedby",hint.id);
      wrapper.append(label,hint,input,lock);
    }
    input.disabled=field.locked||defaultsPending;
    input.dataset.defaultId=field.id;
    groups.get(field.group).append(wrapper);
  }
  syncDefaultSelection();
  $("save-defaults").disabled=defaultsPending||!fields.some(f=>!f.locked);
}
function syncDefaultSelection(){
  const mode=$("default-search_mode"),providers=$("default-search_providers");
  if(mode&&providers)providers.closest(".default-field").hidden=mode.value!=="selected";
}
function readDefaultInput(field){
  const input=$("default-"+field.id);
  if(field.type==="array")return field.choices?[...input.querySelectorAll("input:checked")].map(n=>n.value):input.value.split(/[,\n]+/).map(v=>v.trim()).filter(Boolean);
  if(field.type==="integer")return Number(input.value);
  return input.value;
}
$("defaults-form").addEventListener("invalid",event=>{
  // Open a collapsed group before native browser validation tries to focus it.
  const field=event.target.closest("[data-default-id]");
  if(field){
    const group=field.closest("details");
    if(group)group.open=true;
    const label=state?.defaults?.fields?.find(f=>f.id===field.dataset.defaultId)?.label;
    feedback("defaults-feedback","Check "+(label||"the highlighted setting")+".",true);
  }
},true);
$("defaults-form").addEventListener("input",()=>{defaultsDirty=true;syncDefaultSelection();feedback("defaults-feedback");});
$("reset-defaults").addEventListener("click",()=>{renderDefaultSettings(true);feedback("defaults-feedback","Unsaved changes reset.");});
$("builtin-defaults").addEventListener("click",()=>{renderDefaultSettings(true,true);feedback("defaults-feedback","Built-in values prepared. Save to apply them.");});
$("defaults-form").addEventListener("submit",async event=>{
  event.preventDefault();if(defaultsPending)return;
  const fields=state.defaults?.fields||[],values={};
  for(const field of fields){
    if(field.locked)continue;
    const value=readDefaultInput(field);
    if(JSON.stringify(value)!==JSON.stringify(field.value))values[field.id]=value;
  }
  if(!Object.keys(values).length){feedback("defaults-feedback","No changes to save.");defaultsDirty=false;return;}
  const epoch=authEpoch;
  defaultsPending=true;busyCount++;
  const button=$("save-defaults");setPending(button,true,"Saving…");
  const inputs=[...$("defaults-form").querySelectorAll("[data-default-id],button")].filter(n=>n!==button&&!n.disabled);
  for(const n of inputs)n.disabled=true;
  try{
    state.defaults=await api("defaults","PUT",{values});
    defaultsDirty=false;
    if(["answer_provider","research_provider","research_effort"].some(k=>Object.hasOwn(values,k))&&!nativePending){
      $("native-provider").value="";$("native-effort").value="";
    }
    renderDefaultSettings(true);renderChoices();renderNativeOptions();
    try{await refresh(true);if(epoch===authEpoch)feedback("defaults-feedback","Defaults saved. Subsequent MCP calls inherit these values.");}
    catch{if(epoch===authEpoch&&state)feedback("defaults-feedback","Defaults saved. Dashboard refresh failed; refresh to see current server information.",true);}
  }catch(error){if(epoch===authEpoch)feedback("defaults-feedback",error.message,true);}
  finally{
    defaultsPending=false;busyCount--;
    for(const n of inputs)n.disabled=false;
    setPending(button,false);
    if(!defaultsDirty)renderDefaultSettings(true);
  }
});
function renderAiSettings(force=false){
  if(!state)return;
  const settings=state.settings,locked=settings.locked||[];
  const global=$("ai-search-enabled"),field=$("ai-provider-field");
  global.disabled=aiSettingsPending||locked.includes("ai_search_enabled");
  field.disabled=aiSettingsPending||locked.includes("ai_search_providers");
  $("ai-policy-lock").textContent=locked.includes("ai_search_enabled")||locked.includes("ai_search_providers")?"Some values are managed by the server environment.":"";
  if(aiSettingsDirty&&!force)return;
  aiSettingsDirty=false;global.checked=!!settings.ai_search_enabled;
  const allowed=new Set(settings.ai_search_providers||[]);
  const list=$("ai-provider-list");list.replaceChildren();
  for(const capability of (state.native||[]).filter(p=>p.automatic_search!==false)){
    const label=element("label","","switch-label ai-provider-row");
    const input=document.createElement("input");
    input.type="checkbox";input.className="switch";input.setAttribute("role","switch");input.value=capability.id;
    input.checked=allowed.has(capability.id);
    input.setAttribute("aria-label","Authorize "+providerName(capability.id)+" for AI search");
    const desc=element("span","","stack");desc.append(element("strong",providerName(capability.id)),element("small",(capability.available?"Configured":"Setup required")+" · "+capability.pricing,"muted"));
    label.append(input,desc);list.append(label);
  }
  $("save-ai-settings").disabled=aiSettingsPending||(global.disabled&&field.disabled);
}
$("ai-settings-form").addEventListener("input",()=>{aiSettingsDirty=true;feedback("ai-settings-feedback");});
$("reset-ai-settings").addEventListener("click",()=>{if(state){renderAiSettings(true);feedback("ai-settings-feedback","Unsaved changes reset.");}});
$("ai-settings-form").addEventListener("submit",async event=>{
  event.preventDefault();if(aiSettingsPending)return;
  const data={},global=$("ai-search-enabled"),field=$("ai-provider-field");
  if(!global.disabled)data.ai_search_enabled=global.checked;
  if(!field.disabled)data.ai_search_providers=[...field.querySelectorAll("input:checked")].map(n=>n.value);
  const enabled=data.ai_search_enabled??state.settings.ai_search_enabled;
  const names=data.ai_search_providers??state.settings.ai_search_providers??[];
  if(enabled&&!names.length){feedback("ai-settings-feedback","Select at least one authorized provider.",true);return;}
  if(enabled){
    const consent=JSON.stringify([state.settings.locked,state.settings.ai_search_enabled,
      state.settings.ai_search_providers,global.checked,[...field.querySelectorAll("input:checked")].map(n=>n.value),
      (state.native||[]).filter(p=>names.includes(p.id)).map(p=>[p.id,p.pricing,p.available])]);
    if(!await confirmAction("Authorize AI-enhanced searches?","Future searches may spend credits with "+names.map(providerName).join(", ")+". This applies to all MCP clients using this server.","Authorize and save",false))return;
    const current=JSON.stringify([state.settings.locked,state.settings.ai_search_enabled,
      state.settings.ai_search_providers,global.checked,[...field.querySelectorAll("input:checked")].map(n=>n.value),
      (state.native||[]).filter(p=>names.includes(p.id)).map(p=>[p.id,p.pricing,p.available])]);
    if(current!==consent){feedback("ai-settings-feedback","AI policy or provider information changed. Review and confirm again.",true);return;}
  }
  if(aiSettingsPending)return;
  const epoch=authEpoch;
  aiSettingsPending=true;busyCount++;
  const button=$("save-ai-settings");setPending(button,true,"Saving…");global.disabled=true;field.disabled=true;
  try{
    state.settings=await api("settings","PUT",data);aiSettingsDirty=false;syncSearchMode();
    try{await refresh(true);if(epoch===authEpoch)feedback("ai-settings-feedback","AI search settings saved.");}
    catch{if(epoch===authEpoch&&state)feedback("ai-settings-feedback","AI search settings saved. Dashboard refresh failed; refresh to see current server information.",true);}
  }
  catch(error){if(epoch===authEpoch)feedback("ai-settings-feedback",error.message,true);}
  finally{aiSettingsPending=false;busyCount--;setPending(button,false);renderAiSettings();}
});
function appendNativeSources(list,sources){
  for(const source of sources||[]){
    const li=element("li");li.value=source.index;
    const url=safeHttpUrl(source.url);
    if(url)li.append(link(source.title||url.hostname,url.href));else li.append(element("span",source.title+" (URL unavailable)"));
    if(source.excerpt)li.append(element("p",source.excerpt,"muted small"));
    list.append(li);
  }
}
function nativeMode(){return document.querySelector("input[name=native-mode]:checked").value;}
function updateNativeCount(){
  const input=$("native-query"),limit=Number(input.dataset.limit||4000),count=Array.from(input.value).length;
  input.setCustomValidity(count>limit?"Use at most "+limit+" characters.":
    count&&!input.value.trim()?"Enter a non-empty question.":"");
  $("native-count").textContent=count+" / "+limit;
  syncNativeRun();
}
function syncNativeRun(){
  const mode=nativeMode(),provider=$("native-provider").value;
  const available=state?.native?.some(p=>p.id===provider&&p.available&&p[mode]);
  $("native-run").disabled=nativePending||!available||!$("native-query").validity.valid||
    (mode==="research"&&!$("native-effort").value);
}
$("native-query").addEventListener("input",()=>{updateNativeCount();feedback("native-feedback");});
function renderNativeOptions(){
  if(nativePending)return;
  const mode=nativeMode(),select=$("native-provider"),old=select.value;
  const caps=(state?.native||[]).filter(p=>p[mode]);
  select.replaceChildren(new Option("Choose a provider",""));
  for(const p of caps){
    const option=new Option((p[mode+"_name"]||providerName(p.id))+(p.available?"":" — setup required"),p.id);
    option.disabled=!p.available; select.append(option);
  }
  const configured=defaultValue(mode==="research"?"research_provider":"answer_provider");
  if(caps.some(p=>p.id===old&&p.available))select.value=old;
  else if(configured&&configured!=="explicit"){
    if(caps.some(p=>p.id===configured&&p.available))select.value=configured;
    else select.value="";
  }
  else select.value="";
  const selectedCap=caps.find(p=>p.id===select.value&&p.available);
  const question=$("native-query");
  question.dataset.limit=String(mode==="answer"?(selectedCap?.answer_max_chars||4000):4000);
  question.maxLength=Number(question.dataset.limit)*2;
  updateNativeCount();
  $("native-pricing").textContent=selectedCap?(selectedCap.pricing+(mode==="research"&&selectedCap.research_wait_note?" "+selectedCap.research_wait_note:"")):
    (caps.some(p=>p.available)?"Choose an available provider to review pricing.":"No configured provider for this capability. Add an API key in Providers.");
  $("native-links").replaceChildren();
  if(selectedCap)$("native-links").append(link("API documentation",mode==="research"?(selectedCap.research_docs||selectedCap.docs):selectedCap.docs),link("Pricing",selectedCap.pricing_url));
  const effortSelect=$("native-effort"),previousEffort=effortSelect.value;
  effortSelect.replaceChildren(new Option("Choose research effort",""));
  for(const [value,label] of Object.entries(selectedCap?.efforts||{}))effortSelect.append(new Option(label,value));
  if(previousEffort&&[...effortSelect.options].some(o=>o.value===previousEffort))effortSelect.value=previousEffort;
  else if([...effortSelect.options].some(o=>o.value===defaultValue("research_effort")))effortSelect.value=defaultValue("research_effort");
  else effortSelect.value="";
  effortSelect.disabled=nativePending||!selectedCap;
  $("native-effort-field").hidden=mode!=="research";
  $("native-run").textContent=nativePending?"Working…":mode==="answer"?"Generate answer":"Run research";
  syncNativeRun();
  select.disabled=nativePending||!caps.some(p=>p.available);
}
for(const input of document.querySelectorAll("input[name=native-mode]"))input.addEventListener("change",()=>{$("native-provider").value="";$("native-effort").value="";renderNativeOptions();});
$("native-provider").addEventListener("change",()=>{$("native-effort").value="";renderNativeOptions();});
$("native-effort").addEventListener("change",renderNativeOptions);
$("native-form").addEventListener("submit",async event=>{
  event.preventDefault();if(nativePending)return;
  const query=$("native-query").value.trim(),provider=$("native-provider").value,mode=nativeMode(),effort=$("native-effort").value||"lite";
  const capability=state?.native?.find(p=>p.id===provider&&p.available&&p[mode]);
  if(!query||!capability){feedback("native-feedback","Enter a question and select an available provider.",true);return;}
  if(Array.from(query).length>Number($("native-query").dataset.limit||4000)){feedback("native-feedback","Question exceeds this provider’s character limit.",true);return;}
  if(mode==="research"&&!$("native-effort").value){feedback("native-feedback","Choose a supported research effort.",true);return;}
  if(!await confirmAction("Use provider AI?",providerName(provider)+" may spend API credits. "+capability.pricing+(mode==="research"?" "+($("native-effort").selectedOptions[0]?.textContent||"")+"."+(capability.research_wait_note?" "+capability.research_wait_note:""):""),"Continue",false))return;
  if(nativePending)return;
  const current=state?.native?.find(p=>p.id===provider&&p.available&&p[mode]);
  if(!current||current.pricing!==capability.pricing||nativeMode()!==mode||
      $("native-provider").value!==provider||$("native-query").value.trim()!==query||
      (mode==="research"&&($("native-effort").value!==effort||
        current.efforts?.[effort]!==capability.efforts?.[effort]))){
    feedback("native-feedback","Provider or request changed. Review the current options and confirm again.",true);return;
  }
  const epoch=authEpoch;
  nativePending=true;busyCount++;
  const inputs=[...$("native-form").querySelectorAll("textarea,select,input,button")].filter(n=>!n.disabled);
  for(const n of inputs)n.disabled=true;
  $("native-run").textContent="Working…";$("native-run").setAttribute("aria-busy","true");
  feedback("native-feedback",mode==="research"&&capability.research_wait_note?capability.research_wait_note:"Waiting for the provider…");$("native-result").hidden=true;
  try{
    const data=await api("native","POST",{query,provider,mode,effort,allow_cost:true});
    $("native-heading").textContent=capability[data.mode+"_name"]||providerName(data.provider)+(data.mode==="research"?" · Research":" · Answer");
    $("native-answer").textContent=data.answer;
    $("native-note").textContent=(data.truncated?"Response truncated to limit context. ":"")+"Generated by the provider; not independently verified. "+(data.source_kind==="search_results"?"These are retrieved search results, not verified citations.":data.source_kind==="grounding"?"These are grounding sources returned for the synthesized answer.":"Citation numbering follows the upstream response.");
    $("native-sources").replaceChildren();
    appendNativeSources($("native-sources"),data.sources);
    $("native-result").hidden=false;feedback("native-feedback","Completed.");
  }catch(error){if(epoch===authEpoch)feedback("native-feedback",error.message,true);}
  finally{
    nativePending=false;busyCount--;for(const n of inputs)n.disabled=false;
    $("native-run").removeAttribute("aria-busy");renderNativeOptions();
  }
});
function logFilters(){
  const provider=$("logs-provider"),previous=provider.value;
  provider.replaceChildren(new Option("All providers",""));
  for(const p of state?.providers||[])provider.append(new Option(providerName(p.id),p.id));
  if([...provider.options].some(o=>o.value===previous))provider.value=previous;
}
function renderLogs(){
  const list=$("logs-list");list.replaceChildren();
  for(const row of logRows){
    const article=element("article","","log-entry");
    const head=element("div","","log-head");
    const level=element("span",row.level,"badge");
    head.append(level,element("strong",row.operation||row.event),element("span",row.outcome,"muted"));
    const date=new Date(row.timestamp);
    const meta=element("div","","log-meta muted");
    meta.append(element("span",Number.isNaN(date.getTime())?"Unknown time":date.toLocaleString()),
      ...(row.provider?[element("span",providerName(row.provider))]:[]),
      ...(row.status_code?[element("span","HTTP "+row.status_code)]:[]),
      ...(Number.isInteger(row.duration_ms)?[element("span",number(row.duration_ms)+" ms")]:[]),
      ...(Number.isInteger(row.result_count)?[element("span",number(row.result_count)+(row.event==="cache"?" entries":" results"))]:[]));
    article.append(head,meta);
    if(row.request_id){
      const details=element("div","","log-meta");
      details.append(element("code",row.request_id),action("Related events",()=>{
        $("logs-level").value="";$("logs-provider").value="";$("logs-period").value="all";
        $("logs-search").value=row.request_id;changeLogs();$("logs-search").focus();
      }));
      article.append(details);
    }
    list.append(article);
  }
  if(!logRows.length)list.append(element("p","No matching events.","muted"));
  $("logs-export").disabled=!logRows.length;
}
async function loadLogs(){
  if(!state||activePage!=="logs"||logsPending)return;
  logsPending=true;const revision=logsRevision,epoch=authEpoch;
  $("logs-refresh").disabled=true;
  const params=new URLSearchParams({level:$("logs-level").value,provider:$("logs-provider").value,period:$("logs-period").value,search:logsSearch,limit:"200"});
  try{
    const data=await api("logs?"+params.toString());
    if(epoch!==authEpoch||revision!==logsRevision)return;
    logRows=Array.isArray(data.events)?data.events:[];
    const healthy=data.enabled&&data.healthy!==false;
    $("logs-health").textContent=healthy?"Recording":data.enabled?"Write failed":"Not available";
    $("logs-health").classList.toggle("ready",healthy);
    renderLogs();
    feedback("logs-feedback",data.enabled?number(logRows.length)+" events · Updated "+new Date().toLocaleTimeString()+(healthy?"":" · Some events could not be saved."):"Persistent log history is not enabled.",data.enabled&&!healthy);
  }catch(error){
    if(epoch===authEpoch&&revision===logsRevision){
      $("logs-health").textContent="Refresh failed";$("logs-health").classList.remove("ready");
      feedback("logs-feedback",error.message+(logRows.length?" Showing the last successful snapshot.":""),true);
    }
  }finally{
    logsPending=false;$("logs-refresh").disabled=false;
    if(revision!==logsRevision&&state&&activePage==="logs")loadLogs();
  }
}
function changeLogs(){logsSearch=$("logs-search").value.trim();logsRevision++;logRows=[];$("logs-export").disabled=true;$("logs-list").replaceChildren();loadLogs();}
for(const id of ["logs-level","logs-provider","logs-period"])$(id).addEventListener("change",changeLogs);
$("logs-search").addEventListener("change",changeLogs);
$("logs-search").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();changeLogs();}});
$("logs-refresh").addEventListener("click",()=>{
  if($("logs-search").value.trim()!==logsSearch)changeLogs();
  else loadLogs();
});
$("logs-export").addEventListener("click",()=>{
  if(!state||!logRows.length)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(logRows,null,2)],{type:"application/json"}));
  const a=document.createElement("a");a.href=url;a.download="prism-events.json";a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});
function pollLogs(){if(!document.hidden&&$("logs-live").checked&&activePage==="logs")loadLogs();}
$("logs-live").addEventListener("change",pollLogs);
document.addEventListener("visibilitychange",pollLogs);
setInterval(pollLogs,5000);
function connection(){
  let url;
  try{url=safeHttpUrl($("endpoint").value);if(!url||url.hash||url.search)throw new Error();}
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
