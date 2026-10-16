import {fromMarkdown} from 'mdast-util-from-markdown';
import {gfm} from 'micromark-extension-gfm';
import {gfmFromMarkdown} from 'mdast-util-gfm';
import TurndownService from 'turndown';
import {gfm as turndownGfm} from 'turndown-plugin-gfm';
export {decodeString} from 'micromark-util-decode-string';
export function parse(source) {
  return fromMarkdown(source, {extensions: [gfm()], mdastExtensions: [gfmFromMarkdown()]});
}

const converter = new TurndownService({headingStyle:'atx', bulletListMarker:'-',
  codeBlockStyle:'fenced', emDelimiter:'*', strongDelimiter:'**'});
converter.use(turndownGfm);
const escapeAlt = text => (text || '').replace(/([\\\[\]])/g, '\\$1').replace(/\s+/g, ' ');
function imageMarkdown(node) {
  const img=node.tagName==='IMG'?node:node.querySelector('img');
  const url=node.dataset.mdUrl || img?.dataset.mdUrl || img?.getAttribute('src') || '';
  return '!['+escapeAlt(node.dataset.mdAlt || img?.alt || '')+']('+url+')';
}
converter.addRule('granka-image', {filter:node=>node.classList?.contains('md-image'),
  replacement:(_content,node)=>imageMarkdown(node)});
converter.addRule('granka-image-bare', {filter:'img', replacement:(_content,node)=>imageMarkdown(node)});
converter.addRule('granka-link', {filter:'a', replacement:(content,node)=>{
  const url=node.dataset.mdUrl || node.getAttribute('href') || '';
  return '['+content+']('+url+(node.title?' "'+node.title.replace(/"/g,'\\"')+'"':'')+')';
}});
converter.addRule('granka-code', {filter:node=>node.nodeName==='PRE',replacement:(_content,node)=>{
  const content=(node.querySelector('code')||node).textContent;
  const longest=Math.max(2,...(content.match(/`+/g)||[]).map(x=>x.length));
  const fence='`'.repeat(longest+1);
  return '\n\n'+fence+(node.dataset.language||'')+'\n'+content.replace(/\n$/,'')+'\n'+fence+'\n\n';
}});
export function toMarkdown(node) { return converter.turndown(node); }
