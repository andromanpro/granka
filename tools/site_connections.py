"""Public connection choices and revision-guarded machine-config updates."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request
import uuid
from .file_lock import file_lock
from .ssh_tunnel import parse_proxy
from .site_topology import order,git_enabled,validate

STAGES={'nas':'Предпросмотр','staging':'Стейдж','prod':'Сайт'}


class SettingsConflict(ValueError):pass


def read(path):
    raw=Path(path).read_bytes()if Path(path).is_file()else b'{}'
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError('Конфигурация должна быть JSON-объектом')
    return data,hashlib.sha256(raw).hexdigest()


def system_proxy():
    proxies=urllib.request.getproxies()
    value=proxies.get('https')or proxies.get('http')or proxies.get('all')
    if not value:return {'available':False,'label':'Системный прокси не настроен'}
    try:parse_proxy(value)
    except ValueError:return {'available':False,'label':'Системный прокси требует проверки: нужен HTTP CONNECT или SOCKS5 без пароля в адресе'}
    return {'available':True,'label':value}


def snapshot(path):
    data,revision=read(path);site=data.get('site',{});environments=site.get('environments',{})
    rows=[]
    sequence=order(site);keys=sequence+[k for k in environments if k not in sequence]
    for key in keys:
        label=STAGES.get(key,key)
        env=environments.get(key,{});proxy=env.get('ssh_proxy','')
        mode='system'if proxy=='system'else'custom'if proxy else'direct'
        # Invalid legacy values never disclose a password in the Settings page.
        if mode=='custom':
            try:parse_proxy(proxy)
            except (ValueError,TypeError):proxy=''
        rows.append({'id':key,'label':env.get('label')or label,'url':env.get('url',''),'included':key in sequence,
                     'configured':bool(env),'mode':mode,'proxy':proxy if mode=='custom'else'',
                     'ssh':env.get('ssh',[]),'wp':env.get('wp',[]),'container':env.get('container',''),'docker':env.get('docker','docker')})
    return {'revision':revision,'environments':rows,'system_proxy':system_proxy(),
            'site':{'enabled':site.get('enabled',False),'pipeline':order(site),'git_enabled':git_enabled(site),
                    'repository':site.get('repository',''),'remote':site.get('remote',''),'branch':site.get('branch')or'main'}}


def update(path, mutate, *, routes=None, expected_revision=None, site=None):
    path=Path(path)
    with file_lock(path.with_name(path.name+'.lock'),'Настройки уже сохраняются в другом окне. Повтори после завершения.'):
        data,revision=read(path)
        if routes is not None or site is not None:
            if expected_revision!=revision:raise SettingsConflict('Настройки изменились в другом окне. Открой настройки заново перед сохранением.')
        if site is not None:data['site']=validate(site)
        if routes is not None:
            if not isinstance(routes,dict)or set(routes)-data.get('site',{}).get('environments',{}).keys():raise ValueError('Неизвестный контур подключения')
            environments=data.get('site',{}).get('environments',{})
            for key,row in routes.items():
                if key not in environments:raise ValueError('Площадка ещё не настроена: '+key)
                if not isinstance(row,dict)or set(row)-{'mode','proxy'}:raise ValueError('Некорректная настройка подключения')
                mode=row.get('mode')
                if mode=='direct':environments[key].pop('ssh_proxy',None)
                elif mode=='system':environments[key]['ssh_proxy']='system'
                elif mode=='custom':
                    proxy=row.get('proxy','')
                    if not isinstance(proxy,str)or len(proxy)>2048:raise ValueError('Некорректный адрес прокси')
                    proxy=proxy.strip();parse_proxy(proxy);environments[key]['ssh_proxy']=proxy
                else:raise ValueError('Выбери маршрут: напрямую, системный или свой прокси')
        result=mutate(data)
        temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
            for attempt in range(8):
                try:os.replace(temporary,path);break
                except OSError as error:
                    if os.name!='nt'or getattr(error,'winerror',None)not in {5,32,33}or attempt==7:raise
                    time.sleep(.04*(attempt+1))
        finally:temporary.unlink(missing_ok=True)
        return result
