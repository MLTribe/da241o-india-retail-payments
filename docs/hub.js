// Combined pages: each sub-tab shows one existing page in embedded mode (header and footer hidden).
// Hash format: #<view> or #<view>/<anchor inside that page>.
(function () {
  const bar = document.querySelector("[data-hub]");
  const frame = document.getElementById("view");
  const views = JSON.parse(bar.dataset.views);
  const keys = Object.keys(views);
  const siteTitle = document.title;
  const plain = s => s.replace(/&amp;/g, "&");

  keys.forEach(key => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "tab");
    b.dataset.key = key;
    b.innerHTML = views[key][1];
    b.addEventListener("click", () => { location.hash = key; });
    bar.appendChild(b);
  });

  function show() {
    const [wanted, anchor] = decodeURIComponent(location.hash.slice(1)).split("/");
    const key = keys.includes(wanted) ? wanted : keys[0];
    bar.querySelectorAll("button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.key === key)));
    const src = `${views[key][0]}?embed=1${anchor ? `#${anchor}` : ""}`;
    if (frame.getAttribute("src") !== src) {
      frame.style.height = "1400px";
      frame.setAttribute("src", src);
      frame.title = plain(views[key][1]);
    }
    document.title = `${plain(views[key][1])} · ${siteTitle}`;
  }

  window.addEventListener("message", e => {
    if (e.source !== frame.contentWindow || !e.data) return;
    if (e.data.type === "um-height") frame.style.height = `${Math.max(400, Math.ceil(e.data.height) + 8)}px`;
    if (e.data.type === "um-scroll") window.scrollTo({ top: frame.getBoundingClientRect().top + window.scrollY + e.data.top - 70 });
  });
  window.addEventListener("hashchange", show);
  show();
})();
