"""Machine-local configuration with portable, private-by-default data directories."""
import copy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def state_directory():
    if os.environ.get('GRANKA_STATE_DIR'):
        return Path(os.path.expandvars(os.environ['GRANKA_STATE_DIR'])).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'Granka'
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/Granka'
    return Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'granka'


STATE = state_directory()
CONFIG_FILE = Path(os.environ.get('GRANKA_CONFIG',str(ROOT/'granka-config.json'))).expanduser().resolve()
DEFAULTS = {
    'server': {'port':8901},
    'roots': {'articles':str(STATE/'articles')},
    'image_dirs': [],
    'planning': {'source_dirs': {}},
    'corrections_file':str(STATE/'corrections.md'),
    'voice': {'corpus_dir':str(STATE/'voice-corpus'),'profile_file':str(STATE/'profiles/voice-profile.json')},
    'speech': {'python':sys.executable,'model_dir':str(STATE/'models/whisper'),
               'models':['small','base'],'language':'ru','offline':True},
    'feedback': {'command':[]},
    'site': {'enabled':False,'repository':'','remote':'','branch':'main','environments':{}},
    'git': {'bypass_proxy':False},
    'codex_binary':'', 'openrouter_key_file':'',
}


def load():
    data=json.loads(CONFIG_FILE.read_text(encoding='utf-8')) if CONFIG_FILE.is_file() else {}
    if not isinstance(data,dict):raise ValueError('Конфигурация должна быть JSON-объектом')
    result=copy.deepcopy(DEFAULTS)
    for key,value in data.items():
        if key=='roots':result[key]=value
        elif isinstance(result.get(key),dict) and isinstance(value,dict):result[key].update(value)
        else:result[key]=value
    port=result['server']['port']
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('Порт должен быть в диапазоне 1024–65535')
    if not isinstance(result['roots'],dict) or not result['roots']:raise ValueError('Настрой хотя бы одну рабочую папку')
    return result


def path(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('Нужен непустой путь')
    expanded=Path(os.path.expandvars(value)).expanduser()
    return (CONFIG_FILE.parent/expanded).resolve() if not expanded.is_absolute() else expanded.resolve()


def roots():
    return {key:path(value) for key,value in load()['roots'].items()}


def git_environment():
    result=dict(os.environ)
    if load()['git']['bypass_proxy']:
        for key in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:result[key]=''
    return result


def git_options():
    return ['-c','http.proxy='] if load()['git']['bypass_proxy'] else []
