"""Configurable WordPress destinations; legacy receipt keys remain readable."""
from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import urlsplit

LEGACY=('nas','staging','prod')


def order(site):
    envs=site.get('environments',{})
    result=site.get('pipeline')
    if result is None:result=[k for k in LEGACY if k in envs]+[k for k in envs if k not in LEGACY]
    if not isinstance(result,list)or len(result)>8 or any(not isinstance(k,str)or not re.fullmatch('[a-z][a-z0-9_-]{0,39}',k)or k not in envs for k in result)or len(set(result))!=len(result):
        raise ValueError('Проверь порядок площадок: нужны уникальные идентификаторы подключённых сайтов')
    return result


def git_enabled(site):return site.get('git_enabled',bool(site.get('repository')and site.get('remote')))


def final_stage(plan):return (plan.get('pipeline')or list(LEGACY))[-1]


def publication_result(plan):return plan.get('stages',{}).get(final_stage(plan),{})


def previous_stage(plan,stage):
    sequence=plan.get('pipeline')or list(LEGACY)
    if stage not in sequence:raise ValueError('Площадка не входит в маршрут этого снимка')
    index=sequence.index(stage);return sequence[index-1]if index else None


def signature(site):
    sequence=order(site)
    targets={k:{a:v for a,v in site['environments'][k].items()if a not in {'ssh_proxy','label'}}for k in sequence}
    archive={k:site.get(k,'')for k in ['repository','remote','branch']}if git_enabled(site)else None
    return hashlib.sha256(json.dumps({'pipeline':sequence,'targets':targets,'git':archive},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate(site):
    if not isinstance(site,dict)or set(site)-{'enabled','pipeline','environments','git_enabled','repository','remote','branch'}:raise ValueError('Некорректные настройки публикации')
    result=deepcopy(site)
    if type(result.get('enabled',False))is not bool or type(git_enabled(result))is not bool:raise ValueError('Некорректное включение публикации или Git')
    envs=result.get('environments',{})
    if not isinstance(envs,dict)or len(envs)>8:raise ValueError('Допускается до восьми площадок')
    sequence=order(result)
    if result.get('enabled')and not sequence:raise ValueError('Добавь хотя бы один сайт для публикации')
    for key,env in envs.items():
        if not isinstance(env,dict)or set(env)-{'label','url','ssh','wp','container','docker','ssh_proxy'}:raise ValueError('Некорректное подключение площадки')
        for name in ['label','url','container','docker']:
            value=env.get(name,'')
            if not isinstance(value,str)or len(value)>2000 or any(c in value for c in '\0\r\n'):raise ValueError('Некорректное поле площадки: '+name)
        url=urlsplit(env.get('url',''))
        if env.get('url')and(url.scheme not in {'http','https'}or not url.hostname or url.username or url.password or url.query or url.fragment):raise ValueError('Укажи URL WordPress без пароля, параметров и якоря')
        for name in ['ssh','wp']:
            value=env.get(name,[])
            if not isinstance(value,list)or len(value)>40 or any(not isinstance(v,str)or not v or len(v)>2000 or any(c in v for c in '\0\r\n')for v in value):raise ValueError('Проверь параметры '+name.upper())
        if result.get('enabled')and key in sequence and(not env.get('url')or not env.get('ssh')or not env.get('wp')):raise ValueError('У площадки «'+(env.get('label')or key)+'» нужны URL, SSH и WP-CLI')
        if not isinstance(env.get('ssh_proxy',''),str):raise ValueError('Некорректный маршрут прокси')
        if env.get('ssh_proxy')not in {None,'','system'}:
            from .ssh_tunnel import parse_proxy
            parse_proxy(env['ssh_proxy'])
        env['url']=env.get('url','').rstrip('/')
    for name in ['repository','remote','branch']:
        value=result.get(name,'')
        if not isinstance(value,str)or len(value)>2000 or any(c in value for c in '\0\r\n'):raise ValueError('Некорректная настройка Git: '+name)
    if result.get('enabled')and git_enabled(result)and(not result.get('repository')or not result.get('remote')):raise ValueError('Для Git нужны локальный репозиторий и remote')
    remote=urlsplit(result.get('remote',''))
    if remote.username or remote.password:raise ValueError('Используй Git credential helper, без пароля в remote')
    result['pipeline']=sequence;result['git_enabled']=git_enabled(result)
    return result
