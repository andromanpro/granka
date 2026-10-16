"""Immutable article bundles promoted through Git, preview, staging and production."""
import datetime
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote,urlsplit
import uuid
from .ai_provider import atomic_json
from .editorial_requests import editorial_requests
from .site_wordpress import STAGES,WordPress,fingerprint,load_engine
from . import config as environment

ENGINE=Path(__file__).with_name('site_engine.py')


def source_hash(data):
    v=data['versions'][-1]
    return fingerprint({'id':v['id'],'text':v['text'],'cover':v.get('cover')})


def metadata(values):
    if not isinstance(values,dict):raise ValueError('Некорректные поля публикации')
    result={}
    for key,cap in [('title',300),('slug',100),('excerpt',1000),('date',10)]:
        value=values.get(key,'')
        if not isinstance(value,str) or len(value)>cap or '\0' in value or '\n' in value:raise ValueError('Некорректное поле '+key)
        result[key]=value.strip()
    if not result['title']:raise ValueError('Нужен заголовок статьи')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',result['slug']):raise ValueError('Адрес: строчные латинские буквы, цифры и дефис')
    date=datetime.date.fromisoformat(result['date'])
    if date>datetime.date.today():raise ValueError('Будущая дата создаст отложенный пост. Для этого выпуска выбери сегодняшнюю или прошлую дату.')
    result['status']=values.get('status','draft')
    if result['status'] not in {'draft','publish'}:raise ValueError('Выбери черновик или публикацию на проде')
    for key in ['categories','tags']:
        items=values.get(key,[])
        if not isinstance(items,list) or len(items)>10 or any(not isinstance(x,str) or not x.strip() or len(x)>80 or '\0' in x or '\n' in x for x in items):
            raise ValueError('Некорректный список '+key)
        result[key]=list(dict.fromkeys(x.strip() for x in items))
    if not result['categories']:raise ValueError('Укажи хотя бы одну категорию')
    return result


class Images(HTMLParser):
    def __init__(self):super().__init__();self.sources=[]
    def handle_starttag(self,tag,attrs):
        if tag=='img':self.sources.extend(v for k,v in attrs if k=='src' and v)


