import { t, localizedOption, locale, localize } from './i18n.js';
import {translateError} from './i18n.js';
import { $, api, el, button, toast, events } from './core.js';
let target = null, refreshTimer, sequence = 0;
const states = {scheduled:t("ui.c4c9abc154de"), sending:t("ui.408d1167e921"), sent:t("ui.382ded9c6877"), failed:t("ui.f5ab15c5c77a"), error:t("ui.2e92862ecd99"), unknown:t("ui.8cf46ff28cc1"), missed:t("ui.b961eeba4384"), cancelled:t("ui.23a2a9bfcf74")};
const offset = () => {
  const minutes = -new Date().getTimezoneOffset(), abs = Math.abs(minutes);
  return 'UTC' + (minutes >= 0 ? '+' : '-') + String(Math.floor(abs / 60)).padStart(2,'0') + ':' + String(abs % 60).padStart(2,'0');
};
export function openSchedule(record) {
  target = record;
  $('#schedule-channel').textContent = record.channel.title + ' · ' + (record.channel.username ? '@'+record.channel.username : record.channel.chat_id);
  $('#schedule-preview').textContent = record.text;
  $('#schedule-timezone').value = record.schedule?.timezone || offset();
  const next = new Date(Date.now() + 3600000), local = new Date(next.getTime() - next.getTimezoneOffset()*60000);
  $('#schedule-when').value = record.schedule?.when || local.toISOString().slice(0,16);
  const windows = record.schedule?.windows || {days:[0,1,2,3,4,5,6], start:'00:00', end:'23:59'};
  $('#schedule-start').value = windows.start; $('#schedule-end').value = windows.end;
  for (const checkbox of document.querySelectorAll('[data-schedule-day]')) checkbox.checked = windows.days.includes(Number(checkbox.dataset.scheduleDay));
  $('#schedule-confirm').checked = false; $('#schedule-error').textContent = '';
  $('#schedule-dialog').showModal();
}
export async function refreshQueue() {
  const own = ++sequence;
  try {
    const result = await api('/api/telegram/queue'); if (own !== sequence) return;
    const list = $('#scheduled-posts'); list.replaceChildren();
    localize($('#scheduled-worker'),"textContent",()=>result.last_error ? translateError(result.last_error) : (result.running ? t("ui.6a9163591241") : t("ui.16d36cc77c9b")));
    for (const record of result.entries) {
      const details = el('details', 'scheduled-post');
      details.appendChild(el('summary', '', record.schedule.when.replace('T',' · ')+' '+record.schedule.timezone+' — '+(states[record.schedule.state] || record.schedule.state)+' · '+record.channel.title));
      details.appendChild(el('p', 'fine-print', record.rel));
      details.appendChild(el('pre', 'scheduled-preview', record.text));
      if (record.attachments?.length)details.appendChild(el('p','fine-print',record.attachments.map(a=>a.name).join(' · ')));
      if (record.schedule.error || record.error)details.appendChild(el('p','error',()=>translateError(record.schedule.error || record.error)));
      if (record.url) { const link=el('a','',()=>t("ui.1ff55226736e")); link.href=record.url; link.target='_blank'; link.rel='noreferrer'; details.appendChild(link); }
      if (['prepared','failed'].includes(record.status) && record.schedule.state !== 'cancelled') {
        details.appendChild(button(()=>t("ui.758cc605c321"),()=>openSchedule(record)));
        details.appendChild(button(()=>t("ui.5660f265acc4"),async()=>{
          try {await api('/api/telegram/cancel-schedule',{method:'POST',body:{id:record.id,expected_schedule_revision:record.schedule.revision}});await refreshQueue();events.dispatchEvent(new Event('schedule-changed'));}
          catch(error){toast(error.message,true);}
        }));
      }
      list.appendChild(details);
    }
    if (!result.entries.length)localize(list,"textContent",()=>t("ui.4999718429be"));
  } catch(error){$('#scheduled-worker').textContent=error.message;}
}
export function initScheduledPosts() {
  const queueDialog=$('#scheduled-queue-dialog');
  $('#planner-scheduled-open').addEventListener('click',()=>{
    $('#scheduled-queue-content').appendChild($('#scheduled-panel'));
    queueDialog.showModal();refreshQueue();
    clearInterval(refreshTimer);refreshTimer=setInterval(refreshQueue,5000);
  });
  $('#scheduled-queue-close').addEventListener('click',()=>queueDialog.close());
  queueDialog.addEventListener('close',()=>{
    $('#scheduled-details').appendChild($('#scheduled-panel'));clearInterval(refreshTimer);
  });
  $('#schedule-close').addEventListener('click',()=>$('#schedule-dialog').close());
  $('#schedule-submit').addEventListener('click',async()=>{
    if (!target)return;
    const submit=$('#schedule-submit');submit.disabled=true;
    try {
      await api('/api/telegram/schedule?'+new URLSearchParams({root:target.root,f:target.rel}),{method:'POST',body:{
        id:target.id,confirmed:$('#schedule-confirm').checked,operation_id:crypto.randomUUID(),review_revision:target.review_revision,expected_schedule_revision:target.schedule?.revision,
        when:$('#schedule-when').value,timezone:$('#schedule-timezone').value,
        windows:{days:[...document.querySelectorAll('[data-schedule-day]:checked')].map(n=>Number(n.dataset.scheduleDay)),start:$('#schedule-start').value,end:$('#schedule-end').value},
      }});
      $('#schedule-dialog').close();await refreshQueue();events.dispatchEvent(new Event('schedule-changed'));toast(t("ui.f43ebfdb71f3"));
    } catch(error){$('#schedule-error').textContent=error.message;}
    finally{submit.disabled=false;}
  });
  $('#scheduled-refresh').addEventListener('click',refreshQueue);
  $('#scheduled-details').addEventListener('toggle',()=>{
    clearInterval(refreshTimer);
    if ($('#scheduled-details').open){refreshQueue();refreshTimer=setInterval(()=>{if($('#telegramdialog').open)refreshQueue();},5000);}
  });
}
