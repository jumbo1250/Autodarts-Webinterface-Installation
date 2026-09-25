const PING_PATH   = '/api/ping';
const PING_MARKER = 'autodarts-webpanel';
const CACHE_KEY   = 'autodarts_webpanel_url';
const CACHE_TTL   = 4 * 60 * 60 * 1000; // 4 Stunden
const PING_TIMEOUT = 1500; // ms – Pi kann beim ersten Request kurz brauchen
const WINDOW_SIZE  = 100;  // parallele Verbindungen gleichzeitig

// Fallback-Subnetze wenn WebRTC keine IP liefert
const FALLBACK_SUBNETS = [
  '192.168.178', // Fritzbox (AT/DE Standard)
  '192.168.0',
  '192.168.1',
  '192.168.2',
  '192.168.100',
  '10.0.0',
  '10.0.1',
  '10.1.1',
  '172.16.0',
];

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'GET_WEBPANEL_URL') {
    discoverWebpanel(msg.localIps || null)
      .then(url => sendResponse({ url }))
      .catch(() => sendResponse({ url: null }));
    return true;
  }
  if (msg.type === 'CLEAR_CACHE') {
    chrome.storage.local.remove(CACHE_KEY, () => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'SET_MANUAL_URL') {
    chrome.storage.local.set({ [CACHE_KEY]: { url: msg.url, ts: Date.now() } }, () => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'OPEN_TAB') {
    chrome.tabs.create({ url: msg.url, active: true });
    sendResponse({ ok: true });
    return true;
  }
});

async function discoverWebpanel(hintIps) {
  console.log('[ADW] Discovery gestartet, hintIps:', hintIps);

  // 1. Cache
  const cached = await getCache();
  if (cached) {
    console.log('[ADW] Cache gefunden:', cached, '→ prüfe...');
    if (await pingOnce(cached)) { console.log('[ADW] Cache OK'); return cached; }
    console.log('[ADW] Cache veraltet');
  }

  // 2. mDNS
  console.log('[ADW] Versuche autodarts.local...');
  if (await pingOnce('http://autodarts.local')) {
    console.log('[ADW] Gefunden via mDNS');
    await setCache('http://autodarts.local');
    return 'http://autodarts.local';
  }

  // 3. Alle lokalen IPs sammeln
  let allIps = hintIps && hintIps.length ? hintIps : null;
  if (!allIps) {
    console.log('[ADW] Keine hintIps → versuche Offscreen-WebRTC...');
    const offscreenIp = await getLocalIpViaOffscreen();
    console.log('[ADW] Offscreen IP:', offscreenIp);
    allIps = offscreenIp ? [offscreenIp] : null;
  }

  let subnets;
  if (allIps) {
    const detected = [...new Set(allIps.map(ip => ip.split('.').slice(0, 3).join('.')))];
    subnets = [...detected, ...FALLBACK_SUBNETS.filter(s => !detected.includes(s))];
    console.log('[ADW] Erkannte Subnetze:', detected, '→ scanne:', subnets);
  } else {
    subnets = FALLBACK_SUBNETS;
    console.log('[ADW] Keine IP erkannt → Fallback-Subnetze:', subnets);
  }

  // 4. Kandidaten: erst 1-60, dann 61-254
  const round1 = [];
  const round2 = [];
  for (const s of subnets) {
    for (let i = 1;   i <= 60;  i++) round1.push(`http://${s}.${i}`);
    for (let i = 61; i <= 254; i++) round2.push(`http://${s}.${i}`);
  }

  console.log(`[ADW] Runde 1: ${round1.length} Kandidaten (Window ${WINDOW_SIZE}, Timeout ${PING_TIMEOUT}ms)`);
  const t1 = Date.now();
  const hit1 = await scanRace(round1);
  console.log(`[ADW] Runde 1 fertig nach ${Date.now()-t1}ms, Treffer:`, hit1);
  if (hit1) { await setCache(hit1); return hit1; }

  console.log(`[ADW] Runde 2: ${round2.length} Kandidaten`);
  const t2 = Date.now();
  const hit2 = await scanRace(round2);
  console.log(`[ADW] Runde 2 fertig nach ${Date.now()-t2}ms, Treffer:`, hit2);
  if (hit2) { await setCache(hit2); return hit2; }

  throw new Error('Webpanel nicht gefunden');
}

