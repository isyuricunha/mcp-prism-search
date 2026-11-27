
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
const provider=(id,extra={})=>({id,kind:"general",description:"A search engine for web research.",enabled:true,available:true,credential:false,editable:false,managed:false,env:null,...extra});
let data={defaults:JSON.parse(await fs.readFile(new URL("./defaults-fixture.json",import.meta.url),"utf8")),native:[{id:"exa",answer:true,research:true,efforts:{lite:"Deep Lite",deep:"Deep"},available:true,pricing:"$5/1,000 calls",docs:"https://exa.ai/docs/reference/answer",pricing_url:"https://exa.ai/pricing"},{id:"youcom",answer:true,research:true,available:false,pricing:"API credits",docs:"https://you.com/docs/guides/research",pricing_url:"https://you.com/pricing"}],server:{version:"9.8.7-test",sdk_version:"2.3.0",protocol_version:"2026-07-28",transport:"streamable-http"},providers:[provider("keenable"),provider("freeserp"),provider("tinyfish",{editable:true,credential:true,env:"TINYFISH_API_KEY"})],metrics:[],counters:{},cache:{total_files:12,total_bytes:10240,cache_dir:"/data/cache",namespaces:{search:{files:10,bytes:8192},fetch:{files:2,bytes:2048}}},settings:{timeout:15,search_ttl:21600,fetch_ttl:7200,preferred:null,ai_search_enabled:false,ai_search_providers:[],locked:[]}};
await page.route("http://prism-test.invalid/**",async route=>{
 const url=new URL(route.request().url());
 const pathname=url.pathname;
 if(pathname.startsWith("/api/")){
   let status=200, body={ok:true};
   if(pathname==="/api/login") logged=true;
   else if(!logged){status=401;body={error:"Authentication required"};}
   else if(pathname==="/api/overview")body=data;
   else if(pathname.startsWith("/api/providers/")){updates++;const id=decodeURIComponent(pathname.split("/").pop());const d=route.request().postDataJSON();const p=data.providers.find(x=>x.id===id);p.enabled=d.enabled;p.available=d.enabled;if(d.remove)p.credential=false;}
   else if(pathname==="/api/settings"){Object.assign(data.settings,route.request().postDataJSON());body=data.settings;}
   else if(pathname==="/api/defaults"){
     if(route.request().method()==="PUT"){for(const [id,value] of Object.entries(route.request().postDataJSON().values)){data.defaults.fields.find(f=>f.id===id).value=value;}}
     body=data.defaults;
   }
   else if(pathname==="/api/cache/clear"){cleared++;body={deleted_files:2,deleted_bytes:2048};}
   else if(pathname==="/api/logout")logged=false;
   else if(pathname==="/api/native"){lastNative=route.request().postDataJSON();body={provider:"exa",mode:"answer",answer:"Generated answer <script>bad</script>",sources:[{index:1,url:"https://example.org",title:"Native source",excerpt:"Evidence"},{index:2,url:null,title:"Invalid URL",excerpt:""}],truncated:false};}
   else if(pathname==="/api/search"){lastSearch=route.request().postDataJSON();body={cached:false,provider_attempts:["keenable: ok"],results:[{title:"Example",url:"https://example.com",snippet:"Test result"}],answers:lastSearch.include_answers?[{provider:"exa",mode:"answer",answer:"Enhanced answer <script>bad</script>",source_kind:"grounding",sources:[{index:2,url:"https://example.org",title:"Grounded source",excerpt:"Evidence"}]}]:[],answer_attempts:lastSearch.include_answers?["exa: ok"]:[]};}
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
const sections=["Overview","Providers","Playground","Cache","Settings","Connection"];
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
 assert.equal(await page.locator("#sidebar-version").textContent(),"v9.8.7-test");
 assert.ok((await page.locator("#server-facts").textContent()).includes("2.3.0"));
 const viewports=[1440,900,768,720,450,375];
 for(const width of viewports){
  await page.setViewportSize({width,height:900});
  for(const name of sections){
   await nav(name);
   assert.equal(await page.locator("#page-title").textContent(),name);
   const dims=await page.evaluate(()=>({view:document.documentElement.clientWidth,body:document.documentElement.scrollWidth}));
   assert.ok(dims.body<=dims.view+1,JSON.stringify({width,name,dims}));
  }
  console.log("Viewport",width,"all six sections fit");
 }
 await page.setViewportSize({width:1440,height:900});
 for(const name of sections){await nav(name);await audit(name+"-dark");}
 await page.locator("#theme").click();
 for(const name of sections){await nav(name);await audit(name+"-white");}
 await page.locator("#theme").click();
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
 await nav("Settings");await page.locator("#timeout").fill("23");
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 assert.equal(await page.locator("#timeout").inputValue(),"23");
 await page.locator("#preferred-label").click();
 await page.locator('input[name=preferred-engine][value=freeserp]').check();
 await page.getByRole("button",{name:"Save settings"}).click();
 await page.waitForFunction(()=>document.querySelector("#settings-feedback").textContent.includes("Settings saved"));
 assert.equal(data.settings.preferred,"freeserp");assert.equal(data.settings.timeout,23);
 // Server-driven defaults are not duplicated by the browser.
 assert.equal(await page.locator("#default-search_max_results").inputValue(),"10");
 await page.locator("#default-search_max_results").fill("7");
 await page.locator("#default-search_mode").selectOption("keyless");
 await page.locator("#refresh").click();await page.waitForFunction(()=>!document.querySelector("#refresh").disabled);
 assert.equal(await page.locator("#default-search_max_results").inputValue(),"7");
 await page.locator("#save-defaults").click();
 await page.waitForFunction(()=>document.querySelector("#defaults-feedback").textContent.includes("Defaults saved"));
 assert.equal(data.defaults.fields.find(f=>f.id==="search_max_results").value,7);
 assert.equal(data.defaults.fields.find(f=>f.id==="search_mode").value,"keyless");
 await page.locator('#default-fields details').filter({has:page.locator("summary",{hasText:"Fetching"})}).locator("summary").click();
 assert.equal(await page.locator("#default-fetch_timeout").isDisabled(),true);
 await audit("defaults-advanced-dark");
 await page.locator("#theme").click();await audit("defaults-advanced-white");await page.locator("#theme").click();

 await nav("Cache");
 await page.getByRole("button",{name:"Clear all cache",exact:true}).click();
 await page.getByRole("dialog").getByRole("button",{name:"Clear cache",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector("#cache-feedback").textContent.includes("Removed"));
 assert.equal(cleared,1);
 await nav("Playground");await page.locator("#query").fill("test query");
 await page.getByRole("button",{name:"Run search"}).click();
 await page.getByRole("link",{name:"Example ↗"}).waitFor();
 assert.equal(lastSearch.mode,undefined);assert.equal(lastSearch.providers,undefined);assert.equal(lastSearch.max_results,undefined);
 assert.ok((await page.locator("#search-default-summary").textContent()).includes("Keyless fallback"));
 assert.ok((await page.locator("#search-default-summary").textContent()).includes("Up to 7"));
 assert.equal(await page.locator("#search-overrides").evaluate(e=>e.open),false);
 await page.locator("#search-overrides > summary").click();
 await page.locator('input[name=search-mode][value=auto]').check();
 await page.locator("#max-results").fill("3");
 await page.locator("#search-recency").selectOption("any");
 await page.getByRole("button",{name:"Run search"}).click();
 await page.waitForFunction(()=>!document.querySelector("#run-search").disabled);
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
 await page.locator("#run-search").click();
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
 await page.getByRole("dialog").getByRole("button",{name:"Run search",exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector("#run-search").disabled);
 assert.equal(lastSearch.include_answers,false);
 assert.equal(await page.locator("#search-ai-results").isVisible(),false);

 await page.locator("#native-panel > summary").click();
 await page.locator("#native-query").fill("test answer");
 await page.locator("#native-run").click();
 await page.getByRole("dialog").getByRole("button",{name:"Cancel",exact:true}).click();
 assert.equal(lastNative,null);
 await page.locator("#native-run").click();
 await page.getByRole("dialog").getByRole("button",{name:"Continue",exact:true}).click();
 await page.locator("#native-result").waitFor({state:"visible"});
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
 assert.equal(await page.locator("#native-run").isDisabled(),false);
 await page.locator("#native-effort").selectOption("deep");
 await page.locator("#native-run").click();
 await page.getByRole("dialog").getByRole("button",{name:"Continue",exact:true}).click();
 await page.locator("#native-result").waitFor({state:"visible"});
 assert.equal(lastNative.mode,"research");
 assert.equal(lastNative.effort,"deep");
 await page.locator("#native-panel > summary").click();
 await nav("Connection");await page.locator("#endpoint").fill("https://example.com/mcp");
 assert.equal(JSON.parse(await page.locator("#connection-json").textContent()).mcp["prism-search"].url,"https://example.com/mcp");
 await page.locator("#endpoint").fill("https://user:secret@example.com/mcp");
 assert.equal(await page.locator("#copy-config").isDisabled(),true);
 await page.goBack();await page.waitForFunction(()=>document.querySelector("#page-title").textContent==="Playground");
 await page.goForward();await page.waitForFunction(()=>document.querySelector("#page-title").textContent==="Connection");
 await nav("Overview");if(process.env.PRISM_TEST_OUTPUT) await page.screenshot({path:path.join(process.env.PRISM_TEST_OUTPUT,"overview.png"),fullPage:true});
 await page.setViewportSize({width:375,height:812});await nav("Providers");if(process.env.PRISM_TEST_OUTPUT) await page.screenshot({path:path.join(process.env.PRISM_TEST_OUTPUT,"providers.png"),fullPage:true});
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
