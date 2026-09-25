'use strict';

const PING_PATH    = '/api/ping';
const PING_MARKER  = 'autodarts-webpanel';
const PING_TIMEOUT = 3000;
const BATCH_SIZE   = 40;
const CACHE_KEY    = 'autodarts_webpanel_url';
const CACHE_TTL    = 4 * 60 * 60 * 1000;

const FALLBACK_SUBNETS = [
  '192.168.178', '192.168.0', '192.168.1', '192.168.2',
  '192.168.100', '10.0.0', '10.0.1', '10.1.1', '172.16.0',
];

const statusDot  = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const statusUrl  = document.getElementById('statusUrl');
const btnSearch  = document.getElementById('btnSearch');
const btnOpen    = document.getElementById('btnOpen');
const btnClear   = document.getElementById('btnClear');
const manualUrl  = document.getElementById('manualUrl');
const btnManual  = document.getElementById('btnManual');
const debugBox   = document.getElementById('debugBox');
const debugText  = document.getElementById('debugText');

function showDebug(msg) {
  debugBox.style.display = 'block';
  debugText.textContent  = msg;
}

function setStatus(state, url) {
  statusDot.className   = 'dot';
  statusUrl.textContent = url || '';
  if (state === 'searching') {
    statusDot.classList.add('yellow');
    statusText.textContent = 'Suche läuft…';
    btnSearch.disabled = true;
  } else if (state === 'found') {
    statusDot.classList.add('green');
    statusText.textContent = 'Webpanel gefunden!';
    btnOpen.disabled   = false;
    btnSearch.disabled = false;
  } else if (state === 'error') {
    statusDot.classList.add('red');
    statusText.textContent = 'Nicht gefunden – IP manuell eintragen';
    btnSearch.disabled = false;
  } else {
    statusDot.classList.add('grey');
    statusText.textContent = 'Bereit';
    btnSearch.disabled = false;
  }
}

function openUrl(url) {
  chrome.tabs.create({ url, active: true });
  window.close();
}

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
            /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) && !ip.startsWith('169.254') && !ip.startsWith('127.')
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

async function pingOnce(baseUrl, timeoutMs) {
  try {
    const ctrl  = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const resp  = await fetch(`${baseUrl}${PING_PATH}`, { signal: ctrl.signal, cache: 'no-store', credentials: 'omit' });
    clearTimeout(timer);
    if (!resp.ok) return false;
    const json = await resp.json();
    return json && json.app === PING_MARKER;
  } catch {
    return false;
  }
}

async function ping(baseUrl) {
  // Erster Versuch mit kurzem Timeout, falls keine Antwort → zweiter Versuch mit längerem
  if (await pingOnce(baseUrl, 800)) return true;
  return pingOnce(baseUrl, PING_TIMEOUT);
}

async function scanBatches(candidates) {
  for (let i = 0; i < candidates.length; i += BATCH_SIZE) {
    const batch = candidates.slice(i, i + BATCH_SIZE);
    showDebug(`Teste: ${batch[0].replace('http://','')} … ${batch[batch.length-1].replace('http://','')}`);

    // Sofort zurück wenn erste IP positiv antwortet – nicht auf den Rest warten
    const hit = await new Promise(resolve => {
      let remaining = batch.length;
      for (const url of batch) {
        ping(url).then(ok => {
          if (ok) { resolve(url); return; }
          if (--remaining === 0) resolve(null);
        }).catch(() => { if (--remaining === 0) resolve(null); });
      }
    });

    if (hit) return hit;
  }
  return null;
}

async function discoverWebpanel() {
  const cached = await getCache();
  if (cached && await ping(cached)) return cached;

  if (await ping('http://autodarts.local')) return 'http://autodarts.local';

  const localIps = await getLocalIps();

  if (localIps) {
    // Erkannte Subnetze: 192.168.x.x zuerst (VPN-IPs wie 10.x.x.x nach hinten)
    const detected = [...new Set(localIps.map(ip => ip.split('.').slice(0, 3).join('.')))];
    detected.sort((a, b) => {
      const aHome = a.startsWith('192.168.');
      const bHome = b.startsWith('192.168.');
      return aHome === bHome ? 0 : aHome ? -1 : 1;
    });
    showDebug(`Scanne: ${detected.map(s => s + '.x').join(', ')}`);

    // Erkannte Subnetze vollständig (1-254) scannen BEVOR Fallbacks
    const detectedAll = [];
    for (const s of detected)
      for (let i = 1; i <= 254; i++) detectedAll.push(`http://${s}.${i}`);

    const hit = await scanBatches(detectedAll);
    if (hit) return hit;

    showDebug('Erkannte Subnetze leer → Fallbacks…');
  } else {
    showDebug('Keine IP erkannt → Fallback-Subnetze');
  }

  // Fallback-Subnetze (die noch nicht gescannt wurden)
  const detectedSet = new Set(localIps
    ? localIps.map(ip => ip.split('.').slice(0, 3).join('.'))
    : []);
  const fbCandidates = [];
  for (const s of FALLBACK_SUBNETS.filter(s => !detectedSet.has(s)))
    for (let i = 1; i <= 254; i++) fbCandidates.push(`http://${s}.${i}`);

  return await scanBatches(fbCandidates);
}

function getCache() {
  return new Promise(resolve => {
    chrome.storage.local.get(CACHE_KEY, data => {
      const e = data[CACHE_KEY];
      resolve(e && e.url && (Date.now() - e.ts < CACHE_TTL) ? e.url : null);
    });
  });
}

function setCache(url) {
  return new Promise(resolve =>
    chrome.storage.local.set({ [CACHE_KEY]: { url, ts: Date.now() } }, resolve)
  );
}

// Init
chrome.storage.local.get(CACHE_KEY, data => {
  const entry = data[CACHE_KEY];
  if (entry && entry.url) {
    setStatus('found', entry.url);
    btnOpen.addEventListener('click', () => openUrl(entry.url), { once: true });
  }
});

btnSearch.addEventListener('click', async () => {
  setStatus('searching');
  debugBox.style.display = 'none';
  const url = await discoverWebpanel();
  if (url) {
    await setCache(url);
    chrome.runtime.sendMessage({ type: 'SET_MANUAL_URL', url });
    setStatus('found', url);
    showDebug(`Gefunden: ${url}`);
    openUrl(url);
  } else {
    setStatus('error');
    showDebug('Nichts gefunden → Pi-IP manuell eintragen');
  }
});

btnOpen.addEventListener('click', () => {
  chrome.storage.local.get(CACHE_KEY, data => {
    const e = data[CACHE_KEY];
    if (e && e.url) openUrl(e.url);
  });
});

btnClear.addEventListener('click', () => {
  chrome.storage.local.remove(CACHE_KEY, () => {
    chrome.runtime.sendMessage({ type: 'CLEAR_CACHE' });
    btnOpen.disabled = true;
    debugBox.style.display = 'none';
    setStatus(null);
    statusUrl.textContent = '';
  });
});

btnManual.addEventListener('click', async () => {
  let url = manualUrl.value.trim();
  if (!url) return;
  if (!url.startsWith('http')) url = 'http://' + url;
  await setCache(url);
  chrome.runtime.sendMessage({ type: 'SET_MANUAL_URL', url });
  setStatus('found', url);
  btnOpen.disabled = false;
  manualUrl.value = '';
  showDebug(`Gespeichert: ${url}`);
});
