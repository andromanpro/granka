() => {
  const results = [];
  function check(ok, message) { if (!ok) throw new Error(message); }
  function setup(source, annotations = []) {
    const box = document.createElement('div'); document.body.appendChild(box);
    const view = GrankaMarkdown.render(box, source, annotations, {mark:a=>{
      const m=document.createElement('mark');m.dataset.id=a.id;return m;
    }});
    return {box, view};
  }
  function selected(source, selector, visible, expected, occurrence = 0) {
    const {box,view}=setup(source);
    const target=box.querySelectorAll(selector)[occurrence];
    check(target, 'Missing '+selector+' for '+source);
    const walker=document.createTreeWalker(target,NodeFilter.SHOW_TEXT);
    let text='',nodes=[],n;
    while((n=walker.nextNode())){nodes.push({n,start:text.length});text+=n.data;}
    const start=text.indexOf(visible),end=start+visible.length;
    check(start>=0,'Visible text missing: '+visible+' / '+text);
    const first=nodes.find(x=>x.start+x.n.length>start);
    const last=nodes.find(x=>x.start<end&&x.start+x.n.length>=end);
    const range=document.createRange();range.setStart(first.n,start-first.start);range.setEnd(last.n,end-last.start);
    const offsets=view.selectionOffsets(range);
    check(offsets,'No offsets for '+source);
    const actual=[...source].slice(offsets.start,offsets.end).join('');
    check(actual===expected,'Wrong quote: '+JSON.stringify({source,visible,expected,actual,offsets}));
    box.remove();results.push({visible,quote:actual});
  }
  selected('# Заголовок\n','h1','Заголовок','Заголовок');
  selected('До **😀 жирно** после','strong','😀 жирно','😀 жирно');
  selected('До **жирно** и [ссылка](https://example.org/) после','p','жирно и ссылка','жирно** и [ссылка');
  selected('Повтор.\n\nПовтор. Ещё.','p','Повтор.','Повтор.',1);
  selected('Текст \\*звёзды\\* и &amp; знак.','p','*звёзды*','\\*звёзды\\*');
  selected('Буква &#x1f600; тут.','p','😀','&#x1f600;');
  selected('> Строка\n> продолжение','blockquote','продолжение','продолжение');
  selected('- Строка\n  продолжение','li','продолжение','продолжение');
  selected('- один\n  - **вложено**\n','strong','вложено','вложено');
  selected('`x &amp; y`','code','&amp;','&amp;');
  selected('`` a ` b ``','code','a ` b','a ` b');
  selected('`первая\nвторая`','code','первая вторая','первая\nвторая');
  selected('```js\n  const x = 1;\n```','pre code','const x','const x');
  selected('    строка\n    продолжение\n','pre code','продолжение','продолжение');
  selected('| Имя | Смысл |\n|---|---|\n| **одно** | два \\| три |','td','два | три','два \\| три',1);
  selected('---\ntitle: metadata\n---\n\n# 😀 Текст','h1','😀 Текст','😀 Текст');
  selected('> Одна строка\r\n> другая строка','blockquote','другая строка','другая строка');
  selected('[название][id]\n\n[id]: https://example.org/','a','название','название');

  {
    const source='До **жирно** после';
    const {box,view}=setup(source,[{id:'a1',start:0,end:[...source].length}]);
    check(box.querySelectorAll('mark[data-id="a1"]').length===3,'Annotation must cross inline styles');
    check(box.textContent==='До жирно после','Formatting delimiters leaked');
    const range=document.createRange();range.selectNodeContents(box.querySelector('p'));
    const off=view.selectionOffsets(range);
    check(off.start===0&&off.end===[...source].length,'Element-boundary selection');
    box.remove();results.push({annotation:'split across strong, same id, element selection'});
  }
  {
    window.__markdownAttack=0;
    const source='<script>window.__markdownAttack=1</script>\n\n[x](javascript:alert(1)) [y](data:text/html,evil)\n\n<img src=x onerror="window.__markdownAttack=2">';
    const {box}=setup(source);
    check(!box.querySelector('script,img,iframe,object'),'Unsafe DOM element');
    check(![...box.querySelectorAll('a')].some(a=>/^(javascript|data):/i.test(a.href)),'Unsafe link');
    check(window.__markdownAttack===0,'HTML executed');box.remove();results.push({security:'HTML stays text, dangerous schemes disabled'});
  }
  {
    const {box}=setup('- [x] готово\n- [ ] потом\n\n~~удалено~~');
    check(box.querySelectorAll('input[type=checkbox]:disabled').length===2,'Task lists');
    check(box.querySelector('del')?.textContent==='удалено','Strikethrough');box.remove();results.push({gfm:'task lists, strikethrough'});
  }
  return results;
}
