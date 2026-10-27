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
  const where = channels.length > 1 ? 'на сайте и в Telegram' : channels[0].channel === 'site' ? 'на сайте' : 'в Telegram';
  const details = channels.map(value => (value.channel === 'site' ? 'Сайт' : 'Telegram') +
    (value.version ? ' · ' + value.version : '') + (value.url ? ' · ' + value.url : ''));
  if (editing) details.push('Есть несохранённые изменения');
  else if (changed) details.push('Есть изменения после публикации');
  return { label: 'Опубликовано ' + where + (changed ? ' · есть правки' : ''), changed, channels, title: details.join('\n') };
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
