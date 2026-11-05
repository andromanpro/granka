import { t, localizedOption, locale, localize } from './i18n.js';
import { $ } from './core.js';
let rows = [], config = {}, system = {};
function node(tag, cls, text) { const n=document.createElement(tag);if(cls)n.className=cls;if(text)localize(n,'textContent',typeof text==='function'?text:()=>text);return n; }
function field(parent, text, value, change, attr, multiline=false) {
  const label=node('label',null,text), input=node(multiline?'textarea':'input');input.value=value||'';
  if(attr)input.setAttribute(attr[0],attr[1]);input.autocomplete='off';input.spellcheck=false;
  if(multiline)input.rows=3;
  input.addEventListener('input',()=>change(input.value));label.append(input);parent.append(label);return input;
}
function button(text, title, run, disabled=false) {const b=node('button','quiet',text);b.type='button';localize(b,'title',()=>title);b.disabled=disabled;b.addEventListener('click',run);return b;}
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
    const badge=node('small',null,!env.included?t("ui.ed66bd39d5e9"):env.id===final?t("ui.862fad69fe90"):t("ui.01958cff60fd"));
    const actions=node('div','connection-actions');
    for(const [text,delta]of[['↑',-1],['↓',1]])actions.append(button(text,t("ui.93f7d70b0155"),()=>{const target=index+delta;[rows[index],rows[target]]=[rows[target],rows[index]];renderRows();},index+delta<0||index+delta>=rows.length));
    actions.append(button(()=>t("ui.be99b1361201"),t("ui.57177ae1ce2a"),()=>{rows=rows.filter(r=>r!==env);renderRows();}));heading.append(title,badge,actions);row.append(heading);
    const active=node('label','connection-active'),check=node('input');check.type='checkbox';check.checked=env.included;check.dataset.siteIncluded=env.id;
    check.addEventListener('change',()=>{env.included=check.checked;renderRows();});active.append(check,document.createTextNode(t("ui.9565ee1512e2")));row.append(active);
    const label=node('label',null,t("ui.ab71afb97f2e")),select=node('select');select.dataset.connectionMode=env.id;
    for(const [id,text]of[['direct',t("ui.a672e5372a16")],['system',t("ui.5bf5fdbeef34")],['custom',t("ui.992eec9dc06a")]]){const o=node('option',null,text);o.value=id;select.append(o);}select.value=env.mode;label.append(select);row.append(label);
    const custom=node('div','connection-custom');field(custom,t("ui.ef480d15ab4e"),env.proxy,v=>env.proxy=v,['data-connection-proxy',env.id]);row.append(custom);
    const hint=node('small','connection-route');row.append(hint);
    function route(){custom.hidden=select.value!=='custom';env.mode=select.value;localize(hint,"textContent",()=>select.value==='direct'?t("ui.45855133a4ec"):select.value==='system'?'SSH → '+system.label+t("ui.adfd13f0817b"):t("ui.8f93ee4b45a6"));}
    select.addEventListener('change',route);route();
    const details=node('details','connection-details');details.open=!!env.fresh;details.append(node('summary',null,t("ui.8ce1889553e4")));
    const grid=node('div','settings-grid');details.append(grid);
    field(grid,t("ui.0918b4ba9268"),env.label,v=>{env.label=v;title.textContent=v||'WordPress';},['data-site-label',env.id]);
    field(grid,t("ui.33f6cc060b42"),env.url,v=>env.url=v,['data-site-url',env.id]);
    let parsed=parseSSH(env.ssh||[]);
    let rawSSH,rawWP;
    const rebuildSSH=()=>{env.ssh=[...parsed.extra,'-p',parsed.port||'22',...(parsed.key?['-i',parsed.key]:[]),...(parsed.host?[(parsed.user?parsed.user+'@':'')+parsed.host]:[])];if(rawSSH)rawSSH.value=JSON.stringify(env.ssh);env.sshError=false;};
    field(grid,t("ui.652cbbf668c6"),parsed.host,v=>{parsed.host=v.trim();rebuildSSH();},['data-site-host',env.id]);
    field(grid,t("ui.1f8ff0aeb887"),parsed.user,v=>{parsed.user=v.trim();rebuildSSH();},['data-site-user',env.id]);
    field(grid,t("ui.2c30a4e36b84"),parsed.port,v=>{parsed.port=v.trim();rebuildSSH();},['data-site-port',env.id]);
    field(grid,t("ui.cd7ff34e1426"),parsed.key,v=>{parsed.key=v.trim();rebuildSSH();},['data-site-key',env.id]);
    const wpPath=(env.wp||[]).find(a=>a.startsWith('--path='))?.slice(7)||'';
    field(grid,t("ui.45d80d7d998e"),wpPath,v=>{env.wp=(env.wp||['wp']).filter(a=>!a.startsWith('--path='));if(v)env.wp.push('--path='+v);if(rawWP)rawWP.value=JSON.stringify(env.wp);env.wpError=false;},['data-site-wp-path',env.id]);
    field(grid,t("ui.2bf6eb946906"),env.container,v=>{env.container=v.trim();const path=(env.wp||[]).find(a=>a.startsWith('--path='));env.wp=env.container?[env.docker||'docker','exec','-i',env.container,'wp','--allow-root']:['wp'];if(path)env.wp.push(path);if(rawWP)rawWP.value=JSON.stringify(env.wp);env.wpError=false;},['data-site-container',env.id]);
    const advanced=node('details','connection-advanced');advanced.append(node('summary',null,t("ui.dd8cd6fcd8cd")));
    const parsedArgs=v=>{const args=JSON.parse(v);if(!Array.isArray(args)||args.some(a=>typeof a!=='string'))throw Error('args');return args;};
    rawSSH=field(advanced,t("ui.a028b95e854b"),JSON.stringify(env.ssh||[]),v=>{try{env.ssh=parsedArgs(v);env.sshError=false;}catch(_){env.sshError=true;}},['data-site-ssh',env.id],true);
    rawWP=field(advanced,t("ui.91c5a7c0d97d"),JSON.stringify(env.wp||['wp']),v=>{try{env.wp=parsedArgs(v);env.wpError=false;}catch(_){env.wpError=true;}},['data-site-wp',env.id],true);
    advanced.append(node('p','fine-print',t("ui.ba2e290109ac")));
    details.append(advanced);row.append(details);list.append(row);
  });
}
export function renderSiteSettings(value) {
  rows=structuredClone(value?.environments||[]);config=structuredClone(value?.site||{});system=value?.system_proxy||{};
  $('#site-config-enabled').checked=!!config.enabled;$('#site-git-enabled').checked=!!config.git_enabled;
  for(const key of ['repository','remote','branch'])$('#site-git-'+key).value=config[key]||'';
  localize($('#system-proxy-state'),"textContent",()=>t("ui.7c45c31fa635")+(system.label||t("ui.6153e4ed39a8")));
  $('#site-git-fields').hidden=!config.git_enabled;renderRows();
}
export function siteSettingsValue() {
  if(rows.some(r=>r.sshError||r.wpError))throw Error(t("ui.9cca791e7a87"));
  const envs={};for(const r of rows){envs[r.id]={label:r.label.trim(),url:r.url.trim(),ssh:r.ssh,wp:r.wp};if(r.container)envs[r.id].container=r.container;if(r.docker)envs[r.id].docker=r.docker;if(r.mode==='system')envs[r.id].ssh_proxy='system';else if(r.mode==='custom')envs[r.id].ssh_proxy=r.proxy.trim();}
  return {enabled:$('#site-config-enabled').checked,environments:envs,pipeline:rows.filter(r=>r.included).map(r=>r.id),git_enabled:$('#site-git-enabled').checked,repository:$('#site-git-repository').value.trim(),remote:$('#site-git-remote').value.trim(),branch:$('#site-git-branch').value.trim()||'main'};
}
export function initSiteSettings() {
  $('#site-add-destination').addEventListener('click',()=>{rows.push({id:'site-'+crypto.randomUUID().slice(0,8),label:t("ui.3cce343936a4"),url:'',ssh:[],wp:['wp'],container:'',mode:'direct',proxy:'',included:true,configured:true,fresh:true});renderRows();});
  $('#site-git-enabled').addEventListener('change',()=>$('#site-git-fields').hidden=!$('#site-git-enabled').checked);
}
