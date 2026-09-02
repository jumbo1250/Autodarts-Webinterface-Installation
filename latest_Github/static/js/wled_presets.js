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

function initWledPresets(){
  if (window.__wledPresetsInitialized) return;
  window.__wledPresetsInitialized = true;

  const TYPES = [
    {id:'player1', label:t('wled_presets.type.player1', 'Spieler 1 / Player 1'), kind:'fixed', arg:'-IDE', duration:false, info:t('wled_presets.info.player1', 'Idle-/Turn-Effekt für Spieler 1. Bleibt normalerweise aktiv, daher keine Sekunden.')},
    {id:'player2', label:t('wled_presets.type.player2', 'Spieler 2 / Player 2'), kind:'fixed', arg:'-IDE2', duration:false, info:t('wled_presets.info.player2', 'Idle-/Turn-Effekt für Spieler 2. Bleibt normalerweise aktiv, daher keine Sekunden.')},
    {id:'player3', label:t('wled_presets.type.player3', 'Spieler 3 / Player 3'), kind:'fixed', arg:'-IDE3', duration:false, info:t('wled_presets.info.player3', 'Idle-/Turn-Effekt für Spieler 3. Bleibt normalerweise aktiv, daher keine Sekunden.')},
    {id:'player4', label:t('wled_presets.type.player4', 'Spieler 4 / Player 4'), kind:'fixed', arg:'-IDE4', duration:false, info:t('wled_presets.info.player4', 'Idle-/Turn-Effekt für Spieler 4. Bleibt normalerweise aktiv, daher keine Sekunden.')},
    {id:'player5', label:t('wled_presets.type.player5', 'Spieler 5 / Player 5'), kind:'fixed', arg:'-IDE5', duration:false, info:t('wled_presets.info.player5', 'Idle-/Turn-Effekt für Spieler 5. Bleibt normalerweise aktiv, daher keine Sekunden.')},
    {id:'player6', label:t('wled_presets.type.player6', 'Spieler 6 / Player 6'), kind:'fixed', arg:'-IDE6', duration:false, info:t('wled_presets.info.player6', 'Idle-/Turn-Effekt für Spieler 6. Bleibt normalerweise aktiv, daher keine Sekunden.')},

    {id:'leg', label:t('wled_presets.type.leg', 'Leg gewonnen / Game won'), kind:'fixed', arg:'-G', duration:true, info:t('wled_presets.info.leg', 'Wird abgespielt, wenn ein Leg gewonnen wurde.')},
    {id:'match', label:t('wled_presets.type.match', 'Match gewonnen / Match won'), kind:'fixed', arg:'-M', duration:true, info:t('wled_presets.info.match', 'Wird abgespielt, wenn das Match gewonnen wurde.')},
    {id:'busted', label:t('wled_presets.type.busted', 'Busted / Überworfen'), kind:'fixed', arg:'-B', duration:true, info:t('wled_presets.info.busted', 'Wird abgespielt, wenn ein Spieler überwirft.')},
    {id:'player_joined', label:t('wled_presets.type.player_joined', 'Spieler beigetreten / Player joined'), kind:'fixed', arg:'-PJ', duration:true, info:t('wled_presets.info.player_joined', 'Wird abgespielt, wenn ein Spieler beitritt.')},
    {id:'player_left', label:t('wled_presets.type.player_left', 'Spieler verlassen / Player left'), kind:'fixed', arg:'-PL', duration:true, info:t('wled_presets.info.player_left', 'Wird abgespielt, wenn ein Spieler die Lobby/das Match verlässt.')},
    {id:'board_stop_effect', label:t('wled_presets.type.board_stop_effect', 'Board gestoppt / Board stopped'), kind:'fixed', arg:'-BSE', duration:true, info:t('wled_presets.info.board_stop_effect', 'Effekt, wenn das Board während des Spiels gestoppt wird.')},
    {id:'calibration', label:t('wled_presets.type.calibration', 'Kalibrierung / Calibration'), kind:'fixed', arg:'-CE', duration:true, info:t('wled_presets.info.calibration', 'Effekt bei Kalibrierung.')},
    {id:'high_finish', label:t('wled_presets.type.high_finish', 'High Finish'), kind:'fixed', arg:'-HF', duration:true, info:t('wled_presets.info.high_finish', 'High-Finish-Effekt. Sinnvoll zusammen mit -HFO, z. B. High Finish ab 100.')},
    {id:'checkout', label:t('wled_presets.type.checkout', 'Checkout / Takeout'), kind:'fixed', arg:'-TOE', duration:true, info:t('wled_presets.info.checkout', 'Effekt, wenn Takeout/Checkout erkannt oder ausgelöst wird.')},
    {id:'bull', label:t('wled_presets.type.bull', 'Bull / Bullseye'), kind:'fixed', arg:'-DSBULL', duration:true, info:t('wled_presets.info.bull', 'Einzeldart-Effekt für Bull oder Bullseye. Benötigt Single-Dart-Events vom Caller.')},
    {id:'score0', label:t('wled_presets.type.score0', 'Score 0 / Score 0'), kind:'fixed', arg:'-S0', duration:true, info:t('wled_presets.info.score0', 'Effekt für Gesamtscore 0.')},
    {id:'sleep_effect', label:t('wled_presets.type.sleep_effect', 'Sleep-Effekt / Sleep effect'), kind:'fixed', arg:'-SLE', duration:true, info:t('wled_presets.info.sleep_effect', 'Effekt nach Inaktivität. Timeout wird mit -SLET eingestellt.')},

    {id:'score_exact', label:t('wled_presets.type.score_exact', 'Exakter Score / Exact score'), kind:'score_exact', duration:true, info:t('wled_presets.info.score_exact', 'Effekt für einen exakten Gesamtscore von 0 bis 180.')},
    {id:'score_range', label:t('wled_presets.type.score_range', 'Score-Bereich / Score range'), kind:'score_range', duration:true, info:t('wled_presets.info.score_range', 'Effekt für einen Gesamtscore-Bereich. Das Argument wird automatisch als -A1 bis -A12 vergeben.')},

    {id:'high_finish_on', label:t('wled_presets.type.high_finish_on', 'High-Finish ab Score / High finish threshold'), kind:'option_int', arg:'-HFO', value:'100', min:2, max:170, info:t('wled_presets.info.high_finish_on', 'Legt fest, ab welchem Restscore ein High-Finish-Effekt (-HF) ausgelöst wird. Beispiel: 100.')},
    {id:'wled_off', label:t('wled_presets.type.wled_off', 'Nach Match-Ende ausschalten / Turn off after match end'), kind:'option_bool', arg:'-OFF', value:'1', info:t('wled_presets.info.wled_off', 'OFF schaltet WLED aus, wenn das Match beendet ist.')},
    {id:'wled_off_at_start', label:t('wled_presets.type.wled_off_at_start', 'Beim Verbinden ausschalten / Turn off on connect'), kind:'option_bool', arg:'-SOFF', value:'1', info:t('wled_presets.info.wled_off_at_start', 'SOFF schaltet WLED aus, sobald darts-wled startet oder sich mit WLED verbindet. Das löscht alte Effekte. Unterschied: OFF = nach Match-Ende, SOFF = beim Start/Connect.')},
    {id:'sleep_timeout', label:t('wled_presets.type.sleep_timeout', 'Sleep nach Sekunden / Sleep timeout seconds'), kind:'option_int', arg:'-SLET', value:'300', min:1, max:86400, info:t('wled_presets.info.sleep_timeout', 'Sekunden ohne Spielereignis, bis der Sleep-Effekt (-SLE) startet. Standard: 300 Sekunden.')},
    {id:'sleep_off_timeout', label:t('wled_presets.type.sleep_off_timeout', 'Nach Sleep aus in Minuten / Turn off after sleep minutes'), kind:'option_int', arg:'-SLEOFF', value:'0', min:0, max:1440, info:t('wled_presets.info.sleep_off_timeout', 'Minuten im Sleep-Modus bis WLED komplett ausgeschaltet wird. 0 bedeutet: nicht ausschalten, im Sleep-Effekt bleiben.')},
    {id:'brightness', label:t('wled_presets.type.brightness', 'Effekt-Helligkeit / Effect brightness'), kind:'option_int', arg:'-BRI', value:'175', min:1, max:255, info:t('wled_presets.info.brightness', 'Helligkeit für Effekte von 1 bis 255. Niedriger ist oft besser für Autodarts-Kameras.')},
    {id:'duration', label:t('wled_presets.type.duration', 'Globale Dauer / Global duration'), kind:'option_int', arg:'-DU', value:'5', min:0, max:999, info:t('wled_presets.info.duration', 'Globale Effektdauer. Kann zusammen mit -BSS genutzt werden, wenn das Board während Effekten gestoppt wird.')},
    {id:'board_stop_start', label:t('wled_presets.type.board_stop_start', 'Board Stop/Start Delay / Board stop-start delay'), kind:'option_float', arg:'-BSS', value:'0.4', min:0, max:60, step:'0.1', info:t('wled_presets.info.board_stop_start', 'Verzögerung in Sekunden, bevor das Board nach einem gestoppten Effekt wieder startet. Beispiel: 0.4.')},
    {id:'board_stop_after_win', label:t('wled_presets.type.board_stop_after_win', 'Board nach Sieg stoppen / Stop board after win'), kind:'option_bool', arg:'-BSW', value:'1', info:t('wled_presets.info.board_stop_after_win', 'Stoppt das Board nach Leg- oder Matchgewinn.')},

    {id:'dart_multiplier', label:t('wled_presets.type.dart_multiplier', 'DMU: Dart-Multiplikator/Feld'), kind:'advanced', arg:'-DMU', value:'"3=ps|5"', placeholder:'"3=ps|5"', info:t('wled_presets.info.dart_multiplier', 'Spezialargument. Reagiert während des Wurfs auf Single/Double/Triple oder Felder wie t20/d25. Vorsicht: Kann die Autodarts-Erkennung stören, wenn Kameras LED-Licht sehen.')},
    {id:'combo', label:t('wled_presets.type.combo', 'CMB: Dart-Kombination'), kind:'advanced', arg:'-CMB', value:'"t20,t20,t20=ps|4|d:8"', placeholder:'"t20,t20,t20=ps|4|d:8"', info:t('wled_presets.info.combo', 'Spezialargument. Reagiert auf eine Kombination aus drei Darts, Reihenfolge egal. Beispiel: t20,t20,t20.')},
    {id:'player_idle_name', label:t('wled_presets.type.player_idle_name', 'PIDE: Spielername Idle'), kind:'advanced', arg:'-PIDE', value:'"john=ps|10"', placeholder:'"john=ps|10" "jane=ps|11"', info:t('wled_presets.info.player_idle_name', 'Spezialargument. Spielername-spezifischer Idle-/Turn-Effekt. Überschreibt IDE, wenn der Name passt.')}
  ];

  for(let i = 1; i <= 20; i++){
    TYPES.splice(19 + i - 1, 0, {
      id:`single_dart_${i}`,
      label:t(`wled_presets.type.single_dart_${i}`, `Single Dart ${i}`),
      kind:'fixed',
      arg:`-DS${i}`,
      duration:true,
      info:t('wled_presets.info.single_dart', 'Einzeldart-Effekt für den Wert 1 bis 20. Benötigt Single-Dart-Events vom Caller.')
    });
  }

  const state = { rows: [], wepsText: '"Dart-Led1.local"', targets: [], expertMode: false };

  const typeSelect = document.getElementById('typeSelect');
  const rowsEl = document.getElementById('rows');
  const messageEl = document.getElementById('message');
  const previewEl = document.getElementById('preview');
  const mainGrid = document.getElementById('mainGrid');
  const debugBtn = document.getElementById('debugBtn');
  const loadBtn = document.getElementById('loadBtn');
  const saveBtn = document.getElementById('saveBtn');
  const clearBtn = document.getElementById('clearBtn');
  const targetsSelect = document.getElementById('targetsSelect');
  const targetsCards = document.getElementById('targetsCards');
  const targetsHint = document.getElementById('targetsHint');
  const targetsAllBtn = document.getElementById('targetsAllBtn');
  const targetsNoneBtn = document.getElementById('targetsNoneBtn');
  const typeInfoBtn = document.getElementById('typeInfoBtn');
  const expertBtn = document.getElementById('expertBtn');
  const infoModal = document.getElementById('wledInfoModal');
  const infoBody = document.getElementById('wledInfoBody');
  const infoClose = document.getElementById('wledInfoClose');
  const apiLoadUrl = window.WLED_PRESETS_CONFIG?.apiLoadUrl;
  const apiSaveUrl = window.WLED_PRESETS_CONFIG?.apiSaveUrl;
  const apiSendUrl = window.WLED_PRESETS_CONFIG?.apiSendUrl;
  const apiDeleteUrl = window.WLED_PRESETS_CONFIG?.apiDeleteUrl;

  function populateTypeSelect(){
    const current = typeSelect.value;
    typeSelect.innerHTML = '';
    TYPES
      .filter(ti => state.expertMode || ti.kind !== 'advanced')
      .forEach(ti => {
        const opt = document.createElement('option');
        opt.value = ti.id;
        opt.textContent = `${ti.arg ? ti.arg + ' · ' : ''}${ti.label}`;
        typeSelect.appendChild(opt);
      });
    if(current && [...typeSelect.options].some(o => o.value === current)) typeSelect.value = current;
    updateTypeInfo();
  }

  function uid(){ return Math.random().toString(36).slice(2, 10); }

  function clampInt(v, min, max){
    let n = parseInt(v, 10);
    if(Number.isNaN(n)) n = min;
    return Math.max(min, Math.min(max, n));
  }

  function clampFloat(v, min, max){
    let n = parseFloat(String(v).replace(',', '.'));
    if(Number.isNaN(n)) n = min;
    n = Math.max(min, Math.min(max, n));
    return String(Number(n.toFixed(3))).replace(',', '.');
  }

  function isPresetKind(row){
    return ['fixed', 'score_exact', 'score_range', 'unknown', 'dmu_field'].includes(row.kind);
  }

  function setDebugOpen(open){
    mainGrid.classList.toggle('preview-hidden', !open);
    debugBtn.textContent = open
      ? t('wled_presets.hide_debug', '🐞 Debug ausblenden')
      : t('wled_presets.show_debug', '🐞 Debug anzeigen');
  }

  function findType(id){ return TYPES.find(ti => ti.id === id); }

  function updateTypeInfo(){
    // Inline help is intentionally disabled. Use the ⓘ button/modal instead.
  }

  function openInfoModal(){
    if(!infoModal || !infoBody) return;
    const type = findType(typeSelect.value);
    const title = type ? `${type.arg ? type.arg + ' · ' : ''}${type.label}` : t('wled_presets.info_modal_title', 'Argument-Info');
    const info = type ? (type.info || '') : '';
    const usesPreset = type && ['fixed', 'score_exact', 'score_range'].includes(type.kind);
    const presetNote = usesPreset
      ? `<div class="mini-item strong"><code>ps|</code> · ${escapeHtml(t('wled_presets.help.preset_required', 'Dieses Ereignis verwendet ein WLED-Preset. Die Presetnummer muss am WLED-Controller gespeichert sein oder vorher mit „Senden“ gespeichert werden.'))}</div>`
      : '';
    infoBody.innerHTML = `
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(info)}</p>
      <div class="mini-list">
        ${presetNote}
        <div class="mini-item"><code>-SOFF</code> · ${escapeHtml(t('wled_presets.help.soff_short', 'schaltet WLED beim Start/Verbinden von darts-wled aus. Nützlich, um alte Effekte zu löschen.'))}</div>
        <div class="mini-item"><code>-OFF</code> · ${escapeHtml(t('wled_presets.help.off_short', 'schaltet WLED nach dem Match-Ende aus.'))}</div>
        <div class="mini-item"><code>-DMU / -CMB / -PIDE</code> · ${escapeHtml(t('wled_presets.help.advanced_short', 'Spezialargumente für Experten. Sie brauchen eine eigene Syntax und sind deshalb erst über „Spezialargumente anzeigen“ sichtbar.'))}</div>
      </div>`;
    infoModal.hidden = false;
  }

  function closeInfoModal(){
    if(infoModal) infoModal.hidden = true;
  }

  function showMessage(text, kind='info'){
    if(!text){ messageEl.innerHTML=''; return; }
    const cls = kind === 'warn' ? 'warn-box' : 'info';
    messageEl.innerHTML = `<div class="${cls}">${text}</div>`;
  }

  function escapeHtml(v){
    return String(v)
      .replaceAll('&','&amp;')
      .replaceAll('<','&lt;')
      .replaceAll('>','&gt;')
      .replaceAll('"','&quot;')
      .replaceAll("'", '&#39;');
  }

  function hasDuplicateType(typeId){
    return state.rows.some(r => r.typeId === typeId);
  }

  function normalizeRow(raw){
    const base = raw || {};
    // Important: visual DMU rows also use arg='-DMU'.
    // Do NOT match them against the advanced -DMU type, otherwise every visual DMU row
    // becomes the placeholder advanced row (default "3=ps|5").
    const meta = base.kind === 'dmu_field'
      ? {}
      : (TYPES.find(ti => ti.id === base.typeId || ti.arg === base.arg) || {});
    const row = Object.assign({
      id: uid(),
      preset: null,
      typeId: meta.id || 'fixed',
      label: meta.label || t('wled_presets.row.entry', 'Eintrag'),
      kind: meta.kind || 'fixed',
      arg: meta.arg || null,
      duration: meta.duration || false,
      seconds: '',
      score: 180,
      from: 0,
      to: 60,
      value: meta.value || '',
      min: meta.min ?? 0,
      max: meta.max ?? 999,
      step: meta.step || '1',
      placeholder: meta.placeholder || ''
    }, base);
    if(!row.id) row.id = uid();
    if(meta.id) row.typeId = meta.id;
    if(meta.label) row.label = meta.label;
    if(meta.kind) row.kind = meta.kind;
    if(meta.arg) row.arg = meta.arg;
    if(meta.duration !== undefined) row.duration = meta.duration;
    if(meta.min !== undefined) row.min = meta.min;
    if(meta.max !== undefined) row.max = meta.max;
    if(meta.step) row.step = meta.step;
    if(meta.placeholder) row.placeholder = meta.placeholder;

    const preset = clampInt(row.preset, 0, 999);
    row.preset = preset > 0 ? preset : null;

    if(row.kind === 'score_exact'){
      row.score = clampInt(row.score, 0, 180);
      row.duration = true;
    }
    if(row.kind === 'score_range'){
      row.from = clampInt(row.from, 0, 180);
      row.to = clampInt(row.to, 0, 180);
      row.duration = true;
    }
    if(row.kind === 'option_bool'){
      row.value = String(row.value).trim() === '0' ? '0' : '1';
      row.duration = false;
      row.preset = null;
    }
    if(row.kind === 'option_int'){
      row.value = String(clampInt(row.value, Number(row.min || 0), Number(row.max || 999)));
      row.duration = false;
      row.preset = null;
    }
    if(row.kind === 'option_float'){
      row.value = clampFloat(row.value, Number(row.min || 0), Number(row.max || 999));
      row.duration = false;
      row.preset = null;
    }
    if(row.kind === 'advanced'){
      row.duration = false;
      row.preset = null;
      row.value = String(row.value || row.placeholder || '').replace(/[\r\n;]/g, ' ').trim();
    }
    if(row.kind === 'dmu_field'){
      row.arg = '-DMU';
      row.duration = true;
      row.dmuKey = String(row.dmuKey || row.key || '1').trim().toLowerCase();
      if(!/^(?:[123]|[sdt](?:[1-9]|1[0-9]|20)|s25|d25)$/.test(row.dmuKey)) row.dmuKey = '1';
      row.label = row.label || `DMU ${row.dmuKey}`;
    }
    if(row.kind === 'unknown'){
      row.arg = row.arg || '-?';
      row.label = row.label || `${t('wled_presets.unknown', 'Unbekannt')} / ${row.arg}`;
    }
    return row;
  }

  function sortRowsInPlace(){
    state.rows.sort((a, b) => {
      const ag = isPresetKind(a) ? 0 : (String(a.kind || '').startsWith('option_') ? 1 : 2);
      const bg = isPresetKind(b) ? 0 : (String(b.kind || '').startsWith('option_') ? 1 : 2);
      if(ag !== bg) return ag - bg;
      const ap = clampInt(a.preset, 0, 999) || 999999;
      const bp = clampInt(b.preset, 0, 999) || 999999;
      if(ap !== bp) return ap - bp;
      return String(a.arg || a.id || '').localeCompare(String(b.arg || b.id || ''));
    });
  }

  function assignMissingPresets(){
    const used = new Set();
    const missing = [];

    state.rows.forEach((row) => {
      if(!isPresetKind(row)){
        row.preset = null;
        return;
      }
      const preset = clampInt(row.preset, 0, 999);
      if(preset >= 1 && !used.has(preset)){
        row.preset = preset;
        used.add(preset);
      } else {
        row.preset = null;
        missing.push(row);
      }
    });

    // Reuse the first free WLED preset slot, same behavior as the old dropdown.
    // Example: if PS17 was deleted, the next new entry proposes PS17 again.
    let nextPreset = 1;
    missing.forEach((row) => {
      while(used.has(nextPreset)) nextPreset += 1;
      row.preset = nextPreset;
      used.add(nextPreset);
      nextPreset += 1;
    });
  }

  function dmuRowsFromAdvanced(row){
    const value = String(row?.value || '').trim();
    if(row?.kind !== 'advanced' || row?.arg !== '-DMU' || !value) return [];
    const out = [];
    const rx = /"([^"=]+)=ps\|(\d+)(?:\|([^"|]+))?"/g;
    let m;
    while((m = rx.exec(value))){
      const key = String(m[1] || '').trim().toLowerCase();
      if(!/^(?:[123]|[sdt](?:[1-9]|1[0-9]|20)|s25|d25)$/.test(key)) continue;
      out.push(normalizeRow({
        id: uid(), preset: clampInt(m[2], 1, 999), kind:'dmu_field', typeId:`dmu_${key}`,
        label:`DMU ${key}`, arg:'-DMU', duration:true, seconds:String(m[3] || '').trim(), dmuKey:key
      }));
    }
    return out;
  }

  function dedupeDmuRows(){
    const seen = new Map();
    const result = [];
    const putDmu = (row) => {
      const key = String(row.dmuKey || '').trim().toLowerCase();
      if(!/^(?:[123]|[sdt](?:[1-9]|1[0-9]|20)|s25|d25)$/.test(key)) return;
      row.kind = 'dmu_field';
      row.arg = '-DMU';
      row.dmuKey = key;
      row.typeId = `dmu_${key}`;
      row.label = row.label || `DMU ${key}`;
      const old = seen.get(key);
      if(old){
        const oldIdx = result.indexOf(old);
        if(oldIdx >= 0) result.splice(oldIdx, 1);
      }
      seen.set(key, row);
      result.push(row);
    };

    state.rows.forEach((row) => {
      if(row.kind === 'advanced' && row.arg === '-DMU'){
        dmuRowsFromAdvanced(row).forEach(putDmu);
        return;
      }
      if(row.kind === 'dmu_field'){
        putDmu(row);
        return;
      }
      result.push(row);
    });
    state.rows = result;
  }

  function nextFreePreset(){
    const used = state.rows
      .filter(isPresetKind)
      .map(row => clampInt(row.preset, 0, 999))
      .filter(v => v >= 1);
    const usedSet = new Set(used);
    for(let i = 1; i <= 999; i += 1){
      if(!usedSet.has(i)) return i;
    }
    return 999;
  }

  function rowSignature(row){
    if(!row) return '';
    return JSON.stringify({
      typeId: row.typeId || '',
      kind: row.kind || '',
      arg: row.arg || '',
      preset: isPresetKind(row) ? clampInt(row.preset, 0, 999) : null,
      seconds: row.duration ? String(row.seconds || '').trim() : '',
      score: row.kind === 'score_exact' ? clampInt(row.score, 0, 180) : null,
      from: row.kind === 'score_range' ? clampInt(row.from, 0, 180) : null,
      to: row.kind === 'score_range' ? clampInt(row.to, 0, 180) : null,
      value: ['option_bool','option_int','option_float','advanced'].includes(row.kind) ? String(row.value || '').trim() : '',
      dmuKey: row.kind === 'dmu_field' ? String(row.dmuKey || '') : ''
    });
  }

  function rowIsUnsaved(row){
    return rowSignature(row) !== String(row._savedSignature || '');
  }

  function rowNeedsSend(row){
    return isPresetKind(row) && rowSignature(row) !== String(row._sentSignature || '');
  }

  function rowStatus(row){
    if(rowIsUnsaved(row)){
      return {
        className: 'dirty-unsaved',
        label: t('wled_presets.status.unsaved', 'Nicht gespeichert'),
        title: t('wled_presets.status.unsaved_title', 'Diese Zeile wurde geändert und ist noch nicht in start-custom.sh gespeichert.')
      };
    }
    if(rowNeedsSend(row)){
      return {
        className: 'dirty-unsent',
        label: t('wled_presets.status.unsent', 'Noch nicht gesendet'),
        title: t('wled_presets.status.unsent_title', 'Diese Zeile ist gespeichert, aber das zugehörige WLED-Preset wurde in dieser Sitzung noch nicht gesendet/gespeichert.')
      };
    }
    return {
      className: 'clean',
      label: t('wled_presets.status.ok', 'OK'),
      title: t('wled_presets.status.ok_title', 'Diese Zeile ist gespeichert.')
    };
  }

  function updateRowStatusInDom(row){
    const status = rowStatus(row);
    const input = rowsEl.querySelector(`[data-id="${CSS.escape(row.id)}"]`);
    const tr = input ? input.closest('tr') : null;
    if(!tr) return;
    tr.classList.remove('dirty-unsaved', 'dirty-unsent', 'clean');
    tr.classList.add(status.className);
    const badge = tr.querySelector('.row-status');
    if(badge){
      badge.className = `row-status ${status.className}`;
      badge.textContent = status.label;
      badge.title = status.title;
    }
  }

  function setRows(rows, opts = {}){
    const previous = new Map((state.rows || []).map(row => [row.id, row]));
    state.rows = (rows || []).map((raw) => {
      const row = normalizeRow(raw);
      const old = previous.get(row.id);
      if(opts.markSaved){
        row._savedSignature = rowSignature(row);
      } else if(old && old._savedSignature !== undefined){
        row._savedSignature = old._savedSignature;
      }
      if(opts.assumeSent && isPresetKind(row)){
        row._sentSignature = rowSignature(row);
      } else if(old && old._sentSignature !== undefined){
        row._sentSignature = old._sentSignature;
      }
      return row;
    });
    assignMissingPresets();
    sortRowsInPlace();
    if(opts.markSaved){
      state.rows.forEach((row) => { row._savedSignature = rowSignature(row); });
    }
    if(opts.assumeSent){
      state.rows.forEach((row) => { if(isPresetKind(row)) row._sentSignature = rowSignature(row); });
    }
    render();
  }

  function setBusy(button, busy, busyText){
    if(!button) return;
    if(busy){
      button.dataset.originalText = button.textContent;
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.textContent = busyText;
    } else {
      button.disabled = false;
      if(button.dataset.originalHtml){
        button.innerHTML = button.dataset.originalHtml;
      } else if(button.dataset.originalText){
        button.textContent = button.dataset.originalText;
      }
    }
  }

  function normalizeTarget(item, index){
    if(typeof item === 'string'){
      const host = item.trim();
      if(!host) return null;
      return { slot: index + 1, host, label: host, online: true, selected: true };
    }
    if(!item || typeof item !== 'object') return null;
    const slot = Number(item.slot || 0) || (index + 1);
    const host = String(item.host || '').trim();
    if(!host) return null;
    const label = String(item.label || item.name || host || `${t('wled_presets.target.wled', 'WLED')} ${index + 1}`).trim();
    return { slot, host, label, online: item.online !== false, selected: item.online !== false };
  }

  function loadTargetsFromStorage(){
    let raw = null;
    try{
      raw = localStorage.getItem('wledPresetTargets') || localStorage.getItem('reachableWledTargets');
      const arr = JSON.parse(raw || '[]');
      state.targets = Array.isArray(arr) ? arr.map(normalizeTarget).filter(Boolean) : [];
    } catch {
      state.targets = [];
    }
    renderTargets();
  }

  function renderTargets(){
    targetsSelect.innerHTML = '';
    targetsCards.innerHTML = '';

    if(!state.targets.length){
      const opt = document.createElement('option');
      opt.disabled = true;
      opt.textContent = t('wled_presets.no_targets', 'Keine erreichbaren WLEDs von der Hauptseite übernommen');
      targetsSelect.appendChild(opt);
      targetsCards.innerHTML = `<div class="target-empty">${t('wled_presets.no_targets_open_main', 'Keine erreichbaren WLEDs von der Hauptseite übernommen. Öffne die Preset-Seite bitte über die Hauptseite.')}</div>`;
      targetsHint.textContent = t('wled_presets.open_via_main', 'Bitte über die Hauptseite öffnen, damit die WLED-Liste übernommen wird.');
      return;
    }

    state.targets.forEach((target) => {
      const selected = target.selected !== false;

      const opt = document.createElement('option');
      opt.value = target.host;
      opt.textContent = target.label;
      opt.selected = selected;
      targetsSelect.appendChild(opt);

      const card = document.createElement('label');
      card.className = `target-card${selected ? ' active' : ''}${target.online === false ? ' disabled' : ''}`;
      card.dataset.host = target.host;
      card.innerHTML = `
        <input type="checkbox" class="target-check" ${selected ? 'checked' : ''} />
        <div class="target-main">
          <div class="target-title">${escapeHtml(target.label)}</div>
          <div class="target-host">${escapeHtml(target.host)}</div>
          <div class="target-status">${target.online === false ? t('wled_presets.target.offline', 'Nicht erreichbar') : t('wled_presets.target.online', 'Erreichbar')}</div>
        </div>
      `;

      const checkbox = card.querySelector('.target-check');
      checkbox.addEventListener('click', (e) => e.stopPropagation());
      checkbox.addEventListener('change', () => setTargetSelected(target.host, checkbox.checked));
      card.addEventListener('click', () => setTargetSelected(target.host, !selected));
      targetsCards.appendChild(card);
    });

    const onlineCount = state.targets.filter(ti => ti.online !== false).length;
    const selectedCount = state.targets.filter(ti => ti.selected !== false).length;
    targetsHint.textContent = `${selectedCount} ${t('wled_presets.targets_selected', 'ausgewählt')} · ${onlineCount} ${t('wled_presets.targets_online', 'erreichbar')}`;
  }

  function setTargetSelected(host, selected){
    const target = state.targets.find(ti => ti.host === host);
    if(!target) return;
    target.selected = !!selected;
    renderTargets();
  }

  function setAllTargets(selected){
    state.targets = state.targets.map(ti => ({ ...ti, selected: !!selected }));
    renderTargets();
  }

  function getSelectedTargets(){
    return state.targets
      .filter(ti => ti.selected !== false)
      .map(ti => ({ slot: ti.slot || 0, host: ti.host || '', label: ti.label || ti.host || `${t('wled_presets.target.wled', 'WLED')} ${ti.slot || ''}` }))
      .filter(ti => ti.host);
  }

  async function sendPresetToTargets(presetNumber, triggerButton){
    const targets = getSelectedTargets();
    if(!targets.length){
      showMessage(t('wled_presets.select_one_target', 'Bitte mindestens einen WLED-Controller auswählen.'), 'warn');
      return false;
    }

    setBusy(triggerButton, true, '…');
    let ok = 0;
    const failed = [];

    try{
      for(const target of targets){
        try{
          const res = await fetch(apiSendUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preset: presetNumber, slot: target.slot || 0, host: target.host || '' })
          });
          const data = await res.json().catch(() => ({}));
          if(!res.ok || !data.ok) throw new Error(data.msg || `HTTP ${res.status}`);
          ok++;
        } catch(err){
          failed.push(`${target.label || target.host}: ${err && err.message ? err.message : err}`);
        }
      }

      if(!failed.length){
        showMessage(t('wled_presets.preset_saved_on_count', 'Preset {preset} wurde auf {count} WLED-Controller gespeichert.').replace('{preset}', presetNumber).replace('{count}', ok));
        return true;
      } else {
        showMessage(t('wled_presets.preset_saved_partial', 'Preset {preset} gespeichert: {ok} ok, {failed} fehlgeschlagen.<br>{details}').replace('{preset}', presetNumber).replace('{ok}', ok).replace('{failed}', failed.length).replace('{details}', escapeHtml(failed.join(' | '))), 'warn');
        return false;
      }
    } catch(err){
      showMessage(t('wled_presets.send_failed_with_error', 'Senden fehlgeschlagen: {error}').replace('{error}', escapeHtml(err.message || err)), 'warn');
      return false;
    } finally {
      setBusy(triggerButton, false);
    }
  }

  async function deletePresetFromTargets(presetNumber, triggerButton){
    const targets = getSelectedTargets();
    if(!targets.length || !apiDeleteUrl){
      showMessage(t('wled_presets.deleted_local_only', 'Preset {preset} wurde lokal gelöscht. Es war kein WLED-Controller ausgewählt.').replace('{preset}', presetNumber), 'warn');
      return;
    }

    setBusy(triggerButton, true, '…');
    let ok = 0;
    const failed = [];

    try{
      for(const target of targets){
        try{
          const res = await fetch(apiDeleteUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preset: presetNumber, slot: target.slot || 0, host: target.host || '' })
          });
          const data = await res.json().catch(() => ({}));
          if(!res.ok || !data.ok) throw new Error(data.msg || `HTTP ${res.status}`);
          ok++;
        } catch(err){
          failed.push(`${target.label || target.host}: ${err && err.message ? err.message : err}`);
        }
      }

      if(!failed.length){
        showMessage(t('wled_presets.preset_deleted_on_count', 'Preset {preset} wurde auf {count} WLED-Controller gelöscht.').replace('{preset}', presetNumber).replace('{count}', ok));
      } else {
        showMessage(t('wled_presets.preset_deleted_partial', 'Preset {preset} lokal gelöscht: {ok} ok, {failed} fehlgeschlagen.<br>{details}').replace('{preset}', presetNumber).replace('{ok}', ok).replace('{failed}', failed.length).replace('{details}', escapeHtml(failed.join(' | '))), 'warn');
      }
    } finally {
      setBusy(triggerButton, false);
    }
  }

  async function loadCurrentSettings(autoLoad=false){
    try{
      setBusy(loadBtn, true, t('wled_presets.loading', 'Lade…'));
      const res = await fetch(apiLoadUrl, {cache:'no-store'});
      const data = await res.json();
      if(!res.ok || !data.ok) throw new Error(data.msg || t('wled_presets.load_failed', 'Laden fehlgeschlagen.'));
      state.wepsText = data.wepsText || '"Dart-Led1.local"';
      setRows(data.rows || [], { markSaved: true, assumeSent: true });
      if(!autoLoad) showMessage(data.msg || t('wled_presets.loaded_current', 'Aktuelle Einstellungen geladen.'));
    } catch(err){
      showMessage(t('wled_presets.load_current_failed', 'Aktuelle Einstellungen konnten nicht geladen werden: {error}').replace('{error}', escapeHtml(err.message || err)), 'warn');
    } finally {
      setBusy(loadBtn, false);
    }
  }

  async function saveCurrentSettings(opts = {}){
    dedupeDmuRows();
    const silent = opts.silent === true;
    const triggerButton = opts.triggerButton || null;
    assignMissingPresets();
    setBusy(triggerButton, true, t('wled_presets.saving', 'Speichere…'));
    sortRowsInPlace();

    try{
      const res = await fetch(apiSaveUrl, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({rows: state.rows})
      });

      const data = await res.json();
      if(!res.ok || !data.ok) throw new Error(data.msg || t('wled_presets.save_failed', 'Speichern fehlgeschlagen.'));

      state.wepsText = data.wepsText || state.wepsText;
      setRows(data.rows || state.rows, { markSaved: true, assumeSent: false });
      if(!silent) showMessage(data.msg || t('wled_presets.saved', 'Gespeichert.'));
      return true;
    } catch(err){
      showMessage(t('wled_presets.save_failed_with_error', 'Speichern fehlgeschlagen: {error}').replace('{error}', escapeHtml(err.message || err)), 'warn');
      return false;
    } finally {
      setBusy(triggerButton, false);
    }
  }

  function addRow(typeId){
    const type = findType(typeId);
    if(!type) return;

    if(['fixed','option_bool','option_int','option_float','advanced'].includes(type.kind) && hasDuplicateType(type.id)){
      showMessage(t('wled_presets.duplicate_fixed', '„{label}“ ist schon vorhanden. Dieses Argument soll nur einmal vorkommen.').replace('{label}', type.label), 'warn');
      return;
    }

    const row = {
      id: uid(),
      preset: isPresetKind(type) ? nextFreePreset() : null,
      typeId: type.id,
      label: type.label,
      kind: type.kind,
      arg: type.arg || null,
      duration: type.duration || false,
      seconds: type.duration ? '3' : '',
      score: 180,
      from: 0,
      to: 60,
      value: type.value || '',
      min: type.min ?? 0,
      max: type.max ?? 999,
      step: type.step || '1',
      placeholder: type.placeholder || ''
    };

    if(type.id === 'leg') row.seconds = '4';
    if(type.id === 'match') row.seconds = '5';
    if(type.id === 'high_finish') row.seconds = '5';
    if(type.id === 'score0') row.seconds = '3';
    if(type.id === 'checkout') row.seconds = '3';
    if(type.id === 'bull') row.seconds = '3';

    const normalized = normalizeRow(row);
    normalized._savedSignature = '';
    normalized._sentSignature = '';
    state.rows.push(normalized);
    assignMissingPresets();
    sortRowsInPlace();
    showMessage('');
    render();
  }

  async function deleteRowAndSync(id, triggerButton){
    const currentRow = state.rows.find(r => r.id === id);
    if(!currentRow) return;

    const presetNumber = isPresetKind(currentRow) ? clampInt(currentRow.preset, 0, 999) : 0;
    const snapshot = state.rows.map((row) => ({ ...row }));
    state.rows = state.rows.filter(r => r.id !== id);
    assignMissingPresets();
    sortRowsInPlace();

    const saved = await saveCurrentSettings({ silent: true });
    if(!saved){
      state.rows = snapshot;
      render();
      return;
    }

    if(presetNumber >= 1){
      await deletePresetFromTargets(presetNumber, triggerButton);
    } else {
      showMessage(t('wled_presets.deleted', 'Eintrag gelöscht.'));
    }
  }

  function isPresetKind(obj){
    return ['fixed', 'score_exact', 'score_range', 'unknown', 'dmu_field'].includes((obj || {}).kind);
  }

  function rowArgDisplay(row){
    if(row.kind === 'dmu_field') return `-DMU ${row.dmuKey || ''}`;
    if(row.kind === 'fixed' || row.kind === 'unknown' || row.kind === 'option_bool' || row.kind === 'option_int' || row.kind === 'option_float' || row.kind === 'advanced') return row.arg;
    if(row.kind === 'score_exact') return `-S${row.score}`;
    return t('wled_presets.arg_auto', '(auto: -A1 / -A2 / ...)');
  }

  function lineForRow(row, presetNumber, areaIndex){
    if(row.kind === 'dmu_field') return '';
    if(row.kind === 'fixed'){
      const spacing = row.arg.length < 4 ? '   ' : '  ';
      if(row.duration){
        const sec = (row.seconds || '').trim();
        return `  ${row.arg}${spacing}"ps|${presetNumber}${sec ? `|${sec}` : ''}"`;
      }
      return `  ${row.arg}${spacing}"ps|${presetNumber}"`;
    }
    if(row.kind === 'dmu_field'){
      return `<span class="readonly">${escapeHtml(row.dmuKey || '')}</span>`;
    }
    if(row.kind === 'unknown'){
      const arg = row.arg || '-?';
      const spacing = arg.length < 4 ? '   ' : '  ';
      const sec = (row.seconds || '').trim();
      return `  ${arg}${spacing}"ps|${presetNumber}${sec ? `|${sec}` : ''}"`;
    }
    if(row.kind === 'score_exact'){
      const score = clampInt(row.score, 0, 180);
      const sec = (row.seconds || '').trim();
      return `  -S${score} "ps|${presetNumber}${sec ? `|${sec}` : ''}"`;
    }
    if(row.kind === 'score_range'){
      const from = clampInt(row.from, 0, 180);
      const to = clampInt(row.to, 0, 180);
      const sec = (row.seconds || '').trim();
      return `  -A${areaIndex} ${from}-${to} "ps|${presetNumber}${sec ? `|${sec}` : ''}"`;
    }
    if(row.kind === 'option_bool' || row.kind === 'option_int' || row.kind === 'option_float'){
      const spacing = row.arg.length < 4 ? '   ' : '  ';
      return `  ${row.arg}${spacing}${String(row.value || '').trim()}`;
    }
    if(row.kind === 'advanced'){
      const spacing = row.arg.length < 4 ? '   ' : '  ';
      return `  ${row.arg}${spacing}${String(row.value || '').replace(/[\r\n;]/g, ' ').trim()}`;
    }
    return '';
  }

  function buildLines(){
    dedupeDmuRows();
    assignMissingPresets();
    sortRowsInPlace();
    let areaCounter = 0;
    const lines = [];
    const dmuParts = [];
    state.rows.forEach((row) => {
      if(row.kind === 'dmu_field'){
        const key = String(row.dmuKey || '').trim().toLowerCase();
        if(!key || !row.preset) return;
        const sec = row.duration ? String(row.seconds || '').trim() : '';
        dmuParts.push(`"${key}=ps|${row.preset}${sec ? `|${sec}` : ''}"`);
        return;
      }
      let areaIndex = null;
      if(row.kind === 'score_range') areaIndex = ++areaCounter;
      const line = lineForRow(row, row.preset || 0, areaIndex);
      if(line) lines.push(line);
    });
    if(dmuParts.length) lines.push(`  -DMU   ${dmuParts.join(' ')}`);
    return lines;
  }

  function buildPreview(){
    const slash = '\\';
    const header = [
      '#!/usr/bin/env bash',
      'set -e',
      '',
      'cd /var/lib/autodarts/extensions/darts-wled',
      'source .venv/bin/activate',
      '',
      '# ' + t('wled_presets.preview.player_idle', 'Player/Idle (bleibt stehen)'),
      '# ' + t('wled_presets.preview.events_seconds', 'Events (mit Sekunden)'),
      '# ' + t('wled_presets.preview.options', 'Optionen wie -OFF/-SOFF sind keine Presets'),
      '',
      'exec python darts-wled.py ' + slash,
      '  -CON "127.0.0.1:8079" ' + slash,
      '  -WEPS ' + (state.wepsText || '"Dart-Led1.local"') + ' ' + slash
    ];

    const lines = buildLines();
    if(lines.length === 0){
      return header.join('\n') + '\n  # ' + t('wled_presets.preview.no_lines', 'hier würden die Preset-Zeilen stehen');
    }

    const body = lines.map((line, i) => i === lines.length - 1 ? line : (line + ' ' + slash));
    return header.join('\n') + '\n' + body.join('\n');
  }

  function renderExtra(row){
    if(row.kind === 'score_exact'){
      return `<div class="extra">
        <input class="mini" type="number" min="0" max="180" step="1" value="${escapeHtml(row.score)}" data-field="score" data-id="${row.id}" />
        <span class="readonly">${t('wled_presets.range_0_180', '0 bis 180')}</span>
      </div>`;
    }
    if(row.kind === 'score_range'){
      return `<div class="extra">
        <input class="mini" type="number" min="0" max="180" step="1" value="${escapeHtml(row.from)}" data-field="from" data-id="${row.id}" />
        <span class="readonly">${t('wled_presets.to', 'bis')}</span>
        <input class="mini" type="number" min="0" max="180" step="1" value="${escapeHtml(row.to)}" data-field="to" data-id="${row.id}" />
        <span class="readonly">${t('wled_presets.arg_auto_assigned', 'Arg wird automatisch zugeteilt')}</span>
      </div>`;
    }
    if(row.kind === 'option_bool'){
      return `<div class="extra">
        <select data-field="value" data-id="${row.id}">
          <option value="1" ${String(row.value) !== '0' ? 'selected' : ''}>${t('wled_presets.enabled', 'aktiv / 1')}</option>
          <option value="0" ${String(row.value) === '0' ? 'selected' : ''}>${t('wled_presets.disabled', 'aus / 0')}</option>
        </select>
      </div>`;
    }
    if(row.kind === 'option_int' || row.kind === 'option_float'){
      return `<div class="extra">
        <input class="mini" type="number" min="${escapeHtml(row.min ?? 0)}" max="${escapeHtml(row.max ?? 999)}" step="${escapeHtml(row.step || (row.kind === 'option_float' ? '0.1' : '1'))}" value="${escapeHtml(row.value)}" data-field="value" data-id="${row.id}" />
        <span class="readonly">${escapeHtml(row.min ?? 0)}–${escapeHtml(row.max ?? 999)}</span>
      </div>`;
    }
    if(row.kind === 'advanced'){
      return `<div class="extra wide-extra">
        <input class="advanced-input" type="text" value="${escapeHtml(row.value)}" placeholder="${escapeHtml(row.placeholder || '')}" data-field="value" data-id="${row.id}" />
      </div>`;
    }
    if(row.kind === 'dmu_field'){
      return `<span class="readonly">${escapeHtml(row.dmuKey || '')}</span>`;
    }
    if(row.kind === 'unknown'){
      return `<span class="readonly">${t('wled_presets.unknown_arg_taken_directly', 'unbekanntes Argument wird direkt übernommen')}</span>`;
    }
    return `<span class="readonly">${t('wled_presets.no_extra_needed', 'kein Zusatz nötig')}</span>`;
  }

  function render(){
    dedupeDmuRows();
    assignMissingPresets();
    sortRowsInPlace();
    rowsEl.innerHTML = '';

    state.rows.forEach((row) => {
      const tr = document.createElement('tr');
      const status = rowStatus(row);
      tr.classList.add(status.className);
      const presetCell = isPresetKind(row)
        ? `<span class="preset-box">Preset ${escapeHtml(row.preset || '?')}</span>`
        : `<span class="readonly">${t('wled_presets.no_preset', 'kein Preset')}</span>`;
      const secondsCell = row.duration
        ? `<input class="mini" type="number" min="0" step="1" value="${escapeHtml(row.seconds)}" data-field="seconds" data-id="${row.id}" />`
        : `<span class="readonly">${isPresetKind(row) ? t('wled_presets.duration_permanent', 'dauerhaft') : t('wled_presets.config_value', 'Konfiguration')}</span>`;
      const sendButton = isPresetKind(row)
        ? `<button class="secondary" data-action="send" data-id="${row.id}" title="${t('wled_presets.save_wled_preset_title', 'Speichert zuerst die start-custom.sh und sendet/speichert danach den aktuellen WLED-Zustand in diese Presetnummer')}" aria-label="${t('wled_presets.save_wled_preset', 'Speichern/Senden')}"><span aria-hidden="true" style="display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;margin-right:8px;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="display:block"><path d="M3 11.5L21 3L12.5 21L11 13L3 11.5Z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>${t('wled_presets.save_wled_preset', 'Speichern/Senden')}</button>`
        : `<button class="secondary" data-action="save-row" data-id="${row.id}" title="${t('wled_presets.save_entry_title', 'Speichert diesen Eintrag in start-custom.sh. Es wird kein WLED-Preset gesendet.')}" aria-label="${t('wled_presets.save_entry', 'Speichern')}"><span aria-hidden="true" style="display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;margin-right:8px;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="display:block"><path d="M5 20H19" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M6 4H16L18 6V20H6V4Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 4V10H15V4" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 15H15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></span>${t('wled_presets.save_entry', 'Speichern')}</button>`;

      tr.innerHTML = `
        <td>${presetCell}</td>
        <td>
          <div class="badge">${escapeHtml(rowArgDisplay(row))}</div>
          <small>${escapeHtml(row.label)}</small>
          <span class="row-status ${escapeHtml(status.className)}" title="${escapeHtml(status.title)}">${escapeHtml(status.label)}</span>
        </td>
        <td>${secondsCell}</td>
        <td>${renderExtra(row)}</td>
        <td>
          <div class="row-actions">
            ${sendButton}
            <button class="danger" data-action="remove" data-id="${row.id}" title="${t('wled_presets.delete', 'Löschen')}" aria-label="${t('wled_presets.delete', 'Löschen')}">
              <span aria-hidden="true" style="display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;margin-right:8px;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="display:block"><path d="M4 7H20" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M9.5 3H14.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M8 7V18C8 19.1046 8.89543 20 10 20H14C15.1046 20 16 19.1046 16 18V7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M10.5 10.5V16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M13.5 10.5V16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></span>${t('wled_presets.delete', 'Löschen')}</button>
          </div>
        </td>
      `;
      rowsEl.appendChild(tr);
    });

    previewEl.value = buildPreview();
    if(typeof visualRenderAll === 'function') visualRenderAll();
    validate();
  }



  const visualIcons = {
    player:`<svg viewBox="0 0 64 64"><circle cx="32" cy="20" r="11"/><path d="M13 56c4-18 34-18 38 0"/><path d="M21 56h22"/></svg>`,
    busted:`<svg viewBox="0 0 64 64"><path d="M16 48l12-20 8 9 12-21"/><path d="M14 16l36 36"/><path d="M45 14l5 2-2 5"/><path d="M19 51l-5-2 2-5"/></svg>`,
    leg:`<svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="18"/><circle cx="32" cy="32" r="7"/><path d="M32 6v12M32 46v12M6 32h12M46 32h12"/><path d="M46 18l-9 9"/></svg>`,
    match:`<svg viewBox="0 0 64 64"><path d="M21 12h22v10a11 11 0 0 1-22 0z"/><path d="M21 17H9c1 13 7 19 16 20"/><path d="M43 17h12c-1 13-7 19-16 20"/><path d="M32 33v14"/><path d="M22 56h20"/><path d="M32 18l3 6 6 1-4 5 1 7-6-3-6 3 1-7-4-5 6-1z"/></svg>`,
    high:`<svg viewBox="0 0 64 64"><path d="M17 49c-2-10 7-15 10-23 5 6 4 11 4 11 4-4 6-11 6-18 9 8 14 18 10 30-3 8-10 11-15 11s-12-3-15-11z"/><path d="M31 53c-4-5 2-10 3-15 5 5 7 10 3 15"/></svg>`,
    calibration:`<svg viewBox="0 0 64 64"><path d="M14 20v-6h10"/><path d="M40 14h10v6"/><path d="M50 44v6H40"/><path d="M24 50H14v-6"/><rect x="20" y="24" width="24" height="18" rx="4"/><circle cx="32" cy="33" r="6"/><path d="M28 24l3-5h8l3 5"/></svg>`,
    sleep:`<svg viewBox="0 0 64 64"><path d="M45 45A22 22 0 0 1 19 17 24 24 0 1 0 45 45z"/><path d="M40 14h13l-13 13h13"/><path d="M48 35h8l-8 8h8"/></svg>`,
    boardstop:`<svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="23"/><circle cx="32" cy="32" r="9"/><path d="M24 22v20"/><path d="M40 22v20"/><path d="M32 9v46M9 32h46"/><path d="M16 16l32 32M48 16L16 48"/></svg>`,
    joined:`<svg viewBox="0 0 64 64"><circle cx="26" cy="22" r="10"/><path d="M9 56c4-17 30-17 34 0"/><path d="M48 24v18"/><path d="M39 33h18"/></svg>`,
    left:`<svg viewBox="0 0 64 64"><circle cx="26" cy="22" r="10"/><path d="M9 56c4-17 30-17 34 0"/><path d="M39 33h18"/></svg>`,
    off:`<svg viewBox="0 0 64 64"><path d="M32 9v24"/><path d="M20 17a24 24 0 1 0 24 0"/></svg>`,
    soff:`<svg viewBox="0 0 64 64"><path d="M32 10v18"/><path d="M21 18a21 21 0 1 0 22 0"/><path d="M48 46A17 17 0 0 1 31 25a18 18 0 1 0 17 21z"/></svg>`,
    checkout:`<svg viewBox="0 0 64 64"><path d="M14 34l11 11 25-27"/><path d="M12 52h40"/><path d="M22 17h20"/></svg>`
  };
  const visualColors = ["#28d8ff","#6ee7f9","#65ff4f","#ffd34e","#ff9c45","#ff4e6a"];
  const visualSelection = [];
  let visualModalCtx = null;

  function visualNormalizeDmuKey(key){
    const k = String(key || '').trim().toLowerCase();
    if(['1','s','single','singles','alle singles','all singles'].includes(k)) return '1';
    if(['2','d','double','doubles','doppel','alle doppel','alle doubles','all doubles'].includes(k)) return '2';
    if(['3','t','triple','triples','trippel','alle triple','alle triples','all triples'].includes(k)) return '3';
    return /^(?:[123]|[sdt](?:[1-9]|1[0-9]|20)|s25|d25)$/.test(k) ? k : '';
  }
  function visualBulkKey(btn){
    if(!btn) return '';
    const raw = btn.getAttribute('data-dmu-key') || btn.dataset?.dmuKey || btn.getAttribute('data-bulk') || btn.dataset?.bulk || '';
    const key = visualNormalizeDmuKey(raw);
    if(key) return key;
    const txt = String(btn.textContent || btn.getAttribute('data-key-label') || '').toLowerCase();
    if(txt.includes('single')) return '1';
    if(txt.includes('doppel') || txt.includes('double')) return '2';
    if(txt.includes('triple') || txt.includes('trippel')) return '3';
    return '';
  }
  function visualFieldToDmu(field){
    if(field === 'BULL') return 'd25';
    if(field === 'SBULL') return 's25';
    return visualNormalizeDmuKey(String(field || '').toLowerCase());
  }
  function visualDmuToField(key){
    key = String(key || '').toUpperCase();
    if(key === 'D25') return 'BULL';
    if(key === 'S25') return 'SBULL';
    if(['1','2','3'].includes(String(key))) return '';
    return key;
  }
  function visualDmuKeyLabel(key){
    key = visualNormalizeDmuKey(key);
    if(key === '1') return t('wled_presets.visual.all_singles', 'Alle Singles');
    if(key === '2') return t('wled_presets.visual.all_doubles', 'Alle Doppel');
    if(key === '3') return t('wled_presets.visual.all_triples', 'Alle Triple');
    if(key === 's25') return t('wled_presets.visual.single_bull', 'Single Bull / s25');
    if(key === 'd25') return t('wled_presets.visual.bullseye', 'Bullseye / d25');
    if(/^s\d+$/.test(key)) return t('wled_presets.visual.single_field', 'Single {n}').replace('{n}', key.slice(1));
    if(/^d\d+$/.test(key)) return t('wled_presets.visual.double_field', 'Double {n}').replace('{n}', key.slice(1));
    if(/^t\d+$/.test(key)) return t('wled_presets.visual.triple_field', 'Triple {n}').replace('{n}', key.slice(1));
    return t('wled_presets.visual.dmu_key', 'DMU {key}').replace('{key}', key);
  }
  function visualFieldScore(field){
    if(field === 'BULL') return 50;
    if(field === 'SBULL') return 25;
    const p = field.charAt(0);
    const n = parseInt(field.slice(1), 10);
    if(!Number.isFinite(n)) return 0;
    if(p === 'D') return n * 2;
    if(p === 'T') return n * 3;
    return n;
  }
  function visualFindDmuRow(key){ const k = visualNormalizeDmuKey(key); return k ? state.rows.find(r => r.kind === 'dmu_field' && visualNormalizeDmuKey(r.dmuKey) === k) : null; }
  function visualFindScoreRow(score){ return state.rows.find(r => r.kind === 'score_exact' && clampInt(r.score, 0, 180) === clampInt(score, 0, 180)); }
  function visualFindRangeRow(from, to){ return state.rows.find(r => r.kind === 'score_range' && clampInt(r.from,0,180) === clampInt(from,0,180) && clampInt(r.to,0,180) === clampInt(to,0,180)); }
  function visualFindTypeRow(typeId){ return state.rows.find(r => r.typeId === typeId); }
  function visualTargetFromSelection(){
    if(visualSelection.length === 1){
      const field = visualSelection[0];
      const key = visualFieldToDmu(field);
      if(!key) return null;
      return {kind:'dmu_field', dmuKey:key, title:visualDmuKeyLabel(key), label:visualDmuKeyLabel(key), existing: visualFindDmuRow(key)};
    }
    if(visualSelection.length > 1){
      const total = visualSelection.reduce((sum, f) => sum + visualFieldScore(f), 0);
      return {kind:'score_exact', score:total, title:`${visualSelection.join(' + ')} = ${total}`, label:t('wled_presets.visual.score_label', 'Score {score}').replace('{score}', total), existing: visualFindScoreRow(total)};
    }
    return null;
  }
  function visualCreateRowFromContext(ctx){
    if(ctx.kind === 'fixed' || String(ctx.kind || '').startsWith('option_')){
      const type = findType(ctx.typeId);
      if(!type) return null;
      return normalizeRow({
        id: uid(), preset: isPresetKind(type) ? nextFreePreset() : null, typeId: type.id, label: type.label,
        kind: type.kind, arg: type.arg || null, duration: type.duration || false, seconds: type.duration ? '3' : '',
        value: type.value || '', min: type.min ?? 0, max: type.max ?? 999, step: type.step || '1'
      });
    }
    if(ctx.kind === 'dmu_field'){
      const key = visualNormalizeDmuKey(ctx.dmuKey);
      if(!key) return null;
      return normalizeRow({id: uid(), preset: nextFreePreset(), kind:'dmu_field', typeId:`dmu_${key}`, label:ctx.label || visualDmuKeyLabel(key), arg:'-DMU', duration:true, seconds:'3', dmuKey:key});
    }
    if(ctx.kind === 'score_exact'){
      return normalizeRow({id: uid(), preset: nextFreePreset(), kind:'score_exact', typeId:'score_exact', label:t('wled_presets.visual.score_label', 'Score {score}').replace('{score}', ctx.score), arg:`-S${ctx.score}`, duration:true, seconds:'3', score:ctx.score});
    }
    if(ctx.kind === 'score_range'){
      const from = clampInt(ctx.from ?? 0, 0, 180);
      const to = clampInt(ctx.to ?? 60, 0, 180);
      return normalizeRow({id: uid(), preset: nextFreePreset(), kind:'score_range', typeId:'score_range', label:t('wled_presets.visual.range_label', 'Bereich {from}-{to}').replace('{from}', from).replace('{to}', to), arg:null, duration:true, seconds:'3', from, to});
    }
    return null;
  }
  function visualContextNeedsSeconds(ctx, row){
    if(!ctx) return false;
    if(String(ctx.kind || '').startsWith('option_')) return false;
    if(ctx.kind === 'fixed'){
      const type = findType(ctx.typeId);
      return !!(type && type.duration);
    }
    if(row && row.duration === false) return false;
    return ctx.kind === 'dmu_field' || ctx.kind === 'score_exact' || ctx.kind === 'score_range';
  }

  function visualOpenConfig(ctx){
    if(!ctx) return;
    const modal = document.getElementById('visualConfigModal');
    if(!modal) return;
    const row = ctx.existing || null;
    if(ctx.kind === 'dmu_field') {
      ctx.dmuKey = visualNormalizeDmuKey(ctx.dmuKey);
      ctx.label = ctx.label || visualDmuKeyLabel(ctx.dmuKey);
      ctx.title = ctx.title || visualDmuKeyLabel(ctx.dmuKey);
    }
    visualModalCtx = {...ctx};
    if(ctx.kind === 'dmu_field') modal.dataset.dmuKey = ctx.dmuKey || '';
    else delete modal.dataset.dmuKey;
    document.getElementById('visualConfigTitle').textContent = ctx.title || t('wled_presets.visual.led_define', 'LED definieren');
    document.getElementById('visualConfigSummary').textContent = ctx.summary || ctx.label || ctx.title || '';
    const presetInput = document.getElementById('visualConfigPreset');
    const secondsInput = document.getElementById('visualConfigSeconds');
    if(presetInput){
      presetInput.readOnly = true;
      presetInput.setAttribute('readonly', 'readonly');
      presetInput.classList.add('locked-preset-input');
      presetInput.title = t('wled_presets.visual.preset_auto_locked', 'Presetnummer wird automatisch vergeben.');
    }
    const secondsWrap = document.getElementById('visualConfigSecondsWrap');
    const valueWrap = document.getElementById('visualConfigValueWrap');
    const rangeWrap = document.getElementById('visualConfigRangeWrap');
    const rangeFrom = document.getElementById('visualConfigRangeFrom');
    const rangeTo = document.getElementById('visualConfigRangeTo');
    const valueInput = document.getElementById('visualConfigValue');
    const sendBtn = document.getElementById('visualConfigSend');
    const saveBtn = document.getElementById('visualConfigSave');
    const deleteBtn = document.getElementById('visualConfigDelete');
    const isOption = String(ctx.kind || '').startsWith('option_');
    const isRange = ctx.kind === 'score_range';
    const needsSeconds = visualContextNeedsSeconds(ctx, row);
    const setVisualHidden = (el, hide) => {
      if(!el) return;
      el.hidden = !!hide;
      el.style.display = hide ? 'none' : '';
      el.setAttribute('aria-hidden', hide ? 'true' : 'false');
    };
    setVisualHidden(presetInput.closest('label'), isOption);
    setVisualHidden(secondsWrap, !needsSeconds);
    setVisualHidden(valueWrap, !isOption);
    setVisualHidden(rangeWrap, !isRange);
    if(isOption){
      const type = findType(ctx.typeId);
      valueInput.value = row ? String(row.value || '1') : String(type?.value || '1');
      if(secondsInput){ secondsInput.disabled = true; secondsInput.tabIndex = -1; secondsInput.value = ''; }
      sendBtn.hidden = true;
      saveBtn.hidden = false;
    }else{
      presetInput.value = row && row.preset ? row.preset : nextFreePreset();
      secondsInput.value = needsSeconds ? (row ? String(row.seconds || '') : '3') : '';
      secondsInput.disabled = !needsSeconds;
      secondsInput.tabIndex = needsSeconds ? 0 : -1;
      sendBtn.hidden = false;
      saveBtn.hidden = true;
      if(isRange){
        if(rangeFrom) rangeFrom.value = row ? clampInt(row.from,0,180) : clampInt(ctx.from ?? 0,0,180);
        if(rangeTo) rangeTo.value = row ? clampInt(row.to,0,180) : clampInt(ctx.to ?? 60,0,180);
      }
    }
    deleteBtn.hidden = !row;
    modal.hidden = false;
  }
  function visualCloseConfig(){ const m=document.getElementById('visualConfigModal'); if(m) m.hidden=true; visualModalCtx=null; }
  async function visualSaveConfig(send){
    const ctx = visualModalCtx; if(!ctx) return;
    let row = ctx.existing || null;
    if(ctx.kind === 'score_range'){
      const from = clampInt(document.getElementById('visualConfigRangeFrom')?.value ?? ctx.from ?? 0, 0, 180);
      const to = clampInt(document.getElementById('visualConfigRangeTo')?.value ?? ctx.to ?? 60, 0, 180);
      if(!row){ row = visualFindRangeRow(from, to) || visualCreateRowFromContext({...ctx, from, to}); if(!row) return; state.rows.push(row); }
      row.kind = 'score_range'; row.typeId = 'score_range'; row.from = from; row.to = to; row.label = t('wled_presets.visual.range_label', 'Bereich {from}-{to}').replace('{from}', from).replace('{to}', to); row.duration = true;
    }else if(!row){
      row = visualCreateRowFromContext(ctx); if(!row) return; state.rows.push(row);
    }
    if(ctx.kind === 'dmu_field'){
      // DMU has many valid keys: 1/2/3 for all singles/doubles/triples and exact fields like s20/d20/t20/s25/d25.
      // Always use the locked key from the clicked button/board field; never infer it from another input.
      const key = visualNormalizeDmuKey((visualModalCtx && visualModalCtx.dmuKey) || ctx.dmuKey || document.getElementById('visualConfigModal')?.dataset?.dmuKey);
      if(!key){ showMessage(t('wled_presets.visual.invalid_dmu_key', 'DMU-Key fehlt oder ist ungültig.'), 'warn'); return; }
      row.kind = 'dmu_field'; row.arg = '-DMU'; row.dmuKey = key; row.typeId = `dmu_${key}`; row.label = ctx.label || visualDmuKeyLabel(key); row.duration = true;
    }
    if(String(ctx.kind || '').startsWith('option_')){
      row.value = document.getElementById('visualConfigValue').value;
    }else{
      row.preset = clampInt(document.getElementById('visualConfigPreset').value, 1, 999);
      const needsSeconds = visualContextNeedsSeconds(ctx, row);
      row.duration = !!needsSeconds;
      row.seconds = needsSeconds ? String(document.getElementById('visualConfigSeconds').value || '').trim() : '';
    }
    const trigger = send ? document.getElementById('visualConfigSend') : document.getElementById('visualConfigSave');
    const saved = await saveCurrentSettings({silent:true, triggerButton:trigger});
    if(!saved) return;
    const current = state.rows.find(r => r.id === row.id) || row;
    if(send && isPresetKind(current)){
      const sent = await sendPresetToTargets(current.preset, trigger);
      if(sent){ current._savedSignature = rowSignature(current); current._sentSignature = rowSignature(current); }
    }else{
      showMessage(t('wled_presets.saved', 'Gespeichert.'));
    }
    visualCloseConfig(); render();
  }
  async function visualDeleteConfig(){
    const ctx = visualModalCtx; if(!ctx || !ctx.existing) return;
    await deleteRowAndSync(ctx.existing.id, document.getElementById('visualConfigDelete'));
    visualCloseConfig(); render();
  }
  function visualEl(n){return document.createElementNS('http://www.w3.org/2000/svg', n);}
  function visualPolar(r,d){const a=(d-90)*Math.PI/180;return [r*Math.cos(a),r*Math.sin(a)];}
  function visualPath(r1,r2,a1,a2){
    const [x1,y1]=visualPolar(r2,a1),[x2,y2]=visualPolar(r2,a2),[x3,y3]=visualPolar(r1,a2),[x4,y4]=visualPolar(r1,a1);
    return `M${x1} ${y1} A${r2} ${r2} 0 0 1 ${x2} ${y2} L${x3} ${y3} A${r1} ${r1} 0 0 0 ${x4} ${y4}Z`;
  }
  function visualBuildBoard(){
    const root = document.getElementById('visualDartboard'); if(!root || root.dataset.ready) return;
    root.dataset.ready = '1';
    const nums=[20,1,18,4,13,6,10,15,2,17,3,19,7,16,8,11,14,9,12,5];
    const svg=visualEl('svg'); svg.setAttribute('viewBox','-200 -200 400 400');
    const defs=visualEl('defs'); const grad=visualEl('linearGradient'); grad.id='visualRainbow'; grad.setAttribute('x1','0%'); grad.setAttribute('x2','100%');
    [['0%','#29d8ff'],['25%','#884dff'],['50%','#ff405c'],['75%','#ffd845'],['100%','#65ff4f']].forEach(s=>{const stop=visualEl('stop'); stop.setAttribute('offset',s[0]); stop.setAttribute('stop-color',s[1]); grad.appendChild(stop);});
    defs.appendChild(grad); svg.appendChild(defs);
    const bg=visualEl('circle'); bg.setAttribute('r','188'); bg.classList.add('board-bg'); svg.appendChild(bg);
    const ring=visualEl('circle'); ring.setAttribute('r','186'); ring.setAttribute('stroke','url(#visualRainbow)'); ring.classList.add('rainbow-ring'); svg.appendChild(ring);
    const rings=[{p:'D',r1:150,r2:170,t:'double'},{p:'S',r1:100,r2:150,t:'single'},{p:'T',r1:80,r2:100,t:'triple'},{p:'S',r1:18,r2:80,t:'single'}];
    for(let i=0;i<20;i++){
      const n=nums[i], a1=i*18-9, a2=i*18+9;
      for(const r of rings){
        const path=visualEl('path'); path.setAttribute('d',visualPath(r.r1,r.r2,a1,a2)); path.dataset.field=r.p+n; path.dataset.cx=visualPolar((r.r1+r.r2)/2,i*18)[0]; path.dataset.cy=visualPolar((r.r1+r.r2)/2,i*18)[1];
        path.classList.add('dart-field',r.t); if(r.t==='single') path.classList.add(i%2===0?'dark':'light'); else path.classList.add(i%2===0?'red':'green'); svg.appendChild(path);
      }
      const [tx,ty]=visualPolar(181,i*18); const text=visualEl('text'); text.setAttribute('x',tx); text.setAttribute('y',ty); text.classList.add('board-num'); text.textContent=n; svg.appendChild(text);
    }
    const ob=visualEl('circle'); ob.setAttribute('r','18'); ob.dataset.field='SBULL'; ob.dataset.cx='0'; ob.dataset.cy='13'; ob.classList.add('outer-bull'); svg.appendChild(ob);
    const bull=visualEl('circle'); bull.setAttribute('r','8'); bull.dataset.field='BULL'; bull.dataset.cx='0'; bull.dataset.cy='0'; bull.classList.add('bull'); svg.appendChild(bull);
    root.innerHTML=''; root.appendChild(svg);
    svg.addEventListener('click', (e) => {
      const marker = e.target.closest && e.target.closest('.visual-marker');
      if(marker){ const key = visualNormalizeDmuKey(marker.dataset.dmuKey); const row = visualFindDmuRow(key); visualOpenConfig({kind:'dmu_field', dmuKey:key, title:visualDmuKeyLabel(key), label:visualDmuKeyLabel(key), existing:row}); return; }
      const fieldEl = e.target.closest && e.target.closest('[data-field]');
      if(!fieldEl) return;
      visualAddFieldSelection(fieldEl.dataset.field);
    });
  }
  function visualAddFieldSelection(field){
    if(visualSelection.length >= 3){ visualSelection.length = 0; return visualRenderSelection(); }
    visualSelection.push(field);
    visualRenderSelection();
  }
  function visualRenderSelection(){
    const counts = new Map(); visualSelection.forEach(f => counts.set(f, (counts.get(f)||0)+1));
    document.querySelectorAll('#visualDartboard [data-field]').forEach(el => {
      el.classList.remove('visual-select-1','visual-select-2','visual-select-3');
      const c = counts.get(el.dataset.field) || 0;
      if(c === 1) el.classList.add('visual-select-1');
      if(c === 2) el.classList.add('visual-select-2');
      if(c >= 3) el.classList.add('visual-select-3');
    });
    const target = visualTargetFromSelection();
    const combo = document.getElementById('visualDartComboValue'); if(combo) combo.textContent = target ? target.title : '-';
  }
  function visualAddTile(parent, cfg){
    if(!parent) return;
    const row = cfg.typeId ? visualFindTypeRow(cfg.typeId) : null;
    const b=document.createElement('button'); b.type='button'; b.className='visual-tile'; if(row) b.classList.add('has-preset'); b.style.setProperty('--tile-color', cfg.color || '#39d9ff');
    b.innerHTML = `${visualIcons[cfg.icon] || ''}${cfg.num ? `<div class="num">${cfg.num}</div>` : `<div class="label">${escapeHtml(cfg.label)}</div>`}${row && row.preset ? `<span class="visual-ps-badge">PS ${row.preset}</span>` : ''}`;
    b.addEventListener('click', () => { visualOpenConfig({kind:cfg.kind, typeId:cfg.typeId, title:cfg.label, label:cfg.label, existing:row}); });
    parent.appendChild(b);
  }
  function visualRenderTiles(){
    const players=document.getElementById('visualPlayers'), events=document.getElementById('visualEvents'), system=document.getElementById('visualSystem');
    if(!players || !events || !system) return;
    players.innerHTML=''; events.innerHTML=''; system.innerHTML='';
    for(let i=1;i<=6;i++) visualAddTile(players,{icon:'player',num:i,label:t('wled_presets.visual.player_n', 'Spieler {n}').replace('{n}', i),typeId:`player${i}`,kind:'fixed',color:visualColors[i-1]});
    [
      {icon:'busted',label:t('wled_presets.visual.busted', 'Busted'),typeId:'busted',kind:'fixed',color:'#ff405c'},
      {icon:'leg',label:t('wled_presets.visual.leg_won', 'Leg won'),typeId:'leg',kind:'fixed',color:'#65ff4f'},
      {icon:'match',label:t('wled_presets.visual.match_won', 'Match won'),typeId:'match',kind:'fixed',color:'#ffc845'},
      {icon:'high',label:t('wled_presets.visual.high_finish', 'High finish'),typeId:'high_finish',kind:'fixed',color:'#ff7045'},
      {icon:'calibration',label:t('wled_presets.visual.calibration', 'Calibration'),typeId:'calibration',kind:'fixed',color:'#39d9ff'},
      {icon:'checkout',label:t('wled_presets.visual.checkout', 'Checkout'),typeId:'checkout',kind:'fixed',color:'#64ff4f'}
    ].forEach(x=>visualAddTile(events,x));
    [
      {icon:'sleep',label:t('wled_presets.visual.sleep', 'Sleep'),typeId:'sleep_effect',kind:'fixed',color:'#9b5cff'},
      {icon:'boardstop',label:t('wled_presets.visual.board_stopped', 'Board stopped'),typeId:'board_stop_effect',kind:'fixed',color:'#ffa640'},
      {icon:'joined',label:t('wled_presets.visual.player_joined', 'Player joined'),typeId:'player_joined',kind:'fixed',color:'#65ff4f'},
      {icon:'left',label:t('wled_presets.visual.player_left', 'Player left'),typeId:'player_left',kind:'fixed',color:'#ff405c'},
      {icon:'off',label:t('wled_presets.visual.off', 'OFF'),typeId:'wled_off',kind:'option_bool',color:'#ff3131'},
      {icon:'soff',label:t('wled_presets.visual.soff', 'SOFF'),typeId:'wled_off_at_start',kind:'option_bool',color:'#1e93ff'}
    ].forEach(x=>visualAddTile(system,x));
  }
  function visualRenderMarkers(){
    document.querySelectorAll('#visualDartboard .visual-marker').forEach(x=>x.remove());
    document.querySelectorAll('#visualDartboard [data-field]').forEach(el => el.classList.remove('visual-has-preset'));
    const svg = document.querySelector('#visualDartboard svg'); if(!svg) return;
    state.rows.filter(r => r.kind === 'dmu_field').forEach(row => {
      const key = visualNormalizeDmuKey(row.dmuKey);
      if(!key || /^[123]$/.test(key)) return;
      const field = visualDmuToField(key);
      const el = document.querySelector(`#visualDartboard [data-field="${CSS.escape(field)}"]`);
      if(!el) return;
      el.classList.add('visual-has-preset');
      const x = parseFloat(el.dataset.cx || '0'), y = parseFloat(el.dataset.cy || '0');
      const g = visualEl('g'); g.classList.add('visual-marker'); g.dataset.dmuKey = key; g.setAttribute('transform',`translate(${x} ${y})`);
      const c = visualEl('circle'); c.setAttribute('r','10'); const txt=visualEl('text'); txt.textContent = String(row.preset || '?');
      g.appendChild(c); g.appendChild(txt); svg.appendChild(g);
    });
    document.querySelectorAll('.visual-bulk').forEach(btn => {
      const key = visualBulkKey(btn);
      const row = visualFindDmuRow(key);
      btn.classList.toggle('has-preset', !!row);
      btn.classList.toggle('active', !!row);
      const baseLabel = visualDmuKeyLabel(key) || btn.getAttribute('data-key-label') || btn.textContent.replace(/ · PS .*/, '');
      btn.textContent = baseLabel + (row ? ` · PS ${row.preset}` : '');
    });
  }
  function visualRenderScores(){
    const list = document.getElementById('visualScoreList'); if(!list) return; list.innerHTML='';
    const scores = state.rows.filter(r => r.kind === 'score_exact').sort((a,b)=>clampInt(a.score,0,180)-clampInt(b.score,0,180));
    const ranges = state.rows.filter(r => r.kind === 'score_range').sort((a,b)=>clampInt(a.from,0,180)-clampInt(b.from,0,180));
    if(!scores.length && !ranges.length){ list.innerHTML = `<div class="visual-score-empty">${escapeHtml(t('wled_presets.visual.no_scores', 'Noch keine Gesamtsummen oder Bereiche gespeichert.'))}</div>`; return; }
    scores.forEach(row => { const b=document.createElement('button'); b.type='button'; b.className='visual-score-btn'; b.innerHTML=`${escapeHtml(row.score)} · <b>PS ${escapeHtml(row.preset)}</b>`; b.addEventListener('click',()=>visualOpenConfig({kind:'score_exact', score:row.score, title:`Score ${row.score}`, label:`Score ${row.score}`, existing:row})); list.appendChild(b); });
    ranges.forEach(row => { const b=document.createElement('button'); b.type='button'; b.className='visual-score-btn'; b.innerHTML=`${escapeHtml(row.from)}-${escapeHtml(row.to)} · <b>PS ${escapeHtml(row.preset)}</b>`; const title=t('wled_presets.visual.range_label', 'Bereich {from}-{to}').replace('{from}', row.from).replace('{to}', row.to); b.addEventListener('click',()=>visualOpenConfig({kind:'score_range', from:row.from, to:row.to, title, label:title, existing:row})); list.appendChild(b); });
  }
  function visualRenderAll(){ visualBuildBoard(); visualRenderTiles(); visualRenderMarkers(); visualRenderScores(); visualRenderSelection(); }
  function visualInit(){
    visualBuildBoard(); visualRenderAll();
    const led=document.getElementById('visualLedDefineBtn'); if(led && !led.dataset.bound){ led.dataset.bound='1'; led.addEventListener('click',()=>{ const ctx=visualTargetFromSelection(); if(!ctx){ showMessage(t('wled_presets.visual.select_field_first','Bitte zuerst ein Feld oder bis zu 3 Darts auswählen.'),'warn'); return; } visualOpenConfig(ctx); }); }
    document.querySelectorAll('.visual-bulk').forEach(btn => { if(btn.dataset.bound) return; btn.dataset.bound='1'; btn.addEventListener('click',()=>{
      const key = visualBulkKey(btn);
      if(!/^[123]$/.test(key)){ showMessage(t('wled_presets.visual.invalid_dmu_key', 'DMU-Key fehlt oder ist ungültig.'), 'warn'); return; }
      const row=visualFindDmuRow(key);
      const label = visualDmuKeyLabel(key);
      visualOpenConfig({kind:'dmu_field', dmuKey:key, title:label, label:label, existing:row});
    }); });
    const close=document.getElementById('visualConfigClose'); if(close && !close.dataset.bound){ close.dataset.bound='1'; close.addEventListener('click', visualCloseConfig); }
    const modal=document.getElementById('visualConfigModal'); if(modal && !modal.dataset.bound){ modal.dataset.bound='1'; modal.addEventListener('click', e => { if(e.target === modal) visualCloseConfig(); }); }
    const save=document.getElementById('visualConfigSave'); if(save && !save.dataset.bound){ save.dataset.bound='1'; save.addEventListener('click',()=>visualSaveConfig(false)); }
    const send=document.getElementById('visualConfigSend'); if(send && !send.dataset.bound){ send.dataset.bound='1'; send.addEventListener('click',()=>visualSaveConfig(true)); }
    const del=document.getElementById('visualConfigDelete'); if(del && !del.dataset.bound){ del.dataset.bound='1'; del.addEventListener('click', visualDeleteConfig); }
    const range=document.getElementById('visualRangeHintBtn'); if(range && !range.dataset.bound){ range.dataset.bound='1'; range.addEventListener('click',()=>{
      visualOpenConfig({kind:'score_range', from:0, to:60, title:t('wled_presets.visual.range_from_to', 'Bereich von-bis'), label:t('wled_presets.type.score_range', 'Score-Bereich')});
    }); }
    const legacy=document.getElementById('visualLegacyToggleBtn'); if(legacy && !legacy.dataset.bound){ legacy.dataset.bound='1'; legacy.addEventListener('click',()=>{ document.body.classList.toggle('legacy-open'); legacy.textContent = document.body.classList.contains('legacy-open') ? t('wled_presets.visual.legacy_hide', 'Erweiterte Liste ausblenden') : t('wled_presets.visual.legacy_show', 'Erweiterte Liste anzeigen'); }); }
  }

  function validate(){
    const warnings = [];
    const exactSeen = new Map();
    const typeSeen = new Set();
    let areaCount = 0;

    state.rows.forEach((r) => {
      if(['fixed','option_bool','option_int','option_float','advanced'].includes(r.kind)){
        if(typeSeen.has(r.typeId)) warnings.push(t('wled_presets.warning.duplicate_arg', 'Argument {arg} ist mehrfach vorhanden.').replace('{arg}', r.arg));
        typeSeen.add(r.typeId);
      }
      if(r.kind === 'score_exact'){
        const s = clampInt(r.score, 0, 180);
        if(exactSeen.has(s)) warnings.push(t('wled_presets.warning.exact_duplicate', 'Exakter Score {score} ist mehrfach vorhanden (Presets {line1} und {line2}).').replace('{score}', s).replace('{line1}', exactSeen.get(s)).replace('{line2}', r.preset));
        else exactSeen.set(s, r.preset);
      }
      if(r.kind === 'score_range'){
        areaCount += 1;
        const from = clampInt(r.from, 0, 180);
        const to = clampInt(r.to, 0, 180);
        if(from > to) warnings.push(t('wled_presets.warning.invalid_range', 'Bereich in Preset {line} ist ungültig: von {from} bis {to}.').replace('{line}', r.preset).replace('{from}', from).replace('{to}', to));
      }
      if(r.kind === 'advanced'){
        if(!String(r.value || '').trim().startsWith('"')) warnings.push(t('wled_presets.warning.advanced_quotes', '{arg}: Bitte Definitionen in Anführungszeichen schreiben, z. B. "3=ps|5".').replace('{arg}', r.arg));
        if(r.arg === '-DMU') warnings.push(t('wled_presets.warning.dmu_camera', 'DMU wird während des Wurfs ausgelöst. Nur nutzen, wenn die Autodarts-Kameras kein LED-Licht oder Reflexionen sehen.'));
      }
    });
    if(areaCount > 12) warnings.push(t('wled_presets.warning.too_many_areas', 'darts-wled unterstützt nur -A1 bis -A12. Du hast mehr als 12 Score-Bereiche.'));

    if(warnings.length) showMessage(warnings.join('<br>'), 'warn');
    else showMessage('');
  }

  rowsEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if(!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    if(action === 'remove'){
      await deleteRowAndSync(id, btn);
      return;
    }
    if(action === 'send'){
      const row = state.rows.find(r => r.id === id);
      if(row && row.preset){
        const saved = await saveCurrentSettings({ silent: true });
        if(!saved) return;
        const currentRow = state.rows.find(r => r.id === id);
        const sent = await sendPresetToTargets(currentRow ? currentRow.preset : row.preset, btn);
        if(sent && currentRow){
          currentRow._savedSignature = rowSignature(currentRow);
          currentRow._sentSignature = rowSignature(currentRow);
          render();
        }
      }
      return;
    }
    if(action === 'save-row'){
      await saveCurrentSettings({ triggerButton: btn });
      return;
    }
  });

  rowsEl.addEventListener('input', (e) => {
    const el = e.target;
    if(!(el instanceof HTMLInputElement) && !(el instanceof HTMLSelectElement)) return;
    const id = el.dataset.id;
    const field = el.dataset.field;
    const row = state.rows.find(r => r.id === id);
    if(!row || !field) return;
    row[field] = el.value;
    if(row.kind === 'option_int') row.value = String(clampInt(row.value, Number(row.min || 0), Number(row.max || 999)));
    if(row.kind === 'option_float') row.value = clampFloat(row.value, Number(row.min || 0), Number(row.max || 999));
    previewEl.value = buildPreview();
    updateRowStatusInDom(row);
    validate();
  });

  rowsEl.addEventListener('change', (e) => {
    const el = e.target;
    if(!(el instanceof HTMLSelectElement)) return;
    const id = el.dataset.id;
    const field = el.dataset.field;
    const row = state.rows.find(r => r.id === id);
    if(!row || !field) return;
    row[field] = el.value;
    previewEl.value = buildPreview();
    updateRowStatusInDom(row);
    validate();
  });

  document.getElementById('addBtn').addEventListener('click', () => addRow(typeSelect.value));
  if(saveBtn) saveBtn.addEventListener('click', () => saveCurrentSettings({ triggerButton: saveBtn }));
  typeSelect.addEventListener('change', updateTypeInfo);
  if(typeInfoBtn) typeInfoBtn.addEventListener('click', openInfoModal);
  if(infoClose) infoClose.addEventListener('click', closeInfoModal);
  if(infoModal) infoModal.addEventListener('click', (e) => { if(e.target === infoModal) closeInfoModal(); });
  if(expertBtn) expertBtn.addEventListener('click', () => {
    state.expertMode = !state.expertMode;
    expertBtn.textContent = state.expertMode
      ? t('wled_presets.expert_hide', 'Spezialargumente ausblenden')
      : t('wled_presets.expert_show', 'Spezialargumente anzeigen');
    populateTypeSelect();
    if(state.expertMode) showMessage(t('wled_presets.expert_warning', 'Spezialargumente sind nur für erfahrene Nutzer gedacht. Normale Preset-Events funktionieren ohne Freitext.'), 'warn');
    else showMessage('');
  });
  loadBtn.addEventListener('click', () => loadCurrentSettings(false));
  debugBtn.addEventListener('click', () => {
    const isHidden = mainGrid.classList.contains('preview-hidden');
    setDebugOpen(isHidden);
  });
  if(clearBtn){
    clearBtn.style.display = 'none';
    clearBtn.addEventListener('click', () => showMessage(t('wled_presets.clear_disabled', 'Alles leeren ist aus Sicherheitsgründen ausgeblendet.'), 'warn'));
  }
  targetsAllBtn.addEventListener('click', () => setAllTargets(true));
  targetsNoneBtn.addEventListener('click', () => setAllTargets(false));

  populateTypeSelect();
  visualInit();
  setDebugOpen(false);
  render();
  loadTargetsFromStorage();
  loadCurrentSettings(true);
}

document.addEventListener('DOMContentLoaded', () => {
  const start = () => {
    if (typeof window.t !== 'function') {
      window.t = (key, fallback) => (fallback === undefined || fallback === null ? key : fallback);
    }
    if (typeof window.applyDefaultLanguage === 'function') {
      window.applyDefaultLanguage();
    }
    initWledPresets();
  };

  if (window.lang_config && window.lang_config.data) {
    start();
    return;
  }

  const startedAt = Date.now();
  const timer = window.setInterval(() => {
    if (window.lang_config || Date.now() - startedAt > 2000) {
      window.clearInterval(timer);
      start();
    }
  }, 50);
});
