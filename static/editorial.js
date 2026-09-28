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

  /* --------------------------------------------------- text-scramble flare -- */
  var GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/#*·—";
  function scramble(el){
    if (el.dataset.ehLock === "1") return;
    var nodes = [];
    (function walk(n){
      for (var i=0;i<n.childNodes.length;i++){
        var c = n.childNodes[i];
        if (c.nodeType === 3 && c.nodeValue.trim().length){
          var s = document.createElement("span"); s.textContent = c.nodeValue; n.replaceChild(s, c);
          nodes.push({ el:s, txt:c.nodeValue });
        } else if (c.nodeType === 1 && !c.classList.contains("badge")) { walk(c); }
      }
    })(el);
    if (!nodes.length) return;
    el.dataset.ehLock = "1";
    var start = performance.now(), DUR = 250;
    (function frame(now){
      var p = Math.min(1, (now - start) / DUR);
      nodes.forEach(function(it){
        var reveal = Math.floor(p * it.txt.length), out = "";
        for (var i=0;i<it.txt.length;i++){ out += (it.txt[i] === " " || i < reveal) ? it.txt[i] : GLYPHS[(Math.random()*GLYPHS.length)|0]; }
        it.el.textContent = out;
      });
      if (p < 1) requestAnimationFrame(frame);
      else { nodes.forEach(function(it){ it.el.textContent = it.txt; }); el.dataset.ehLock = "0"; }
    })(start);
  }
  function bindScramble(root){
    (root || document).querySelectorAll(".navstrip button, .home-title, h2, h3.eh-scr, [data-scramble]").forEach(function(el){
      if (el.dataset.ehScr === "1") return; el.dataset.ehScr = "1";
      el.addEventListener("mouseenter", function(){ scramble(el); });
    });
  }
  bindScramble(document);

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

  /* rebind scramble when tabs re-render dynamic headings */
  var mo = new MutationObserver(function(){ bindScramble(document); });
  mo.observe(document.getElementById("app") || document.body, { childList:true, subtree:true });
})();
