'use strict';

const path = require('path');
const http = require('http');
const express = require('express');
const { WebSocketServer } = require('ws');
const { Client } = require('ssh2');

const PORT = Number(process.env.PORT) || 3000;
const HOST = process.env.HOST || '0.0.0.0';
// Optional shared secret. If set, every connect request must include a matching
// token. Use this whenever the bridge is reachable from anywhere but localhost
// so it can't be abused as an open SSH proxy.
const BRIDGE_TOKEN = process.env.BRIDGE_TOKEN || '';

const app = express();
const rootDir = path.join(__dirname, '..');
const publicDir = path.join(rootDir, 'public');

app.use(express.static(publicDir));
// Serve the terminal library straight from node_modules so the app stays
// self-contained and works without any CDN / external network.
app.use('/vendor/xterm', express.static(path.join(rootDir, 'node_modules', '@xterm', 'xterm')));
app.use('/vendor/addon-fit', express.static(path.join(rootDir, 'node_modules', '@xterm', 'addon-fit')));

app.get('/health', (_req, res) => res.json({ ok: true, tokenRequired: Boolean(BRIDGE_TOKEN) }));
// Lets the client know up front whether it needs to send a token.
app.get('/config', (_req, res) => res.json({ tokenRequired: Boolean(BRIDGE_TOKEN) }));

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

wss.on('connection', (ws) => {
  /** @type {import('ssh2').Client | null} */
  let conn = null;
  /** @type {import('ssh2').ClientChannel | null} */
  let stream = null;
  let state = 'idle'; // idle | connecting | connected

  const sendCtrl = (obj) => {
    if (ws.readyState === ws.OPEN) {
      try { ws.send(JSON.stringify(obj)); } catch { /* ignore */ }
    }
  };

  const cleanup = () => {
    try { if (stream) stream.end(); } catch { /* ignore */ }
    try { if (conn) conn.end(); } catch { /* ignore */ }
    stream = null;
    conn = null;
    state = 'idle';
  };

  ws.on('message', (data, isBinary) => {
    // Binary frames are raw terminal keystrokes once a session is live.
    if (isBinary) {
      if (stream) stream.write(data);
      return;
    }

    let msg;
    try {
      msg = JSON.parse(data.toString());
    } catch {
      return; // ignore malformed control frames
    }

    switch (msg.type) {
      case 'connect':
        handleConnect(msg);
        break;
      case 'resize':
        if (stream && msg.cols && msg.rows) {
          stream.setWindow(msg.rows, msg.cols, 0, 0);
        }
        break;
      case 'data':
        // Text fallback for clients that send keystrokes as JSON.
        if (stream && typeof msg.data === 'string') stream.write(msg.data);
        break;
      case 'disconnect':
        cleanup();
        break;
      default:
        break;
    }
  });

  ws.on('close', cleanup);
  ws.on('error', cleanup);

  function handleConnect(msg) {
    if (state !== 'idle') {
      sendCtrl({ type: 'error', message: 'A session is already active.' });
      return;
    }
    if (BRIDGE_TOKEN && msg.token !== BRIDGE_TOKEN) {
      sendCtrl({ type: 'error', message: 'Invalid bridge token.' });
      return;
    }

    const host = String(msg.host || '').trim();
    const username = String(msg.username || '').trim();
    const port = Number(msg.port) || 22;
    if (!host || !username) {
      sendCtrl({ type: 'error', message: 'Host and username are required.' });
      return;
    }

    state = 'connecting';
    sendCtrl({ type: 'status', status: 'connecting', message: `Connecting to ${username}@${host}:${port}…` });

    conn = new Client();

    conn.on('ready', () => {
      state = 'connected';
      sendCtrl({ type: 'status', status: 'connected', message: `Connected to ${username}@${host}` });

      conn.shell(
        { term: msg.term || 'xterm-256color', cols: Number(msg.cols) || 80, rows: Number(msg.rows) || 24 },
        (err, s) => {
          if (err) {
            sendCtrl({ type: 'error', message: `Could not open shell: ${err.message}` });
            cleanup();
            return;
          }
          stream = s;
          stream.on('data', (d) => {
            if (ws.readyState === ws.OPEN) ws.send(d, { binary: true });
          });
          if (stream.stderr) {
            stream.stderr.on('data', (d) => {
              if (ws.readyState === ws.OPEN) ws.send(d, { binary: true });
            });
          }
          stream.on('close', () => {
            sendCtrl({ type: 'status', status: 'closed', message: 'Session closed.' });
            cleanup();
            try { ws.close(); } catch { /* ignore */ }
          });

          // Optionally fire off a command right after the shell opens — e.g. `claude`.
          if (msg.runCommand && typeof msg.runCommand === 'string') {
            const cmd = msg.runCommand.endsWith('\n') ? msg.runCommand : `${msg.runCommand}\n`;
            stream.write(cmd);
          }
        }
      );
    });

    conn.on('keyboard-interactive', (_name, _instr, _lang, prompts, finish) => {
      // Many servers fall back to keyboard-interactive for password auth.
      finish(prompts.map(() => msg.password || ''));
    });

    conn.on('error', (err) => {
      sendCtrl({ type: 'error', message: err.message || 'SSH connection error.' });
      cleanup();
    });

    conn.on('end', () => {
      sendCtrl({ type: 'status', status: 'closed' });
    });

    const cfg = {
      host,
      port,
      username,
      readyTimeout: 20000,
      keepaliveInterval: 15000,
      tryKeyboard: true,
    };
    if (msg.privateKey) {
      cfg.privateKey = msg.privateKey;
      if (msg.passphrase) cfg.passphrase = msg.passphrase;
    }
    if (msg.password) cfg.password = msg.password;
    if (!cfg.privateKey && !cfg.password) {
      sendCtrl({ type: 'error', message: 'Provide a password or a private key.' });
      cleanup();
      return;
    }

    try {
      conn.connect(cfg);
    } catch (e) {
      sendCtrl({ type: 'error', message: e.message || 'Failed to start connection.' });
      cleanup();
    }
  }
});

server.listen(PORT, HOST, () => {
  // eslint-disable-next-line no-console
  console.log(`SSH bridge running at http://${HOST}:${PORT}  (token ${BRIDGE_TOKEN ? 'required' : 'not set'})`);
});
