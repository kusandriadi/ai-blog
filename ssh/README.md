# SSH Console — iPhone SSH app

An installable web app (PWA) for SSHing into a server from your iPhone and running
the `claude` command — or any other command — from a real terminal.

Because browsers can't open raw TCP/SSH sockets, the app has two parts:

1. **Frontend** (`public/`) — a full-screen terminal UI built on
   [xterm.js](https://xtermjs.org/) that you *Add to Home Screen* on iPhone.
2. **Bridge** (`server/`) — a small Node.js server that proxies a browser
   WebSocket to a real SSH connection (via [`ssh2`](https://github.com/mscdex/ssh2)).

```
iPhone (PWA terminal)  ⇄  WebSocket  ⇄  Node bridge  ⇄  SSH  ⇄  your server → claude
```

## Quick start

You run the bridge on any machine that can reach your target server (your Mac,
a home box, a small VPS, etc.).

```bash
cd ssh
npm install
npm start          # serves the app + bridge on http://0.0.0.0:3000
```

Then on your iPhone (same network, or wherever the bridge is reachable):

1. Open `http://<bridge-host>:3000` in Safari.
2. Tap **Share → Add to Home Screen**. Launch it from the icon for a full-screen,
   chrome-free app.
3. Enter your server's host / username / password (or paste a private key),
   leave **Run `claude` automatically** checked, and tap **Connect**.

You'll drop into a shell and `claude` starts immediately. The bottom key bar adds
`esc`, `tab`, `ctrl`, `alt`, arrows and `^C` — the keys iOS keyboards lack but
TUIs like `claude` need.

## Configuration

Environment variables:

| Var            | Default     | Purpose                                                              |
| -------------- | ----------- | ------------------------------------------------------------------- |
| `PORT`         | `3000`      | Port the bridge listens on.                                         |
| `HOST`         | `0.0.0.0`   | Interface to bind.                                                   |
| `BRIDGE_TOKEN` | *(unset)*   | Shared secret. If set, the app shows a "Bridge token" field and the bridge rejects any connect request without it. |

```bash
BRIDGE_TOKEN="a-long-random-string" PORT=8080 npm start
```

## Security notes

This bridge can open an SSH session to any host with the credentials you give it,
so treat it like an SSH client — don't expose it carelessly.

- **Don't put it on the public internet unauthenticated.** Set `BRIDGE_TOKEN`,
  and/or keep it on your LAN / behind a VPN / Tailscale.
- **Use TLS for remote access.** Put it behind a reverse proxy (Caddy, nginx,
  Cloudflare Tunnel) so traffic is `https`/`wss`. The app auto-uses `wss` when
  loaded over `https`.
- **Saved credentials** (when you tick *Remember this server*) are stored only in
  your phone's `localStorage`, never on the bridge. The bridge keeps credentials
  in memory for the duration of a session only.
- Prefer **SSH keys** over passwords where you can.

## How it works

- Terminal keystrokes are sent to the bridge as binary WebSocket frames and
  written straight to the SSH channel; SSH output comes back as binary frames and
  is written to xterm.js.
- Control messages (connect, resize, disconnect) are JSON text frames.
- A PTY is allocated (`xterm-256color`) and `setWindow` keeps the remote size in
  sync as the phone rotates or the keyboard appears, so full-screen TUIs render
  correctly.

## Files

```
ssh/
├── server/index.js          # HTTP static server + WebSocket↔SSH bridge
└── public/
    ├── index.html           # app shell
    ├── css/styles.css       # mobile-first dark UI
    ├── js/app.js            # connection manager + terminal + helper keys
    ├── manifest.webmanifest # PWA metadata
    ├── sw.js                # service worker (offline app shell)
    └── icons/               # app icons
```
