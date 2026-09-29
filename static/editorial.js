/* ============================================================================
   CreatorHaven — editorial.js
   The tactile layer over the app: a mathematical custom cursor (Phase 6/7),
   text-scramble flares (Phase 19), a mutable subtle click engine (Phase 5,
   OFF by default), and an off-canvas Control Deck (Phase 17/18).
   Self-contained, scoped, and non-destructive to app.js.
   ========================================================================== */
(function(){
  "use strict";
  if (window.matchMedia && window.matchMedia("(pointer:coarse)").matches) {
    // touch device — skip the desktop cursor, but still wire the deck + clicks
  }

  /* -------------------------------------------------- subtle click engine --- */
  var Audio = {
    ctx:null, master:null, clip:null,
    on: (function(){ try { return localStorage.getItem("eh_sound") === "1"; } catch(e){ return false; } })(),
    _curve:function(c){ var n=1024, a=new Float32Array(n); for(var i=0;i<n;i++){ var x=i/(n-1)*2-1; a[i]=Math.max(-c,Math.min(c,x)); } return a; },
    init:function(){
      if (this.ctx) return;
      var AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
      this.ctx = new AC();
      this.clip = this.ctx.createWaveShaper(); this.clip.curve = this._curve(0.8); this.clip.oversample = "2x";
      this.master = this.ctx.createGain(); this.master.gain.value = 0.5;
      this.master.connect(this.clip); this.clip.connect(this.ctx.destination);
    },
    click:function(peak, freq){
      if (!this.on) return;
      this.init(); if (!this.ctx) return;
      if (this.ctx.state !== "running") this.ctx.resume();
      var t = this.ctx.currentTime, o = this.ctx.createOscillator(), g = this.ctx.createGain();
      o.type = "triangle"; o.frequency.setValueAtTime(freq || (860 + Math.random()*300), t);
      g.gain.setValueAtTime(peak || 0.22, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.035);
      o.connect(g); g.connect(this.master); o.start(t); o.stop(t + 0.04);
    }
  };

  /* ------------------------------------------------- mathematical cursor ---- */
  var cursor = document.createElement("div"); cursor.id = "eh-cursor"; document.body.appendChild(cursor);
  var ptr = { x: innerWidth/2, y: innerHeight/2 }, pos = { x: ptr.x, y: ptr.y }, EASE = 0.19, seeded = false, rot = 0;
  // custom cursor is OFF by default (native cursor stays) — opt in via the Control Deck
  var cursorOn = (function(){ try { return localStorage.getItem("eh_cursor") === "1"; } catch(e){ return false; } })();
  function applyCursor(){
    if (cursorOn) { document.body.classList.add("eh-cursor-on"); cursor.style.display = "block"; }
    else { document.body.classList.remove("eh-cursor-on"); cursor.style.display = "none"; }
  }
  applyCursor();
  addEventListener("pointermove", function(e){
    ptr.x = e.clientX; ptr.y = e.clientY;
    if (!seeded) { pos.x = ptr.x; pos.y = ptr.y; seeded = true; }   // no center-start jump
  }, { passive:true });
  (function loop(){
    if (cursorOn) {
      pos.x += (ptr.x - pos.x) * EASE; pos.y += (ptr.y - pos.y) * EASE;
      // The diamond's 45deg turn lives INSIDE the transform, after the centring translate, so it
      // spins the shape about its own centre. As the CSS `rotate` property it was applied after
      // the whole transform and swung the pointer position itself around the page origin.
      rot += ((cursor.classList.contains("eh-active") ? 45 : 0) - rot) * 0.25;
      if (Math.abs(rot) < 0.05) rot = 0;
      cursor.style.transform = "translate(" + pos.x + "px," + pos.y + "px) translate(-50%,-50%) rotate(" + rot.toFixed(2) + "deg)";
    }
    requestAnimationFrame(loop);
  })();
  var INTERACTIVE = "a,button,input,select,textarea,.tile,.card,.seg button,.navstrip button,.qa-tile,.home-chips button,.home-chips a,[data-eh-cursor]";
  var TEXTY = "p,.muted,.hint,.home-tag,pre,td,li";
  document.addEventListener("mouseover", function(e){
    var t = e.target;
    if (t.closest && t.closest(INTERACTIVE)) { cursor.classList.add("eh-active"); cursor.classList.remove("eh-text"); }
    else if (t.closest && t.closest(TEXTY)) { cursor.classList.add("eh-text"); cursor.classList.remove("eh-active"); }
  });
  document.addEventListener("mouseout", function(e){
    var t = e.target;
    if (t.closest && t.closest(INTERACTIVE)) cursor.classList.remove("eh-active");
    if (t.closest && t.closest(TEXTY)) cursor.classList.remove("eh-text");
  });

  /* ------------------------------------------------------------ letter pop -- */
  // Replaces the old text-scramble flare: hovering a tab name or heading lifts the letter under
  // the pointer a little (CSS, see LETTER POP in style.css); on touch a tap runs one wave through
  // the word. Each text node is split into a no-wrap span per word holding an inline-block span
  // per letter. textContent stays identical and the letters are never changed.
  var POP_SEL = ".navstrip button, .home-title, h2, h3.eh-scr, [data-scramble]";
  var KEEP = ".badge,svg,a,button,input,select,textarea,label";   // never split inside these
  function looseText(el){
    var out = [], w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    for (var n = w.nextNode(); n; n = w.nextNode()){
      var p = n.parentNode, keep = p.closest(KEEP);
      if (!n.nodeValue.trim() || p.classList.contains("eh-l") || (keep && keep !== el && el.contains(keep))) continue;
      out.push(n);
    }
    return out;
  }
  // Letter spans are hidden from screen readers (some read inline-block letters one by one),
  // so the host carries the words as its label, plus a visible badge count (the Alerts tab).
  function popLabel(el){
    var words = [];
    el.querySelectorAll(".eh-w").forEach(function(w){ words.push(w.textContent); });
    var label = words.join(" ");
    var badge = el.querySelector(".badge:not(.hidden)");
    if (badge && badge.textContent.trim()) label += " (" + badge.textContent.trim() + ")";
    if (label && el.getAttribute("aria-label") !== label) el.setAttribute("aria-label", label);
  }
  function splitLetters(el, texts){
    var letters = 0;
    texts.forEach(function(t){
      var frag = document.createDocumentFragment();
      t.nodeValue.split(/(\s+)/).forEach(function(part){
        if (!part) return;
        if (!part.trim()) { frag.appendChild(document.createTextNode(part)); return; }
        var word = document.createElement("span"); word.className = "eh-w"; word.setAttribute("aria-hidden", "true");
        Array.from(part).forEach(function(ch){
          var l = document.createElement("span"); l.className = "eh-l"; l.textContent = ch;
          l.style.setProperty("--i", letters++); word.appendChild(l);
        });
        frag.appendChild(word);
      });
      t.parentNode.replaceChild(frag, t);
    });
    // a long heading's wave still finishes in about half a second
    el.style.setProperty("--step", Math.min(24, 440 / Math.max(1, letters)).toFixed(1) + "ms");
  }
  function bindPop(){
    document.querySelectorAll(POP_SEL).forEach(function(el){
      var texts = looseText(el);
      if (texts.length) splitLetters(el, texts);
      if (el.querySelector(".eh-w")) popLabel(el);
    });
  }
  bindPop();
  // the Alerts count shows/hides by class and changes its text: keep the tab's label in step
  var alertBadge = document.getElementById("alertBadge");
  if (alertBadge) new MutationObserver(function(){
    var b = alertBadge.closest("button"); if (b && b.querySelector(".eh-w")) popLabel(b);
  }).observe(alertBadge, { attributes:true, attributeFilter:["class"], childList:true, characterData:true, subtree:true });
  document.addEventListener("pointerdown", function(e){
    if (e.pointerType === "mouse" || !e.target.closest) return;
    var host = e.target.closest(POP_SEL);
    if (!host || !host.querySelector(".eh-l")) return;
    host.classList.remove("eh-wave"); void host.offsetWidth; host.classList.add("eh-wave");
    clearTimeout(host._ehWave);
    host._ehWave = setTimeout(function(){ host.classList.remove("eh-wave"); }, 1100);
  }, { passive:true });

  /* --------------------------------------------------- tactile click wiring - */
  document.addEventListener("pointerdown", function(e){
    if (e.target.closest && e.target.closest("a,button,.seg button,.navstrip button,[data-eh-cursor]")) Audio.click(0.4, 900);
  });
  var lastHover = 0;
  document.addEventListener("mouseover", function(e){
    if (e.target.closest && e.target.closest(".navstrip button,.tile,.seg button,.home-chips button")) {
      var now = performance.now(); if (now - lastHover > 40) { lastHover = now; Audio.click(0.14, 1050); }
    }
  });

  /* ---------------------------------------------- off-canvas Control Deck --- */
  var trigger = document.createElement("button"); trigger.id = "eh-deck-trigger"; trigger.type = "button";
  trigger.textContent = "CONTROL DECK";
  var scrim = document.createElement("div"); scrim.id = "eh-deck-scrim";
  var deck = document.createElement("aside"); deck.id = "eh-deck"; deck.setAttribute("aria-hidden","true");
  deck.innerHTML =
    '<div class="eh-deck-head"><b>CONTROL<br><em>DECK</em></b>' +
      '<button class="eh-close" type="button" aria-label="Close">✕</button></div>' +
    '<h5>Session</h5>' +
      '<div class="eh-row"><span>Quota</span><b id="eh-quota">–</b></div>' +
      '<div class="eh-row"><span>Theme</span><b id="eh-theme">Editorial</b></div>' +
    '<h5>System</h5>' +
      '<div class="eh-row"><span>Tactile Clicks</span><button class="eh-sw" id="eh-sw-sound" data-on="false" type="button" aria-label="Toggle clicks"></button></div>' +
      '<div class="eh-row"><span>Custom Cursor</span><button class="eh-sw" id="eh-sw-cursor" data-on="false" type="button" aria-label="Toggle cursor"></button></div>' +
    '<h5>Jump To</h5>' +
      '<button class="eh-cta eh-accent" data-eh-jump="home" type="button">Home →</button>' +
      '<button class="eh-cta" data-eh-jump="dashboard" type="button">Dashboard →</button>' +
      '<button class="eh-cta" data-eh-jump="studio" type="button">Studio →</button>' +
      '<button class="eh-cta" data-eh-jump="calendar" type="button">Ledger →</button>' +
    '<h5>Quick Actions</h5>' +
      '<button class="eh-cta" id="eh-sync" type="button">Sync Now →</button>' +
      '<button class="eh-cta" id="eh-settings" type="button">Open Settings →</button>';
  document.body.appendChild(trigger); document.body.appendChild(scrim); document.body.appendChild(deck);

  function openDeck(){ deck.classList.add("eh-open"); scrim.classList.add("eh-open"); deck.setAttribute("aria-hidden","false"); syncQuota(); Audio.click(0.45,780); }
  function closeDeck(){ deck.classList.remove("eh-open"); scrim.classList.remove("eh-open"); deck.setAttribute("aria-hidden","true"); Audio.click(0.3,700); }
  trigger.addEventListener("click", openDeck);
  scrim.addEventListener("click", closeDeck);
  deck.querySelector(".eh-close").addEventListener("click", closeDeck);
  addEventListener("keydown", function(e){ if (e.key === "Escape") closeDeck(); });

  function fireTab(name){ var b = document.querySelector('#tabs button[data-tab="'+name+'"]'); if (b) b.click(); }
  deck.querySelectorAll("[data-eh-jump]").forEach(function(b){
    b.addEventListener("click", function(){ fireTab(b.getAttribute("data-eh-jump")); closeDeck(); });
  });
  var syncBtn = deck.querySelector("#eh-sync");
  if (syncBtn) syncBtn.addEventListener("click", function(){ var s = document.getElementById("syncBtn"); if (s) s.click(); closeDeck(); });
  var setBtn = deck.querySelector("#eh-settings");
  if (setBtn) setBtn.addEventListener("click", function(){ var a = document.getElementById("acct"); if (a) a.click(); closeDeck(); });

  function syncQuota(){ var q = document.getElementById("quota"); var out = deck.querySelector("#eh-quota"); if (q && out) out.textContent = (q.textContent || "–").replace(/quota/i,"").trim() || "–"; }

  /* deck switches */
  var swSound = deck.querySelector("#eh-sw-sound"); swSound.setAttribute("data-on", Audio.on ? "true":"false");
  swSound.addEventListener("click", function(){
    Audio.on = !Audio.on; swSound.setAttribute("data-on", Audio.on ? "true":"false");
    try { localStorage.setItem("eh_sound", Audio.on ? "1":"0"); } catch(e){}
    if (Audio.on) Audio.click(0.4, 900);
  });
  var swCursor = deck.querySelector("#eh-sw-cursor");
  swCursor.setAttribute("data-on", cursorOn ? "true" : "false");
  swCursor.addEventListener("click", function(){
    cursorOn = !cursorOn; swCursor.setAttribute("data-on", cursorOn ? "true":"false");
    try { localStorage.setItem("eh_cursor", cursorOn ? "1":"0"); } catch(e){}
    applyCursor(); Audio.click(0.3, 900);
  });

  /* ------------------------------------------------------------ phones ----- */
  // The side tab of the deck would cover the right edge of a phone screen, so phones get a
  // menu button in the header instead (shown by the phone layer in style.css).
  var PHONE = window.matchMedia ? window.matchMedia("(max-width:760px)") : { matches:false };
  var ctl = document.querySelector(".tb-ctl");
  if (ctl) {
    var menuBtn = document.createElement("button");
    menuBtn.type = "button"; menuBtn.className = "eh-deck-btn"; menuBtn.setAttribute("aria-label", "Menu: sync, quota and shortcuts");
    menuBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 12h16M4 17h16"/></svg>';
    menuBtn.addEventListener("click", openDeck);
    ctl.insertBefore(menuBtn, ctl.firstChild);
  }
  // The sideways-scrolling strips (tabs, Studio sections, channel chips) keep their selected
  // item centred. Measured with rects, so it works whatever the strip's current scroll is.
  function centerActive(strip, sel, smooth){
    if (!PHONE.matches || !strip) return;
    var a = strip.querySelector(sel); if (!a) return;
    var s = strip.getBoundingClientRect(), r = a.getBoundingClientRect();
    if (!s.width) return;                                   // strip is on a hidden tab
    var delta = (r.left + r.width / 2) - (s.left + s.width / 2);
    if (Math.abs(delta) > 2) strip.scrollBy({ left: delta, behavior: smooth ? "smooth" : "auto" });
  }
  // react only when an item GAINS its selected class (the tap wave toggles classes too)
  function watchSelected(strip, cls, onGain){
    if (!strip) return;
    new MutationObserver(function(recs){
      for (var i = 0; i < recs.length; i++) {
        var t = recs[i].target;
        if (t.tagName === "BUTTON" && t.classList.contains(cls) && !new RegExp("\\b" + cls + "\\b").test(recs[i].oldValue || "")) { onGain(); return; }
      }
    }).observe(strip, { attributes:true, attributeFilter:["class"], attributeOldValue:true, subtree:true });
  }
  var tabsNav = document.getElementById("tabs"), studioNav = document.querySelector(".studio-nav"), chanList = document.getElementById("chanList");
  // a new tab starts at the top of the page, like opening a new page
  watchSelected(tabsNav, "active", function(){
    centerActive(tabsNav, "button.active", true);
    if (PHONE.matches) window.scrollTo(0, 0);
    requestAnimationFrame(function(){ centerActive(studioNav, "button.on"); centerActive(chanList, ".chrow.on"); });
  });
  watchSelected(studioNav, "on", function(){ centerActive(studioNav, "button.on", true); });
  if (chanList) new MutationObserver(function(){ centerActive(chanList, ".chrow.on"); }).observe(chanList, { childList:true });
  centerActive(tabsNav, "button.active");
  // picking a tool scrolls its pane into view (on a phone the pane sits under the tool list)
  var toolList = document.getElementById("toolList");
  if (toolList) toolList.addEventListener("click", function(e){
    if (!PHONE.matches || !e.target.closest("button[data-tool]")) return;
    setTimeout(function(){ var p = document.getElementById("toolPane"); if (p) p.scrollIntoView({ behavior:"smooth", block:"start" }); }, 60);
  });

  /* split new headings when tabs re-render their content (once per frame at most) */
  var popQueued = false;
  var mo = new MutationObserver(function(){
    if (popQueued) return; popQueued = true;
    requestAnimationFrame(function(){ popQueued = false; bindPop(); });
  });
  mo.observe(document.getElementById("app") || document.body, { childList:true, subtree:true });
})();
