
import { chromium } from "playwright-core";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const root=fileURLToPath(new URL("../dist/",import.meta.url));
const browser=process.env.PRISM_TEST_CDP ? await chromium.connectOverCDP(process.env.PRISM_TEST_CDP) : await chromium.launch({headless:true,executablePath:process.env.PRISM_TEST_BROWSER});
const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:"reduce"});
const page=await context.newPage();
const errors=[];let lastSearch=null;let lastNative=null;
page.on("pageerror",e=>errors.push(e.message));
let logged=false, updates=0, cleared=0;
let holdSettings=null,holdNative=null,failOverview=false,logsHealthy=true,failLogs=false;
// Deliberate response latency exposes stale DOM/fixture assertions on fast runners.
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function responseFor(endpoint, predicate=()=>true){
 return page.waitForResponse(response=>response.url().endsWith(endpoint)&&
   response.request().method()==="POST"&&predicate(response.request().postDataJSON()));
}
async function searchComplete(response){
 await response;
 await page.waitForFunction(()=>!document.querySelector("#run-search").disabled &&
   !document.querySelector("#search-output").hidden);
}
async function nativeComplete(response){
 await response;
 await page.waitForFunction(()=>!document.querySelector("#native-run").hasAttribute("aria-busy") &&
   !document.querySelector("#native-result").hidden);
}
const provider=(id,extra={})=>({id,kind:"general",description:"A search engine for web research.",enabled:true,available:true,credential:false,editable:false,managed:false,env:null,...extra});
let data={defaults:JSON.parse(await fs.readFile(new URL("./defaults-fixture.json",import.meta.url),"utf8")),native:[{id:"tinyfish",answer:true,research:true,automatic_search:false,available:true,answer_max_chars:2000,answer_name:"TinyFish · TinyAnswer",research_name:"TinyFish · TinyResearch",efforts:{deep:"Deep — $2/run",max:"Max — $3/run"},pricing:"Account-dependent Answer; Research $2/$3",research_wait_note:"Up to 45 minutes",docs:"https://docs.tinyfish.ai/tinyanswer",research_docs:"https://docs.tinyfish.ai/tinyresearch/reference",pricing_url:"https://www.tinyfish.ai/pricing"},{id:"exa",answer:true,research:true,efforts:{lite:"Deep Lite",deep:"Deep"},available:true,pricing:"$5/1,000 calls",docs:"https://exa.ai/docs/reference/answer",pricing_url:"https://exa.ai/pricing"},{id:"youcom",answer:true,research:true,available:false,pricing:"API credits",docs:"https://you.com/docs/guides/research",pricing_url:"https://you.com/pricing"}],server:{version:"9.8.7",build_kind:"release",commit:null,sdk_version:"2.3.0",protocol_version:"2026-07-28",transport:"streamable-http"},providers:[provider("keenable"),provider("freeserp"),provider("tinyfish",{editable:true,credential:true,env:"TINYFISH_API_KEY"})],metrics:[],counters:{},cache:{total_files:12,total_bytes:10240,cache_dir:"/data/cache",namespaces:{search:{files:10,bytes:8192},fetch:{files:2,bytes:2048}}},settings:{timeout:15,search_ttl:21600,fetch_ttl:7200,preferred:null,ai_search_enabled:false,ai_search_providers:[],locked:[]}};
await page.route("http://prism-test.invalid/**",async route=>{
 const url=new URL(route.request().url());
 const pathname=url.pathname;
 if(pathname.startsWith("/api/")){
   let status=200, body={ok:true};
   if(pathname==="/api/login") logged=true;
   else if(!logged){status=401;body={error:"Authentication required"};}
   else if(pathname==="/api/overview"){if(failOverview){failOverview=false;status=503;body={error:"Unavailable"};}else body=data;}
   else if(pathname==="/api/logs"){
    const entries=[{timestamp:"2026-11-27T17:00:00Z",level:"WARNING",event:"provider",outcome:"limited",provider:"tinyfish",status_code:429,duration_ms:250,request_id:"0123456789abcdef0123456789abcdef"},{timestamp:"2026-11-27T16:59:00Z",level:"INFO",event:"operation",operation:"web_search",outcome:"ok",result_count:3}];
    body={enabled:true,healthy:logsHealthy,events:entries.filter(r=>(!url.searchParams.get("level")||r.level===url.searchParams.get("level"))&&(!url.searchParams.get("provider")||r.provider===url.searchParams.get("provider"))&&(!url.searchParams.get("search")||JSON.stringify(r).includes(url.searchParams.get("search")))),limit:200};
    if(failLogs){status=503;body={error:"Unavailable"};failLogs=false;}
   }
   else if(pathname.startsWith("/api/providers/")){updates++;const id=decodeURIComponent(pathname.split("/").pop());const d=route.request().postDataJSON();const p=data.providers.find(x=>x.id===id);if(d.credential)p.credential=true;if(d.remove)p.credential=false;p.configured=!p.env||p.credential;p.enabled=d.enabled&&p.configured&&!p.enabled_locked;p.available=p.enabled;}
   else if(pathname==="/api/settings"){Object.assign(data.settings,route.request().postDataJSON());body=data.settings;if(holdSettings)await holdSettings;}
   else if(pathname==="/api/defaults"){
     if(route.request().method()==="PUT"){for(const [id,value] of Object.entries(route.request().postDataJSON().values)){data.defaults.fields.find(f=>f.id===id).value=value;}}
     body=data.defaults;
   }
   else if(pathname==="/api/cache/clear"){cleared++;body={deleted_files:2,deleted_bytes:2048};}
   else if(pathname==="/api/logout")logged=false;
   else if(pathname==="/api/native"){lastNative=route.request().postDataJSON();body={provider:"exa",mode:lastNative.mode,answer:"Generated answer <script>bad</script>",sources:[{index:1,url:"https://example.org",title:"Native source",excerpt:"Evidence"},{index:2,url:null,title:"Invalid URL",excerpt:""},{index:3,url:"https://user:pass@example.org",title:"Embedded credentials",excerpt:""}],truncated:false};}
   else if(pathname==="/api/search"){lastSearch=route.request().postDataJSON();body={cached:false,provider_attempts:["keenable: ok"],results:[{title:"Example",url:"https://example.com",snippet:"Test result"},{title:"Unsafe",url:"javascript:alert(1)",snippet:"Untrusted"}],answers:lastSearch.include_answers?[{provider:"exa",mode:"answer",answer:"Enhanced answer <script>bad</script>",source_kind:"grounding",sources:[{index:2,url:"https://example.org",title:"Grounded source",excerpt:"Evidence"}]}]:[],answer_attempts:lastSearch.include_answers?["exa: ok"]:[]};}
   if(pathname==="/api/native"&&holdNative)await holdNative;
   if(["/api/search","/api/native"].includes(pathname))await delay(120);
   await route.fulfill({status,json:body});return;
 }
 const name=pathname==="/"?"index.html":pathname.slice(1);
 if(!["index.html","app.js","app.css","theme.css","favicon.svg"].includes(name) && !/^_astro\/[a-zA-Z0-9_.-]+\.js$/.test(name))return route.fulfill({status:404});
 const types={"html":"text/html","js":"text/javascript","css":"text/css","svg":"image/svg+xml"};
 await route.fulfill({headers:{"Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'"},contentType:types[name.split(".").pop()],body:await fs.readFile(root+"/"+name)});
});

