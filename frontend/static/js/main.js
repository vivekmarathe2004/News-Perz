/**
 * NewsPerz client interactions.
 * Vanilla ES2020, no dependencies. Every hook is optional so the same file
 * runs on feed pages, auth pages and the saved page.
 */

const csrfToken = () =>
  (document.querySelector('meta[name="csrf-token"]')?.content || '').trim();

const jsonHeaders = () => ({
  'Content-Type': 'application/json',
  'X-CSRFToken': csrfToken(),
});

const announce = (message) => {
  const status = document.querySelector('.feed-status-text');
  if (!status) return;
  status.hidden = !message;
  status.textContent = message || '';
};

/* ── Theme ─────────────────────────────────────────────────────── */
let themeTransitionTimer;
function initTheme() {
  const root = document.documentElement;
  const toggle = document.getElementById('themeToggle');
  const apply = (theme) => {
    root.classList.add('theme-transition');
    root.setAttribute('data-theme', theme);
    try { localStorage.setItem('newsperz-theme', theme); } catch (e) { /* private mode */ }
    if (toggle) {
      toggle.setAttribute('aria-label', theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    }
    window.clearTimeout(themeTransitionTimer);
    themeTransitionTimer = window.setTimeout(() => root.classList.remove('theme-transition'), 380);
  };
  if (!toggle) return;
  toggle.addEventListener('click', () => {
    apply(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  });
}

/* ── Mobile navigation drawer ──────────────────────────────────── */
function initDrawer() {
  const button = document.getElementById('hamburgerBtn');
  const sidebar = document.getElementById('sidebarNav');
  const scrim = document.getElementById('navScrim');
  if (!button || !sidebar) return;

  const setOpen = (open) => {
    sidebar.classList.toggle('open', open);
    button.classList.toggle('open', open);
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-label', open ? 'Close navigation menu' : 'Open navigation menu');
    if (scrim) scrim.hidden = !open;
    document.body.style.overflow = open ? 'hidden' : '';
  };

  button.addEventListener('click', () => setOpen(!sidebar.classList.contains('open')));
  scrim?.addEventListener('click', () => setOpen(false));
  sidebar.addEventListener('click', (event) => {
    if (event.target.closest('a')) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sidebar.classList.contains('open')) {
      setOpen(false);
      button.focus();
    }
  });
  window.addEventListener('resize', () => {
    if (window.innerWidth > 768) setOpen(false);
  });
}

/* ── Flash messages ────────────────────────────────────────────── */
function initFlashes() {
  document.querySelectorAll('.flash').forEach((banner) => {
    const dismiss = () => banner.remove();
    banner.querySelector('.flash-close')?.addEventListener('click', dismiss);
    // aria-live content needs time to be read; 8s is still snappier than a page change.
    setTimeout(dismiss, 8000);
  });
}

/* ── Bookmark toggle (delegated) ───────────────────────────────── */
function setBookmarkState(buttons, saved) {
  buttons.forEach((btn) => {
    btn.classList.toggle('bookmarked', saved);
    btn.setAttribute('aria-pressed', String(saved));
    const title = btn.dataset.title || 'this story';
    btn.setAttribute('aria-label', (saved ? 'Remove saved story: ' : 'Save story: ') + title);
    btn.setAttribute('title', saved ? 'Remove from Saved' : 'Save for later');
  });
}

async function toggleBookmark(button) {
  const url = button.dataset.url;
  if (!url) return;
  const wasSaved = button.classList.contains('bookmarked');
  const peers = Array.from(document.querySelectorAll('.bookmark-btn')).filter(
    (btn) => btn.dataset.url === url
  );

  setBookmarkState(peers, !wasSaved);
  const endpoint = wasSaved ? '/bookmark/remove' : '/bookmark/add';
  const payload = { url };
  if (!wasSaved) {
    Object.assign(payload, {
      title: button.dataset.title || '',
      source: button.dataset.source || '',
      description: button.dataset.description || '',
      image: button.dataset.image || '',
      publishedAt: button.dataset.publishedAt || '',
    });
  }

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'ok') throw new Error(data.message || 'Request failed');
    button.blur();
  } catch (error) {
    setBookmarkState(peers, wasSaved);
    announce(error.message === 'Your session expired. Reload the page.'
      ? error.message
      : 'Could not update your saved stories. Please try again.');
    console.error('Bookmark toggle failed:', error);
  }
}

function initBookmarks() {
  document.addEventListener('click', (event) => {
    const bookmark = event.target.closest('.bookmark-btn');
    if (bookmark) {
      event.preventDefault();
      toggleBookmark(bookmark);
      return;
    }

    const remove = event.target.closest('[data-remove-saved]');
    if (remove) {
      event.preventDefault();
      removeSavedCard(remove);
    }
  });
}

