"""Atomic, reviewable decisions for one explicit editorial rule."""
import time


def rule_key(annotation):
    value=annotation.get('rule_id')
    return value if isinstance(value,str) and value else None


def decide_batch(data,version,rule,items,batch_id):
    if data['versions'][-1]['id']!=version:raise ValueError('Пакет доступен только для текущей версии')
    if not isinstance(rule,str) or not rule:raise ValueError('Выбери конкретное правило')
    if not isinstance(items,list) or not 1<=len(items)<=100:raise ValueError('Пакет: от 1 до 100 решений')
    current={a['id']:a for a in data['annotations'] if a['version']==version};pending=[];seen=set()
    for item in items:
        if not isinstance(item,dict):raise ValueError('Некорректное решение')
        identifier=item.get('id')
        if not isinstance(identifier,str):raise ValueError('Некорректный id карточки')
        ann=current.get(identifier)
        if identifier in seen or not ann or ann['type']!='candidate' or ann.get('decision') or rule_key(ann)!=rule or ann.get('editorial_request'):
            raise ValueError('Карточка изменилась, уже решена или относится к другому правилу')
        seen.add(identifier);action=item.get('action')
        if action=='accept':
            index=item.get('variant');variants=ann.get('variants',[])
            if type(index) is not int or not 0<=index<len(variants):raise ValueError('Выбери вариант каждой замены')
            text=variants[index]
            if not isinstance(text,str) or not text.strip() or text.strip()==ann['quote'].strip():raise ValueError('Нет готовой замены')
            decision={'action':'accept','text':text,'intent':'replace'}
        elif action=='reject':
            reason=item.get('reason','worse')
            if reason not in {'voice','ok-here','fp','worse'}:raise ValueError('Укажи причину отказа')
            decision={'action':'reject','reason':reason}
        else:raise ValueError('Для пакета доступны принятие варианта и отказ')
        pending.append((ann,decision))
    ts=time.strftime('%Y-%m-%dT%H:%M:%S')
    if len({(a.get('rule_title') or '').strip().casefold() for a,_ in pending})!=1:
        raise ValueError('У карточек разные описания правила; разбери их отдельно')
    for ann,decision in pending:ann['decision']={**decision,'batch_id':batch_id,'ts':ts}
    data.setdefault('history',[]).append({'ts':ts,'action':'decisions-batch','version':version,'rule_id':rule,
        'batch_id':batch_id,'count':len(pending),'annotations':[a['id'] for a,_ in pending]})
    return {'count':len(pending),'batch_id':batch_id}


def undo_batch(data,version,batch_id):
    if data['versions'][-1]['id']!=version:raise ValueError('Версия уже изменилась')
    event=next((e for e in reversed(data.get('history',[])) if e.get('action')=='decisions-batch' and e.get('batch_id')==batch_id and e.get('version')==version),None)
    if not event:raise ValueError('Пакет не найден')
    annotations={a['id']:a for a in data['annotations'] if a['version']==version}
    targets=[annotations.get(aid) for aid in event['annotations']]
    if any(a is None or (a.get('decision') or {}).get('batch_id')!=batch_id for a in targets):
        raise ValueError('Часть решений уже изменена. Переиграй оставшиеся карточки отдельно.')
    for ann in targets:ann['decision']=None
    data.setdefault('history',[]).append({'ts':time.strftime('%Y-%m-%dT%H:%M:%S'),'action':'batch-undo',
                                        'version':version,'batch_id':batch_id,'count':len(targets)})
    return {'count':len(targets)}
