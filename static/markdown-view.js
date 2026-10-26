/* Safe Markdown DOM + mapping from rendered text back to the immutable source. */
(function () {
  'use strict';
  function render(box, source, annotations, options = {}) {
    const rows = [], blocks = [], cp = [0];
    let u = 0;
    for (const ch of source) { u += ch.length; cp.push(u); }
    const spans = annotations.map(a => ({...a, ustart: cp[a.start], uend: cp[a.end]}));
    const make = tag => document.createElement(tag);
    const slice = node => source.slice(node.position.start.offset, node.position.end.offset);
    box.replaceChildren();

    // Each rendered UTF-16 unit knows its original interval. Escapes/entities may
    // cover several source units; blockquote/list prefixes have no rendered unit.
    function mapping(value, start, end, literal = false) {
      const starts = [], ends = [];
      let i = start, j = 0;
      while (j < value.length && i < end) {
        const raw = source.slice(i, end);
        let consumed = 1, decoded = source[i];
        if (source[i] === '\r' && source[i + 1] === '\n') { decoded = '\n'; consumed = 2; }
        else if (!literal) {
          const match = /^(?:\\[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~]|&(?:#[xX][\da-fA-F]+|#\d+|[a-zA-Z][a-zA-Z0-9]+);)/.exec(raw);
          if (match) { consumed = match[0].length; decoded = GrankaMarkdownParser.decodeString(match[0]); }
        }
        if (value.startsWith(decoded, j)) {
          for (let k = 0; k < decoded.length; k++) {
            starts.push(i); ends.push(i + consumed);
          }
          i += consumed; j += decoded.length;
        } else if (literal && /[\r\n]/.test(source[i]) && value[j] === ' ') {
          starts.push(i); ends.push(i + consumed); i += consumed; j++;
        } else if (/[\t >]/.test(source[i])) {
          // CommonMark strips indentation and quote markers from continuations.
          i++;
        } else {
          return null;
        }
      }
      if (!literal && /\S/.test(source.slice(i, end))) return null;
      return j === value.length ? {starts, ends} : null;
    }

    function append(parent, value, map) {
      if (!value) return;
      const relevant = spans.filter(a => a.uend > map.starts[0] && a.ustart < map.ends.at(-1));
      let begin = 0, previous = [];
      function flush(end) {
        if (end <= begin) return;
        const text = document.createTextNode(value.slice(begin, end));
        if (previous.length) {
          const mark = options.mark(previous[0], previous);
          mark.appendChild(text); parent.appendChild(mark);
        } else parent.appendChild(text);
        rows.push({node: text, starts: map.starts.slice(begin, end), ends: map.ends.slice(begin, end)});
        begin = end;
      }
      for (let i = 0; i < value.length; i++) {
        const active = relevant.filter(a => map.starts[i] < a.uend && map.ends[i] > a.ustart);
        if (active.length !== previous.length || active.some((a, j) => a !== previous[j])) {
          flush(i); previous = active;
        }
      }
      flush(value.length);
    }

    function rawText(parent, start, end) {
      const value = source.slice(start, end);
      append(parent, value, {
        starts: Array.from({length: value.length}, (_, i) => start + i),
        ends: Array.from({length: value.length}, (_, i) => start + i + 1)
      });
    }

    function text(parent, node, literal = false, start, end) {
      start ??= node.position.start.offset; end ??= node.position.end.offset;
      const map = mapping(node.value, start, end, literal);
      if (map) append(parent, node.value, map);
      else {
        // Never fabricate offsets: uncommon syntax remains readable as source.
        const fallback = make('span'); fallback.className = 'md-source-fragment';
        rawText(fallback, start, end); parent.appendChild(fallback);
      }
    }

    function safeUrl(url, image = false) {
      if (typeof url !== 'string' || /[\x00-\x20\x7f]/.test(url)) return null;
      try {
        const parsed = new URL(url, location.href);
        if (['http:', 'https:'].includes(parsed.protocol)) return parsed.href;
        if (!image && parsed.protocol === 'mailto:') return parsed.href;
      } catch (_) {}
      return null;
    }

    const definitions = new Map();
    const hidden = /^\ufeff?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/.exec(source);
    const parsedSource = hidden ? hidden[0].replace(/[^\r\n]/g, ' ') + source.slice(hidden[0].length) : source;
    const tree = options.raw ? null : GrankaMarkdownParser.parse(parsedSource);
    function scan(n) {
      if (n.type === 'definition') definitions.set(n.identifier, n);
      for (const c of n.children || []) scan(c);
    }
    if (tree) scan(tree);

    function children(parent, node) { for (const child of node.children || []) visit(parent, child); }
    function link(parent, node) {
      const definition = node.type === 'linkReference' ? definitions.get(node.identifier) : node;
      const url = definition && safeUrl(definition.url);
      const a = make(url ? 'a' : 'span');
      if (url) { a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      if (url) a.dataset.mdUrl=definition.url;
      if (definition?.title) a.title = definition.title;
      children(a, node); parent.appendChild(a);
    }
    function image(parent, node) {
      const definition = node.type === 'imageReference' ? definitions.get(node.identifier) : node;
      const figure = make('span'); figure.className = 'md-image';
      const raw = definition?.url || '';
      figure.dataset.mdUrl=raw;figure.dataset.mdAlt=node.alt||'';figure.contentEditable='false';
      const remote = /^https?:\/\//i.test(raw);
      const url = remote ? safeUrl(raw, true) : (!/^[a-z][a-z\d+.-]*:/i.test(raw) && !raw.startsWith('//') ? options.imageUrl?.(raw) : null);
      const caption = make('span'); caption.className = 'md-image-caption';
      caption.textContent = node.alt || 'Иллюстрация';
      if (url) {
        const video = /\.mp4(?:[?#]|$)/i.test(raw);
        const img = make(video ? 'video' : 'img'); img.src = url;
        if(video){img.controls=true;img.preload='auto';img.setAttribute('aria-label',node.alt||'Видео');}
        else {img.alt = node.alt || '';img.loading = 'lazy';}
        img.referrerPolicy = 'no-referrer';
        img.addEventListener('error', () => { img.hidden = true; caption.textContent += video?' · видео недоступно':' · изображение недоступно'; });
        figure.appendChild(img);
      }
      figure.appendChild(caption); parent.appendChild(figure);
    }
    function visit(parent, node) {
      const tags = {paragraph:'p', blockquote:'blockquote', emphasis:'em', strong:'strong', delete:'del'};
      if (tags[node.type]) { const e = make(tags[node.type]); children(e, node); parent.appendChild(e); return; }
      switch (node.type) {
        case 'root':
          for(const child of node.children||[]){
            const count=parent.childNodes.length;visit(parent,child);
            const created=[...parent.childNodes].slice(count);
            if(created.length===1&&created[0].nodeType===Node.ELEMENT_NODE){
              const element=created[0],key='b'+blocks.length;element.dataset.mdBlock=key;
              blocks.push({key,element,start:child.position.start.offset,end:child.position.end.offset,
                snapshot:semantic(element)});
            }
          }
          break;
        case 'text': text(parent, node); break;
        case 'heading': { const h = make('h' + Math.min(6, node.depth)); children(h, node); parent.appendChild(h); break; }
        case 'list': { const l = make(node.ordered ? 'ol' : 'ul'); if (node.ordered && node.start != null) l.start = node.start; children(l, node); parent.appendChild(l); break; }
        case 'listItem': {
          const li = make('li');
          if (typeof node.checked === 'boolean') {
            li.className = 'md-task'; const cb = make('input'); cb.type = 'checkbox';
            cb.checked = node.checked; cb.disabled = true; cb.setAttribute('aria-label', node.checked ? 'Выполнено' : 'Не выполнено'); li.appendChild(cb);
          }
          children(li, node); parent.appendChild(li); break;
        }
        case 'break': parent.appendChild(make('br')); break;
        case 'thematicBreak': parent.appendChild(make('hr')); break;
        case 'link': case 'linkReference': link(parent, node); break;
        case 'image': case 'imageReference': image(parent, node); break;
        case 'inlineCode': {
          const e = make('code'), raw = slice(node), delimiter = /^`+/.exec(raw)?.[0].length || 1;
          let start = node.position.start.offset + delimiter, end = node.position.end.offset - delimiter;
          if (/^[ \r\n]/.test(source.slice(start, end)) && /[ \r\n]$/.test(source.slice(start, end)) && /[^ ]/.test(node.value)) { start++; end--; }
          text(e, {...node, value:node.value.replace(/\r\n|\r|\n/g, ' ')}, true, start, end); parent.appendChild(e); break;
        }
        case 'code': {
          const pre = make('pre'), code = make('code');
          let start = node.position.start.offset, end = node.position.end.offset;
          if (/^ {0,3}(?:`{3,}|~{3,})/.test(slice(node))) start = source.indexOf('\n', start) + 1;
          if (node.lang) pre.dataset.language = node.lang;
          text(code, node, true, start, end); pre.appendChild(code); parent.appendChild(pre); break;
        }
        case 'table': {
          const wrap = make('div'); wrap.className = 'md-table'; const table = make('table');
          const head = make('thead'), body = make('tbody');
          node.children.forEach((row, i) => {
            const tr = make('tr');
            row.children.forEach((cell, j) => { const td = make(i === 0 ? 'th' : 'td');
              if (['left','right','center'].includes(node.align?.[j])) td.style.textAlign = node.align[j];
              children(td, cell); tr.appendChild(td); });
            (i === 0 ? head : body).appendChild(tr);
          });
          table.append(head, body); wrap.appendChild(table); parent.appendChild(wrap); break;
        }
        case 'definition': break;
        case 'html': { const e = make('span'); e.className = 'md-source-fragment'; rawText(e, node.position.start.offset, node.position.end.offset); parent.appendChild(e); break; }
        default: if (node.children) children(parent, node); else if (node.position) rawText(parent, node.position.start.offset, node.position.end.offset);
      }
    }
    if (options.raw) rawText(box, 0, source.length); else visit(box, tree);

    function semantic(node){
      const clone=node.cloneNode(true);
      for(const m of clone.querySelectorAll('mark'))m.replaceWith(...m.childNodes);
      for(const e of [clone,...clone.querySelectorAll('*')]){
        e.removeAttribute('data-md-block');e.removeAttribute('contenteditable');e.removeAttribute('data-picked');
      }
      return clone.outerHTML;
    }
    function markdownOf(nodes){
      const holder=make('div');for(const n of nodes)holder.appendChild(n.cloneNode(true));
      return GrankaMarkdownParser.toMarkdown(holder);
    }
    function serialize(){
      if(options.raw)return box.innerText;
      const original=new Map(blocks.map(b=>[b.key,b])),seen=new Set(),current=[],patches=[];
      for(const node of box.childNodes){
        if(node.nodeType===Node.TEXT_NODE&&!node.textContent.trim())continue;
        const key=node.nodeType===Node.ELEMENT_NODE?node.dataset.mdBlock:null;
        const known=original.get(key);
        if(known&&!seen.has(key)){
          seen.add(key);current.push({node,known});
          if(semantic(node)!==known.snapshot)patches.push({start:known.start,end:known.end,text:markdownOf([node])});
        }else current.push({node});
      }
      for(const block of blocks)if(!seen.has(block.key)){
        // Removing a block takes its own separator with it, otherwise blank lines pile up.
        const gap=/^(?:[ \t]*\r?\n){1,2}/.exec(source.slice(block.end));
        patches.push({start:block.start,end:block.end+(gap?gap[0].length:0),text:''});
      }
      for(let i=0;i<current.length;i++){
        if(current[i].known)continue;
        const first=i,nodes=[];while(i<current.length&&!current[i].known)nodes.push(current[i++].node);
        const next=current[i]?.known,prev=current[first-1]?.known;
        const at=prev?prev.end:next?next.start:blocks[0]?.start||0;
        const md=markdownOf(nodes);
        patches.push({start:at,end:at,text:(prev?'\n\n':'')+md+(next?'\n\n':'')});i--;
      }
      let text=source;
      for(const p of patches.sort((a,b)=>b.start-a.start||b.end-a.end))text=text.slice(0,p.start)+p.text+text.slice(p.end);
      return text;
    }

    function toCp(offset, roundUp = false) {
      let lo = 0, hi = cp.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (cp[mid] < offset) lo = mid + 1; else hi = mid; }
      return cp[lo] === offset || roundUp ? lo : Math.max(0, lo - 1);
    }
    return {
      serialize,
      blockOffsets(element) {
        const block = blocks.find(b => b.element === element);
        return block ? {start:toCp(block.start), end:toCp(block.end,true)} : null;
      },
      selectionOffsets(range) {
        if (!box.contains(range.startContainer) || !box.contains(range.endContainer) || range.collapsed) return null;
        let start = null, end = null;
        for (const row of rows) {
          const n = row.node;
          if (!range.intersectsNode(n)) continue;
          const from = range.startContainer === n ? range.startOffset : 0;
          const until = range.endContainer === n ? range.endOffset : n.length;
          if (from >= until || range.comparePoint(n, until) < 0 || range.comparePoint(n, from) > 0) continue;
          if (start == null) start = row.starts[from];
          end = row.ends[until - 1];
        }
        return start != null && end > start ? {start:toCp(start), end:toCp(end, true)} : null;
      },
      rangeAtOffset(offset) {
        const wanted = cp[offset]; let best = null, distance = Infinity;
        for (const row of rows) for (let i = 0; i < row.starts.length; i++) {
          const d = Math.abs(row.starts[i] - wanted);
          if (d < distance) { best = {node:row.node, offset:i}; distance = d; }
          if (d === 0) break;
        }
        for(const row of rows)if(row.ends.at(-1)===wanted){best={node:row.node,offset:row.node.length};break;}
        if (!best) return null;
        const range = document.createRange(); range.setStart(best.node, best.offset); range.collapse(true); return range;
      }
    };
  }
  window.GrankaMarkdown = {render};
})();
