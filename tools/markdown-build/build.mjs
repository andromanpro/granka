import {build} from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
const output = '../../static/vendor/markdown-parser.js';
const result = await build({entryPoints:['entry.js'], bundle:true, minify:true,
  format:'esm', platform:'browser', target:'es2020',
  outfile:output, metafile:true, legalComments:'none'});
const dirs = new Set();
for (const file of Object.keys(result.metafile.inputs)) {
  let dir=path.dirname(path.resolve(file));
  while (dir!==path.dirname(dir)) {
    if (fs.existsSync(path.join(dir,'package.json'))) {dirs.add(dir);break;}
    dir=path.dirname(dir);
  }
}
const licenses=[];
for(const dir of [...dirs].sort()) {
  const pkg=JSON.parse(fs.readFileSync(path.join(dir,'package.json'),'utf8'));
  if(pkg.private)continue;
  const license=fs.readdirSync(dir).find(n=>/^licen[cs]e(?:\..*)?$/i.test(n));
  if(!license)throw new Error('Missing license: '+pkg.name);
  licenses.push(pkg.name+' '+pkg.version+'\n'+fs.readFileSync(path.join(dir,license),'utf8'));
}
fs.writeFileSync(output.replace('.js','.LICENSE.txt'),licenses.join('\n\n--------------------\n\n'));
console.log('Bundled parser and '+licenses.length+' dependency licenses');
