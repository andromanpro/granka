import { $ } from './core.js';
let rows = [], config = {}, system = {};
function node(tag, cls, text) { const n=document.createElement(tag);if(cls)n.className=cls;if(text)n.textContent=text;return n; }
function field(parent, text, value, change, attr, multiline=false) {
  const label=node('label',null,text), input=node(multiline?'textarea':'input');input.value=value||'';
  if(attr)input.setAttribute(attr[0],attr[1]);input.autocomplete='off';input.spellcheck=false;
  if(multiline)input.rows=3;
  input.addEventListener('input',()=>change(input.value));label.append(input);parent.append(label);return input;
}
function button(text, title, run, disabled=false) {const b=node('button','quiet',text);b.type='button';b.title=title;b.disabled=disabled;b.addEventListener('click',run);return b;}
function parseSSH(args) {
  const parsed={host:'',user:'',port:'22',key:'',extra:[]};let target='';
  for(let i=0;i<args.length;i++){
    const a=args[i];if(a==='-p')parsed.port=args[++i]||'22';else if(a==='-i')parsed.key=args[++i]||'';
    else if(a==='-o'||a==='-F'||a==='-J'){parsed.extra.push(a,args[++i]||'');}
    else if(a.startsWith('-'))parsed.extra.push(a);else target=a;
  }
  const at=target.lastIndexOf('@');parsed.user=at<0?'':target.slice(0,at);parsed.host=at<0?target:target.slice(at+1);return parsed;
}
function renderRows() {
  const list=$('#site-connections');list.replaceChildren();
  const included=rows.filter(r=>r.included), final=included.at(-1)?.id;
  $('#site-empty').hidden=!!rows.length;$('#site-add-destination').disabled=rows.length>=8;
  rows.forEach((env,index)=>{
    const row=node('div','connection-row');row.dataset.stage=env.id;
    const heading=node('div','connection-heading'),title=node('strong',null,env.label||'WordPress');
    const badge=node('small',null,!env.included?'Не участвует':env.id===final?'Сайт публикации':'Предварительная площадка');
    const actions=node('div','connection-actions');
    for(const [text,delta]of[['↑',-1],['↓',1]])actions.append(button(text,'Изменить порядок',()=>{const target=index+delta;[rows[index],rows[target]]=[rows[target],rows[index]];renderRows();},index+delta<0||index+delta>=rows.length));
    actions.append(button('Удалить','Убрать подключение из настроек',()=>{rows=rows.filter(r=>r!==env);renderRows();}));heading.append(title,badge,actions);row.append(heading);
    const active=node('label','connection-active'),check=node('input');check.type='checkbox';check.checked=env.included;check.dataset.siteIncluded=env.id;
    check.addEventListener('change',()=>{env.included=check.checked;renderRows();});active.append(check,document.createTextNode(' Использовать в маршруте'));row.append(active);
    const label=node('label',null,'Подключение'),select=node('select');select.dataset.connectionMode=env.id;
    for(const [id,text]of[['direct','Напрямую'],['system','Системный прокси'],['custom','Указать прокси']]){const o=node('option',null,text);o.value=id;select.append(o);}select.value=env.mode;label.append(select);row.append(label);
    const custom=node('div','connection-custom');field(custom,'Адрес прокси',env.proxy,v=>env.proxy=v,['data-connection-proxy',env.id]);row.append(custom);
    const hint=node('small','connection-route');row.append(hint);
    function route(){custom.hidden=select.value!=='custom';env.mode=select.value;hint.textContent=select.value==='direct'?'SSH → сервер напрямую':select.value==='system'?'SSH → '+system.label+' → сервер':'HTTP CONNECT или SOCKS5. Без пароля в адресе.';}
    select.addEventListener('change',route);route();
    const details=node('details','connection-details');details.open=!!env.fresh;details.append(node('summary',null,'Параметры WordPress и SSH'));
    const grid=node('div','settings-grid');details.append(grid);
    field(grid,'Название',env.label,v=>{env.label=v;title.textContent=v||'WordPress';},['data-site-label',env.id]);
    field(grid,'Адрес WordPress',env.url,v=>env.url=v,['data-site-url',env.id]);
    let parsed=parseSSH(env.ssh||[]);
    let rawSSH,rawWP;
    const rebuildSSH=()=>{env.ssh=[...parsed.extra,'-p',parsed.port||'22',...(parsed.key?['-i',parsed.key]:[]),...(parsed.host?[(parsed.user?parsed.user+'@':'')+parsed.host]:[])];if(rawSSH)rawSSH.value=JSON.stringify(env.ssh);env.sshError=false;};
    field(grid,'SSH-сервер',parsed.host,v=>{parsed.host=v.trim();rebuildSSH();},['data-site-host',env.id]);
    field(grid,'SSH-пользователь',parsed.user,v=>{parsed.user=v.trim();rebuildSSH();},['data-site-user',env.id]);
    field(grid,'SSH-порт',parsed.port,v=>{parsed.port=v.trim();rebuildSSH();},['data-site-port',env.id]);
    field(grid,'Файл SSH-ключа · необязательно',parsed.key,v=>{parsed.key=v.trim();rebuildSSH();},['data-site-key',env.id]);
    const wpPath=(env.wp||[]).find(a=>a.startsWith('--path='))?.slice(7)||'';
    field(grid,'Каталог WordPress на сервере',wpPath,v=>{env.wp=(env.wp||['wp']).filter(a=>!a.startsWith('--path='));if(v)env.wp.push('--path='+v);if(rawWP)rawWP.value=JSON.stringify(env.wp);env.wpError=false;},['data-site-wp-path',env.id]);
    field(grid,'Docker-контейнер · необязательно',env.container,v=>{env.container=v.trim();const path=(env.wp||[]).find(a=>a.startsWith('--path='));env.wp=env.container?[env.docker||'docker','exec','-i',env.container,'wp','--allow-root']:['wp'];if(path)env.wp.push(path);if(rawWP)rawWP.value=JSON.stringify(env.wp);env.wpError=false;},['data-site-container',env.id]);
    const advanced=node('details','connection-advanced');advanced.append(node('summary',null,'Дополнительные аргументы SSH и WP-CLI'));
    const parsedArgs=v=>{const args=JSON.parse(v);if(!Array.isArray(args)||args.some(a=>typeof a!=='string'))throw Error('args');return args;};
    rawSSH=field(advanced,'Аргументы SSH · JSON-массив',JSON.stringify(env.ssh||[]),v=>{try{env.ssh=parsedArgs(v);env.sshError=false;}catch(_){env.sshError=true;}},['data-site-ssh',env.id],true);
    rawWP=field(advanced,'Команда WP-CLI · JSON-массив',JSON.stringify(env.wp||['wp']),v=>{try{env.wp=parsedArgs(v);env.wpError=false;}catch(_){env.wpError=true;}},['data-site-wp',env.id],true);
    advanced.append(node('p','fine-print','Параметры выполняются на вашем сервере. Содержимое ключей и пароли сюда не вставляются. После правки аргументов сохраните и заново откройте настройки перед изменением простых полей.'));
    details.append(advanced);row.append(details);list.append(row);
  });
}
export function renderSiteSettings(value) {
  rows=structuredClone(value?.environments||[]);config=structuredClone(value?.site||{});system=value?.system_proxy||{};
  $('#site-config-enabled').checked=!!config.enabled;$('#site-git-enabled').checked=!!config.git_enabled;
  for(const key of ['repository','remote','branch'])$('#site-git-'+key).value=config[key]||'';
  $('#system-proxy-state').textContent='Системный прокси: '+(system.label||'не настроен');
  $('#site-git-fields').hidden=!config.git_enabled;renderRows();
}
export function siteSettingsValue() {
  if(rows.some(r=>r.sshError||r.wpError))throw Error('Проверь JSON-массив аргументов SSH/WP-CLI');
  const envs={};for(const r of rows){envs[r.id]={label:r.label.trim(),url:r.url.trim(),ssh:r.ssh,wp:r.wp};if(r.container)envs[r.id].container=r.container;if(r.docker)envs[r.id].docker=r.docker;if(r.mode==='system')envs[r.id].ssh_proxy='system';else if(r.mode==='custom')envs[r.id].ssh_proxy=r.proxy.trim();}
  return {enabled:$('#site-config-enabled').checked,environments:envs,pipeline:rows.filter(r=>r.included).map(r=>r.id),git_enabled:$('#site-git-enabled').checked,repository:$('#site-git-repository').value.trim(),remote:$('#site-git-remote').value.trim(),branch:$('#site-git-branch').value.trim()||'main'};
}
export function initSiteSettings() {
  $('#site-add-destination').addEventListener('click',()=>{rows.push({id:'site-'+crypto.randomUUID().slice(0,8),label:'Мой сайт',url:'',ssh:[],wp:['wp'],container:'',mode:'direct',proxy:'',included:true,configured:true,fresh:true});renderRows();});
  $('#site-git-enabled').addEventListener('change',()=>$('#site-git-fields').hidden=!$('#site-git-enabled').checked);
}