// Sliding Window: hält immer WINDOW_SIZE parallele Requests, bricht ab wenn erster Treffer
function scanRace(candidates) {
  if (!candidates.length) return Promise.resolve(null);

  return new Promise(resolve => {
    const ctrl = new AbortController();
    let index     = 0;
    let active    = 0;
    let done      = false;

    function finish(url) {
      if (done) return;
      done = true;
      ctrl.abort();
      resolve(url);
    }

    function next() {
      while (active < WINDOW_SIZE && index < candidates.length && !done) {
        const url = candidates[index++];
        active++;
        pingAbortable(url, ctrl.signal)
          .then(ok => { if (ok) finish(url); })
          .catch(() => {})
          .finally(() => {
            active--;
            if (!done) {
              if (index < candidates.length) {
                next();
              } else if (active === 0) {
                finish(null);
              }
            }
          });
      }
    }

    next();
  });
}

async function pingAbortable(baseUrl, parentSignal) {
  if (parentSignal.aborted) return false;
  const ctrl  = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), PING_TIMEOUT);
  // Wenn der Parent abbricht, auch diesen Request abbrechen
  const onAbort = () => ctrl.abort();
  parentSignal.addEventListener('abort', onAbort);
  try {
    const resp = await fetch(`${baseUrl}${PING_PATH}`, { signal: ctrl.signal, cache: 'no-store' });
    clearTimeout(timer);
    if (!resp.ok) {
      console.log(`[ADW-HIT] ${baseUrl} → HTTP ${resp.status} (nicht ok)`);
      return false;
    }
    const json = await resp.json();
    const match = json && json.app === PING_MARKER;
    console.log(`[ADW-HIT] ${baseUrl} → match=${match}`, json);
    return match;
  } catch (e) {
    if (e?.name !== 'AbortError') console.log(`[ADW-ERR] ${baseUrl}:`, e?.message);
    return false;
  } finally {
    clearTimeout(timer);
    parentSignal.removeEventListener('abort', onAbort);
  }
}

async function pingOnce(baseUrl) {
  return pingAbortable(baseUrl, new AbortController().signal);
}

async function getLocalIpViaOffscreen() {
  try {
    const existing = await chrome.offscreen.hasDocument?.();
    if (!existing) {
      await chrome.offscreen.createDocument({
        url:           chrome.runtime.getURL('offscreen.html'),
        reasons:       ['WEB_RTC'],
        justification: 'Lokale IP per WebRTC für Netzwerk-Discovery',
      });
    }
    const ips = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('timeout')), 5000);
      chrome.runtime.sendMessage({ target: 'offscreen', type: 'GET_LOCAL_IPS' }, resp => {
        clearTimeout(timer);
        resolve(resp?.ips || null);
      });
    });
    try { await chrome.offscreen.closeDocument(); } catch {}
    return ips && ips.length ? ips[0] : null; // nur erste IP nötig als Fallback-Signal
  } catch {
    try { await chrome.offscreen.closeDocument(); } catch {}
    return null;
  }
}

async function getCache() {
  return new Promise(resolve => {
    chrome.storage.local.get(CACHE_KEY, data => {
      const entry = data[CACHE_KEY];
      if (!entry || !entry.url) return resolve(null);
      if (Date.now() - entry.ts > CACHE_TTL) return resolve(null);
      resolve(entry.url);
    });
  });
}

function setCache(url) {
  return new Promise(resolve => {
    chrome.storage.local.set({ [CACHE_KEY]: { url, ts: Date.now() } }, resolve);
  });
}
