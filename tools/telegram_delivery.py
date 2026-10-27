"""Cross-process delivery exclusion and edits that never create messages."""
from pathlib import Path
from .file_lock import file_lock


class PublicationBusy(ValueError):
    pass


def delivery_lock(directory):
    return file_lock(Path(directory)/'delivery.lock',
        'В другом окне Гранки уже выполняется операция Telegram. Дождись её завершения.',PublicationBusy)


def kind(record):
    if record.get('rich_message')or record.get('mode')=='article':return 'rich'
    count=len(record.get('attachments',[]))
    if count>1:return 'album'
    if count or record.get('with_cover'):return 'media'
    return 'text'


def post_key(record):
    ids=record.get('message_ids')or[record.get('message_id')]
    return (record['channel']['chat_id'],tuple(ids))


def head(records,key=None):
    values=[r for r in records if r['status']=='sent'and(key is None or post_key(r)==key)]
    # Follow the receipt chain, even when Windows timestamps are identical.
    replaced={r.get('target_id'if key is not None else'base_source_id')for r in values}
    values=[r for r in values if r['id']not in replaced]
    return max(values,key=lambda r:(r.get('finished',r['created']),r['id']))if values else None


def validate_edit(new,old):
    old_kind,new_kind=kind(old),kind(new)
    ids=old.get('message_ids')or[old.get('message_id')]
    if not ids or any(type(v)is not int or v<=0 for v in ids):raise ValueError('У прежней публикации нет подтверждённых номеров сообщений')
    if new_kind in {'text','rich'}and old_kind not in {'text','rich'}:
        raise ValueError('Медиа-пост нельзя заменить текстовым сообщением. Сохрани его формат или явно выбери «Создать новый».')
    if new_kind=='media'and old_kind=='album':raise ValueError('Для обновления альбома сохрани число вложений. Новые сообщения автоматически не создаются.')
    if new_kind=='album'and(old_kind!='album'or len(new['attachments'])!=len(ids)):
        raise ValueError('Для обновления альбома нужно то же число вложений. Для другого состава явно выбери «Создать новый».')


def edit_calls(new,old):
    validate_edit(new,old)
    base={'chat_id':old['channel']['chat_id'],'message_id':old['message_id']}
    mode=kind(new)
    if mode in {'text','rich'}:
        value={'rich_message':new['rich_message']}if mode=='rich'else{'text':new['text'],'link_preview_options':{'is_disabled':True}}
        files={k:Path(v)for k,v in new.get('media_paths',{}).items()}if mode=='rich'else None
        return [('editMessageText',{**base,**value},files)]
    files={k:Path(v)for k,v in new.get('media_paths',{}).items()}
    items=new.get('attachments',[])
    if not items:items=[{'field':'cover','type':'photo'}];files={'cover':Path(new['photo_path'])}
    ids=old.get('message_ids')or[old['message_id']]
    calls=[]
    for i,item in enumerate(items):
        value={'type':item['type'],'media':'attach://media'}
        if i==0:value['caption']=new['text']
        if item['type']=='video':value['supports_streaming']=True
        calls.append(('editMessageMedia',{**base,'message_id':ids[i],'media':value},{'media':files[item['field']]}))
    return calls