class SitePublisher:
    def __init__(self,state,repo=None,remote=None,wordpress=None,environments=None):
        config=environment.load()['site']
        self.state=state
        self.repo=Path(repo) if repo is not None else environment.path(config['repository']) if config.get('repository') else None
        self.remote=remote if remote is not None else config.get('remote','')
        self.branch=config.get('branch','main')
        self.environments=environments if environments is not None else config.get('environments',{})
        self.enabled=(repo is not None or config.get('enabled',False)) and (wordpress is not None or all(stage in self.environments for stage in STAGES))
        self.wordpress=wordpress or (lambda stage,directory:WordPress(stage,directory,self.environments))
        self.lock=threading.RLock()

    def require_config(self):
        if not self.enabled or not self.repo or not self.remote:
            raise ValueError('Настрой site.repository, site.remote и site.environments, затем включи site.enabled')

    @property
    def directory(self):return Path(self.state())/'site-publications'

    def git(self,*args,input=None,check=True):
        self.require_config()
        env=environment.git_environment()
        r=subprocess.run(['git','-C',str(self.repo),*environment.git_options(),*args],input=input,capture_output=True,timeout=120,env=env,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if check and r.returncode:raise RuntimeError('Git: '+r.stderr.decode('utf-8','replace')[-500:])
        return r.stdout.decode('utf-8').strip()

    def info(self,data,root=None,rel=None):
        import frontmatter
        post=frontmatter.loads(data['versions'][-1]['text']);fm=post.metadata
        history=self.plans(root,rel) if root is not None and rel is not None else []
        published=next((p for p in history if p['stages'].get('prod',{}).get('status')=='succeeded'),None)
        prior=(published or (history[0] if history else {})).get('metadata',{})
        heading=re.search(r'^#\s+(.+)$',post.content,re.M)
        title=str(fm.get('title') or (heading[1] if heading else data.get('doc','Статья')))
        table=dict(zip('абвгдеёзийклмнопрстуфхцчшщъыьэюя',
                       ['a','b','v','g','d','e','yo','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','ts','ch','sh','sch','','y','','e','yu','ya']))
        slug=re.sub('[^a-z0-9]+','-',''.join(table.get(c,c) for c in title.lower())).strip('-')[:90].rstrip('-') or 'article'
        return {'configured':bool(self.enabled and self.repo and self.remote),'environments':{k:{'label':self.environments.get(k,{}).get('label',label),'url':self.environments.get(k,{}).get('url','')} for k,label in STAGES.items()},
                'repo':str(self.repo) if self.repo else '',
                'publication':({'url':published['stages']['prod']['url'],'post_id':published['stages']['prod']['post_id'],
                                'post_status':published['stages']['prod']['post_status'],'version':published['version'],
                                'date':published['metadata']['date'],'changed':published['source_hash']!=source_hash(data)} if published else None),
                'metadata':{'title':title,'slug':str(prior.get('slug') or fm.get('slug') or slug),
                'date':str(prior.get('date') or fm.get('date') or datetime.date.today())[:10],
                'status':prior.get('status') or (fm.get('status') if fm.get('status') in {'draft','publish'} else 'draft'),
                'categories':fm.get('categories') or prior.get('categories') or ['дев-лог'],
                'tags':fm.get('tags',prior.get('tags',[])) or [],'excerpt':fm.get('excerpt',prior.get('excerpt','')) or ''}}

    def path(self,identifier):
        if not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('Неизвестный снимок публикации')
        return self.directory/identifier

    def read(self,identifier):
        path=self.path(identifier)/'plan.json'
        if not path.exists():raise LookupError('Снимок публикации не найден')
        return json.loads(path.read_text(encoding='utf-8'))

    def plans(self,root,rel):
        result=[]
        for p in self.directory.glob('*/plan.json'):
            record=json.loads(p.read_text(encoding='utf-8'))
            if record['root']==root and record['rel']==rel:result.append(record)
        return sorted(result,key=lambda r:r['created'],reverse=True)

    def prepare(self,root,rel,data,document,source_root,values):
        import frontmatter
        self.require_config()
        meta=metadata(values);version=data['versions'][-1]
        owners=self.plans(root,rel)
        published=next((p for p in owners if p['stages'].get('prod',{}).get('status')=='succeeded'),None)
        if published and meta['slug']!=published['metadata']['slug']:
            raise ValueError('У опубликованной статьи сохраняется прежний адрес. Переименование с редиректом — отдельная операция.')
        if published and meta['date']!=published['metadata']['date']:
            raise ValueError('Дата опубликованной статьи сохраняется: она может входить в её адрес.')
        pending=editorial_requests(version['text'])
        if pending:raise ValueError('Перед публикацией заполни редакторские вставки: '+pending[0]['quote'][:100])
        if (self.git('remote','get-url','origin').rstrip('/')!=self.remote.rstrip('/') or
                self.git('remote','get-url','--push','origin').rstrip('/')!=self.remote.rstrip('/')):
            raise ValueError('У blog-content другой origin; проверь настройки публикации')
        post=frontmatter.loads(version['text']);body=post.content
        # The page template already prints the title.
        body=re.sub(r'\A\s*# [^\n]+\n','',body,count=1).lstrip()
        identifier=uuid.uuid4().hex;directory=self.path(identifier);bundle=directory/'bundle';bundle.mkdir(parents=True)
        shutil.copyfile(ENGINE,directory/'engine.py')
        engine=load_engine(directory/'engine.py');parser=Images();parser.feed(engine.render_markdown(body))
        assets={}
        def copy_image(src):
            parsed=urlsplit(src)
            if parsed.scheme in {'http','https'}:return src,None
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:raise ValueError('Неподдерживаемый путь картинки: '+src[:100])
            path=(document.parent/unquote(src)).resolve()
            if not path.is_relative_to(source_root.resolve()) or path.suffix.lower() not in {'.png','.jpg','.jpeg','.gif','.webp'} or not path.is_file():
                raise ValueError('Картинка не найдена в каталоге статьи: '+src[:100])
            raw=path.read_bytes()
            if len(raw)>25*1024*1024:raise ValueError('Картинка больше 25 МБ')
            target='covers/'+hashlib.sha256(raw).hexdigest()+path.suffix.lower()
            (bundle/'covers').mkdir(exist_ok=True);(bundle/target).write_bytes(raw);assets[target]=hashlib.sha256(raw).hexdigest()
            return '../'+target,target
        for src in dict.fromkeys(parser.sources):
            replacement,_=copy_image(src)
            if replacement!=src:
                # Replace only Markdown destinations/definitions and HTML src attributes, not prose.
                escaped=re.escape(src)
                body=re.sub(r'(?<=\()'+escaped+r'(?=[\s)])',lambda m:replacement,body)
                body=re.sub(r'(?<=<)'+escaped+r'(?=>)',lambda m:replacement,body)
                body=re.sub(r'(?m)^(\s*\[[^\]]+\]:\s*)'+escaped+r'(?=\s|$)',lambda m:m[1]+replacement,body)
                body=re.sub(r'(\bsrc=["\'])'+escaped+r'(?=["\'])',lambda m:m[1]+replacement,body)
        check=Images();check.feed(engine.render_markdown(body))
        if any(not s.startswith(('http://','https://','../covers/')) for s in check.sources):
            raise ValueError('Не удалось перенести все картинки. Используй обычную вставку картинки в Гранке.')
        cover=None;featured=version.get('cover',{}).get('src') or post.metadata.get('featured_image')
        if featured:
            cover_src,cover=copy_image(str(featured))
            if not cover:raise ValueError('Для обложки выбери локальный файл в Гранке')
        file='posts/'+meta['date']+'-'+meta['slug']+'.md'
        actual_source=document.with_name(document.name.removesuffix('.granka.json')+'.md').resolve()
        if actual_source.parent==self.repo.resolve()/'posts':file=actual_source.relative_to(self.repo.resolve()).as_posix()
        else:
            canonical=next((p['file'] for p in owners if p['metadata']['slug']==meta['slug'] and (self.repo/p['file']).is_file()),None)
            if canonical:file=canonical
        owned=any(p['file']==file for p in owners) or actual_source==self.repo.resolve()/file
        for other in (self.repo/'posts').glob('*.md'):
            other_slug=str(frontmatter.load(other).metadata.get('slug') or re.sub(r'^\d{4}-\d{2}-\d{2}-','',other.stem))
            if other_slug==meta['slug'] and (other.relative_to(self.repo).as_posix()!=file or not owned):
                raise ValueError('Этот адрес уже есть в blog-content. Открой существующую статью для обновления или измени slug.')
        if (self.repo/file).exists() and not owned:raise ValueError('Файл назначения уже существует и не принадлежит этой статье')
        fm={**post.metadata,**meta}
        if cover:fm['featured_image']='../'+cover
        (bundle/'posts').mkdir(exist_ok=True)
        (bundle/file).write_text(frontmatter.dumps(frontmatter.Post(body,**fm))+'\n',encoding='utf-8',newline='\n')
        files={name:hashlib.sha256((bundle/name).read_bytes()).hexdigest() for name in [file,*assets]}
        record={'id':identifier,'root':root,'rel':rel,'version':version['id'],'source_hash':source_hash(data),
                'created':time.time(),'metadata':meta,'file':file,'assets':list(assets),'cover':cover,'files':files,
                'engine_hash':hashlib.sha256((directory/'engine.py').read_bytes()).hexdigest(),
                'baseline_files':{name:hashlib.sha256((self.repo/name).read_bytes()).hexdigest() if (self.repo/name).is_file() else None for name in files},
                'stages':{}}
        atomic_json(directory/'plan.json',record)
        return record

    def gate(self,plan,stage):
        self.require_config()
        if stage not in STAGES:raise ValueError('Неизвестный контур')
        previous={'staging':'nas','prod':'staging'}.get(stage)
        if previous and plan['stages'].get(previous,{}).get('status')!='succeeded':
            raise ValueError('Сначала заверши предыдущий контур для этого снимка')

    def verify_previous(self,plan,stage):
        previous={'staging':'nas','prod':'staging'}.get(stage)
        if previous:
            state=self.wordpress(previous,self.path(plan['id'])).inspect(plan['metadata']['slug'])
            if fingerprint(state)!=plan['stages'][previous]['fingerprint']:
                raise ValueError('Страница предыдущего контура изменилась после публикации. Перенеси правку в статью и подготовь новый снимок.')

    def review(self,identifier,stage):
        with self.lock:
            plan=self.read(identifier);self.gate(plan,stage)
            self.verify_previous(plan,stage)
            state=self.wordpress(stage,self.path(identifier)).inspect(plan['metadata']['slug'])
            review={'id':uuid.uuid4().hex,'stage':stage,'fingerprint':fingerprint(state),'created':time.time(),
                    'existing':{k:state[k] for k in ['id','title','status','url','modified']} if state else None}
            plan['review']=review;atomic_json(self.path(identifier)/'plan.json',plan)
            return review

    def push_bundle(self,plan):
        directory=self.path(plan['id']);branch=self.git('branch','--show-current')
        if branch!=self.branch:raise ValueError('Открой настроенную ветку публикации: '+self.branch)
        for args in [('remote','get-url','origin'),('remote','get-url','--push','origin')]:
            if self.git(*args).rstrip('/')!=self.remote.rstrip('/'):raise ValueError('Адрес Git remote изменился после подготовки')
        self.git('fetch','origin',self.branch)
        head=self.git('rev-parse','HEAD');remote=self.git('rev-parse','origin/'+self.branch)
        if head!=remote and head!=plan.get('git_commit'):
            raise ValueError('В blog-content есть другие неотправленные коммиты или ветки разошлись. Синхронизируй репозиторий отдельно.')
        paths=list(plan['files'])
        if not plan.get('git_commit'):
            for name,expected in plan['baseline_files'].items():
                path=(self.repo/name).resolve()
                if not path.is_relative_to(self.repo.resolve()):raise ValueError('Путь вне blog-content')
                actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                if actual!=expected:raise ValueError('Файл в blog-content изменился после подготовки: '+name)
            for name in paths:
                target=self.repo/name;target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.tmp')
                temporary.write_bytes((directory/'bundle'/name).read_bytes());os.replace(temporary,target)
            if self.git('status','--porcelain','--',*paths):
                self.git('add','--',*paths)
                self.git('commit','--only','-m','post: '+plan['metadata']['slug'],'--',*paths)
            plan['git_commit']=self.git('rev-parse','HEAD')
            atomic_json(directory/'plan.json',plan)
        for name,expected in plan['files'].items():
            raw=subprocess.check_output(['git','-C',str(self.repo),'show',plan['git_commit']+':'+name])
            if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Содержимое Git не совпадает со снимком')
        self.git('push','origin',plan['git_commit']+':refs/heads/'+self.branch)
        actual=self.git('ls-remote','origin','refs/heads/'+self.branch).split()[0]
        if actual!=plan['git_commit']:raise RuntimeError('Git remote не подтвердил нужный коммит')
        return {'commit':actual,'url':self.remote.removesuffix('.git')+'/commit/'+actual}

    def deploy(self,identifier,review_id,approved_previous):
        with self.lock:
            plan=self.read(identifier);review=plan.get('review')
            if not review or review['id']!=review_id:raise ValueError('Предпросмотр устарел')
            stage=review['stage'];self.gate(plan,stage)
            self.verify_previous(plan,stage)
            if stage!='nas' and approved_previous is not True:raise ValueError('Подтверди просмотр предыдущего контура')
            if plan['stages'].get(stage,{}).get('status')=='succeeded':return plan['stages'][stage]
            directory=self.path(identifier)
            for name,expected in plan['files'].items():
                if hashlib.sha256((directory/'bundle'/name).read_bytes()).hexdigest()!=expected:raise ValueError('Снимок изменён на диске')
            if hashlib.sha256((directory/'engine.py').read_bytes()).hexdigest()!=plan['engine_hash']:raise ValueError('Движок снимка изменён')
            remote=self.wordpress(stage,directory)
            if fingerprint(remote.inspect(plan['metadata']['slug']))!=review['fingerprint']:raise ValueError('Пост в WordPress изменился. Подготовь предпросмотр заново.')
            plan['stages'][stage]={'status':'running','started':time.time(),'review_id':review_id,'approved_previous':approved_previous}
            atomic_json(directory/'plan.json',plan)
            try:
                if stage=='nas':plan['gitea']=self.push_bundle(plan)
                result=remote.deploy(plan,review['fingerprint'])
                plan['stages'][stage].update(status='succeeded',finished=time.time(),**result)
            except Exception as error:
                plan['stages'][stage].update(status='failed',finished=time.time(),error=str(error))
                atomic_json(directory/'plan.json',plan);raise
            atomic_json(directory/'plan.json',plan)
            return plan['stages'][stage]

    def recover(self):
        for p in self.directory.glob('*/plan.json'):
            plan=json.loads(p.read_text(encoding='utf-8'));changed=False
            for state in plan['stages'].values():
                if state['status']=='running':state.update(status='unknown',error='Публикация прервана. Проверь сайт и подготовь новый предпросмотр.');changed=True
            if changed:atomic_json(p,plan)
