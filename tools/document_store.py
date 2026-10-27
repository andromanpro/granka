"""The only writer of annotation documents: revision checks, validation, atomic save."""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from .covers import validate_cover
from .deep_analysis import validate_reports

_WINDOWS=os.name=='nt'


def validate(data):
    if not isinstance(data,dict):return ['Документ должен быть объектом']
    versions,annotations=data.get('versions'),data.get('annotations')
    if not isinstance(versions,list) or not versions:return ['versions: пустой или не список']
    if not isinstance(annotations,list):return ['annotations: не список']
    errors=[];texts={};ids=set();spans={}
    for v in versions:
        if not isinstance(v,dict) or not isinstance(v.get('id'),str) or not isinstance(v.get('text'),str):
            errors.append('Версия без id/text');continue
        if v['id'] in texts:errors.append('Повтор id версии: '+v['id'])
        try:
            validate_cover(v.get('cover'))
        except ValueError as error:
            errors.append(v['id']+': '+str(error))
        texts[v['id']]=v['text']
    for a in annotations:
        if not isinstance(a,dict):errors.append('Некорректная пометка');continue
        aid=a.get('id')
        if not isinstance(aid,str) or aid in ids:errors.append('Некорректный или повторный id пометки');continue
        ids.add(aid)
        if a.get('type') not in {'candidate','protected','note'}:errors.append(aid+': неизвестный тип')
        text=texts.get(a.get('version'))
        if text is None:errors.append(aid+': версия не найдена');continue
        start,end=a.get('start'),a.get('end')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text):
            errors.append(aid+': позиции вне текста');continue
        if text[start:end]!=a.get('quote'):errors.append(aid+': цитата не совпадает с исходником')
        for left,right,other in spans.get(a['version'],[]):
            if not(end<=left or start>=right):errors.append(aid+': пересечение с '+other)
        spans.setdefault(a['version'],[]).append((start,end,aid))
    return errors + validate_reports(data, texts)


class DocumentStore:
    def __init__(self,roots,lock):self.roots=roots;self.lock=lock

    @staticmethod
    def revision(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]

    def resolve(self,root_id,relative,suffix='.granka.json'):
        root=self.roots().get(root_id)
        if root is None:raise LookupError('unknown root')
        path=(root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.name.lower().endswith(suffix.lower()):
            raise PermissionError('path outside permitted article root')
        return path

    def read(self,path):
        raw=Path(path).read_bytes()
        return json.loads(raw),hashlib.sha256(raw).hexdigest()[:16]

    def mutate(self,path,expected_revision,operation_id,fn):
        path=Path(path)
        with self.lock:
            if not path.is_file():raise LookupError('document not found')
            data,revision=self.read(path)
            operations=data.setdefault('ops',[])
            if operation_id in operations:return 200,{'ok':True,'idempotent':True,'revision':revision}
            if expected_revision!=revision:return 409,{'error':'conflict','revision':revision}
            errors=validate(data)
            if errors:raise ValueError(errors[0])
            result=fn(data)
            errors=validate(data)
            if errors:raise ValueError(errors[0])
            if isinstance(data.get('history'),list):data['history']=data['history'][-800:]
            operations.append(operation_id);data['ops']=operations[-100:]
            temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                temporary.write_text(json.dumps(data,ensure_ascii=False,indent=1),encoding='utf-8',newline='\n')
                # Readers and virus scanners may briefly omit FILE_SHARE_DELETE.
                # Retry the rename only; never repeat the document operation.
                for attempt in range(8):
                    try:
                        temporary.replace(path)
                        break
                    except PermissionError as error:
                        if not _WINDOWS or getattr(error,'winerror',None) not in {5,32,33} or attempt==7:
                            raise
                        time.sleep(min(.02*2**attempt,.25))
                        current_revision=self.revision(path)
                        if current_revision!=revision:
                            return 409,{'error':'conflict','revision':current_revision}
            finally:temporary.unlink(missing_ok=True)
            return 200,{'ok':True,'revision':self.revision(path),**(result if isinstance(result,dict) else {})}
