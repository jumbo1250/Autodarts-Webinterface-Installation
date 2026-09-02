(() => {
  const btn = document.getElementById('extensionsV2InstallBtn');
  if (!btn) return;
  const statusEl = document.getElementById('extensionsV2Status');
  const logDetails = document.getElementById('extensionsV2LogDetails');
  const logEl = document.getElementById('extensionsV2Log');
  const progressWrap = document.getElementById('extensionsV2ProgressWrap');
  const loginBtn = document.getElementById('extensionsV2LoginBtn');
  const authResetBtn = document.getElementById('extensionsV2AuthResetBtn');
  const boardInput = document.getElementById('extensionsV2BoardId');
  const saveBoardBtn = document.getElementById('extensionsV2SaveBoardBtn');
  const boardStatus = document.getElementById('extensionsV2BoardStatus');
  const api = window.app_urls || {};
  let timer = null;
  let installationStartedHere = false;
  let installationWasRunning = false;
  let rebootTriggered = false;

  const tr = (key, fallback) => {
    try { return window.i18n?.t?.(key) || fallback; } catch (_) { return fallback; }
  };
  const setStatus = (text, ok = null) => {
    statusEl.textContent = text;
    statusEl.className = ok === true ? 'msg-ok' : (ok === false ? 'msg-bad' : 'hint');
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 2000);
  };
  async function refresh() {
    try {
      const res = await fetch(api.api_extensions_v2_status, {cache: 'no-store'});
      const data = await res.json();
      if (typeof data.log_tail === 'string' && data.log_tail.trim()) {
        logDetails.style.display = '';
        logEl.textContent = data.log_tail;
        logEl.scrollTop = logEl.scrollHeight;
      }
      if (data.running) {
        installationWasRunning = true;
        btn.disabled = true;
        progressWrap.style.display = '';
        logDetails.open = true;
        setStatus(tr('extensions_v2.installing', 'Installation wird durchgeführt …'));
        schedule();
        return;
      }
      progressWrap.style.display = 'none';
      btn.disabled = false;
      if (data.installed) {
        setStatus(tr('extensions_v2.installed', 'Das neue System ist installiert.'), true);
        btn.querySelector('span').textContent = tr('extensions_v2.reinstall_btn', 'Neu installieren');
        loginBtn.classList.remove('btn-disabled');
        loginBtn.removeAttribute('aria-disabled');
        loginBtn.onclick = null;
        if (authResetBtn) {
          authResetBtn.disabled = false;
          authResetBtn.classList.remove('btn-disabled');
        }

        if (
          installationStartedHere &&
          installationWasRunning &&
          !rebootTriggered
        ) {
          rebootTriggered = true;
          setStatus(
            tr(
              'extensions_v2.rebooting',
              'Installation erfolgreich. Raspberry Pi wird neu gestartet …'
            ),
            true
          );

          setTimeout(async () => {
            try {
              await fetch(api.api_admin_reboot, {
                method: 'POST',
                credentials: 'same-origin'
              });
            } catch (_) {
              // Verbindungsabbruch beim Neustart ist normal.
            }
          }, 2000);

          return;
        }
      } else if (data.state?.status === 'failed') {
        logDetails.open = true;
        setStatus(data.state.message || tr('extensions_v2.failed', 'Installation fehlgeschlagen. Details stehen im Protokoll.'), false);
        btn.querySelector('span').textContent = tr('extensions_v2.repair_btn', 'Installation reparieren');
      } else if (data.damaged) {
        setStatus(tr('extensions_v2.damaged', 'Die neue Installation ist unvollständig und kann repariert werden.'), false);
        btn.querySelector('span').textContent = tr('extensions_v2.repair_btn', 'Installation reparieren');
      } else {
        setStatus(tr('extensions_v2.not_installed', 'Das neue System ist noch nicht installiert.'));
      }
    } catch (_) {
      btn.disabled = false;
      progressWrap.style.display = 'none';
      setStatus(tr('extensions_v2.status_failed', 'Status konnte nicht geladen werden.'), false);
    }
  }

  async function saveBoardId() {
    const boardId = (boardInput?.value || '').trim();
    if (!boardId) {
      boardStatus.textContent = tr('extensions_v2.board_id_required', 'Bitte zuerst die Board-ID eingeben.');
      boardStatus.className = 'msg-bad';
      return false;
    }
    saveBoardBtn.disabled = true;
    try {
      const res = await fetch(api.api_extensions_v2_board_id, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({board_id: boardId})
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.message || 'Fehler');
      boardStatus.textContent = tr('extensions_v2.board_id_saved', 'Board-ID gespeichert.');
      boardStatus.className = 'msg-ok';
      return true;
    } catch (err) {
      boardStatus.textContent = err.message || tr('extensions_v2.board_id_save_failed', 'Board-ID konnte nicht gespeichert werden.');
      boardStatus.className = 'msg-bad';
      return false;
    } finally {
      saveBoardBtn.disabled = false;
    }
  }

  saveBoardBtn?.addEventListener('click', saveBoardId);


  authResetBtn?.addEventListener('click', async () => {
    if (!confirm(tr(
      'extensions_v2.auth_reset_confirm',
      'Caller-Anmeldung wirklich zurücksetzen? Der alte Token wird gesichert, der Caller neu gestartet und die Anmeldeseite geöffnet.'
    ))) return;

    let loginWindow = null;
    try {
      loginWindow = window.open('about:blank', '_blank');
      if (loginWindow) {
        loginWindow.document.write('<p style="font-family:sans-serif">Caller-Anmeldung wird zurückgesetzt …</p>');
      }
    } catch (_) {
      loginWindow = null;
    }

    authResetBtn.disabled = true;
    setStatus(tr('extensions_v2.auth_reset_running', 'Caller-Anmeldung wird zurückgesetzt …'));

    try {
      const res = await fetch(api.api_extensions_v2_auth_reset, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}'
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.message || 'Fehler');

      const url = data.url || `https://${window.location.hostname}:8079`;
      setStatus(data.message || tr('extensions_v2.auth_reset_started', 'Caller-Anmeldung wurde zurückgesetzt.'), true);

      if (loginWindow) {
        loginWindow.location.href = url;
      } else {
        window.open(url, '_blank', 'noopener');
      }

      setTimeout(refresh, 1500);
    } catch (err) {
      if (loginWindow) {
        try { loginWindow.close(); } catch (_) {}
      }
      setStatus(err.message || tr('extensions_v2.auth_reset_failed_short', 'Caller-Anmeldung konnte nicht zurückgesetzt werden.'), false);
    } finally {
      authResetBtn.disabled = false;
    }
  });

  btn.addEventListener('click', async () => {
    if (!(await saveBoardId())) return;
    if (!confirm(tr('extensions_v2.confirm', 'Neues Caller-/WLED-System jetzt installieren? Die bestehende Installation wird vorher gesichert.'))) return;
    installationStartedHere = true;
    installationWasRunning = false;
    rebootTriggered = false;

    btn.disabled = true;
    progressWrap.style.display = '';
    logDetails.style.display = '';
    logDetails.open = true;
    logEl.textContent = tr('extensions_v2.waiting_log', 'Installation wird gestartet. Warte auf die ersten Protokollzeilen …');
    setStatus(tr('extensions_v2.starting', 'Installation wird gestartet …'));
    try {
      const res = await fetch(api.api_extensions_v2_install, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.message || 'Fehler');
      refresh();
    } catch (err) {
      btn.disabled = false;
      progressWrap.style.display = 'none';
      setStatus(err.message || tr('extensions_v2.start_failed', 'Installation konnte nicht gestartet werden.'), false);
    }
  });
  refresh();
})();
