// ─── Config ──────────────────────────────────────────────────────────────────
const SOURCES = {
  all: 'All',
  claude: 'Claude',
  openai: 'OpenAI',
  gemma: 'Gemma',
  kimi: 'Kimi',
  openclaw: 'OpenClaw',
  ollama: 'Ollama',
  cursor: 'Cursor',
  perplexity: 'Perplexity',
  xai: 'xAI',
};

let posts = [];
let currentFilter = 'all';
const READ_KEY = 'read-posts';

// ─── Read state ──────────────────────────────────────────────────────────────
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

// ─── Theme ───────────────────────────────────────────────────────────────────
function getTheme() {
  return localStorage.getItem('theme') || 'light';
}

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.innerHTML = theme === 'light'
    ? `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`
    : `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"></path></svg>`;
}

// ─── Data ────────────────────────────────────────────────────────────────────
async function loadPosts() {
  try {
    const resp = await fetch('data/posts.json');
    posts = await resp.json();
    posts.sort((a, b) => b.date.localeCompare(a.date));
    renderFilters();
    renderPosts();
  } catch (err) {
    console.error(err);
    document.getElementById('empty').style.display = 'block';
    document.getElementById('empty').textContent = 'Failed to load posts.';
  }
}

// ─── Filters ─────────────────────────────────────────────────────────────────
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
        <span>${label}</span><span class="count">${count}</span>
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

// ─── Featured ────────────────────────────────────────────────────────────────
function renderFeatured() {
  const section = document.getElementById('featured-section');
  const grid = document.getElementById('featured');
  const filtered = currentFilter === 'all'
    ? posts
    : posts.filter((p) => p.source === currentFilter);

  const top = filtered.slice(0, 3);
  if (top.length === 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';

  document.getElementById('featured-meta').textContent =
    currentFilter === 'all' ? 'Latest three' : `Latest from ${SOURCES[currentFilter]}`;

  grid.innerHTML = top
    .map((p, i) => {
      const lead = i === 0 ? ' lead' : '';
      const rank = String(i + 1).padStart(2, '0');
      const desc = p.description && p.description.trim() && !/^[A-Z][a-z]{2,3} \d{1,2}, \d{4}$/.test(p.description.trim())
        ? `<p class="feature-desc">${escapeHtml(p.description)}</p>`
        : '';
      return `
      <a class="feature-card${lead}" href="${escapeHtml(p.url)}" target="_blank" rel="noopener" data-id="${escapeHtml(p.id)}">
        <div class="feature-head">
          <span class="feature-rank">№ ${rank}</span>
          <span class="tag tag-${p.source}">${SOURCES[p.source] || p.source}</span>
        </div>
        <h3 class="feature-title">${escapeHtml(p.title)}</h3>
        ${desc}
        <div class="feature-foot">
          <span class="feature-date">${formatDate(p.date)}</span>
          <span class="feature-arrow" aria-hidden="true">→</span>
        </div>
      </a>`;
    })
    .join('');

  grid.querySelectorAll('.feature-card').forEach((el) => {
    el.addEventListener('click', () => markRead(el.dataset.id));
  });
}

// ─── Index list ──────────────────────────────────────────────────────────────
function renderPosts() {
  const filtered = currentFilter === 'all'
    ? posts
    : posts.filter((p) => p.source === currentFilter);

  const offset = 0;
  const list = filtered;

  const postsEl = document.getElementById('posts');
  const emptyEl = document.getElementById('empty');
  document.getElementById('index-meta').textContent =
    currentFilter === 'all'
      ? `${filtered.length} posts · newest first`
      : `${filtered.length} from ${SOURCES[currentFilter]}`;

  if (list.length === 0) {
    postsEl.innerHTML = '';
    emptyEl.style.display = filtered.length === 0 ? 'block' : 'none';
    return;
  }

  emptyEl.style.display = 'none';
  const read = loadRead();

  postsEl.innerHTML = list
    .map((p) => {
      return `
    <li>
      <a class="post${read.has(p.id) ? ' read' : ''}" href="${escapeHtml(p.url)}" target="_blank" rel="noopener" data-id="${escapeHtml(p.id)}" title="${escapeHtml(p.description || '')}">
        <span class="post-date">${formatDate(p.date)}</span>
        <span class="post-source"><span class="tag tag-${p.source}">${SOURCES[p.source] || p.source}</span></span>
        <span class="post-title">${escapeHtml(p.title)}</span>
        <span class="post-arrow" aria-hidden="true">→</span>
      </a>
    </li>`;
    })
    .join('');

  postsEl.querySelectorAll('.post').forEach((el) => {
    el.addEventListener('click', () => {
      markRead(el.dataset.id);
      el.classList.add('read');
    });
  });
}

// ─── Utils ───────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  if (!m) return iso;
  return `${MONTHS[parseInt(m[2], 10) - 1]} ${String(parseInt(m[3], 10)).padStart(2, '0')}, ${m[1]}`;
}

// ─── Install Banner (PWA) ────────────────────────────────────────────────────
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

// ─── Init ────────────────────────────────────────────────────────────────────
(function init() {
  setTheme(getTheme());

  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      setTheme(getTheme() === 'light' ? 'dark' : 'light');
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

  // Unregister any old service worker + clear caches on first load of redesign,
  // then re-register fresh.
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
      try {
        const regs = await navigator.serviceWorker.getRegistrations();
        const needsReset = !localStorage.getItem('sw-redesign-ok');
        if (needsReset) {
          await Promise.all(regs.map((r) => r.unregister()));
          if (window.caches) {
            const keys = await caches.keys();
            await Promise.all(keys.map((k) => caches.delete(k)));
          }
          localStorage.setItem('sw-redesign-ok', '1');
        }
        navigator.serviceWorker.register('sw.js').catch(() => {});
      } catch {}
    });
  }

  loadPosts();
})();
