import { $, S, events, allowNavigation, toast } from './core.js';

const pages = {
  library: 'source-dialog', calendar: 'plannerdialog',
  telegram: 'telegramdialog', settings: 'settingsdialog', help: 'helpdialog',
};
const sections = ['library', 'editor', 'spices', 'diff', 'history', 'calendar', 'telegram', 'settings', 'help'];
let articleSection = 'editor', returnSection = 'editor', handlers, routing = false;
let sequence = 0;
let activeRoute = null, pendingRoute = null;

function articleRoute() {
  return S.view === 'diff' ? 'diff' : S.pane === 'history' ? 'history' : S.pane === 'spices' ? 'spices' : 'editor';
}
function highlight(section, { push = false, updateHash = true } = {}) {
  document.body.dataset.section = section;
  document.body.dataset.view = S.view;
  document.body.dataset.pane = S.pane;
  for (const button of document.querySelectorAll('button[data-section]')) {
    const selected = button.dataset.section === section;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  const hash = '#' + section;
  if (updateHash && location.hash !== hash) history[push ? 'pushState' : 'replaceState'](null, '', hash);
}
function openPage() { return document.querySelector('.workspace-page[open]'); }
function editing() {
  $('.app-frame').hidden = false;
  highlight(articleSection);
}
function controls() {
  for (const button of document.querySelectorAll('button[data-section]')) {
    button.disabled = !!(S.busy || S.recording || S.workspaceLocked || routing) ||
      (!S.document && ['spices', 'diff', 'history', 'telegram'].includes(button.dataset.section));
  }
}

// These pages retain their existing forms and API handlers. Native modal dialogs
// (unsaved edits, image choice, confirmation) can still be opened over them.
export function showWorkspacePage(selector, section) {
  if (S.workspaceLocked) return false;
  if (activeRoute?.section === section && activeRoute.own !== sequence) return false;
  const page = $(selector);
  const parentModal = document.querySelector('dialog:modal');
  if (!openPage()) returnSection = articleRoute();
  for (const other of document.querySelectorAll('.workspace-page[open]'))
    if (other !== page) other.close();
  if (parentModal && parentModal !== page) {
    // E.g. executor settings opened from image generation. Preserve the parent
    // chooser and its insertion anchor underneath a real nested modal.
    page.classList.add('nested-workspace');
    page.setAttribute('aria-modal', 'true');
    if (!page.open) page.showModal();
  } else {
    $('.app-frame').hidden = true;
    if (!page.open) page.show();
  }
  // Commit the visible page before its ancillary requests finish. Otherwise a
  // quick Back traverses the previous entry while the UI already shows this one.
  const route = activeRoute?.section === section ? activeRoute : null;
  highlight(section, { push: route ? route.push : !routing, updateHash: !!route || !routing });
  page.querySelector('h2')?.focus({ preventScroll: true });
  return true;
}

export async function navigateWorkspace(section, { push = true } = {}) {
  if (!sections.includes(section) || !handlers) return;
  if (routing) {
    if (!push) { ++sequence; pendingRoute = { section, push }; }
    else toast('Дождись завершения текущего действия');
    return;
  }
  if (S.workspaceLocked || S.recording || S.busy) {
    toast('Дождись завершения текущего действия');
    return;
  }
  const own = ++sequence;
  const previous = document.body.dataset.section || articleRoute();
  // Reopening the same page must not discard fields, attachments or a prepared post.
  if (pages[section] && $('#' + pages[section]).open) return;
  if (!S.document && ['spices', 'diff', 'history', 'telegram'].includes(section)) {
    toast('Сначала открой статью в библиотеке');
    return;
  }
  routing = true;
  activeRoute = { section, push, own };
  controls();
  try {
    if (pages[section]) {
      await handlers[section]();
      if (own === sequence && $('#' + pages[section]).open) highlight(section, { push });
      return;
    }
    const view = section === 'diff' ? 'diff' : 'article';
    // Preserve a source draft when merely returning to the editor. A change of
    // representation requires the same save/discard guard as changing versions.
    if (!(section === 'editor' && S.editor) && S.view !== view) {
      if (!(await allowNavigation()) || own !== sequence) {
        highlight(previous); return;
      }
      handlers.setView(view);
    }
    handlers.pane(section === 'history' ? 'history' : section === 'spices' ? 'spices' : 'review');
    articleSection = section;
    for (const page of document.querySelectorAll('.workspace-page[open]')) page.close();
    $('.app-frame').hidden = false;
    $('#inspector').classList.remove('mobile-open');
    if (innerWidth <= 800 && ['spices', 'history'].includes(section))
      $('#inspector').classList.add('mobile-open');
    highlight(section, { push });
  } catch (error) {
    toast(error.message, true);
    highlight(previous);
  } finally {
    activeRoute = null;
    routing = false;
    controls();
    if (pendingRoute) {
      const next = pendingRoute; pendingRoute = null;
      await navigateWorkspace(next.section, { push: next.push });
    }
  }
}

export function initWorkspace(actions) {
  handlers = actions;
  for (const [section, id] of Object.entries(pages)) {
    const page = $('#' + id);
    page.classList.add('workspace-page');
    page.setAttribute('aria-modal', 'false');
    page.querySelector('h2')?.setAttribute('tabindex', '-1');
    page.addEventListener('close', () => {
      page.classList.remove('nested-workspace');
      page.setAttribute('aria-modal', 'false');
      if (openPage()) return;
      if (document.body.dataset.section === section) {
        articleSection = returnSection;
        editing();
      }
    });
  }
  for (const button of document.querySelectorAll('button[data-section]')) {
    button.addEventListener('click', () => navigateWorkspace(button.dataset.section));
  }
  for (const type of ['view', 'pane', 'pane-selected']) events.addEventListener(type, () => {
    articleSection = articleRoute();
    if (!openPage() && !routing) highlight(articleSection);
  });
  for (const type of ['controls', 'document', 'workspace-lock']) events.addEventListener(type, controls);
  window.addEventListener('hashchange', () => navigateWorkspace(location.hash.slice(1), { push: false }));
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || document.querySelector('dialog:modal')) return;
    const page = openPage();
    if (!page) return;
    const cancel = new Event('cancel', { cancelable: true });
    if (page.dispatchEvent(cancel) && !S.workspaceLocked) page.close();
  });
  controls();
  highlight(articleRoute());
}

export async function restoreWorkspace(section) {
  if (sections.includes(section)) await navigateWorkspace(section, { push: false });
  else if (!S.document) {
    const query = new URLSearchParams(location.search);
    // An exact link that no longer exists must retain its error screen. Do not
    // obscure it with an automatic library request or choose a different text.
    if (!['root', 'f', 'source_file', 'source'].some(key => query.has(key)))
      await navigateWorkspace('library', { push: false });
  }
}

const appearanceKey = 'granka.appearance';
export function readAppearance() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(appearanceKey)) || {}; } catch (_) {}
  return {
    font: saved.font === 'sans' ? 'sans' : 'serif',
    size: [16, 18, 20].includes(Number(saved.size)) ? Number(saved.size) : 18,
    motion: saved.motion !== false,
  };
}
export function applyAppearance(value, { persist = false } = {}) {
  document.documentElement.style.setProperty('--reading-font', value.font === 'sans' ? '"Segoe UI", sans-serif' : 'Georgia, "Times New Roman", serif');
  document.documentElement.style.setProperty('--reading-size', value.size + 'px');
  document.body.classList.toggle('no-motion', !value.motion);
  if (persist) localStorage.setItem(appearanceKey, JSON.stringify(value));
}
