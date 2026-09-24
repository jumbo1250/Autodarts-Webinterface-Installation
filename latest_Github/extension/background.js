const PING_PATH   = '/api/ping';
const PING_MARKER = 'autodarts-webpanel';
const SCAN_PORTS  = [80];
const CACHE_KEY   = 'autodarts_webpanel_url';
const CACHE_TTL   = 4 * 60 * 60 * 1000; // 4 Stunden

// Nachrichten vom Content-Script oder Popup beantworten
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'GET_WEBPANEL_URL') {
    discoverWebpanel().then(url => sendResponse({ url })).catch(() => sendResponse({ url: null }));
    return true; // async
  }
  if (msg.type === 'CLEAR_CACHE') {
    chrome.storage.local.remove(CACHE_KEY, () => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'SET_MANUAL_URL') {
    const entry = { url: msg.url, ts: Date.now() };
    chrome.storage.local.set({ [CACHE_KEY]: entry }, () => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'OPEN_TAB') {
    chrome.tabs.create({ url: msg.url, active: true });
    sendResponse({ ok: true });
    return true;
  }
});

async function discoverWebpanel() {
  // 1. Cache prüfen
  const cached = await getCache();
  if (cached) {
    const alive = await ping(cached);
    if (alive) return cached;
  }

  // 2. autodarts.local versuchen
  for (const port of SCAN_PORTS) {
    const url = port === 80 ? 'http://autodarts.local' : `http://autodarts.local:${port}`;
    if (await ping(url)) {
      await setCache(url);
      return url;
    }
  }

  // 3. Subnet per WebRTC bestimmen und scannen
  const localIp = await getLocalIp();
  if (!localIp) throw new Error('Kein lokales Netzwerk gefunden');

  const subnet = localIp.split('.').slice(0, 3).join('.');
  const candidates = [];
  for (let i = 1; i <= 254; i++) {
    const ip = `${subnet}.${i}`;
    if (ip === localIp) continue;
    for (const port of SCAN_PORTS) {
      candidates.push(port === 80 ? `http://${ip}` : `http://${ip}:${port}`);
    }
  }

  const found = await scanParallel(candidates, 25);
  if (found) {
    await setCache(found);
    return found;
  }

  throw new Error('Webpanel nicht gefunden');
}

async function ping(baseUrl) {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    const resp = await fetch(`${baseUrl}${PING_PATH}`, { signal: ctrl.signal, cache: 'no-store' });
    clearTimeout(timer);
    if (!resp.ok) return false;
    const json = await resp.json();
    return json && json.app === PING_MARKER;
  } catch {
    return false;
  }
}

// Parallel-Scan in Batches um Verbindungslimits zu respektieren
async function scanParallel(candidates, batchSize) {
  for (let i = 0; i < candidates.length; i += batchSize) {
    const batch = candidates.slice(i, i + batchSize);
    const results = await Promise.all(batch.map(async url => ({ url, ok: await ping(url) })));
    const hit = results.find(r => r.ok);
    if (hit) return hit.url;
  }
  return null;
}

function getLocalIp() {
  return new Promise(resolve => {
    const pc = new RTCPeerConnection({ iceServers: [] });
    const found = new Set();
    pc.createDataChannel('');
    pc.onicecandidate = e => {
      if (!e.candidate) {
        pc.close();
        const ips = [...found].filter(ip => /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) && !ip.startsWith('169.'));
        // Bevorzuge private Ranges
        const priv = ips.find(ip => ip.startsWith('192.168.') || ip.startsWith('10.') || ip.startsWith('172.'));
        resolve(priv || ips[0] || null);
        return;
      }
      const m = e.candidate.candidate.match(/(\d{1,3}(?:\.\d{1,3}){3})/);
      if (m) found.add(m[1]);
    };
    pc.createOffer().then(o => pc.setLocalDescription(o));
    setTimeout(() => { pc.close(); resolve(null); }, 3000);
  });
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
