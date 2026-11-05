import { t, localizedOption, locale, localize } from './i18n.js';
import { S, api, query, events, emit } from './core.js';

function publicURL(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; }
  catch (_) { return ''; }
}

export function publicationView({ sources = [], site, telegram = [], root, file, version, editing = false }) {
  const same = value => value.root === root && value.granka_rel === file;
  const source = sources.find(value => value.state === 'published' &&
    (same(value) || (value.copies || []).some(same)));
  const channels = [];
  const receipt = site?.publication;
  if (receipt?.post_status === 'publish' && publicURL(receipt.url)) {
    channels.push({ channel: 'site', url: publicURL(receipt.url), version: receipt.version, changed: !!receipt.changed });
  } else if (source) {
    channels.push({ channel: source.channel === 'telegram' ? 'telegram' : 'site', url: publicURL(source.url) });
  }
  const sent = telegram.find(value => value.status === 'sent' && publicURL(value.url));
  if (sent && !channels.some(value => value.channel === 'telegram'))
    channels.push({ channel: 'telegram', url: publicURL(sent.url), version: sent.version, changed: !!sent.version && sent.version !== version });
  if (!channels.length) return null;
  const changed = editing || channels.some(value => value.changed);
  const where = channels.length > 1 ? t("ui.8769f52e5f21") : channels[0].channel === 'site' ? t("ui.17cb6596fd77") : t("ui.d393902a22e2");
  const details = channels.map(value => (value.channel === 'site' ? t("ui.b4541abdaefd") : 'Telegram') +
    (value.version ? ' · ' + value.version : '') + (value.url ? ' · ' + value.url : ''));
  if (editing) details.push(t("ui.382dd64dc0b2"));
  else if (changed) details.push(t("ui.90e1374505fe"));
  return { label: t("ui.a2aef4cb6346") + where + (changed ? t("ui.c072be35e6bc") : ''), changed, channels, title: details.join('\n') };
}

let generation = 0, terminal = '', timer;
async function refresh() {
  if (!S.document) return;
  const own = ++generation, source = query(), revision = S.revision;
  const values = await Promise.allSettled([
    api('/api/sources'), api('/api/site?' + source), api('/api/telegram/publications?' + source),
  ]);
  if (own !== generation || source !== query() || revision !== S.revision) return;
  const result = i => values[i].status === 'fulfilled' ? values[i].value : {};
  S.publication = publicationView({ sources: result(0).sources, site: result(1), telegram: result(2).publications,
    root: S.root, file: S.file, version: S.document.versions.at(-1).id, editing: !!S.editor });
  emit('publication');
}
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => refresh().catch(() => {}), 100);
}
export function initPublicationState() {
  events.addEventListener('document', () => { S.publication = null; ++generation; schedule(); });
  for (const type of ['sync', 'publication-sent']) events.addEventListener(type, schedule);
  events.addEventListener('jobs', () => {
    const signature = S.jobs.filter(job => job.kind === 'site' && !['queued', 'running'].includes(job.status))
      .map(job => job.id + ':' + job.status).join('|');
    if (signature !== terminal) { terminal = signature; schedule(); }
  });
}
