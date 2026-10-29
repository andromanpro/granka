"""Bot settings, reviewed publication snapshots and a durable no-retry outbox."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from .telegram_media import MAX_TOTAL, MIME, validate_bytes
from .telegram_delivery import delivery_lock, PublicationBusy, head, post_key, validate_edit, edit_calls

_UNSET=object()


class TelegramRejected(RuntimeError):
    """Telegram explicitly rejected the request; no message was accepted."""


class TelegramUnknown(RuntimeError):
    """Delivery may have happened. Retrying could duplicate the publication."""


def bot_call(token,method,payload,photo=None,files=None):
    files=dict(files or {})
    if photo:files['photo']=photo
    if files:
        boundary='granka-'+uuid.uuid4().hex;parts=[]
        for key,value in payload.items():
            if isinstance(value,(dict,list,bool)):value=json.dumps(value,ensure_ascii=False)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode('utf-8'))
        for key,path in files.items():
            if not re.fullmatch(r'[A-Za-z0-9_]{1,64}',key):raise ValueError('Некорректное поле файла')
            parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="media{path.suffix}"\r\nContent-Type: {MIME.get(path.suffix.lower(),"application/octet-stream")}\r\n\r\n'.encode(),path.read_bytes(),b'\r\n'])
        parts.append(f'--{boundary}--\r\n'.encode())
        raw=b''.join(parts);ctype='multipart/form-data; boundary='+boundary
    else:
        raw=json.dumps(payload,ensure_ascii=False).encode('utf-8');ctype='application/json'
    req=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,data=raw,headers={'Content-Type':ctype})
    try:
        with urllib.request.urlopen(req,timeout=120 if files else 35) as response:result=json.load(response)
    except urllib.error.HTTPError as error:
        try: result=json.loads(error.read())
        except (ValueError,OSError): result={}
        if error.code<500 and result.get('ok') is False:
            raise TelegramRejected('Telegram отклонил запрос: '+str(result.get('description','ошибка')).replace(token,'[token]')[:300]) from None
        raise TelegramUnknown('Telegram не подтвердил результат. Проверь канал перед повторной отправкой.') from None
    except (urllib.error.URLError,TimeoutError,OSError,ValueError):
        # urllib errors can include the URL and bot token. Never propagate them.
        raise TelegramUnknown('Нет подтверждения Telegram. Проверь канал: пост мог быть отправлен.') from None
    if not isinstance(result,dict) or 'ok' not in result:
        raise TelegramUnknown('Не получено подтверждение Telegram. Проверь канал.')
    if result.get('ok') is not True:
        raise TelegramRejected('Telegram отклонил запрос')
    return result.get('result')


class Telegram:
    def __init__(self,state):
        self.state=state;self.lock=threading.RLock()

    @property
    def directory(self):
        return Path(self.state())/'telegram'

    def config(self):
        p=self.directory/'settings.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'chat_id':'','token_file':''}

    def token(self,config=None):
        config=self.config() if config is None else config
        token=config.get('token','').strip()
        if config.get('token_file'):
            try:token=Path(config['token_file']).read_text(encoding='utf-8-sig').strip()
            except OSError:raise ValueError('Не удалось прочитать файл токена Telegram') from None
        if not token:token=os.environ.get('GRANKA_TELEGRAM_BOT_TOKEN','').strip()
        if not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,100}',token):
            raise ValueError('Укажи токен бота в настройках Telegram')
        return token

    def settings(self):
        config=self.config()
        try:self.token(config);available=True
        except ValueError:available=False
        return {'chat_id':config.get('chat_id',''),'token_file':config.get('token_file',''),
                'publication_mode':self.publication_mode(config),
                'has_token':available,'token_source':'file' if config.get('token_file') else 'local' if config.get('token') else 'environment'}

    def publication_mode(self,config=None):
        config=self.config() if config is None else config
        mode=config.get('publication_mode','channel')
        if not isinstance(mode,str) or mode not in {'channel','manual'}:
            raise ValueError('Выбери публикацию от имени канала или вручную в Telegram')
        return mode

    def require_channel_publication(self):
        if self.publication_mode()!='channel':
            raise ValueError('Выбрана ручная публикация. Для отправки через бота выбери «От имени канала» в настройках Telegram.')

    def save_settings(self,values):
        with self.lock,delivery_lock(self.directory):
            config=self.config()
            if 'publication_mode'in values:config['publication_mode']=values['publication_mode']
            config['publication_mode']=self.publication_mode(config)
            chat=values.get('chat_id',config.get('chat_id',''))
            if not isinstance(chat,str) or (chat and not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{3,31}|-100\d{5,20}',chat.strip())):
                raise ValueError('Укажи @имя канала или его числовой ID -100…')
            config['chat_id']=chat.strip()
            path=values.get('token_file',config.get('token_file',''))
            if not isinstance(path,str) or len(path)>1000:raise ValueError('Некорректный путь к токену')
            config['token_file']=path.strip()
            if values.get('token'):
                if not isinstance(values['token'],str):raise ValueError('Некорректный токен')
                config['token']=values['token'].strip();config['token_file']=''
            if values.get('clear_token') is True:config.pop('token',None);config['token_file']=''
            if config.get('token') or config.get('token_file'):self.token(config)
            self.persist(self.directory/'settings.json',config)
            return self.settings()

    def connection(self):
        config=self.config();token=self.token(config)
        if not config.get('chat_id'):raise ValueError('Укажи канал Telegram в настройках')
        bot=bot_call(token,'getMe',{})
        chat=bot_call(token,'getChat',{'chat_id':config['chat_id']})
        if not isinstance(chat,dict) or chat.get('type')!='channel':raise ValueError('Выбери канал Telegram')
        member=bot_call(token,'getChatMember',{'chat_id':chat['id'],'user_id':bot['id']})
        if member.get('status')!='creator' and not (member.get('status')=='administrator' and member.get('can_post_messages')):
            raise ValueError('Добавь бота администратором канала с правом публикации сообщений')
        return {'bot_id':bot['id'],'bot_username':bot.get('username',''),'chat_id':str(chat['id']),
                'title':chat.get('title',config['chat_id']),'username':chat.get('username','')}

    def fingerprint(self):
        config=self.config()
        mode=self.publication_mode(config)
        # Keep old reviewed channel snapshots valid; manual mode blocks delivery.
        suffix='|manual' if mode=='manual' else ''
        return hashlib.sha256((config.get('chat_id','')+'|'+self.token(config)+suffix).encode()).hexdigest()

    def record_path(self,identifier):
        if not re.fullmatch(r'[a-f0-9]{32}',identifier):raise LookupError('Публикация не найдена')
        return self.directory/'outbox'/identifier/'publication.json'

    def read(self,identifier):
        p=self.record_path(identifier)
        if not p.exists():raise LookupError('Публикация не найдена')
        return json.loads(p.read_text(encoding='utf-8'))

    def persist(self,path,record):
        # Retry local persistence only, never the Telegram API operation.
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
            for attempt in range(8):
                try:temporary.replace(path);return
                except PermissionError as error:
                    if os.name!='nt'or getattr(error,'winerror',None)not in {5,32,33}or attempt==7:raise
                    time.sleep(min(.02*2**attempt,.25))
        finally:temporary.unlink(missing_ok=True)

    def records(self,root,rel,recover=False):
        values=[]
        for path in (self.directory/'outbox').glob('*/publication.json'):
            value=json.loads(path.read_text(encoding='utf-8'))
            if value['root']==root and value['rel']==rel:
                if recover and value['status']=='sending':
                    value.update(status='unknown',error='Отправка была прервана. Проверь канал перед дальнейшими действиями.')
                    self.persist(path,value)
                values.append(value)
        return values

    @staticmethod
    def content_id(record):
        raw=Path(record['photo_path']).read_bytes()if record.get('photo_path')else b''
        legacy=[record['root'],record['rel'],record['channel']['chat_id'],record['text'],hashlib.sha256(raw).hexdigest()]
        identity=legacy+[record['attachments'],record.get('rich_message')]if record.get('attachments')or record.get('rich_message')else legacy
        return hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:32]

    @staticmethod
    def public(record):
        return {k:v for k,v in record.items() if k not in {'config_fingerprint','photo_path','media_paths','rich_message'}}

    def prepare(self,root,rel,version,text,photo=None,media=None,rich_message=None,rich_files=None,action=None,target_id=None,expected_source_id=_UNSET):
        if action not in {None,'create','update'}:raise ValueError('Выбери создание или обновление публикации')
        with self.lock,delivery_lock(self.directory):
            self.require_channel_publication()
            raw=photo.read_bytes() if photo else b''
            if photo and (len(raw)>10*1024*1024 or photo.suffix.lower() not in {'.jpg','.jpeg','.png'}):
                raise ValueError('Для Telegram нужна обложка PNG/JPEG до 10 МБ')
            blobs={};descriptors=[]
            if media and rich_message:raise ValueError('Выбери вложения или статью')
            for i,item in enumerate(media or []):
                path=item['path'] if isinstance(item,dict) else item
                data=path.read_bytes();kind=validate_bytes(data,path.suffix);field='m'+str(i)
                blobs[field]=(path,data);descriptors.append({'type':kind,'name':item.get('name',path.name) if isinstance(item,dict) else path.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'field':field})
            if photo and (len(descriptors)>=10 or rich_message):raise ValueError('Обложка не помещается в выбранный формат')
            if photo and media:
                blobs={'cover':(photo,raw),**blobs};descriptors.insert(0,{'type':'photo','name':'Обложка','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'field':'cover'})
            for field,path in (rich_files or {}).items():
                data=path.read_bytes();kind=validate_bytes(data,path.suffix);blobs[field]=(path,data);descriptors.append({'type':kind,'name':path.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'field':field})
            if sum(len(v[1])for v in blobs.values())>MAX_TOTAL:raise ValueError('Суммарный размер вложений — до 50 МБ')
            if media and len(descriptors)>10:raise ValueError('В альбоме может быть до 10 вложений')
            fingerprint=self.fingerprint();channel=self.connection()
            legacy=[root,rel,channel['chat_id'],text,hashlib.sha256(raw).hexdigest()]
            identity=json.dumps(legacy if not descriptors and not rich_message else legacy+[descriptors,rich_message],ensure_ascii=False,sort_keys=True)
            content_id=hashlib.sha256(identity.encode()).hexdigest()[:32]
            records=[v for v in self.records(root,rel,recover=True)if v['channel']['chat_id']==channel['chat_id']]
            latest=head(records);target=None;identifier=content_id
            if expected_source_id is not _UNSET and expected_source_id!=(latest['id']if latest else None):
                raise ValueError('История публикаций изменилась в другом окне. Открой Telegram заново перед подготовкой.')
            if action=='update':
                target=self.read(target_id or '')
                if target['root']!=root or target['rel']!=rel or target['channel']['chat_id']!=channel['chat_id']or target['status']!='sent':
                    raise ValueError('Выбери опубликованный пост этой статьи в текущем канале')
                if head(records,post_key(target))['id']!=target['id']:raise ValueError('Пост уже обновлён. Открой актуальную публикацию и подготовь правку заново.')
                if (target.get('content_id')or self.content_id(target))==content_id:return {**self.public(target),'unchanged':True}
                identifier=hashlib.sha256((content_id+'|update|'+target['id']).encode()).hexdigest()[:32]
            elif action=='create'and latest:
                identifier=hashlib.sha256((content_id+'|create|'+latest['id']).encode()).hexdigest()[:32]
            path=self.record_path(identifier)
            if path.exists():
                previous=self.read(identifier)
                if previous['status'] in {'prepared','failed'} and previous['config_fingerprint']!=fingerprint:
                    previous.update(config_fingerprint=fingerprint,channel=channel,status='prepared')
                    previous.pop('error',None);self.persist(path,previous)
                return self.public(previous)
            if action is None and latest:raise ValueError('Статья уже опубликована. Выбери «Обновить опубликованный пост» или явно «Создать новый».')
            if any(v['status']in {'unknown','sending'}for v in records):raise ValueError('Есть отправка с неизвестным результатом. Проверь канал; новая отправка заблокирована.')
            record={'id':identifier,'root':root,'rel':rel,'version':version,'text':text,'channel':channel,
                    'sender_mode':'channel',
                    'status':'prepared','created':time.time(),'config_fingerprint':fingerprint,'with_cover':bool(photo),
                    'mode':'article' if rich_message else 'announcement','attachments':descriptors,'action':action or 'create',
                    'content_id':content_id,'base_source_id':latest['id']if latest else None}
            if rich_message:record['rich_message']=rich_message
            if target:
                validate_edit(record,target)
                record.update(target_id=target['id'],target_url=target.get('url',''),target_message_id=target['message_id'])
            if photo and not blobs:
                copy=path.parent/('cover'+photo.suffix.lower());copy.parent.mkdir(parents=True,exist_ok=True)
                copy.write_bytes(raw);record['photo_path']=str(copy)
            if blobs:
                record['media_paths']={};path.parent.mkdir(parents=True,exist_ok=True)
                for field,(source,data) in blobs.items():
                    copy=path.parent/(field+source.suffix.lower());copy.write_bytes(data);record['media_paths'][field]=str(copy)
            if rich_message:record['rich_message']=rich_message
            self.persist(path,record)
            return self.public(record)

    def publish(self,identifier):
        with self.lock,delivery_lock(self.directory):
            record=self.read(identifier)
            if record['status'] in {'sent','sending','unknown'}:return self.public(record)
            self.require_channel_publication()
            if record.get('sender_mode','channel')!='channel':
                raise ValueError('Отправитель публикации изменился. Подготовь предпросмотр заново.')
            if record['config_fingerprint']!=self.fingerprint():
                raise ValueError('Настройки бота изменились. Подготовь предпросмотр заново.')
            path=self.record_path(identifier)
            token=self.token();photo=Path(record['photo_path']) if record.get('photo_path') else None
            records=[v for v in self.records(record['root'],record['rel'])if v['channel']['chat_id']==record['channel']['chat_id']]
            latest=head(records)
            if 'base_source_id'in record:
                if record['base_source_id']!=(latest['id']if latest else None):raise ValueError('В другом окне статья уже опубликована или обновлена. Подготовь актуальный вариант заново.')
            elif latest and latest.get('finished',latest['created'])>record['created']:
                raise ValueError('Статья уже опубликована после подготовки этого снимка. Подготовь её заново.')
            if any(v['id']!=record['id']and v['status']in {'sending','unknown'}for v in records):
                raise ValueError('Другая отправка не подтверждена. Проверь канал перед дальнейшими действиями.')
            target=self.read(record['target_id'])if record.get('action')=='update'else None
            if target and (target['root']!=record['root']or target['rel']!=record['rel']or target['channel']['chat_id']!=record['channel']['chat_id']or target['status']!='sent'):
                raise ValueError('Прежняя публикация больше не подтверждена')
            calls=edit_calls(record,target)if target else None
            files={k:Path(v)for k,v in record.get('media_paths',{}).items()}
            for item in record.get('attachments',[]):
                if hashlib.sha256(files[item['field']].read_bytes()).hexdigest()!=item['sha256']:
                    raise ValueError('Снимок вложения изменился. Подготовь публикацию заново.')
            record.pop('error',None)
            record.update(status='sending',started=time.time());self.persist(path,record)
            payload={'chat_id':record['channel']['chat_id']}
            method='sendPhoto' if photo else 'sendMessage'
            if record.get('rich_message'):
                method='sendRichMessage';payload['rich_message']=record['rich_message']
            elif len(files)>1:
                method='sendMediaGroup';payload['media']=[]
                for i,item in enumerate(record['attachments']):
                    descriptor={'type':item['type'],'media':'attach://'+item['field']}
                    if item['type']=='video':descriptor['supports_streaming']=True
                    if i==0:descriptor['caption']=record['text']
                    payload['media'].append(descriptor)
            elif files:
                item=record['attachments'][0];method='sendVideo' if item['type']=='video' else 'sendPhoto'
                field='video' if item['type']=='video' else 'photo';files={field:files[item['field']]};payload['caption']=record['text']
                if field=='video':payload['supports_streaming']=True
            elif photo:payload['caption']=record['text']
            else:payload.update(text=record['text'],link_preview_options={'is_disabled':True})
            try:
                if calls:
                    done=[]
                    for edit_method,edit_payload,edit_files in calls:
                        try:
                            result=bot_call(token,edit_method,edit_payload,files=edit_files)if edit_files else bot_call(token,edit_method,edit_payload)
                        except TelegramRejected as error:
                            if 'message is not modified'in str(error).lower():result={'message_id':edit_payload['message_id']}
                            elif done:raise TelegramUnknown('Альбом обновлён частично. Проверь канал.')from None
                            else:raise
                        if not isinstance(result,dict)or result.get('message_id')!=edit_payload['message_id']:
                            raise TelegramUnknown('Не подтверждён номер обновлённого сообщения')
                        done.append(result['message_id']);record['completed_edits']=done;self.persist(path,record)
                    result={'message_id':target['message_id']}
                    if target.get('message_ids'):record['message_ids']=target['message_ids']
                else:result=bot_call(token,method,payload,files=files) if files else bot_call(token,method,payload,photo)
                if not calls and method=='sendMediaGroup':
                    if not isinstance(result,list) or len(result)!=len(record['attachments']) or any(not isinstance(v,dict)or type(v.get('message_id'))is not int for v in result):
                        raise TelegramUnknown('Не получено подтверждение всего альбома. Проверь канал.')
                    record['message_ids']=[v['message_id']for v in result];result=result[0]
                if not isinstance(result,dict) or not isinstance(result.get('message_id'),int):
                    raise TelegramUnknown('Не получен номер публикации. Проверь канал.')
                record.update(status='sent',message_id=result['message_id'],finished=time.time())
                username=record['channel'].get('username')
                prefix=username or 'c/'+record['channel']['chat_id'].removeprefix('-100')
                record['url']='https://t.me/'+prefix+'/'+str(result['message_id'])
            except TelegramRejected as error:record.update(status='failed',error=str(error),finished=time.time())
            except Exception:
                record.update(status='unknown',error='Результат отправки неизвестен. Проверь канал; повторная отправка заблокирована.',finished=time.time())
            self.persist(path,record)
            return self.public(record)

    def publications(self,root,rel):
        with self.lock:
            try:
                with delivery_lock(self.directory):records=self.records(root,rel,recover=True)
            except PublicationBusy:records=self.records(root,rel)
        heads={channel:(head([r for r in records if r['channel']['chat_id']==channel])or{}).get('id')for channel in {r['channel']['chat_id']for r in records}}
        post_heads={post_key(r):(head(records,post_key(r))or{}).get('id')for r in records if r['status']=='sent'}
        return [{**self.public(r),'is_source_head':heads[r['channel']['chat_id']]==r['id'],
                 'is_post_head':r['status']=='sent'and post_heads.get(post_key(r))==r['id']}
                for r in sorted(records,key=lambda r:r.get('finished',r['created']),reverse=True)[:30]]
