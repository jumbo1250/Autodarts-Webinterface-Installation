/*
 * Copyright (c) 2026 Peter Rottmann
 * All rights reserved.
 * Proprietary and not open source.
 * No use, modification, distribution, publication, sublicensing,
 * or commercial use without prior express written permission.
 * Applies only to parts created by Peter Rottmann.
 * Third-party components remain under their respective licenses.
 * Provided "as is", without warranty.
 */

(function () {
  function byId(id) {
    return document.getElementById(id);
  }

  function getTexts() {
    return window.autodartsDisplayTexts || {};
  }

  function getInitialInfo() {
    return (window.app_data && window.app_data.autodarts_display_info) || {};
  }

  function setStatus(message, isError) {
    const el = byId('autodartsDisplayStatus');
    if (!el) return;
    el.textContent = message || '';
    el.className = isError ? 'hint msg-bad' : 'hint';
  }

  function backendFallbackLabel(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'x11') return 'Alter TV Modus';
    if (normalized === 'wayland') return 'Neuer TV Modus';
    return 'unbekannt';
  }

  function renderBackend(info) {
    const select = byId('autodartsDisplayBackendSelect');
    const currentEl = byId('autodartsDisplayBackendCurrent');
    if (!select && !currentEl) return;

    const texts = getTexts();
    const configured = String((info && info.configured_backend) || '').trim().toLowerCase();
    const runtime = String((info && (info.runtime_backend || info.backend)) || '').trim().toLowerCase();
    const configuredLabel = String((info && info.configured_backend_label) || backendFallbackLabel(configured));
    const runtimeLabel = String((info && info.backend_label) || backendFallbackLabel(runtime));

    if (select && (configured === 'x11' || configured === 'wayland')) {
      select.value = configured;
    } else if (select && (runtime === 'x11' || runtime === 'wayland')) {
      select.value = runtime;
    }

    if (currentEl) {
      const template = (info && info.pending_reboot)
        ? (texts.backendPending || 'TV-Modus ist für den nächsten Start auf {configured} gesetzt. Aktuell läuft noch {runtime}.')
        : (texts.backendCurrent || 'Aktuell läuft: {runtime}. Nach Neustart eingestellt: {configured}.');
      currentEl.textContent = String(template)
        .replace('{runtime}', runtimeLabel)
        .replace('{configured}', configuredLabel);
      currentEl.className = (info && info.pending_reboot) ? 'hint msg-bad' : 'hint';
    }
  }

  function selectedOutputInfo(info) {
    const outputSelect = byId('autodartsDisplayOutputSelect');
    if (!outputSelect) return null;
    const outputName = String(outputSelect.value || '').trim();
    const outputs = (info && info.outputs) || [];
    return outputs.find((item) => String(item.name || '') === outputName) || null;
  }

  function buildModeLabel(output, mode) {
    const texts = getTexts();
    const fallback = texts.unknownMode || 'unbekannt';
    const label = String((mode && (mode.label || mode.mode || mode.key)) || fallback).trim() || fallback;
    if (!output || !output.name) {
      return String(texts.currentMode || 'Aktuell aktiv: {mode}').replace('{mode}', label);
    }
    return String(texts.currentModeWithOutput || 'Aktuell aktiv auf {output}: {mode}')
      .replace('{output}', String(output.name || ''))
      .replace('{mode}', label);
  }

  function renderCurrentMode(info) {
    const currentEl = byId('autodartsDisplayCurrentMode');
    if (!currentEl) return;
    const output = selectedOutputInfo(info);
    if (!output) {
      currentEl.textContent = '';
      return;
    }
    const modes = Array.isArray(output.modes) ? output.modes : [];
    const currentMode = modes.find((item) => item && item.is_current) || modes[0] || null;
    currentEl.textContent = currentMode ? buildModeLabel(output, currentMode) : '';
  }

  function renderModes(info, preferredModeKey) {
    const modeSelect = byId('autodartsDisplayModeSelect');
    const output = selectedOutputInfo(info);
    if (!modeSelect) return;

    modeSelect.innerHTML = '';

    if (!output || !Array.isArray(output.modes) || !output.modes.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = getTexts().noOutputs || 'Keine aktiven Bildschirm-Ausgänge gefunden.';
      modeSelect.appendChild(option);
      modeSelect.disabled = true;
      renderCurrentMode(info);
      return;
    }

    modeSelect.disabled = false;

    output.modes.forEach((mode) => {
      const option = document.createElement('option');
      option.value = String(mode.key || '');
      option.textContent = String(mode.label || mode.mode || mode.key || '');
      if (preferredModeKey && option.value === preferredModeKey) {
        option.selected = true;
      } else if (!preferredModeKey && mode.is_current) {
        option.selected = true;
      }
      modeSelect.appendChild(option);
    });

    renderCurrentMode(info);
  }

  function renderOutputs(info) {
    renderBackend(info || {});

    const wrap = byId('autodartsDisplayControlsWrap');
    const unavailableWrap = byId('autodartsDisplayUnavailableWrap');
    const unavailableEl = byId('autodartsDisplayUnavailable');
    const outputSelect = byId('autodartsDisplayOutputSelect');
    if (!wrap || !unavailableWrap || !unavailableEl || !outputSelect) return;

    const outputs = Array.isArray(info.outputs) ? info.outputs : [];
    const available = !!info.available && outputs.length > 0;

    if (!available) {
      wrap.style.display = 'none';
      unavailableWrap.style.display = '';
      unavailableEl.textContent = String(info.message || getTexts().unavailable || 'Es wurde kein aktiver Monitor am Raspberry erkannt.');
      outputSelect.innerHTML = '';
      renderModes({ outputs: [] });
      return;
    }

    wrap.style.display = '';
    unavailableWrap.style.display = 'none';
    outputSelect.innerHTML = '';

    outputs.forEach((output, index) => {
      const option = document.createElement('option');
      option.value = String(output.name || '');
      option.textContent = String(output.label || output.name || ('Display ' + (index + 1)));
      outputSelect.appendChild(option);
    });

    renderModes(info);
  }

  function setInfo(info, statusMessage, isError) {
    window.app_data = window.app_data || {};
    window.app_data.autodarts_display_info = info || {};
    renderOutputs(window.app_data.autodarts_display_info);
    if (statusMessage) {
      setStatus(statusMessage, !!isError);
    }
  }

  async function refreshDisplayInfo(showMessage) {
    const texts = getTexts();
    try {
      if (showMessage) {
        setStatus(texts.loading || 'Bildschirm-Liste wird geladen…', false);
      }
      const response = await fetch(window.app_urls.api_autodarts_display_info, {
        method: 'GET',
        cache: 'no-store',
        headers: { 'Accept': 'application/json' }
      });
      const data = await response.json();
      const info = (data && data.display_info) || {};
      setInfo(info, showMessage ? (info.available ? (texts.refreshDone || 'Bildschirm-Liste wurde neu geladen.') : (info.message || texts.unavailable)) : '', !info.available);
    } catch (error) {
      console.error(error);
      setStatus((error && error.message) || texts.refreshFailed || 'Bildschirm-Liste konnte nicht geladen werden.', true);
    }
  }

  async function applyDisplayBackend() {
    const texts = getTexts();
    const select = byId('autodartsDisplayBackendSelect');
    if (!select) return;

    const backend = String(select.value || '').trim();
    if (!backend) {
      setStatus(texts.backendRequired || 'Bitte zuerst einen TV-Modus auswählen.', true);
      return;
    }

    if (!window.confirm(texts.backendConfirm || 'TV-Modus ändern? Die Änderung wird erst nach einem Neustart aktiv.')) {
      return;
    }

    try {
      setStatus(texts.loading || 'Bildschirm-Liste wird geladen…', false);
      const response = await fetch(window.app_urls.api_autodarts_display_backend, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ backend })
      });
      const data = await response.json();
      const ok = !!(response.ok && data && data.ok);
      if (data && data.display_info) {
        setInfo(data.display_info, data.message || '', !ok);
      } else {
        setStatus((data && data.message) || texts.backendApplyFailed || 'Der TV-Modus konnte nicht gesetzt werden.', !ok);
      }
      if (!ok) {
        throw new Error((data && data.message) || texts.backendApplyFailed || 'Der TV-Modus konnte nicht gesetzt werden.');
      }
    } catch (error) {
      console.error(error);
      const statusEl = byId('autodartsDisplayStatus');
      if (!String((statusEl && statusEl.textContent) || '').trim()) {
        setStatus((error && error.message) || texts.networkError || 'Keine Verbindung zum Webpanel möglich.', true);
      }
    }
  }

  function showBackendInfo() {
    const texts = getTexts();
    const title = texts.backendInfoTitle || 'Info TV-Modus';
    const body = texts.backendInfoBody || 'Bevorzugt bitte immer den neuen TV Modus verwenden. Sollte das Bild schwarz bleiben, flackern oder ein alter Fernseher Probleme machen, bitte auf den alten TV Modus wechseln. Wichtig: Nach dem Umschalten ist ein Neustart notwendig.';
    window.alert(title + '\n\n' + body);
  }

  async function applyDisplayMode() {
    const texts = getTexts();
    const outputSelect = byId('autodartsDisplayOutputSelect');
    const modeSelect = byId('autodartsDisplayModeSelect');
    if (!outputSelect || !modeSelect) return;

    const output = String(outputSelect.value || '').trim();
    const mode = String(modeSelect.value || '').trim();

    if (!output) {
      setStatus(texts.outputRequired || 'Bitte zuerst einen Bildschirm-Ausgang auswählen.', true);
      return;
    }
    if (!mode) {
      setStatus(texts.modeRequired || 'Bitte zuerst eine Auflösung auswählen.', true);
      return;
    }

    try {
      setStatus(texts.loading || 'Bildschirm-Liste wird geladen…', false);
      const response = await fetch(window.app_urls.api_autodarts_display_mode, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ output, mode })
      });
      const data = await response.json();
      const ok = !!(response.ok && data && data.ok);
      if (data && data.display_info) {
        setInfo(data.display_info, data.message || '', !ok);
      } else {
        setStatus((data && data.message) || texts.applyFailed || 'Die Auflösung konnte nicht gesetzt werden.', !ok);
      }
      if (!ok) {
        throw new Error((data && data.message) || texts.applyFailed || 'Die Auflösung konnte nicht gesetzt werden.');
      }
    } catch (error) {
      console.error(error);
      const statusEl = byId('autodartsDisplayStatus');
      if (!String((statusEl && statusEl.textContent) || '').trim()) {
        setStatus((error && error.message) || texts.networkError || 'Keine Verbindung zum Webpanel möglich.', true);
      }
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    const root = byId('autodartsDisplayBox');
    if (!root) return;

    const outputSelect = byId('autodartsDisplayOutputSelect');
    const applyBtn = byId('autodartsDisplayApplyBtn');
    const refreshBtn = byId('autodartsDisplayRefreshBtn');
    const backendApplyBtn = byId('autodartsDisplayBackendApplyBtn');
    const backendInfoBtn = byId('autodartsDisplayBackendInfoBtn');

    setInfo(getInitialInfo());

    if (outputSelect) {
      outputSelect.addEventListener('change', function () {
        renderModes((window.app_data && window.app_data.autodarts_display_info) || {});
      });
    }

    if (applyBtn) {
      applyBtn.addEventListener('click', applyDisplayMode);
    }

    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        refreshDisplayInfo(true);
      });
    }

    if (backendApplyBtn) {
      backendApplyBtn.addEventListener('click', applyDisplayBackend);
    }

    if (backendInfoBtn) {
      backendInfoBtn.addEventListener('click', showBackendInfo);
    }

    refreshDisplayInfo(false);
  });
})();
