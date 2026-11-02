// Pages shown inside data.html / evidence.html. Opened on their own, they move to their combined page;
// opened with ?embed=1, they hide the site header and footer and report their height to the parent.
(function () {
  const HUB = {
    "explore.html": "data.html#series",
    "trends.html": "data.html#trends",
    "landscape.html": "data.html#landscape",
    "hypotheses.html": "evidence.html#register",
    "breaks.html": "evidence.html#breaks"
  };
  const page = location.pathname.split("/").pop() || "index.html";
  const embedded = new URLSearchParams(location.search).get("embed") === "1" && window.parent !== window;

  if (!embedded) {
    if (HUB[page]) location.replace(HUB[page] + (location.hash ? `/${location.hash.slice(1)}` : ""));
    return;
  }

  document.documentElement.classList.add("embedded");
  const style = document.createElement("style");
  style.textContent = "html.embedded .topbar, html.embedded body > .wrap > footer, html.embedded footer { display: none !important; } html.embedded body { background: transparent; }";
  document.head.appendChild(style);
  const base = document.createElement("base");
  base.target = "_top";
  document.head.appendChild(base);

  const post = msg => window.parent.postMessage(msg, location.origin === "null" ? "*" : location.origin);
  const report = () => post({ type: "um-height", height: document.documentElement.scrollHeight });
  const scrollTo = id => {
    const el = id && document.getElementById(id);
    if (el) post({ type: "um-scroll", top: el.getBoundingClientRect().top + window.scrollY });
  };

  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    e.preventDefault();
    scrollTo(decodeURIComponent(a.getAttribute("href").slice(1)));
  });

  window.addEventListener("DOMContentLoaded", () => {
    report();
    new ResizeObserver(report).observe(document.body);
  });
  window.addEventListener("load", () => {
    report();
    scrollTo(decodeURIComponent(location.hash.slice(1)));
  });
})();
