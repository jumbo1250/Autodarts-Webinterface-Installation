'use strict';

const CACHE_KEY = 'autodarts_webpanel_url';

const statusDot  = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const statusUrl  = document.getElementById('statusUrl');
const btnSearch  = document.getElementById('btnSearch');
const btnOpen    = document.getElementById('btnOpen');
const btnClear   = document.getElementById('btnClear');
const manualUrl  = document.getElementById('manualUrl');
const btnManual  = document.getElementById('btnManual');

function setStatus(state, url) {
  statusDot.className = 'dot';
  statusUrl.textContent = url || '';
  if (state === 'searching') {
    statusDot.classList.add('yellow');
    statusText.textContent = 'Suche läuft…';
    btnSearch.disabled = true;
  } else if (state === 'found') {
    statusDot.classList.add('green');
    statusText.textContent = 'Webpanel gefunden!';
    btnOpen.disabled = false;
    btnSearch.disabled = false;
  } else if (state === 'error') {
    statusDot.classList.add('red');
    statusText.textContent = 'Nicht gefunden – manuelle IP eingeben?';
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

// Beim Öffnen: gecachte URL anzeigen
chrome.storage.local.get(CACHE_KEY, data => {
  const entry = data[CACHE_KEY];
  if (entry && entry.url) {
    setStatus('found', entry.url);
    btnOpen.addEventListener('click', () => openUrl(entry.url), { once: true });
  }
});

// Suchen & Öffnen
btnSearch.addEventListener('click', () => {
  setStatus('searching');
  chrome.runtime.sendMessage({ type: 'GET_WEBPANEL_URL' }, resp => {
    if (resp && resp.url) {
      setStatus('found', resp.url);
      openUrl(resp.url);
    } else {
      setStatus('error');
    }
  });
});

// Letzte URL öffnen (falls gecacht)
btnOpen.addEventListener('click', () => {
  chrome.storage.local.get(CACHE_KEY, data => {
    const entry = data[CACHE_KEY];
    if (entry && entry.url) openUrl(entry.url);
  });
});

// Cache leeren
btnClear.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'CLEAR_CACHE' }, () => {
    btnOpen.disabled = true;
    setStatus(null);
    statusUrl.textContent = '';
  });
});

// Manuelle URL speichern
btnManual.addEventListener('click', () => {
  let url = manualUrl.value.trim();
  if (!url) return;
  if (!url.startsWith('http')) url = 'http://' + url;
  chrome.runtime.sendMessage({ type: 'SET_MANUAL_URL', url }, () => {
    setStatus('found', url);
    btnOpen.disabled = false;
    manualUrl.value = '';
  });
});
