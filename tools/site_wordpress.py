"""Use the existing blog renderer and WordPress CLI over the configured SSH routes."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid
from . import config as environment

STAGES = {'nas':'Предпросмотр','staging':'Стейдж','prod':'Прод'}


def load_engine(path):
    spec=importlib.util.spec_from_file_location('granka_site_engine_'+uuid.uuid4().hex,path)
    module=importlib.util.module_from_spec(spec)
    try:spec.loader.exec_module(module)
    except (ImportError,SystemExit):raise ValueError('Для экспорта установи зависимости: py -3.14 -m pip install -r requirements.txt') from None
    return module


def fingerprint(state):
    return hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class WordPress:
    def __init__(self,stage,directory,environments=None):
        environments=environment.load()['site']['environments'] if environments is None else environments
        if stage not in STAGES or stage not in environments:raise ValueError('Настрой контуры site.environments в конфигурации')
        self.stage=stage;self.env=environments[stage];self.directory=Path(directory)
        for key in ['ssh','wp']:
            if not isinstance(self.env.get(key),list) or not self.env[key] or not all(isinstance(v,str) and v for v in self.env[key]):
                raise ValueError('Некорректная настройка контура: '+key)
        if not self.env.get('url','').startswith(('https://','http://')):raise ValueError('Укажи URL контура')

    def ssh(self,command,input=None):
        cmd=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12',*(os.path.expanduser(arg) for arg in self.env['ssh']),command]
        for attempt in range(3):
            try:
                result=subprocess.run(cmd,input=input,capture_output=True,timeout=180,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except subprocess.TimeoutExpired:
                raise RuntimeError('SSH не подтвердил завершение. Проверь результат на сайте перед повтором.') from None
            with (self.directory/'wordpress.log').open('ab') as log:
                log.write(result.stdout+b'\n'+result.stderr+b'\n')
            # These errors prove that the TCP connection never opened. Do not retry
            # timeouts or disconnects after the remote command may have started.
            preconnect=re.search(rb'^ssh: connect to host .+ port \d+: (Connection timed out|Connection refused|No route to host)\s*$',result.stderr)
            if result.returncode==255 and preconnect and attempt<2:time.sleep(attempt+1);continue
            break
        if result.returncode:raise RuntimeError('Команда WordPress завершилась ошибкой. Подробности в журнале задания.')
        return result.stdout.decode('utf-8').strip()

    def wp(self,args,content=None):
        # Expand only trusted home-directory paths, quote every article-derived argument.
        prefix=[]
        for arg in self.env['wp']:
            if arg.startswith('~/'):prefix.append('"$HOME"/'+shlex.quote(arg[2:]))
            elif arg.startswith('--path=~/'):prefix.append('--path="$HOME"/'+shlex.quote(arg[9:]))
            else:prefix.append(shlex.quote(arg))
        return self.ssh(' '.join(prefix+[shlex.quote(str(a)) for a in args]),
                        content.encode('utf-8') if isinstance(content,str) else content)

    def inspect(self,slug):
        encoded=base64.b64encode(slug.encode()).decode()
        php=("$p=get_posts(['post_type'=>['post','page'],'post_status'=>'any','numberposts'=>10,'name'=>base64_decode('"+encoded+"')]);"
             "if(count($p)>1){throw new Exception('Duplicate slug');}"
             "if(!$p){echo 'null';return;} $p=$p[0];"
             "$r=['id'=>$p->ID,'type'=>$p->post_type,'slug'=>$p->post_name,'title'=>$p->post_title,'status'=>$p->post_status,"
             "'content'=>$p->post_content,'excerpt'=>$p->post_excerpt,'date'=>$p->post_date,'modified'=>$p->post_modified_gmt,"
             "'categories'=>wp_get_post_terms($p->ID,'category',['fields'=>'names']),'tags'=>wp_get_post_terms($p->ID,'post_tag',['fields'=>'names']),"
             "'cover'=>(int)get_post_thumbnail_id($p->ID),'url'=>get_permalink($p->ID)];echo json_encode($r);")
        state=json.loads(self.wp(['eval',php]))
        if state and state['type']!='post':raise ValueError('Этот адрес занят страницей сайта; выбери другой slug')
        return state

    def media(self,path,engine):
        path=Path(path)
        # Exported filenames are content hashes; lookup cannot inject SQL or collide by basename.
        if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|jpeg|webp|gif)',path.name):raise ValueError('Непроверенное имя картинки')
        existing=engine.find_attachment_by_filename(path.name,'')
        if not existing:
            pattern=json.dumps('%/granka-%-'+path.name)
            php=('global $wpdb;$id=$wpdb->get_var($wpdb->prepare("SELECT ID FROM $wpdb->posts '
                 'WHERE post_type=\'attachment\' AND guid LIKE %s ORDER BY ID DESC LIMIT 1",'+pattern+'));if($id)echo $id;')
            legacy=self.wp(['eval',php])
            if legacy:existing=(int(legacy),'')
        if existing:
            mid=existing[0];url=self.wp(['eval',f'echo wp_get_attachment_url({int(mid)});'])
            if not url.startswith(self.env['url']+'/'):raise ValueError('Адрес картинки относится к другому контуру')
            return mid,url
        temporary='/tmp/granka-'+uuid.uuid4().hex
        remote=temporary+'/'+path.name
        self.remote_shell('mkdir -m 700 '+shlex.quote(temporary)+' && cat > '+shlex.quote(remote),path.read_bytes())
        try:
            mid=int(self.wp(['media','import',remote,'--porcelain']).strip())
            url=self.wp(['eval',f'echo wp_get_attachment_url({mid});'])
            if not url.startswith(self.env['url']+'/'):raise ValueError('Картинка импортирована в другой контур')
            return mid,url
        finally:
            self.remote_shell('rm -f '+shlex.quote(remote)+' && rmdir '+shlex.quote(temporary))

    def remote_shell(self,script,input=None):
        if self.env.get('container'):
            command=shlex.join([self.env.get('docker','docker'),'exec','-i',self.env['container'],'sh','-c',script])
        else:command=script
        return self.ssh(command,input)

    def deploy(self,bundle,baseline):
        import frontmatter
        engine=load_engine(self.directory/'engine.py')
        engine.run_wp=lambda args,site_dir,capture=True:self.wp(args)
        before=self.inspect(bundle['metadata']['slug'])
        if fingerprint(before)!=baseline:raise ValueError('Пост изменился после предпросмотра. Проверь его заново.')
        post=frontmatter.load(self.directory/'bundle'/bundle['file']);meta=bundle['metadata']
        urls={};cover_id=0
        for item in bundle['assets']:
            mid,url=self.media(self.directory/'bundle'/item,engine)
            urls['../'+item]=url
            if item==bundle.get('cover'):cover_id=mid
        content=post.content
        for source,url in urls.items():content=content.replace(source,url)
        html=engine.render_markdown(content)
        categories=[str(engine.ensure_term('category',term,'')) for term in meta['categories']]
        tags=[str(engine.ensure_term('post_tag',term,'')) for term in meta['tags']]
        desired_status=meta['status'] if self.stage=='prod' else 'publish'
        server_date=self.wp(['eval',"echo current_time('Y-m-d');"])
        if desired_status=='publish' and meta['date']>server_date:
            raise ValueError('Дата статьи ещё не наступила в часовом поясе сайта')
        args=['post','update',str(before['id']),'-'] if before else ['post','create','-','--post_type=post','--porcelain']
        args += ['--post_title='+meta['title'],'--post_name='+meta['slug'],'--post_status='+desired_status,
                 '--post_excerpt='+meta['excerpt'],'--post_date='+meta['date']+' 00:00:00']
        result=self.wp(args,html)
        post_id=before['id'] if before else int(result.strip())
        self.wp(['post','term','set',str(post_id),'category',*categories,'--by=id'])
        if tags:self.wp(['post','term','set',str(post_id),'post_tag',*tags,'--by=id'])
        else:self.wp(['post','term','remove',str(post_id),'post_tag','--all'])
        if cover_id:self.wp(['post','meta','update',str(post_id),'_thumbnail_id',str(cover_id)])
        elif before and before['cover']:self.wp(['post','meta','delete',str(post_id),'_thumbnail_id'])
        after=self.inspect(meta['slug'])
        if (not after or after['id']!=post_id or after['title']!=meta['title'] or after['status']!=desired_status or
            after['excerpt']!=meta['excerpt'] or after['cover']!=cover_id or
            set(after['categories'])!=set(meta['categories']) or set(after['tags'])!=set(meta['tags']) or
            after['content'].strip()!=html.strip()):
            raise RuntimeError('WordPress сохранил не все поля ожидаемым образом. Следующий контур заблокирован; проверь результат.')
        if not after['url'].startswith(self.env['url']+'/'):raise RuntimeError('WordPress вернул ссылку другого контура')
        return {'post_id':post_id,'url':after['url'],'post_status':after['status'],'fingerprint':fingerprint(after)}
