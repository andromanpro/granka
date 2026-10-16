"""Article-scoped snapshots; preserve unrelated staged work and retry pending pushes."""
from pathlib import Path
import threading
import time


class GitHistory:
    def __init__(self,roots,resolve,run):
        self.roots=roots;self.resolve=resolve;self.run=run;self.lock=threading.Lock();self.synced={}

    def repository(self,root_id):
        root=self.roots().get(root_id)
        if root is None:return None
        result=self.run(root,'rev-parse','--show-toplevel')
        return Path(result.stdout.strip()) if result.returncode==0 else None

    def paths(self,root_id,relative,repo):
        if not relative:raise ValueError('Выбери статью для снимка')
        document=self.resolve(root_id,relative)
        stem=document.name.removesuffix('.granka.json')
        candidates=[document,document.with_name(stem+'.md'),document.parent/(stem+'.assets')]
        paths=[]
        for path in candidates:
            path=path.resolve()
            if not path.is_relative_to(repo.resolve()):raise PermissionError('Файл за пределами репозитория')
            rel=path.relative_to(repo.resolve()).as_posix()
            if path.exists() or self.run(repo,'ls-files','--error-unmatch','--',rel).returncode==0:paths.append(rel)
        return paths

    def state(self,root_id,relative=None):
        repo=self.repository(root_id)
        if repo is None:return {'repo':False}
        paths=self.paths(root_id,relative,repo) if relative else []
        status=self.run(repo,'status','--porcelain','--',*paths)
        lines=[line for line in status.stdout.splitlines() if line.strip()]
        log=self.run(repo,'log','-1','--format=%h|%cr|%s').stdout.strip().split('|',2)
        ahead=self.run(repo,'rev-list','--count','@{upstream}..HEAD')
        return {'repo':True,'dirty':len(lines),'files':[line[3:] for line in lines[:8]],
                'ahead':int(ahead.stdout.strip()) if ahead.returncode==0 else None,
                'last':dict(zip(['hash','when','msg'],(log+['','',''])[:3])), 'last_sync':self.synced.get(root_id)}

    def sync(self,root_id,message,push=True,relative=None):
        with self.lock:
            repo=self.repository(root_id)
            if repo is None:return {'ok':False,'error':'Для статьи не найден Git-репозиторий'}
            paths=self.paths(root_id,relative,repo)
            state=self.run(repo,'status','--porcelain','--',*paths)
            committed=False
            if state.stdout.strip():
                add=self.run(repo,'add','--',*paths)
                if add.returncode:return {'ok':False,'error':add.stderr[-300:]}
                commit=self.run(repo,'commit','--only','-m',message,'--',*paths)
                if commit.returncode:return {'ok':False,'error':(commit.stderr or commit.stdout)[-300:]}
                committed=True
            result={'ok':True,'committed':committed,'nothing':not committed}
            if push:
                sent=self.run(repo,'push','origin','HEAD',timeout=90)
                result['pushed']=sent.returncode==0
                if sent.returncode:result['push_error']=(sent.stderr or 'Git remote недоступен')[-300:]
                else:self.synced[root_id]=time.strftime('%H:%M:%S')
            return result
