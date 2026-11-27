import { mountStarfieldDepth } from "./starfield-depth";

const layer = document.getElementById("background-layer")!;
const toggle = document.getElementById("starfield-toggle") as HTMLInputElement;
const hint = document.getElementById("starfield-hint")!;
let dispose: (() => void) | undefined;
let enabled = false;
try { enabled = localStorage.getItem("prism-starfield") === "true"; } catch {}

function sync() {
  const dark = document.documentElement.dataset.theme !== "yu-daylight";
  toggle.checked = enabled;
  toggle.disabled = !dark;
  hint.textContent = dark
    ? "Optional animated background. Uses your browser, not the server."
    : "Available in Dark mode. Your preference is preserved.";
  const active = enabled && dark && !document.hidden;
  if (active && !dispose) dispose = mountStarfieldDepth(layer);
  else if (!active && dispose) {
    dispose();
    dispose = undefined;
  }
}
toggle.addEventListener("change", () => {
  enabled = toggle.checked;
  try { localStorage.setItem("prism-starfield", String(enabled)); } catch {}
  sync();
});
const observer = new MutationObserver(sync);
observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
document.addEventListener("visibilitychange", sync);
window.addEventListener("pagehide", () => {
  dispose?.();
  dispose = undefined;
});
window.addEventListener("pageshow", sync);
sync();
