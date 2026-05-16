const SOURCES = {
  all: 'All',
  claude: 'Claude',
  codex: 'Codex',
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
  postsEl.innerHTML = filtered
    .map(
      (p) => `
    <a class="post" href="${escapeHtml(p.url)}" target="_blank" rel="noopener" title="${escapeHtml(p.description || '')}">
      <span class="post-date">${formatDate(p.date)}</span>
      <span class="post-source source-${p.source}">${p.source}</span>
      <span class="post-title">${escapeHtml(p.title)}</span>
    </a>`
    )
    .join('');
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  return m ? `${m[3]}-${m[2]}-${m[1]}` : iso;
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
