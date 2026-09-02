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
    return window.autodartsThemeTexts || {};
  }

  const EXTENSION_BRIDGE_SOURCE = 'autodarts-theme-extension';
  const WEBPANEL_BRIDGE_SOURCE = 'autodarts-theme-webpanel';

  function bridgeRequest(action, extra, timeoutMs) {
    return new Promise(function (resolve) {
      const requestId = 'theme-' + Date.now() + '-' + Math.random().toString(36).slice(2);
      let done = false;
      const timeout = window.setTimeout(function () {
        if (done) return;
        done = true;
        window.removeEventListener('message', onMessage);
        resolve(null);
      }, Number(timeoutMs || 10000));

      function onMessage(event) {
        if (done || event.source !== window || event.origin !== window.location.origin) return;
        const data = event.data;
        if (!data || data.source !== EXTENSION_BRIDGE_SOURCE || data.requestId !== requestId || data.action !== action) return;
        done = true;
        window.clearTimeout(timeout);
        window.removeEventListener('message', onMessage);
        resolve(data.response || null);
      }

      window.addEventListener('message', onMessage);
      window.postMessage(Object.assign({
        source: WEBPANEL_BRIDGE_SOURCE,
        requestId: requestId,
        action: action
      }, extra || {}), window.location.origin);
    });
  }

  function notifyExtensionLegacySelection(selected) {
    // Best effort only. Old extensions and old browser setups simply ignore it.
    bridgeRequest('legacy-select', { selected: selected || 'default' }, 1800).catch(function () {});
  }

  async function syncThemesToBrowser() {
    const button = byId('autodartsThemeSyncBtn');
    const statusEl = byId('autodartsThemeStatus');
    const texts = getTexts();
    const oldDisabled = button ? button.disabled : false;

    if (button) button.disabled = true;
    if (statusEl) statusEl.textContent = texts.syncRunning || 'Themes werden mit der Browser-Extension synchronisiert…';

    try {
      const result = await bridgeRequest('sync-library', {}, 15000);
      if (!result) {
        if (statusEl) statusEl.textContent = texts.syncExtensionMissing || 'Keine kompatible Theme-Extension erkannt. Bitte die aktuelle Extension installieren oder aktualisieren.';
        return;
      }
      if (!result.ok) {
        throw new Error(result.error || texts.syncFailed || 'Themes konnten nicht mit dem Browser synchronisiert werden.');
      }
      const count = Number(result.count || 0);
      const template = texts.syncDone || '{count} Themes wurden im Browser gespeichert.';
      if (statusEl) statusEl.textContent = String(template).replaceAll('{count}', String(count));
    } catch (error) {
      console.error(error);
      if (statusEl) statusEl.textContent = (error && error.message) || texts.syncFailed || 'Themes konnten nicht mit dem Browser synchronisiert werden.';
    } finally {
      if (button) button.disabled = oldDisabled;
    }
  }

  function getSelectedOption(selectEl) {
    return selectEl && selectEl.options ? selectEl.options[selectEl.selectedIndex] : null;
  }

  function selectedAuthor(selectEl) {
    const option = getSelectedOption(selectEl);
    return option && option.dataset ? String(option.dataset.author || '').trim() : '';
  }

  function selectedPreviewUrl(selectEl) {
    const option = getSelectedOption(selectEl);
    return option && option.dataset ? String(option.dataset.previewUrl || '').trim() : '';
  }

  function selectedModes(selectEl) {
    const option = getSelectedOption(selectEl);
    return option && option.dataset ? String(option.dataset.modes || '').trim() : '';
  }

  function selectedResolution(selectEl) {
    const option = getSelectedOption(selectEl);
    return option && option.dataset ? String(option.dataset.resolution || '').trim() : '';
  }

  function updateAuthor(selectEl, authorEl) {
    if (!authorEl) return;
    authorEl.textContent = selectedAuthor(selectEl) || getTexts().authorUnknown || 'unbekannt';
  }

  function updateMetadata(selectEl) {
    const texts = getTexts();
    const modesEl = byId('autodartsThemeModes');
    const resolutionEl = byId('autodartsThemeResolution');
    if (modesEl) {
      modesEl.textContent = selectedModes(selectEl) || texts.metaUnknown || 'nicht angegeben';
    }
    if (resolutionEl) {
      resolutionEl.textContent = selectedResolution(selectEl) || texts.metaUnknown || 'nicht angegeben';
    }
  }

  function updatePreview(selectEl) {
    const wrap = byId('autodartsThemePreviewWrap');
    const img = byId('autodartsThemePreviewImg');
    const empty = byId('autodartsThemeNoPreview');
    const previewUrl = selectedPreviewUrl(selectEl);
    const texts = getTexts();

    if (!wrap || !img || !empty) return;

    if (previewUrl) {
      img.src = previewUrl;
      img.alt = texts.previewAlt || 'Theme Vorschau';
      img.style.cursor = 'zoom-in';
      wrap.style.display = '';
      empty.style.display = 'none';
    } else {
      img.removeAttribute('src');
      img.style.cursor = 'default';
      wrap.style.display = 'none';
      empty.style.display = '';
    }
  }

  function formatThemeText(template, selectEl, authorValue) {
    const texts = getTexts();
    const author = String(authorValue || selectedAuthor(selectEl) || texts.authorUnknown || 'unbekannt').trim();
    const modes = String(selectedModes(selectEl) || texts.metaUnknown || 'nicht angegeben').trim();
    const resolution = String(selectedResolution(selectEl) || texts.metaUnknown || 'nicht angegeben').trim();
    return String(template || '')
      .replaceAll('{author}', author)
      .replaceAll('{modes}', modes)
      .replaceAll('{resolution}', resolution);
  }

  function showModal(title, body, imageUrl) {
    const modal = byId('autodartsThemeModal');
    const titleEl = byId('autodartsThemeModalTitle');
    const bodyEl = byId('autodartsThemeModalBody');
    const imageEl = byId('autodartsThemeModalImage');
    if (!modal || !titleEl || !bodyEl || !imageEl) {
      alert([title, body].filter(Boolean).join('\n\n'));
      return;
    }

    titleEl.textContent = title || '';
    bodyEl.textContent = body || '';

    if (imageUrl) {
      imageEl.src = imageUrl;
      imageEl.style.display = 'block';
    } else {
      imageEl.removeAttribute('src');
      imageEl.style.display = 'none';
    }

    modal.style.display = 'flex';
  }

  function closeModal() {
    const modal = byId('autodartsThemeModal');
    const imageEl = byId('autodartsThemeModalImage');
    if (imageEl) {
      imageEl.removeAttribute('src');
      imageEl.style.display = 'none';
    }
    if (modal) modal.style.display = 'none';
  }

  async function applyThemeSelection() {
    const selectEl = byId('autodartsThemeSelect');
    const statusEl = byId('autodartsThemeStatus');
    const authorEl = byId('autodartsThemeAuthor');
    const texts = getTexts();
    if (!selectEl) return;

    const selected = String(selectEl.value || '').trim();
    if (!selected) {
      showModal(texts.infoTitle || 'Info', texts.noThemeSelected || 'Bitte ein Theme auswählen.');
      return;
    }

    try {
      const response = await fetch(window.app_urls.api_autodarts_theme_select, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected })
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error((data && data.message) || texts.saveFailed || 'Theme konnte nicht gespeichert werden.');
      }

      updateAuthor(selectEl, authorEl);
      updatePreview(selectEl);
      if (statusEl) {
        statusEl.textContent = data.message || '';
      }

      // Keep the classic Webpanel -> active CSS workflow. New extensions are
      // merely informed so their eye/dropdown can reflect the same selection.
      notifyExtensionLegacySelection(data.filename || selected);

      if (selected !== 'default') {
        const author = String(data.author || texts.authorUnknown || 'unbekannt').trim();
        const message = formatThemeText(
          texts.thanksBody || 'Wir danken:\\n{author}\\n\\nfür die Bereitstellung dieses Themes.',
          selectEl,
          author
        );
        showModal(texts.thanksTitle || 'Vielen Dank', message, selectedPreviewUrl(selectEl));
      }
    } catch (error) {
      console.error(error);
      showModal(texts.infoTitle || 'Info', (error && error.message) || texts.networkError || 'Keine Verbindung zum Webpanel möglich.');
    }
  }

  function bindModalEvents() {
    const modal = byId('autodartsThemeModal');
    const closeX = byId('autodartsThemeModalCloseX');
    const closeBtn = byId('autodartsThemeModalCloseBtn');
    const previewImg = byId('autodartsThemePreviewImg');
    const selectEl = byId('autodartsThemeSelect');

    if (closeX) closeX.addEventListener('click', closeModal);
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (modal) {
      modal.addEventListener('click', function (event) {
        if (event.target === modal) closeModal();
      });
    }
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeModal();
    });

    if (previewImg && selectEl) {
      previewImg.addEventListener('click', function () {
        const previewUrl = selectedPreviewUrl(selectEl);
        if (!previewUrl) return;
        showModal(getTexts().previewTitle || 'Theme-Vorschau', getSelectedOption(selectEl)?.textContent || '', previewUrl);
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    const selectEl = byId('autodartsThemeSelect');
    const authorEl = byId('autodartsThemeAuthor');
    const applyBtn = byId('autodartsThemeApplyBtn');
    const syncBtn = byId('autodartsThemeSyncBtn');
    const infoBtn = byId('autodartsThemeInfoBtn');
    const storeInfoBtn = byId('autodartsThemeStoreInfoBtn');
    const texts = getTexts();

    if (!selectEl) return;

    updateAuthor(selectEl, authorEl);
    updateMetadata(selectEl);
    updatePreview(selectEl);
    bindModalEvents();

    selectEl.addEventListener('change', function () {
      updateAuthor(selectEl, authorEl);
      updateMetadata(selectEl);
      updatePreview(selectEl);
    });

    if (applyBtn) {
      applyBtn.addEventListener('click', applyThemeSelection);
    }

    if (syncBtn) {
      syncBtn.addEventListener('click', syncThemesToBrowser);
    }

    if (infoBtn) {
      infoBtn.addEventListener('click', function () {
        showModal(texts.infoTitle || 'Info', texts.infoBody || 'Hinweis:\nWenn Autodarts größere Änderungen am Layout oder an CSS-Klassen macht, müssen Themes unter Umständen angepasst werden.');
      });
    }

    if (storeInfoBtn) {
      storeInfoBtn.addEventListener('click', function () {
        showModal(texts.storeMissingTitle || 'Chrome Web Store', texts.storeMissingBody || 'Der Chrome-Web-Store-Link ist noch nicht im Webpanel hinterlegt.');
      });
    }
  });
})();
