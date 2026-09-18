/* Atlas HQ tiny UI glue (ticket-037). Display only; zero business logic. */
(function () {
  "use strict";

  // theme: system default, cookie override
  function applyTheme() {
    var m = document.cookie.match(/(?:^|; )atlas_theme=([^;]*)/);
    var pref = m ? decodeURIComponent(m[1]) : "system";
    var dark = pref === "dark" ||
      (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  }
  applyTheme();

  // toast from ?toast= (server redirects with user-visible confirmations)
  function toast(msg) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.style.display = "block";
    setTimeout(function () { el.style.display = "none"; }, 3500);
  }
  var q = new URLSearchParams(window.location.search).get("toast");
  if (q) toast(q);

  // confirm dialogs: <form data-confirm="..."> or <a data-confirm="...">
  document.addEventListener("submit", function (ev) {
    var f = ev.target;
    if (f.dataset && f.dataset.confirm && !window.confirm(f.dataset.confirm)) {
      ev.preventDefault();
    }
  });
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest("[data-confirm]");
    if (a && !window.confirm(a.getAttribute("data-confirm"))) ev.preventDefault();
  });

  // modal helper
  window.atlasModal = function (id, show) {
    var el = document.getElementById(id);
    if (el) el.style.display = show ? "flex" : "none";
  };

  // mobile sidebar
  var toggle = document.getElementById("menu-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      document.getElementById("sidebar").classList.toggle("hidden");
    });
  }
  // setup banner: dismiss per session (guidance, never lock-in)
  try {
    var banner = document.getElementById("setup-banner");
    if (banner && sessionStorage.getItem("setup-banner-off") === "1") {
      banner.style.display = "none";
    }
    var bx = document.getElementById("setup-banner-x");
    if (bx) {
      bx.addEventListener("click", function () {
        banner.style.display = "none";
        try { sessionStorage.setItem("setup-banner-off", "1"); } catch (e) {}
      });
    }
  } catch (e) {}

  // sidebar sections: persist open state, auto-open the active section
  try {
    var open = JSON.parse(localStorage.getItem("atlas-nav-open") || "{}");
    document.querySelectorAll(".nav-sec").forEach(function (sec) {
      var key = sec.getAttribute("data-sec");
      var hasActive = !!sec.querySelector("a.active");
      var isOpen = hasActive || open[key] !== false;
      sec.classList.toggle("closed", !isOpen);
      sec.querySelector(".nav-sec-h").setAttribute("aria-expanded", isOpen);
      sec.querySelector(".nav-sec-h").addEventListener("click", function () {
        var now = sec.classList.toggle("closed");
        open[key] = !now;
        try { localStorage.setItem("atlas-nav-open", JSON.stringify(open)); } catch (e2) {}
      });
    });
  } catch (e3) {}
})();
