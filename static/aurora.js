/* Karva Bhaiya — Aurora effects: starfield, scroll reveal, card spotlight */
(function () {
  if (window.__aurora) return; window.__aurora = true;
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var scrollResetTimer;

  window.addEventListener("scroll", function () {
    document.body.classList.add("is-scrolling");
    clearTimeout(scrollResetTimer);
    scrollResetTimer = window.setTimeout(function () {
      document.body.classList.remove("is-scrolling");
    }, 180);
  }, { passive: true });

  // Starfield
  if (!reduce && !document.querySelector(".player-card, .player-shell")) {
    var c = document.createElement("canvas"); c.id = "au-stars"; document.body.prepend(c);
    var x = c.getContext("2d"), stars = [], w, h, dpr = Math.min(window.devicePixelRatio || 1, 2);
    function size() {
      w = c.width = innerWidth * dpr; h = c.height = innerHeight * dpr;
      c.style.width = innerWidth + "px"; c.style.height = innerHeight + "px";
      var n = Math.min(140, Math.floor(innerWidth * innerHeight / 9000)); stars = [];
      for (var i = 0; i < n; i++) stars.push({ x: Math.random() * w, y: Math.random() * h, r: (Math.random() * 1.2 + .3) * dpr, s: Math.random() * .25 + .05, t: Math.random() * 6.28 });
    }
    size(); addEventListener("resize", size);
    (function loop() {
      if (!document.hidden) {
        x.clearRect(0, 0, w, h);
        for (var i = 0; i < stars.length; i++) {
          var s = stars[i]; s.t += .02; s.y -= s.s * dpr; if (s.y < 0) { s.y = h; s.x = Math.random() * w; }
          x.globalAlpha = .35 + Math.sin(s.t) * .3; x.fillStyle = i % 3 ? "#ffe3a0" : "#f2b632";
          x.beginPath(); x.arc(s.x, s.y, s.r, 0, 6.28); x.fill();
        }
      }
      requestAnimationFrame(loop);
    })();
  }

  // Scroll reveal (also for cards added later by JS)
  var SEL = ".video-card, .study-result, .info-card, .section-heading, .empty-state, .pomodoro-panel";
  var io = "IntersectionObserver" in window && !reduce ? new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("au-in"); io.unobserve(e.target); } });
  }, { threshold: .08 }) : null;
  var idx = 0;
  function scan(root) {
    if (!io) return;
    (root.querySelectorAll ? root : document).querySelectorAll(SEL).forEach(function (el) {
      if (el.__au) return; el.__au = 1; el.classList.add("au-reveal");
      el.style.transitionDelay = ((idx++ % 8) * 45) + "ms"; io.observe(el);
    });
  }
  scan(document);
  new MutationObserver(function (ms) { ms.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) scan(n.parentNode || n); }); }); })
    .observe(document.body, { childList: true, subtree: true });

  // Cursor spotlight
  document.addEventListener("pointermove", function (e) {
    var el = e.target.closest && e.target.closest(".video-card, .study-result, .info-card");
    if (!el) return; var r = el.getBoundingClientRect();
    el.style.setProperty("--mx", (e.clientX - r.left) + "px"); el.style.setProperty("--my", (e.clientY - r.top) + "px");
  }, { passive: true });
})();