async function removeSavedCard(button) {
  const url = button.dataset.url;
  const card = button.closest('.saved-card');
  if (!url || !card) return;
  card.classList.add('is-removing');

  try {
    const response = await fetch('/bookmark/remove', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ url }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'ok') throw new Error(data.message || 'Request failed');
    card.remove();
    const grid = document.getElementById('savedGrid');
    if (grid && grid.children.length === 0) window.location.reload();
  } catch (error) {
    card.classList.remove('is-removing');
    announce('Could not remove that story. Please try again.');
    console.error('Remove saved story failed:', error);
  }
}

/* ── Password reveal (auth pages) ──────────────────────────────── */
function initPasswordToggles() {
  document.querySelectorAll('.toggle-pw').forEach((button) => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.target);
      if (!input) return;
      const reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(reveal));
      button.setAttribute('aria-label', reveal ? 'Hide password' : 'Show password');
    });
  });
}

/* ── Preferences: live area datalist + checkbox styling ────────── */
function initPreferences() {
  const form = document.querySelector('.pref-form');
  if (!form) return;

  const cities = JSON.parse(form.dataset.cities || '{}');
  const states = JSON.parse(form.dataset.states || '{}');
  const citySelect = form.querySelector('[data-city-select]');
  const areaInput = form.querySelector('[data-area-input]');
  const areaOptions = form.querySelector('[data-area-options]');
  const stateInput = form.querySelector('#state');

  const rebuildAreas = (city) => {
    if (!areaOptions) return;
    areaOptions.innerHTML = '';
    (cities[city] || []).forEach((area) => {
      const option = document.createElement('option');
      option.value = area;
      areaOptions.appendChild(option);
    });
  };

  citySelect?.addEventListener('change', () => {
    if (citySelect.value === '__other__') {
      const typed = window.prompt('Which city?');
      if (typed && typed.trim()) {
        const option = document.createElement('option');
        option.value = option.textContent = typed.trim();
        citySelect.add(option, citySelect.options[citySelect.options.length - 2]);
        citySelect.value = option.value;
        if (stateInput && !stateInput.value) stateInput.value = '';
      } else {
        citySelect.value = citySelect.options[0].value;
      }
    }
    const city = citySelect.value;
    rebuildAreas(city);
    if (areaInput) areaInput.value = '';
    if (stateInput && states[city]) stateInput.value = states[city];
  });

  document.querySelectorAll('.topic-checkbox').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      checkbox.closest('.topic-label')?.classList.toggle('topic-label--checked', checkbox.checked);
    });
  });
}

/* ── Relative timestamps ───────────────────────────────────────── */
function humanize(isoDate) {
  if (!isoDate) return '';
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return '';
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (minutes < 2) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
}

function initTimestamps(root = document) {
  root.querySelectorAll('time[datetime]').forEach((element) => {
    const text = humanize(element.getAttribute('datetime'));
    if (text) element.textContent = text;
  });
}

/* ── Image fallbacks (replaces inline onerror) ─────────────────── */
function initImageFallbacks() {
  document.addEventListener(
    'error',
    (event) => {
      const image = event.target;
      if (!(image instanceof HTMLImageElement)) return;
      image.closest('.card-thumb, .saved-thumb, .featured-image-wrap, .story-figure')?.classList.add('thumb-failed');
    },
    true
  );
}

/* ── Infinite scroll ───────────────────────────────────────────── */
function initInfiniteScroll() {
  const feed = document.querySelector('[data-feed]');
  const sentinel = document.querySelector('[data-feed-sentinel]');
  if (!feed || !sentinel || !('IntersectionObserver' in window)) return;
  if (feed.dataset.feedHasMore !== 'true') return;

  const skeleton = document.querySelector('.feed-status .skeleton-group');
  let offset = Number(feed.dataset.feedOffset || 0);
  let loading = false;
  let done = false;
  const seen = new Set(
    Array.from(feed.querySelectorAll('.article-card a[href]')).map((a) => a.getAttribute('href'))
  );

  const setSkeleton = (visible) => { if (skeleton) skeleton.hidden = !visible; };

  const loadMore = async () => {
    if (loading || done) return;
    loading = true;
    setSkeleton(true);
    announce('');
    const params = new URLSearchParams(window.location.search);
    params.set('mode', feed.dataset.feedMode || 'for_you');
    params.set('offset', String(offset));
    if (feed.dataset.feedTopic) params.set('topic', feed.dataset.feedTopic);
    if (feed.dataset.feedSource) params.set('source', feed.dataset.feedSource);

    try {
      const response = await fetch(`/api/feed?${params.toString()}`, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`Feed request failed (${response.status})`);
      const data = await response.json();
      if (!data.articles || data.articles.length === 0) {
        done = true;
        announce('That is every story we found.');
        observer.disconnect();
        return;
      }

      const template = document.createElement('template');
      template.innerHTML = data.html || '';
      const added = [];
      template.content.querySelectorAll('.article-card').forEach((card) => {
        const link = card.querySelector('a[href]');
        if (link && seen.has(link.getAttribute('href'))) return;
        if (link) seen.add(link.getAttribute('href'));
        added.push(card);
      });
      added.forEach((card) => feed.appendChild(card));
      initTimestamps(feed);

      offset = Number(data.next_offset || offset + data.articles.length);
      feed.dataset.feedOffset = String(offset);
      done = !data.has_more;
      if (done) {
        observer.disconnect();
        sentinel.hidden = true;
      }
    } catch (error) {
      announce('Could not load more stories. Scroll to retry.');
      console.error('Infinite scroll failed:', error);
    } finally {
      loading = false;
      setSkeleton(false);
    }
  };

  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) loadMore();
  }, { rootMargin: '400px 0px' });

  observer.observe(sentinel);
}

