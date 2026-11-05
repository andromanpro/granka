// UI-only resources. Never scan or translate the article or arbitrary DOM text.
const catalogs = await Promise.all(['ru','en'].map(async language => {
  const response = await fetch('/static/locales/'+language+'.json');
  if (!response.ok)throw Error('Cannot load UI language resources: '+language);
  return response.json();
})).catch(error=>{
  const warning=document.createElement('p');warning.id='language-load-error';warning.setAttribute('role','alert');
  warning.textContent='Не удалось загрузить интерфейс. Перезапустите Гранку или восстановите установку. / Could not load the interface. Restart Granka or repair the installation.';
  document.body.prepend(warning);throw error;
});
let saved;
try {saved=localStorage.getItem('granka.locale');}catch(_){}
let current = ['ru','en'].includes(saved) ? saved : navigator.language?.startsWith('en') ? 'en' : 'ru';
const bindings = new Map(), attributes = ['title','placeholder','aria-label','alt'];
export const locale = () => current === 'en' ? 'en-US' : 'ru-RU';
export const language = () => current;
function format(key, parameters) {
  const source = catalogs[current==='en'?1:0][key] ?? catalogs[0][key];
  if (source == null)throw Error('Missing UI message: '+key);
  return source.replace(/\{(\d+)\}/g,(_,index)=>String(parameters[Number(index)]??''));
}
class Message extends String {
  constructor(key, parameters) {super(format(key,parameters));this.key=key;this.parameters=parameters;}
  toString(){return format(this.key,this.parameters);}
  valueOf(){return this.toString();}
  toJSON(){return this.toString();}
}
export function t(key,...parameters){return new Message(key,parameters);}
const sourceKeys=new Map(Object.entries(catalogs[0]).map(([key,value])=>[value,key]));
// For known server-authored UI labels only; never use on article prose.
export function uiText(value){const key=sourceKeys.get(value);return key?t(key):value;}
export function recipeLabel(value){const keys={dry:'recipe.dry',kitchen:'recipe.kitchen',water:'recipe.water'};return keys[value.id]?t(keys[value.id]):value.name;}
export function localize(node, property, read) {
  if (!node)return '';
  if(property==='textContent'&&node.nodeType===Node.ELEMENT_NODE){
    const text=document.createTextNode('');node.replaceChildren(text);return localize(text,'textContent',read);
  }
  let props=bindings.get(node);if(!props){props=new Map();bindings.set(node,props);}
  const value=String(read()??'');props.set(property,{read,last:value});node[property]=value;
  if(bindings.size>1500)queueMicrotask(()=>{for(const node of bindings.keys())if(!node.isConnected)bindings.delete(node);});
  return value;
}
function staticResources() {
  const walker=document.createTreeWalker(document.documentElement,NodeFilter.SHOW_COMMENT);
  while(walker.nextNode()){
    const comment=walker.currentNode,key=comment.data.match(/^i18n:(\S+)$/)?.[1],text=comment.nextSibling;
    if(key&&text?.nodeType===Node.TEXT_NODE){
      const prefix=text.textContent.match(/^\s*/)[0],suffix=text.textContent.match(/\s*$/)[0];
      localize(text,'textContent',()=>prefix+String(t(key))+suffix);
    }
  }
  for(const attr of attributes)for(const node of document.querySelectorAll('[data-i18n-'+attr+']')){
    const key=node.getAttribute('data-i18n-'+attr);
    const property={'aria-label':'ariaLabel',title:'title',placeholder:'placeholder',alt:'alt'}[attr];
    localize(node,property,()=>t(key));
  }
}
export function setLanguage(value,{persist=true}={}) {
  if(!['ru','en'].includes(value))throw Error('Unsupported UI language');
  current=value;document.documentElement.lang=value;
  if(persist)try{localStorage.setItem('granka.locale',value);}catch(_){}
  staticResources();
  for(const [node,properties]of bindings){
    if(!node.isConnected){bindings.delete(node);continue;}
    for(const [property,binding]of properties){
      if(String(node[property])!==binding.last){properties.delete(property);continue;}
      const value=String(binding.read()??'');node[property]=value;binding.last=value;
    }
  }
  document.dispatchEvent(new CustomEvent('granka-locale',{detail:{language:value}}));
}
export function localizedOption(read,value) {
  const node=new Option('',value);localize(node,'textContent',read);return node;
}
export function translateError(message) {
  if(current!=='en')return message;
  const key=sourceKeys.get(message);
  return key?String(t(key)):'The operation could not be completed. Details: '+message;
}
setLanguage(current,{persist:false});
