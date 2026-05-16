const SOURCES = {
  all: 'All',
  claude: 'Claude',
  openai: 'OpenAI',
  gemma: 'Gemma',
  kimi: 'Kimi',
  qwen: 'Qwen',
  openclaw: 'OpenClaw',
  ollama: 'Ollama',
  cursor: 'Cursor',
  perplexity: 'Perplexity',
  xai: 'xAI',
};

let posts = [];
let currentFilter = 'all';
const READ_KEY = 'read-posts';

function loadRead() {
  try {
    return new Set(JSON.parse(localStorage.getItem(READ_KEY) || '[]'));
  } catch {
    return new Set();
  }
}

function markRead(id) {
  const read = loadRead();
  if (read.has(id)) return;
  read.add(id);
  localStorage.setItem(READ_KEY, JSON.stringify([...read]));
}

// ─── Theme ────────────────────────────────────────────────────────────────────
function getTheme() {
  return localStorage.getItem('theme') || 'dark';
}

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = theme === 'dark' ? '\u2600\uFE0F' : '\uD83C\uDF19';
}

// ─── Data ─────────────────────────────────────────────────────────────────────
async function loadPosts() {
  try {
    const resp = await fetch('data/posts.json');
    posts = await resp.json();
    posts.sort((a, b) => b.date.localeCompare(a.date));
    renderFilters();
    renderPosts();
  } catch {
    document.getElementById('empty').style.display = 'block';
    document.getElementById('empty').textContent = 'Failed to load posts.';
  }
}

// ─── Filters ──────────────────────────────────────────────────────────────────
function renderFilters() {
  const el = document.getElementById('filters');
  const counts = { all: posts.length };
  posts.forEach((p) => {
    counts[p.source] = (counts[p.source] || 0) + 1;
  });

  el.innerHTML = Object.entries(SOURCES)
    .map(([key, label]) => {
      const count = counts[key] || 0;
      if (key !== 'all' && count === 0) return '';
      return `<button class="filter-btn ${key === currentFilter ? 'active' : ''}" data-filter="${key}">
        ${label}<span class="count">${count}</span>
      </button>`;
    })
    .join('');

  el.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      currentFilter = btn.dataset.filter;
      renderFilters();
      renderPosts();
      const url = currentFilter === 'all' ? './' : `?source=${currentFilter}`;
      history.replaceState(null, '', url);
    });
  });
}

// ─── Posts ─────────────────────────────────────────────────────────────────────
function renderPosts() {
  const filtered =
    currentFilter === 'all'
      ? posts
      : posts.filter((p) => p.source === currentFilter);
  const postsEl = document.getElementById('posts');
  const emptyEl = document.getElementById('empty');
  const statsEl = document.getElementById('stats');

  statsEl.textContent = `${filtered.length} posts`;

  if (filtered.length === 0) {
    postsEl.innerHTML = '';
    emptyEl.style.display = 'block';
    return;
  }

  emptyEl.style.display = 'none';
  const read = loadRead();
  postsEl.innerHTML = filtered
    .map(
      (p) => `
    <a class="post${read.has(p.id) ? ' read' : ''}" href="${escapeHtml(p.url)}" target="_blank" rel="noopener" data-id="${escapeHtml(p.id)}" title="${escapeHtml(p.description || '')}">
      <span class="post-date">${formatDate(p.date)}</span>
      <span class="post-source source-${p.source}">${p.source}</span>
      <span class="post-title">${escapeHtml(p.title)}</span>
    </a>`
    )
    .join('');

  postsEl.querySelectorAll('.post').forEach((el) => {
    el.addEventListener('click', () => {
      markRead(el.dataset.id);
      el.classList.add('read');
    });
  });
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  if (!m) return iso;
  return `${MONTHS[parseInt(m[2], 10) - 1]} ${parseInt(m[3], 10)}, ${m[1]}`;
}

// ─── Install Banner (PWA) ─────────────────────────────────────────────────────
let deferredPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  const banner = document.getElementById('install-banner');
  if (banner) banner.classList.add('show');
});

function installApp() {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  deferredPrompt.userChoice.then(() => {
    deferredPrompt = null;
    const banner = document.getElementById('install-banner');
    if (banner) banner.classList.remove('show');
  });
}

function dismissInstall() {
  const banner = document.getElementById('install-banner');
  if (banner) banner.classList.remove('show');
}

// ─── Init ─────────────────────────────────────────────────────────────────────
(function init() {
  setTheme(getTheme());
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      setTheme(getTheme() === 'dark' ? 'light' : 'dark');
    });
  }

  const params = new URLSearchParams(window.location.search);
  if (params.get('source') && SOURCES[params.get('source')]) {
    currentFilter = params.get('source');
  }

  const installBtn = document.getElementById('btn-install');
  if (installBtn) installBtn.addEventListener('click', installApp);
  const dismissBtn = document.getElementById('btn-dismiss');
  if (dismissBtn) dismissBtn.addEventListener('click', dismissInstall);

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    });
  }

  loadPosts();
})();