/* ── Story reader: excerpt loader, progress, copy link ─────────── */
const STORY_STATUS_TEXT = {
  blocked: 'This publisher does not allow in-app previews. The full story is one click away.',
  empty: 'We could not find a readable excerpt for this story. Open the publisher for the full report.',
  error: 'The publisher did not respond. You can still open the full story below.',
  disabled: 'In-app previews are turned off. Open the publisher for the full report.',
  missing: 'This preview is no longer available.',
};

function initStory() {
  const story = document.querySelector('[data-story]');
  if (!story) return;

  const progress = document.querySelector('[data-reading-progress] span');
  const updateProgress = () => {
    if (!progress) return;
    const rect = story.getBoundingClientRect();
    const total = Math.max(story.offsetHeight - window.innerHeight + 160, 1);
    const read = Math.min(Math.max(160 - rect.top, 0), total);
    progress.style.transform = `scaleX(${(read / total).toFixed(4)})`;
  };
  document.addEventListener('scroll', updateProgress, { passive: true });
  window.addEventListener('resize', updateProgress);
  updateProgress();

  const mount = story.querySelector('[data-story-body]');
  if (!mount || mount.querySelector('p')) return;

  const note = story.querySelector('[data-story-note]');
  const showMessage = (message) => {
    mount.innerHTML = '';
    const paragraph = document.createElement('p');
    paragraph.className = 'story-body-fallback';
    paragraph.textContent = message;
    mount.appendChild(paragraph);
    if (note) note.hidden = false;
  };

  fetch(`/api/story/${encodeURIComponent(mount.dataset.storyBody)}/body`, {
    headers: { Accept: 'application/json' },
  })
    .then((response) => {
      if (!response.ok && response.status !== 404) throw new Error(`Preview failed (${response.status})`);
      return response.json();
    })
    .then((data) => {
      if (data.status === 'ok' && data.paragraphs?.length) {
        mount.innerHTML = '';
        data.paragraphs.forEach((text) => {
          const paragraph = document.createElement('p');
          paragraph.textContent = text;
          mount.appendChild(paragraph);
        });
        if (note) note.hidden = false;
        document.querySelectorAll('[data-reading-minutes]').forEach((el) => {
          el.textContent = `${data.reading_minutes} min read`;
        });
        document.querySelectorAll('[data-reading-fact]').forEach((el) => {
          el.textContent = `${data.reading_minutes} min preview`;
        });
        updateProgress();
      } else {
        showMessage(STORY_STATUS_TEXT[data.status] || STORY_STATUS_TEXT.error);
      }
    })
    .catch(() => showMessage(STORY_STATUS_TEXT.error));
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const field = document.createElement('textarea');
  field.value = text;
  field.setAttribute('readonly', '');
  field.className = 'sr-only';
  document.body.appendChild(field);
  field.select();
  document.execCommand('copy');
  field.remove();
}

function initCopyLinks() {
  document.querySelectorAll('[data-copy-link]').forEach((button) => {
    button.addEventListener('click', async () => {
      const link = button.dataset.copyLink;
      if (!link) return;
      const status = document.querySelector('[data-copy-status]');
      try {
        await copyText(link);
        if (status) status.textContent = 'Link copied to your clipboard.';
      } catch (error) {
        if (status) status.textContent = 'Could not copy the link. Copy it from the address bar instead.';
        console.error('Copy link failed:', error);
      }
    });
  });
}

/* ── Boot ──────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initDrawer();
  initFlashes();
  initBookmarks();
  initPasswordToggles();
  initPreferences();
  initTimestamps();
  initImageFallbacks();
  initInfiniteScroll();
  initStory();
  initCopyLinks();
});
