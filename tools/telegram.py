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
from .ai_provider import atomic_json


class TelegramRejected(RuntimeError):
    """Telegram explicitly rejected the request; no message was accepted."""


class TelegramUnknown(RuntimeError):
    """Delivery may have happened. Retrying could duplicate the publication."""


def bot_call(token,method,payload,photo=None):
    if photo:
        boundary='granka-'+uuid.uuid4().hex;parts=[]
        for key,value in payload.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode('utf-8'))
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="cover{photo.suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
                      photo.read_bytes(),f'\r\n--{boundary}--\r\n'.encode()])
        raw=b''.join(parts);ctype='multipart/form-data; boundary='+boundary
    else:
        raw=json.dumps(payload,ensure_ascii=False).encode('utf-8');ctype='application/json'
    req=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,data=raw,headers={'Content-Type':ctype})
    try:
        with urllib.request.urlopen(req,timeout=35) as response:result=json.load(response)
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
                'has_token':available,'token_source':'file' if config.get('token_file') else 'local' if config.get('token') else 'environment'}

    def save_settings(self,values):
        with self.lock:
            config=self.config()
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
            atomic_json(self.directory/'settings.json',config)
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
        return hashlib.sha256((config.get('chat_id','')+'|'+self.token(config)).encode()).hexdigest()

    def record_path(self,identifier):
        if not re.fullmatch(r'[a-f0-9]{32}',identifier):raise LookupError('Публикация не найдена')
        return self.directory/'outbox'/identifier/'publication.json'

    def read(self,identifier):
        p=self.record_path(identifier)
        if not p.exists():raise LookupError('Публикация не найдена')
        return json.loads(p.read_text(encoding='utf-8'))

    @staticmethod
    def public(record):
        return {k:v for k,v in record.items() if k not in {'config_fingerprint','photo_path'}}

    def prepare(self,root,rel,version,text,photo=None):
        with self.lock:
            fingerprint=self.fingerprint();channel=self.connection()
            raw=photo.read_bytes() if photo else b''
            if photo and (len(raw)>10*1024*1024 or photo.suffix.lower() not in {'.jpg','.jpeg','.png'}):
                raise ValueError('Для Telegram нужна обложка PNG/JPEG до 10 МБ')
            identity=json.dumps([root,rel,channel['chat_id'],text,hashlib.sha256(raw).hexdigest()],ensure_ascii=False)
            identifier=hashlib.sha256(identity.encode()).hexdigest()[:32]
            path=self.record_path(identifier)
            if path.exists():
                previous=self.read(identifier)
                if previous['status'] in {'prepared','failed'} and previous['config_fingerprint']!=fingerprint:
                    previous.update(config_fingerprint=fingerprint,channel=channel,status='prepared')
                    previous.pop('error',None);atomic_json(path,previous)
                return self.public(previous)
            record={'id':identifier,'root':root,'rel':rel,'version':version,'text':text,'channel':channel,
                    'status':'prepared','created':time.time(),'config_fingerprint':fingerprint,'with_cover':bool(photo)}
            if photo:
                copy=path.parent/('cover'+photo.suffix.lower());copy.parent.mkdir(parents=True,exist_ok=True)
                copy.write_bytes(raw);record['photo_path']=str(copy)
            atomic_json(path,record)
            return self.public(record)

    def publish(self,identifier):
        with self.lock:
            record=self.read(identifier)
            if record['status'] in {'sent','sending','unknown'}:return self.public(record)
            if record['config_fingerprint']!=self.fingerprint():
                raise ValueError('Настройки бота изменились. Подготовь предпросмотр заново.')
            path=self.record_path(identifier)
            token=self.token();photo=Path(record['photo_path']) if record.get('photo_path') else None
            record.pop('error',None)
            record.update(status='sending',started=time.time());atomic_json(path,record)
            payload={'chat_id':record['channel']['chat_id']}
            if photo:payload['caption']=record['text']
            else:payload.update(text=record['text'],link_preview_options={'is_disabled':True})
            try:
                result=bot_call(token,'sendPhoto' if photo else 'sendMessage',payload,photo)
                if not isinstance(result,dict) or not isinstance(result.get('message_id'),int):
                    raise TelegramUnknown('Не получен номер публикации. Проверь канал.')
                record.update(status='sent',message_id=result['message_id'],finished=time.time())
                username=record['channel'].get('username')
                prefix=username or 'c/'+record['channel']['chat_id'].removeprefix('-100')
                record['url']='https://t.me/'+prefix+'/'+str(result['message_id'])
            except TelegramRejected as error:record.update(status='failed',error=str(error),finished=time.time())
            except Exception:
                record.update(status='unknown',error='Результат отправки неизвестен. Проверь канал; повторная отправка заблокирована.',finished=time.time())
            atomic_json(path,record)
            return self.public(record)

    def publications(self,root,rel):
        records=[]
        with self.lock:
            for path in (self.directory/'outbox').glob('*/publication.json'):
                value=json.loads(path.read_text(encoding='utf-8'))
                if value['root']==root and value['rel']==rel:
                    if value['status']=='sending':
                        value.update(status='unknown',error='Отправка была прервана. Проверь канал перед дальнейшими действиями.')
                        atomic_json(path,value)
                    records.append(self.public(value))
        return sorted(records,key=lambda r:r['created'],reverse=True)[:30]
