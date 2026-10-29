"""Persistent background jobs, without automatic retries after uncertain failures."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from .ai_provider import atomic_json
_WINDOWS = os.name == 'nt'


class JobConflict(RuntimeError): pass


class Jobs:
    def __init__(self,directory,recover=False):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock();self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='granka-job')
        for path in (self.directory.glob('*/job.json') if recover else []):
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                if data.get('status') in {'queued','running'}:
                    data.update(status='interrupted',message='Сервер был перезапущен. Проверь журнал перед повторным запуском.')
                    atomic_json(path,data)
            except (OSError,ValueError): pass

    def get(self,job_id):
        if not re.fullmatch(r'[a-f0-9]{24}',job_id):raise LookupError('job not found')
        path=self.directory/job_id/'job.json'
        for attempt in range(8):
            try:
                raw = path.read_text(encoding='utf-8')
                break
            except FileNotFoundError:
                raise LookupError('job not found') from None
            except PermissionError as error:
                if not _WINDOWS or getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 7:
                    raise
                # Windows may briefly deny reads while the atomic status file
                # is being replaced. This never retries the job or its provider.
                time.sleep(min(.02 * 2 ** attempt, .25))
        data=json.loads(raw)
        if data.get('kind')=='deep-analysis' and data.get('status')=='running':
            try:
                stage=json.loads((path.parent/'progress.json').read_text(encoding='utf-8'))['stage']
                messages={'map':'Разбор: 1/3 · составляю карту статьи',
                          'diagnose':'Разбор: 2/3 · проверяю пять направлений',
                          'verify':'Разбор: 3/3 · перепроверяю каждое замечание',
                          'complete':'Разбор готов · сохраняю результат'}
                if stage in messages:data['message']=messages[stage]
            except (OSError,ValueError,KeyError,TypeError):pass
        return data

    def list(self,root,rel):
        records=[]
        for path in self.directory.glob('*/job.json'):
            try:
                data=self.get(path.parent.name)
                if data['root']==root and data['rel']==rel:records.append(data)
            except (OSError,ValueError,KeyError):pass
        return sorted(records,key=lambda d:d['created'],reverse=True)[:20]

    def submit(self,kind,root,rel,operation_id,metadata,work):
        identity='|'.join([kind,root,rel,operation_id])
        job_id=hashlib.sha256(identity.encode()).hexdigest()[:24]
        with self.lock:
            directory=self.directory/job_id
            if (directory/'job.json').is_file():return self.get(job_id)
            # One writer/analyser per article, one image request at a time.
            for job in self.list(root,rel):
                if job['status'] in {'queued','running'} and ((job['kind']=='image')==(kind=='image')):
                    raise ValueError('Предыдущее задание ещё выполняется.')
            job={'id':job_id,'kind':kind,'root':root,'rel':rel,'status':'queued','created':time.time(),
                 'message':'Ожидает запуска',**metadata}
            atomic_json(directory/'job.json',job)
            self.pool.submit(self._run,job,work,directory)
            return job

    def _run(self,job,work,directory):
        job={**job,'status':'running','message':{'apply':'Применяю решения','analysis':'Анализирую статью','deep-analysis':'Разбираю статью: карта, замечания и перепроверка','image':'Генерирую картинку','editorial':'Выполняю редакторское поручение','announcement':'Готовлю анонс для Telegram','site':'Публикую статью на сайте','benchmark':'Проверяю модель на авторских решениях','rules':'Предлагаю уточнения личных правил','spice':'Готовлю предложения по рецепту','article-draft':'Пишу первую заготовку статьи'}[job['kind']]}
        atomic_json(directory/'job.json',job)
        try:
            result=work(directory)
            job.update(status='succeeded',message='Готово',result=result)
        except JobConflict as error:job.update(status='conflict',message=str(error))
        except Exception as error:job.update(status='failed',message=str(error)[:1500])
        job['finished']=time.time();atomic_json(directory/'job.json',job)
