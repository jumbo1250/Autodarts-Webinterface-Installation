(function () {
  'use strict';

  const STORE_KEY = 'autodarts_gear_pos';

  function loadPos() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
  }
  function savePos(x, y) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ x, y })); } catch {}
  }

  // Button erzeugen
  const btn = document.createElement('div');
  btn.id    = 'autodarts-webpanel-gear';
  btn.title = 'Autodarts Webpanel öffnen';
  btn.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
      <path d="M12 15.5A3.5 3.5 0 0 1 8.5 12 3.5 3.5 0 0 1 12 8.5a3.5 3.5 0 0 1 3.5 3.5 3.5 3.5 0 0 1-3.5 3.5m7.43-2.92c.04-.34.07-.69.07-1.08s-.03-.74-.07-1.08l2.32-1.82c.21-.16.27-.45.13-.69l-2.2-3.81c-.13-.24-.42-.32-.66-.24l-2.74 1.1c-.57-.44-1.18-.8-1.86-1.07L14.07 2.1c-.04-.27-.28-.47-.57-.47h-4.4c-.29 0-.52.2-.57.47l-.42 2.79c-.68.27-1.29.63-1.86 1.07L3.51 4.87c-.24-.09-.53 0-.66.24l-2.2 3.81c-.14.24-.08.53.13.69l2.32 1.82c-.04.34-.07.7-.07 1.08s.03.74.07 1.08L.8 15.41c-.21.16-.27.45-.13.69l2.2 3.81c.13.24.42.32.66.24l2.74-1.1c.57.44 1.18.8 1.86 1.07l.41 2.79c.05.27.28.47.57.47h4.4c.29 0 .53-.2.57-.47l.41-2.79c.68-.27 1.29-.63 1.86-1.07l2.74 1.1c.24.09.53 0 .66-.24l2.2-3.81c.14-.24.08-.53-.13-.69l-2.32-1.82z"/>
    </svg>
  `;

  Object.assign(btn.style, {
    position:       'fixed',
    zIndex:         '2147483647',
    width:          '44px',
    height:         '44px',
    borderRadius:   '50%',
    background:     'rgba(20,20,20,0.82)',
    border:         '2px solid rgba(255,255,255,0.18)',
    boxShadow:      '0 2px 12px rgba(0,0,0,0.5)',
    cursor:         'grab',
    display:        'flex',
    alignItems:     'center',
    justifyContent: 'center',
    color:          '#ffffff',
    userSelect:     'none',
    transition:     'background 0.15s, transform 0.15s',
    backdropFilter: 'blur(4px)',
  });

  // Position: gespeicherte Position laden oder Standard oben rechts (5vw vom Rand)
  const saved = loadPos();
  if (saved.x !== undefined && saved.y !== undefined) {
    btn.style.left   = saved.x + 'px';
    btn.style.top    = saved.y + 'px';
    btn.style.right  = 'unset';
    btn.style.bottom = 'unset';
  } else {
    btn.style.right  = '5vw';
    btn.style.top    = '18px';
    btn.style.left   = 'unset';
    btn.style.bottom = 'unset';
  }

  // Hover
  btn.addEventListener('mouseenter', () => { btn.style.background = 'rgba(40,40,40,0.95)'; btn.style.transform = 'scale(1.1)'; });
  btn.addEventListener('mouseleave', () => { btn.style.background = 'rgba(20,20,20,0.82)'; btn.style.transform = 'scale(1)'; });

  // Drag-Logik
  let dragging = false, startX, startY, origLeft, origTop;

  btn.addEventListener('mousedown', e => {
    e.preventDefault();
    dragging = false;
    btn.style.cursor = 'grabbing';

    const rect = btn.getBoundingClientRect();
    origLeft = rect.left;
    origTop  = rect.top;
    startX   = e.clientX;
    startY   = e.clientY;

    function onMove(e) {
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (!dragging && Math.abs(dx) + Math.abs(dy) > 4) dragging = true;
      if (!dragging) return;

      let newLeft = origLeft + dx;
      let newTop  = origTop  + dy;
      newLeft = Math.max(0, Math.min(window.innerWidth  - 44, newLeft));
      newTop  = Math.max(0, Math.min(window.innerHeight - 44, newTop));

      btn.style.left   = newLeft + 'px';
      btn.style.top    = newTop  + 'px';
      btn.style.right  = 'unset';
      btn.style.bottom = 'unset';
    }

    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup',   onUp);
      btn.style.cursor = 'grab';

      if (dragging) {
        savePos(parseInt(btn.style.left), parseInt(btn.style.top));
      } else {
        openWebpanel();
      }
    }

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup',   onUp);
  });

  // Status-Dot am Button
  let statusDot = null;
  function setStatus(state) {
    if (!statusDot) {
      statusDot = document.createElement('span');
      Object.assign(statusDot.style, {
        position:      'absolute',
        bottom:        '1px',
        right:         '1px',
        width:         '10px',
        height:        '10px',
        borderRadius:  '50%',
        border:        '2px solid #111',
        transition:    'background 0.3s',
        pointerEvents: 'none',
        display:       'none',
      });
      btn.appendChild(statusDot);
    }
    if (state === 'searching') {
      statusDot.style.display    = 'block';
      statusDot.style.background = '#facc15';
      statusDot.title = 'Suche läuft…';
    } else if (state === 'found') {
      statusDot.style.display    = 'block';
      statusDot.style.background = '#22c55e';
      statusDot.title = 'Gefunden!';
    } else if (state === 'error') {
      statusDot.style.display    = 'block';
      statusDot.style.background = '#ef4444';
      statusDot.title = 'Nicht gefunden – Extension-Icon klicken für manuelle IP';
    } else {
      statusDot.style.display = 'none';
    }
  }

  let searching = false;

  function getLocalIps() {
    return new Promise(resolve => {
      try {
        const pc = new RTCPeerConnection({ iceServers: [] });
        const found = new Set();
        pc.createDataChannel('');
        pc.onicecandidate = e => {
          if (!e.candidate) {
            try { pc.close(); } catch {}
            const ips = [...found].filter(ip =>
              /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) &&
              !ip.startsWith('169.254') && !ip.startsWith('127.')
            );
            resolve(ips.length ? ips : null);
            return;
          }
          const m = e.candidate.candidate.match(/(\d{1,3}(?:\.\d{1,3}){3})/);
          if (m) found.add(m[1]);
        };
        pc.createOffer().then(o => pc.setLocalDescription(o));
        setTimeout(() => { try { pc.close(); } catch {} resolve(null); }, 3000);
      } catch { resolve(null); }
    });
  }

  function openWebpanel() {
    if (searching) return;
    searching = true;
    setStatus('searching');
    getLocalIps().then(localIps => {
      try {
        chrome.runtime.sendMessage({ type: 'GET_WEBPANEL_URL', localIps }, resp => {
          searching = false;
          if (chrome.runtime.lastError) { setStatus('error'); setTimeout(() => setStatus(null), 3000); return; }
          if (resp && resp.url) {
            setStatus('found');
            setTimeout(() => setStatus(null), 2000);
            try { chrome.runtime.sendMessage({ type: 'OPEN_TAB', url: resp.url }); } catch {}
          } else {
            setStatus('error');
            setTimeout(() => setStatus(null), 5000);
          }
        });
      } catch {
        // Extension wurde neu geladen – Tab muss einmal refresht werden
        searching = false;
        setStatus(null);
        btn.title = 'Seite neu laden (F5) um Extension zu aktivieren';
      }
    });
  }

  function attach() {
    if (!document.body || document.getElementById('autodarts-webpanel-gear')) return;
    document.body.appendChild(btn);
  }

  if (document.body) {
    attach();
  } else {
    document.addEventListener('DOMContentLoaded', attach);
  }
})();