const results=[];
const axe=await fs.readFile(require.resolve("axe-core/axe.min.js"),"utf8");
async function audit(name){
 await page.evaluate(axe);
 const findings=await page.evaluate(async()=>await window.axe.run({runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21a","wcag21aa","wcag22aa"]}}));
 for(const item of findings.violations)results.push({page:name,id:item.id,impact:item.impact,nodes:item.nodes.map(n=>({target:n.target,summary:n.failureSummary})).slice(0,6)});
}
async function nav(name){await page.locator("[data-page]").filter({hasText:new RegExp("^"+name+"$")}).click();}
const sections=["Overview","Providers","Playground","Cache","Logs","Settings","Connection"];
try{
 await page.goto("http://prism-test.invalid/");
 await page.locator("#login").waitFor({state:"visible"});
 await audit("login-dark");
 await page.locator("#password-visibility").click();
 assert.equal(await page.locator("#password").getAttribute("type"),"text");
 await page.locator("#password-visibility").click();
 await page.locator("#password").fill("test-only-password");
 await page.getByRole("button",{name:"Sign in",exact:true}).click();
 await page.locator("#dashboard").waitFor({state:"visible"});
 assert.equal(await page.locator("#sidebar-version").textContent(),"v9.8.7");
 assert.ok((await page.locator("#sidebar-version").getAttribute("href")).endsWith("/releases/tag/v9.8.7"));
 assert.ok((await page.locator("#server-facts").textContent()).includes("2.3.0"));
 data.providers.find(p=>p.id==="tinyfish").display_name="TinyFish TinySearch";
 data.metrics=[{name:"tinyfish:answer",requests:1,ok:1,empty:0,errors:0,limited:0,seconds:0.2}];
 await page.evaluate(()=>refresh());
 assert.ok((await page.locator("#metrics").textContent()).includes("TinyFish TinySearch · Answer"));
 data.metrics=[];
 const originalServer={...data.server};
 data.server={...originalServer,version:"9.8.8.dev1+g123abc456",build_kind:"development",commit:"123abc456"};
 await page.evaluate(()=>refresh());
 assert.equal(await page.locator("#sidebar-version").textContent(),"Local development");
 assert.equal(await page.locator("#sidebar-build").textContent(),"Commit 123abc456");
 assert.ok((await page.locator("#sidebar-version").getAttribute("href")).endsWith("/commit/123abc456"));
 assert.ok((await page.locator("#server-facts").textContent()).includes("9.8.8.dev1+g123abc456"));
 data.server={...originalServer,version:"0.0.0+unknown",build_kind:"unknown",commit:null};
 await page.evaluate(()=>refresh());
 assert.equal(await page.locator("#sidebar-version").textContent(),"Unidentified build");
 assert.equal(await page.locator("#sidebar-version").getAttribute("href"),null);
 assert.equal(await page.locator("#sidebar-build").isVisible(),false);
 data.server=originalServer;
 await page.evaluate(()=>refresh());
 assert.equal(await page.locator("#sidebar-version").textContent(),"v9.8.7");
 const viewports=[1440,900,768,720,450,375];
 for(const width of viewports){
  await page.setViewportSize({width,height:900});
  for(const name of sections){
   await nav(name);
   assert.equal(await page.locator("#page-title").textContent(),name);
   const dims=await page.evaluate(()=>({view:document.documentElement.clientWidth,body:document.documentElement.scrollWidth}));
   assert.ok(dims.body<=dims.view+1,JSON.stringify({width,name,dims}));
  }
  console.log("Viewport",width,"all seven sections fit");
 }
 await page.setViewportSize({width:1440,height:900});
 for(const name of sections){await nav(name);await audit(name+"-dark");}
 await page.locator("#theme").click();
 for(const name of sections){await nav(name);await audit(name+"-white");}
 await page.locator("#theme").click();
 await nav("Logs");
 await page.locator("#logs-live").uncheck();
 await page.waitForFunction(()=>document.querySelectorAll(".log-entry").length===2);
 logsHealthy=false;
 await page.locator("#logs-refresh").click();
 await page.waitForFunction(()=>document.querySelector("#logs-health").textContent==="Write failed");
 assert.ok((await page.locator("#logs-feedback").textContent()).includes("could not be saved"));
 failLogs=true;
 await page.locator("#logs-refresh").click();
 await page.waitForFunction(()=>document.querySelector("#logs-health").textContent==="Refresh failed");
 assert.equal(await page.locator("#logs-export").isDisabled(),false);
 assert.ok((await page.locator("#logs-feedback").textContent()).includes("last successful snapshot"));
 logsHealthy=true;
 await page.locator("#logs-refresh").click();
 await page.waitForFunction(()=>document.querySelector("#logs-health").textContent==="Recording");
 await page.locator("#logs-level").selectOption("WARNING");
 await page.waitForFunction(()=>document.querySelectorAll(".log-entry").length===1);
 assert.ok((await page.locator("#logs-list").textContent()).includes("HTTP 429"));
 await page.locator("#logs-provider").selectOption("tinyfish");
 await page.waitForFunction(()=>document.querySelectorAll(".log-entry").length===1);
 await page.locator("#logs-search").fill("missing");
 await page.locator("#logs-search").press("Enter");
 await page.waitForFunction(()=>document.querySelector("#logs-list").textContent.includes("No matching"));
 assert.equal(await page.locator("#logs-export").isDisabled(),true);
 await page.locator("#logs-search").fill("");
 await page.locator("#logs-search").press("Enter");
 await page.waitForFunction(()=>document.querySelectorAll(".log-entry").length===1);
 const downloaded=page.waitForEvent("download");
 await page.locator("#logs-export").click();
 const download=await downloaded;
 assert.equal(download.suggestedFilename(),"prism-events.json");
 const exported=JSON.parse(await fs.readFile(await download.path(),"utf8"));
 assert.equal(exported.length,1);
 assert.equal(exported[0].status_code,429);
 await page.locator(".log-entry").getByRole("button",{name:"Related events",exact:true}).click();
 assert.equal(await page.locator("#logs-level").inputValue(),"");
 assert.equal(await page.locator("#logs-provider").inputValue(),"");
 assert.equal(await page.locator("#logs-search").inputValue(),"0123456789abcdef0123456789abcdef");
 await page.waitForFunction(()=>document.querySelectorAll(".log-entry").length===1);
 assert.ok((await page.locator("#logs-list").textContent()).includes("HTTP 429"));
 await page.locator("#logs-search").fill("unfinished draft");
 const resumed=page.waitForResponse(r=>new URL(r.url()).pathname==="/api/logs");
 await page.locator("#logs-live").evaluate(n=>{n.checked=true;n.dispatchEvent(new Event("change"));});
 const resumedResponse=await resumed;
 assert.equal(new URL(resumedResponse.url()).searchParams.get("search"),"0123456789abcdef0123456789abcdef");
 await page.locator("#logs-live").evaluate(n=>{n.checked=false;n.dispatchEvent(new Event("change"));});
 await page.locator("#logs-search").fill("0123456789abcdef0123456789abcdef");
 await audit("logs-filtered-dark");
 await page.locator("#theme").click();await audit("logs-filtered-white");await page.locator("#theme").click();
 await nav("Providers");
 await page.locator("#provider-filter").fill("no-such-engine");
 await page.locator("#provider-empty").waitFor({state:"visible"});
 await page.locator("#reset-filter").click();
 const row=page.locator('.provider-row[data-provider="tinyfish"]');
 await row.locator("input[type=password]").fill("draft-test-not-secret");
 await page.locator("#refresh").click();
 await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 assert.equal(await row.locator("input[type=password]").inputValue(),"draft-test-not-secret");
 await row.locator("input[type=password]").fill("");
 await row.getByRole("switch").uncheck();
 await row.getByRole("button",{name:"Save",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[data-provider="tinyfish"] .row-feedback').textContent.includes("Saved"));
 assert.equal(updates,1);
 await row.getByRole("button",{name:"Remove key"}).click();
 await page.getByRole("dialog").waitFor({state:"visible"});
 await page.keyboard.press("Escape");
 await page.getByRole("dialog").waitFor({state:"hidden"});
 assert.equal(updates,1);
 // An environment lock arriving during a draft must win over the stale toggle.
 await row.getByRole("switch").check();
 await row.locator("input[type=password]").fill("draft-key");
 const tiny=data.providers.find(p=>p.id==="tinyfish");
 tiny.enabled_locked=true;
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 assert.equal(await row.getByRole("switch").isChecked(),false);
 assert.equal(await row.getByRole("switch").isDisabled(),true);
 assert.equal(await row.locator("input[type=password]").inputValue(),"draft-key");
 await row.locator("input[type=password]").fill("");
 tiny.enabled_locked=false;
 // Missing configuration is off and cannot be enabled without a key.
 tiny.credential=false;tiny.configured=false;tiny.enabled=false;tiny.available=false;
 await page.evaluate(()=>refresh());
 assert.equal(await row.getByRole("switch").isChecked(),false);
 assert.equal(await row.getByRole("switch").isDisabled(),true);
 assert.ok((await row.locator(".badge").textContent()).includes("Setup required"));
 await row.locator("input[type=password]").fill("fixture-key");
 assert.equal(await row.getByRole("switch").isDisabled(),false);
 await row.getByRole("switch").check();
 await row.locator("input[type=password]").fill("");
 assert.equal(await row.getByRole("switch").isChecked(),false);
 assert.equal(await row.getByRole("switch").isDisabled(),true);
 await row.locator("input[type=password]").fill("fixture-key");
 await row.getByRole("switch").check();
 await row.getByRole("button",{name:"Save",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[data-provider="tinyfish"] .badge').textContent==="Available");
 await row.getByRole("button",{name:"Remove key"}).click();
 await page.getByRole("dialog").getByRole("button",{name:"Remove key",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[data-provider="tinyfish"] .badge').textContent==="Setup required");
 assert.equal(await row.getByRole("switch").isChecked(),false);
 assert.equal(await row.getByRole("switch").isDisabled(),true);
 await nav("Settings");await page.locator("#timeout").fill("23");
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 assert.equal(await page.locator("#timeout").inputValue(),"23");
 await page.locator("#preferred-label").click();
 await page.locator('input[name=preferred-engine][value=freeserp]').check();
 let releaseSettings;
 holdSettings=new Promise(resolve=>{releaseSettings=resolve;});
 await page.getByRole("button",{name:"Save settings"}).click();
 try{
  await page.waitForFunction(()=>document.querySelector("#timeout").disabled);
  await page.evaluate(()=>refresh());
  assert.equal(await page.locator("#timeout").isDisabled(),true);
  assert.equal(await page.locator("#reset-settings").isDisabled(),true);
 }finally{releaseSettings();holdSettings=null;}
 await page.waitForFunction(()=>document.querySelector("#settings-feedback").textContent.includes("Settings saved"));
 const savedTimeout=data.settings.timeout;
 const updatedTimeout=savedTimeout===16?17:16;
 failOverview=true;
 await page.locator("#timeout").fill(String(updatedTimeout));
 await page.locator('#settings-form button[type="submit"]').click();
 await page.waitForFunction(()=>document.querySelector("#settings-feedback").textContent.includes("Dashboard refresh failed"));
 assert.equal(await page.locator("#timeout").inputValue(),String(updatedTimeout));
 assert.equal(data.settings.timeout,updatedTimeout);
 await page.evaluate(()=>refresh());
 assert.equal(data.settings.preferred,"freeserp");assert.equal(data.settings.timeout,updatedTimeout);
 // Server-driven defaults are not duplicated by the browser.
 assert.equal(await page.locator("#default-search_max_results").inputValue(),"10");
 await page.locator("#default-search_max_results").fill("7");
 await page.locator("#default-search_mode").selectOption("keyless");
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
  assert.equal(await page.locator("#default-search_max_results").inputValue(),"7");
  const maxField=data.defaults.fields.find(f=>f.id==="search_max_results");
  maxField.locked=true;maxField.value=12;
  await page.evaluate(()=>refresh());
  assert.equal(await page.locator("#default-search_max_results").inputValue(),"12");
  assert.equal(await page.locator("#default-search_max_results").isDisabled(),true);
  assert.equal(await page.locator("#default-search_mode").inputValue(),"keyless");
  maxField.locked=false;maxField.value=10;
  await page.evaluate(()=>refresh());
  assert.equal(await page.locator("#default-search_max_results").isDisabled(),false);
  await page.locator("#default-search_max_results").fill("7");
 await page.locator("#save-defaults").click();
 await page.waitForFunction(()=>document.querySelector("#defaults-feedback").textContent.includes("Defaults saved"));
 assert.equal(data.defaults.fields.find(f=>f.id==="search_max_results").value,7);
 assert.equal(data.defaults.fields.find(f=>f.id==="search_mode").value,"keyless");
 await page.locator('#default-fields details').filter({has:page.locator("summary",{hasText:"Fetching"})}).locator("summary").click();
 assert.equal(await page.locator("#default-fetch_timeout").isDisabled(),true);
 await page.locator("#default-fetch_max_chars").fill("999");
 await page.locator('#default-fields details').filter({has:page.locator("summary",{hasText:"Fetching"})}).locator("summary").click();
 await page.locator("#save-defaults").click();
 assert.equal(await page.locator("#default-fetch_max_chars").isVisible(),true);
 assert.ok((await page.locator("#defaults-feedback").textContent()).includes("Check"));
 await page.locator("#default-fetch_max_chars").fill("12000");
 await page.locator("#reset-defaults").click();
 await audit("defaults-advanced-dark");
 await page.locator("#theme").click();await audit("defaults-advanced-white");await page.locator("#theme").click();

 await nav("Cache");
 await page.getByRole("button",{name:"Clear all cache",exact:true}).click();
 await page.getByRole("dialog").getByRole("button",{name:"Clear cache",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector("#cache-feedback").textContent.includes("Removed"));
 assert.equal(cleared,1);
 failOverview=true;
 await page.getByRole("button",{name:"Clear all cache",exact:true}).click();
 await page.getByRole("dialog").getByRole("button",{name:"Clear cache",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector("#cache-feedback").textContent.includes("Refresh failed"));
 assert.ok((await page.locator("#cache-feedback").textContent()).includes("Removed 2 entries"));
 assert.equal(cleared,2);
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 await nav("Playground");
 assert.equal(await page.locator("#query").getAttribute("maxlength"),"8000");
 await page.locator("#query").fill("😀".repeat(4000));
 assert.equal(await page.locator("#query-count").textContent(),"4000 / 4000");
 assert.equal(await page.locator("#query").evaluate(n=>n.validity.valid),true);
 await page.locator("#query").fill("x".repeat(4001));
 assert.equal(await page.locator("#run-search").isDisabled(),true);
 await page.locator("#query").fill("   ");
 assert.equal(await page.locator("#run-search").isDisabled(),true);
 await page.locator("#query").fill("test query");
 assert.equal(await page.locator("#query-count").textContent(),"10 / 4000");
 const firstSearch=responseFor("/api/search",d=>d.mode===undefined);
 await page.getByRole("button",{name:"Run search"}).click();
 await searchComplete(firstSearch);
 await page.getByRole("link",{name:"Example ↗"}).waitFor();
 assert.equal(await page.locator("#results a").count(),1);
 assert.ok(!(await page.locator("#results").textContent()).includes("javascript:alert(1)"));
 assert.ok((await page.locator("#results").textContent()).includes("URL unavailable"));
 assert.equal(lastSearch.mode,undefined);assert.equal(lastSearch.providers,undefined);assert.equal(lastSearch.max_results,undefined);
 assert.ok((await page.locator("#search-default-summary").textContent()).includes("Keyless fallback"));
 assert.ok((await page.locator("#search-default-summary").textContent()).includes("Up to 7"));
 assert.equal(await page.locator("#search-overrides").evaluate(e=>e.open),false);
 await page.locator("#search-overrides > summary").click();
 await page.locator('input[name=search-mode][value=auto]').check();
 await page.locator("#max-results").fill("31");
 assert.equal(await page.locator("#run-search").isDisabled(),true);
 assert.ok((await page.locator("#max-results-hint").textContent()).includes("whole number"));
 await page.locator("#max-results").fill("3");
 assert.equal(await page.locator("#run-search").isDisabled(),false);
 await page.locator("#search-recency").selectOption("any");
 const autoSearch=responseFor("/api/search",d=>d.mode==="auto");
 await page.getByRole("button",{name:"Run search"}).click();
 await searchComplete(autoSearch);
 assert.equal(lastSearch.mode,"auto");assert.equal(lastSearch.providers,undefined);assert.equal(lastSearch.max_results,3);assert.equal(lastSearch.time_filter,"any");
 // Authorize once, then search without another cost dialog for the authorized engine.
 assert.equal(await page.locator("#search-answers").isDisabled(),true);
 data.providers.push(provider("exa",{editable:true,credential:true,env:"EXA_API_KEY"}));
 await nav("Settings");
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 await page.locator("#ai-search-enabled").check();
 await page.locator('#ai-provider-list input[value=exa]').check();
 await page.locator("#save-ai-settings").click();
 await page.getByRole("dialog").getByRole("button",{name:"Cancel",exact:true}).click();
 assert.equal(data.settings.ai_search_enabled,false);
 await page.locator("#save-ai-settings").click();
 await page.getByRole("dialog").getByRole("button",{name:"Authorize and save",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector("#ai-settings-feedback").textContent.includes("settings saved"));
 assert.equal(data.settings.ai_search_enabled,true);
 assert.deepEqual(data.settings.ai_search_providers,["exa"]);
 await nav("Playground");
 await page.locator('input[name=search-mode][value=selected]').check();
 await page.locator("#clear-selection").click();
 await page.locator('#choices input[value=exa]').check();
 assert.equal(await page.locator("#search-answers").isChecked(),true);
 const enhancedSearch=responseFor("/api/search",d=>d.include_answers===true);
 await page.locator("#run-search").click();
 await searchComplete(enhancedSearch);
 await page.locator("#search-ai-results").waitFor({state:"visible"});
 assert.equal(lastSearch.include_answers,true);
 assert.deepEqual(lastSearch.providers,["exa"]);
 assert.equal(await page.locator("#search-ai-cards script").count(),0);
 assert.equal(await page.locator("#search-ai-cards a").count(),1);
 await audit("enhanced-search-dark");
 await page.locator("#theme").click();
 await audit("enhanced-search-white");
 await page.locator("#theme").click();
 await page.locator("#search-answers").uncheck();
 await page.locator("#run-search").click();
 const optOutResponse=responseFor("/api/search",d=>d.include_answers===false);
 await page.getByRole("dialog").getByRole("button",{name:"Run search",exact:true}).click();
 await searchComplete(optOutResponse);
 await page.waitForFunction(()=>!document.querySelector("#run-search").disabled && document.querySelector("#search-ai-results").hidden);
 assert.equal(lastSearch.include_answers,false);
 assert.equal(await page.locator("#search-ai-results").isVisible(),false);
 const beforeChangedSearch=lastSearch;
 await page.locator("#run-search").click();
 await page.getByRole("dialog").waitFor({state:"visible"});
 data.providers.find(p=>p.id==="exa").available=false;
 await page.evaluate(()=>refresh());
 await page.getByRole("dialog").getByRole("button",{name:"Run search",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector("#search-error").textContent.includes("confirm again"));
 assert.equal(lastSearch,beforeChangedSearch);
 data.providers.find(p=>p.id==="exa").available=true;
 await page.evaluate(()=>refresh());

 await page.locator("#native-panel > summary").click();
 await page.locator("#native-query").fill("test answer");
 assert.equal(await page.locator("#native-provider").inputValue(),"");
 assert.equal(await page.locator("#native-run").isDisabled(),true);
 await page.locator("#native-provider").selectOption("tinyfish");
 assert.equal(await page.locator("#native-query").getAttribute("maxlength"),"4000");
 await page.locator("#native-query").fill("   ");
 assert.equal(await page.locator("#native-run").isDisabled(),true);
 await page.locator("#native-query").fill("x".repeat(2001));
 assert.equal(await page.locator("#native-run").isDisabled(),true);
 await page.locator("#native-query").fill("😀".repeat(2000));
 assert.equal(await page.locator("#native-run").isDisabled(),false);
 assert.equal(await page.locator("#native-count").textContent(),"2000 / 2000");
 assert.equal(await page.locator("#native-query").evaluate(n=>n.validity.valid),true);
 await page.locator("#native-query").fill("test answer");
 assert.equal(await page.locator("#native-provider option:checked").textContent(),"TinyFish · TinyAnswer");
 assert.ok((await page.locator("#native-pricing").textContent()).includes("Account-dependent"));
 assert.equal(await page.locator('#ai-provider-list input[value="tinyfish"]').count(),0);
 await page.locator("#native-provider").selectOption("exa");
 assert.equal(await page.locator("#native-query").getAttribute("maxlength"),"8000");
 await page.locator("#native-run").click();
 await page.getByRole("dialog").getByRole("button",{name:"Cancel",exact:true}).click();
 assert.equal(lastNative,null);
 await page.locator("#native-run").click();
 const exa=data.native.find(p=>p.id==="exa"),originalPricing=exa.pricing;
 exa.pricing="Updated pricing";
 await page.evaluate(()=>refresh());
 await page.getByRole("dialog").getByRole("button",{name:"Continue",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector("#native-feedback").textContent.includes("confirm again"));
 assert.equal(lastNative,null);
 exa.pricing=originalPricing;
 await page.evaluate(()=>refresh());
 await page.locator("#native-run").click();
 let releaseNative;
 holdNative=new Promise(resolve=>{releaseNative=resolve;});
 const answerResponse=responseFor("/api/native",d=>d.mode==="answer");
 await page.getByRole("dialog").getByRole("button",{name:"Continue",exact:true}).click();
 try{
  await page.waitForFunction(()=>document.querySelector("#native-run").hasAttribute("aria-busy"));
  data.native.find(p=>p.id==="exa").available=false;
  await page.evaluate(()=>refresh());
  assert.equal(await page.locator("#native-provider").inputValue(),"exa");
  assert.equal(await page.locator("#native-provider").isDisabled(),true);
  assert.equal(await page.locator("#native-query").isDisabled(),true);
  data.native.find(p=>p.id==="exa").available=true;
  await page.evaluate(()=>refresh());
 }finally{releaseNative();holdNative=null;}
 await nativeComplete(answerResponse);
 assert.equal(lastNative.allow_cost,true);
 assert.equal(lastNative.provider,"exa");
 assert.equal(await page.locator("#native-answer").textContent(),"Generated answer <script>bad</script>");
 assert.equal(await page.locator("#native-answer script").count(),0);
 assert.equal(await page.locator("#native-sources a").count(),1);
 await audit("native-answer-dark");
 await page.locator("#theme").click();
 await audit("native-answer-white");
 await page.locator("#theme").click();
 await page.locator('input[name=native-mode][value=research]').check();
 assert.equal(await page.locator("#native-run").isDisabled(),true);
 await page.locator("#native-provider").selectOption("tinyfish");
 assert.equal(await page.locator("#native-effort").inputValue(),"");
 assert.equal(await page.locator("#native-run").isDisabled(),true);
 await page.locator("#native-effort").selectOption("max");
 assert.ok((await page.locator("#native-pricing").textContent()).includes("45 minutes"));
 await page.locator("#native-run").click();
 assert.ok((await page.getByRole("dialog").textContent()).includes("$3/run"));
 await page.keyboard.press("Escape");
 await page.getByRole("dialog").waitFor({state:"hidden"});
 data.native.find(p=>p.id==="exa").efforts={deep:"Deep"};
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 await page.locator("#native-provider").selectOption("exa");
 assert.equal(await page.locator("#native-effort").inputValue(),"");
 assert.equal(await page.locator("#native-run").isDisabled(),true);
 await page.locator("#native-effort").selectOption("deep");
 assert.equal(await page.locator("#native-run").isDisabled(),false);
 await page.locator("#native-run").click();
 const researchResponse=responseFor("/api/native",d=>d.mode==="research");
 await page.getByRole("dialog").getByRole("button",{name:"Continue",exact:true}).click();
 await nativeComplete(researchResponse);
 assert.equal(lastNative.mode,"research");
 assert.equal(lastNative.effort,"deep");
 assert.ok((await page.locator("#native-heading").textContent()).endsWith(" · Research"));
 // Session expiry must abort the browser wait for an old synthesis.
 holdNative=new Promise(resolve=>{releaseNative=resolve;});
 await page.locator("#native-run").click();
 await page.getByRole("dialog").getByRole("button",{name:"Continue",exact:true}).click();
 try{
  await page.waitForFunction(()=>document.querySelector("#native-run").hasAttribute("aria-busy"));
  logged=false;
  await page.evaluate(()=>refresh().catch(()=>{}));
  await page.locator("#login").waitFor({state:"visible"});
  await page.waitForFunction(()=>!document.querySelector("#native-run").hasAttribute("aria-busy"));
  assert.equal(await page.locator("#native-answer").textContent(),"");
  assert.equal(await page.locator("#native-feedback").textContent(),"");
 }finally{releaseNative();holdNative=null;}
 await page.locator("#password").fill("test-only-password");
 await page.getByRole("button",{name:"Sign in",exact:true}).click();
 await page.locator("#dashboard").waitFor({state:"visible"});
 assert.equal(await page.locator("#native-query").isDisabled(),false);
 await page.locator("#native-panel > summary").click();
 // The fetch controller must stay registered while response JSON is pending.
 const bodyAbort=await page.evaluate(async()=>{
  const original=window.fetch;
  let complete;
  const ready=new Promise(resolve=>{complete=resolve;});
  window.fetch=async(url,options)=>({ok:true,status:200,json:()=>new Promise((resolve,reject)=>{
   options.signal.addEventListener("abort",()=>reject(new DOMException("Aborted","AbortError")),{once:true});
   complete();
  })});
  try{
   const pending=api("overview").catch(e=>e.message);
   await ready;
   const count=sessionRequests.size;
   for(const controller of sessionRequests)controller.abort();
   return {count,error:await pending,remaining:sessionRequests.size};
  }finally{window.fetch=original;}
 });
 assert.equal(bodyAbort.count,1);assert.equal(bodyAbort.remaining,0);
 assert.ok(bodyAbort.error.includes("Cannot reach"));
 await nav("Connection");
 for(const invalid of ["https://example.com:0/mcp","https://example.com/"+String.fromCharCode(92)+"mcp","https://example.com/mcp a","https:example.com/mcp","https:///example.com/mcp","https://example.com/"+"a".repeat(8192)]){
  await page.locator("#endpoint").fill(invalid);
  assert.equal(await page.locator("#copy-config").isDisabled(),true);
 }
  for(const invalid of ["https://@example.com/mcp","https://%65xample.com/mcp","https://example.com%2fother/mcp"]){
   await page.locator("#endpoint").fill(invalid);
   assert.equal(await page.locator("#copy-config").isDisabled(),true);
  }
  await page.locator("#endpoint").fill("https://example.com/mcp");
 assert.equal(JSON.parse(await page.locator("#connection-json").textContent()).mcp["prism-search"].url,"https://example.com/mcp");
 await page.locator("#endpoint").fill("https://user:secret@example.com/mcp");
 assert.equal(await page.locator("#copy-config").isDisabled(),true);
 await page.goBack();await page.waitForFunction(()=>document.querySelector("#page-title").textContent==="Playground");
 await page.goForward();await page.waitForFunction(()=>document.querySelector("#page-title").textContent==="Connection");
 await nav("Overview");if(process.env.PRISM_TEST_OUTPUT) await page.screenshot({path:path.join(process.env.PRISM_TEST_OUTPUT,"overview.png"),fullPage:true});
 await page.setViewportSize({width:375,height:812});await nav("Providers");if(process.env.PRISM_TEST_OUTPUT) await page.screenshot({path:path.join(process.env.PRISM_TEST_OUTPUT,"providers.png"),fullPage:true});
 await nav("Cache");
 await page.getByRole("button",{name:"Clear all cache",exact:true}).click();
 await page.getByRole("dialog").waitFor({state:"visible"});
 logged=false;
 await page.evaluate(()=>refresh().catch(()=>{}));
 await page.locator("#login").waitFor({state:"visible"});
 assert.equal(await page.getByRole("dialog").isVisible(),false);
 assert.equal(cleared,2);
 await page.locator("#password").fill("test-only-password");
 await page.getByRole("button",{name:"Sign in",exact:true}).click();
 await page.locator("#dashboard").waitFor({state:"visible"});
 assert.equal(await page.locator("#cache-feedback").textContent(),"");
 await page.getByRole("button",{name:"Clear all cache",exact:true}).click();
 await page.getByRole("dialog").waitFor({state:"visible"});
 logged=false;
 await page.getByRole("dialog").getByRole("button",{name:"Clear cache",exact:true}).click();
 await page.locator("#login").waitFor({state:"visible"});
 await page.locator("#password").fill("test-only-password");
 await page.getByRole("button",{name:"Sign in",exact:true}).click();
 await page.locator("#dashboard").waitFor({state:"visible"});
 assert.equal(await page.locator("#cache-feedback").textContent(),"");
 assert.equal(cleared,2);
  await nav("Providers");
  await page.locator('.provider-row[data-provider="keenable"]').getByRole("switch").uncheck();
  logged=false;
  await page.locator('.provider-row[data-provider="keenable"]').getByRole("button",{name:"Save",exact:true}).click();
  await page.locator("#login").waitFor({state:"visible"});
  await page.locator("#password").fill("test-only-password");
  await page.getByRole("button",{name:"Sign in",exact:true}).click();
  await page.locator("#dashboard").waitFor({state:"visible"});
  assert.equal(await page.locator("#provider-feedback-keenable").textContent(),"");
  await nav("Settings");
 await page.locator("#timeout").fill("24");
 logged=false;
 await page.getByRole("button",{name:"Save settings"}).click();
 await page.locator("#login").waitFor({state:"visible"});
 await page.locator("#password").fill("test-only-password");
 await page.getByRole("button",{name:"Sign in",exact:true}).click();
 await page.locator("#dashboard").waitFor({state:"visible"});
 assert.equal(await page.locator("#settings-feedback").textContent(),"");
 await page.locator("#logout").click();await page.locator("#login").waitFor({state:"visible"});
 await page.locator("#login-theme").click();await audit("login-white");
 assert.deepEqual(errors,[]);
 console.log("Interaction tests PASS");
 assert.equal(results.length,0,JSON.stringify(results));
}finally{
 if(process.env.PRISM_TEST_OUTPUT) await fs.writeFile(path.join(process.env.PRISM_TEST_OUTPUT,"axe-dashboard.json"),JSON.stringify(results,null,2));
 console.log("Accessibility findings",JSON.stringify(results.map(x=>({page:x.page,id:x.id,impact:x.impact,nodes:x.nodes.map(n=>n.target)}))));
 await context.close();await browser.close();
}
