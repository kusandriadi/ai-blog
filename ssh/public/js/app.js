'use strict';

(function () {
  const STORAGE_KEY = 'ssh-console.servers.v1';

  // ---------- tiny DOM helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const el = {
    connectScreen: $('#connect-screen'),
    termScreen: $('#term-screen'),
    savedSection: $('#saved-section'),
    savedList: $('#saved-list'),
    form: $('#connect-form'),
    formTitle: $('#form-title'),
    id: $('#conn-id'),
    name: $('#conn-name'),
    host: $('#conn-host'),
    port: $('#conn-port'),
    username: $('#conn-username'),
    auth: $('#conn-auth'),
    passwordField: $('#password-field'),
    password: $('#conn-password'),
    keyFields: $('#key-fields'),
    key: $('#conn-key'),
    passphrase: $('#conn-passphrase'),
    tokenField: $('#token-field'),
    token: $('#conn-token'),
    autoClaude: $('#conn-auto-claude'),
    remember: $('#conn-remember'),
    clearForm: $('#clear-form'),
    termStatus: $('#term-status'),
    backBtn: $('#back-btn'),
    claudeBtn: $('#claude-btn'),
    terminalEl: $('#terminal'),
    keybar: $('#keybar'),
    kbdBtn: $('#kbd-btn'),
    toast: $('#toast'),
  };

  let tokenRequired = false;

  // ---------- persistence ----------
  function loadServers() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    } catch {
      return [];
    }
  }
  function saveServers(list) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  }
  function upsertServer(server) {
    const list = loadServers();
    const idx = list.findIndex((s) => s.id === server.id);
    if (idx >= 0) list[idx] = server;
    else list.push(server);
    saveServers(list);
  }
  function deleteServer(id) {
    saveServers(loadServers().filter((s) => s.id !== id));
    renderSaved();
  }

  // ---------- toast ----------
  let toastTimer = null;
  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.toast.classList.add('hidden'), 3200);
  }

  // ---------- saved servers UI ----------
  function renderSaved() {
    const list = loadServers();
    el.savedList.innerHTML = '';
    if (!list.length) {
      el.savedSection.classList.add('hidden');
      return;
    }
    el.savedSection.classList.remove('hidden');
    for (const s of list) {
      const li = document.createElement('li');
      li.className = 'saved-item';
      const info = document.createElement('div');
      info.className = 'info';
      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = s.name || s.host;
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = `${s.username}@${s.host}:${s.port || 22}`;
      info.append(name, meta);

      const connectBtn = document.createElement('button');
      connectBtn.className = 'connect-btn';
      connectBtn.textContent = 'Connect';
      connectBtn.addEventListener('click', () => startSession(s));

      const editBtn = document.createElement('button');
      editBtn.className = 'icon-btn';
      editBtn.textContent = 'Edit';
      editBtn.addEventListener('click', () => fillForm(s));

      const delBtn = document.createElement('button');
      delBtn.className = 'icon-btn';
      delBtn.textContent = '✕';
      delBtn.addEventListener('click', () => {
        if (confirm(`Delete "${s.name || s.host}"?`)) deleteServer(s.id);
      });

      li.append(info, connectBtn, editBtn, delBtn);
      el.savedList.appendChild(li);
    }
  }

  function fillForm(s) {
    el.id.value = s.id || '';
    el.name.value = s.name || '';
    el.host.value = s.host || '';
    el.port.value = s.port || 22;
    el.username.value = s.username || '';
    el.auth.value = s.authType || 'password';
    el.password.value = s.password || '';
    el.key.value = s.privateKey || '';
    el.passphrase.value = s.passphrase || '';
    el.token.value = s.token || '';
    el.autoClaude.checked = s.autoClaude !== false;
    el.remember.checked = Boolean(s.id);
    el.formTitle.textContent = s.id ? 'Edit connection' : 'New connection';
    syncAuthFields();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function clearForm() {
    el.form.reset();
    el.id.value = '';
    el.port.value = 22;
    el.autoClaude.checked = true;
    el.formTitle.textContent = 'New connection';
    syncAuthFields();
  }

  function syncAuthFields() {
    const isKey = el.auth.value === 'key';
    el.keyFields.classList.toggle('hidden', !isKey);
    el.passwordField.classList.toggle('hidden', isKey);
    el.tokenField.classList.toggle('hidden', !tokenRequired);
  }

  function formToServer() {
    return {
      id: el.id.value || String(Date.now()),
      name: el.name.value.trim(),
      host: el.host.value.trim(),
      port: Number(el.port.value) || 22,
      username: el.username.value.trim(),
      authType: el.auth.value,
      password: el.auth.value === 'password' ? el.password.value : '',
      privateKey: el.auth.value === 'key' ? el.key.value : '',
      passphrase: el.auth.value === 'key' ? el.passphrase.value : '',
      token: el.token.value,
      autoClaude: el.autoClaude.checked,
    };
  }

  // ---------- terminal session ----------
  let term = null;
  let fitAddon = null;
  let ws = null;
  let modCtrl = false;
  let modAlt = false;

  function ensureTerminal() {
    if (term) return;
    term = new window.Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      scrollback: 5000,
      theme: { background: '#000000', foreground: '#e6edf3', cursor: '#4f9cff' },
    });
    fitAddon = new window.FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(el.terminalEl);

    term.onData((data) => sendInput(data));
    term.onResize(({ cols, rows }) => sendCtrl({ type: 'resize', cols, rows }));
  }

  function setStatus(text, kind) {
    el.termStatus.textContent = text;
    el.termStatus.className = 'term-status' + (kind ? ' ' + kind : '');
  }

  function sendCtrl(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  // Apply sticky ctrl/alt modifiers, then send keystrokes as a binary frame.
  function sendInput(data) {
    if (modAlt && data) {
      data = '\x1b' + data;
      modAlt = false;
      updateModButtons();
    }
    if (modCtrl && data.length === 1) {
      const c = data.toUpperCase().charCodeAt(0);
      if (c >= 64 && c <= 95) data = String.fromCharCode(c - 64); // ^A..^_
      else if (c >= 97 && c <= 122) data = String.fromCharCode(c - 96);
      modCtrl = false;
      updateModButtons();
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(new TextEncoder().encode(data));
    }
  }

  function refit() {
    if (!fitAddon) return;
    try {
      fitAddon.fit();
    } catch { /* ignore */ }
  }

  function startSession(server) {
    ensureTerminal();
    term.reset();
    el.connectScreen.classList.add('hidden');
    el.termScreen.classList.remove('hidden');
    setStatus('connecting…');
    // Defer fit until the terminal element has its real size.
    requestAnimationFrame(() => {
      refit();
      openSocket(server);
      setTimeout(() => term.focus(), 150);
    });
  }

  function openSocket(server) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      const dims = fitAddon ? fitAddon.proposeDimensions() : null;
      sendCtrl({
        type: 'connect',
        host: server.host,
        port: server.port,
        username: server.username,
        password: server.password || undefined,
        privateKey: server.privateKey || undefined,
        passphrase: server.passphrase || undefined,
        token: server.token || undefined,
        cols: (dims && dims.cols) || term.cols,
        rows: (dims && dims.rows) || term.rows,
        term: 'xterm-256color',
        runCommand: server.autoClaude !== false ? 'claude' : undefined,
      });
    };

    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        handleControl(JSON.parse(ev.data));
      } else {
        term.write(new Uint8Array(ev.data));
      }
    };

    ws.onclose = () => {
      setStatus('disconnected', 'err');
    };
    ws.onerror = () => {
      setStatus('connection error', 'err');
      toast('Could not reach the SSH bridge.');
    };
  }

  function handleControl(msg) {
    if (msg.type === 'status') {
      if (msg.status === 'connected') setStatus(msg.message || 'connected', 'ok');
      else if (msg.status === 'connecting') setStatus(msg.message || 'connecting…');
      else if (msg.status === 'closed') setStatus(msg.message || 'session closed', 'err');
    } else if (msg.type === 'error') {
      setStatus(msg.message || 'error', 'err');
      term.write(`\r\n\x1b[31m✗ ${msg.message}\x1b[0m\r\n`);
      toast(msg.message);
    }
  }

  function endSession() {
    sendCtrl({ type: 'disconnect' });
    if (ws) {
      try { ws.close(); } catch { /* ignore */ }
    }
    ws = null;
    el.termScreen.classList.add('hidden');
    el.connectScreen.classList.remove('hidden');
    renderSaved();
  }

  // ---------- helper key bar ----------
  function updateModButtons() {
    el.keybar.querySelector('[data-mod="ctrl"]').classList.toggle('active', modCtrl);
    el.keybar.querySelector('[data-mod="alt"]').classList.toggle('active', modAlt);
  }

  const SEQUENCES = {
    esc: '\x1b',
    tab: '\t',
    up: '\x1b[A',
    down: '\x1b[B',
    right: '\x1b[C',
    left: '\x1b[D',
    'ctrl-c': '\x03',
  };

  el.keybar.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const mod = btn.dataset.mod;
    const seq = btn.dataset.seq;
    if (mod === 'ctrl') {
      modCtrl = !modCtrl;
      updateModButtons();
      term.focus();
      return;
    }
    if (mod === 'alt') {
      modAlt = !modAlt;
      updateModButtons();
      term.focus();
      return;
    }
    if (seq === 'kbd') {
      term.focus();
      return;
    }
    if (seq && SEQUENCES[seq] !== undefined) {
      sendInput(SEQUENCES[seq]);
      term.focus();
    }
  });

  // ---------- events ----------
  el.auth.addEventListener('change', syncAuthFields);
  el.clearForm.addEventListener('click', clearForm);
  el.backBtn.addEventListener('click', endSession);
  el.claudeBtn.addEventListener('click', () => {
    sendInput('claude\n');
    term.focus();
  });

  el.form.addEventListener('submit', (e) => {
    e.preventDefault();
    const server = formToServer();
    if (!server.host || !server.username) {
      toast('Host and username are required.');
      return;
    }
    if (el.remember.checked) {
      upsertServer(server);
      renderSaved();
    }
    startSession(server);
  });

  // Keep the terminal sized to the viewport, including when the iOS keyboard
  // shows/hides (which fires visualViewport resize events).
  window.addEventListener('resize', refit);
  window.addEventListener('orientationchange', () => setTimeout(refit, 300));
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', refit);
  }

  // ---------- boot ----------
  fetch('config')
    .then((r) => r.json())
    .then((cfg) => {
      tokenRequired = Boolean(cfg.tokenRequired);
      syncAuthFields();
    })
    .catch(() => {});

  renderSaved();
  syncAuthFields();

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    });
  }
})();
