const SOURCES = {
  all: 'All',
  claude: 'Claude',
  codex: 'Codex',
  deepseek: 'DeepSeek',
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
      (p, i) => `
    <div class="post" data-index="${posts.indexOf(p)}" onclick="openPost(${posts.indexOf(p)})">
      <span class="post-date">${p.date}</span>
      <span class="post-source source-${p.source}">${p.source}</span>
      <span class="post-title">${escapeHtml(p.title)}</span>
    </div>`
    )
    .join('');
}

// ─── Post Detail View ─────────────────────────────────────────────────────────
function openPost(index) {
  const post = posts[index];
  if (!post) return;

  const modal = document.getElementById('post-modal');
  const modalTitle = document.getElementById('modal-title');
  const modalMeta = document.getElementById('modal-meta');
  const modalBody = document.getElementById('modal-body');

  modalTitle.textContent = post.title;
  modalMeta.innerHTML = `
    <span class="post-source source-${post.source}">${post.source}</span>
    <span class="modal-date">${post.date}</span>
    <a href="${escapeHtml(post.url)}" target="_blank" rel="noopener" class="modal-link">Open original</a>
  `;

  if (post.body) {
    modalBody.innerHTML = post.body;
  } else if (post.description) {
    modalBody.innerHTML = `<p>${escapeHtml(post.description)}</p><p class="modal-note">Full content not yet fetched. <a href="${escapeHtml(post.url)}" target="_blank" rel="noopener">Read on original site</a></p>`;
  } else {
    modalBody.innerHTML = `<p class="modal-note">Content not available. <a href="${escapeHtml(post.url)}" target="_blank" rel="noopener">Read on original site</a></p>`;
  }

  modal.classList.add('show');
  document.body.style.overflow = 'hidden';
  history.pushState({ post: index }, '', `?post=${post.id}`);
}

function closePost() {
  const modal = document.getElementById('post-modal');
  modal.classList.remove('show');
  document.body.style.overflow = '';
  // Restore URL
  const url = currentFilter === 'all' ? './' : `?source=${currentFilter}`;
  history.pushState(null, '', url);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
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
  // Theme
  setTheme(getTheme());
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      setTheme(getTheme() === 'dark' ? 'light' : 'dark');
    });
  }

  // Read filter from URL
  const params = new URLSearchParams(window.location.search);
  if (params.get('source') && SOURCES[params.get('source')]) {
    currentFilter = params.get('source');
  }

  // Close modal
  const closeBtn = document.getElementById('modal-close');
  if (closeBtn) closeBtn.addEventListener('click', closePost);
  const overlay = document.getElementById('post-modal');
  if (overlay) {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closePost();
    });
  }

  // Back button closes modal
  window.addEventListener('popstate', () => {
    const modal = document.getElementById('post-modal');
    if (modal && modal.classList.contains('show')) {
      modal.classList.remove('show');
      document.body.style.overflow = '';
    }
  });

  // Escape key closes modal
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePost();
  });

  // Install buttons
  const installBtn = document.getElementById('btn-install');
  if (installBtn) installBtn.addEventListener('click', installApp);
  const dismissBtn = document.getElementById('btn-dismiss');
  if (dismissBtn) dismissBtn.addEventListener('click', dismissInstall);

  // Service Worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    });
  }

  loadPosts();
})();
