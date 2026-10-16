# -*- coding: utf-8 -*-
"""Loopback HTTP adapter for the Granka article editor."""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tools.text_edit import edit_text
from tools import iimeter
from tools.document_store import DocumentStore, validate as contract_errors
from tools import ai_provider
from tools import config as environment
from tools.jobs import Jobs, JobConflict
from tools.git_history import GitHistory
from tools.editorial_workflow import apply_decisions, add_analysis, analysis_prompt, ANALYSIS_SCHEMA, add_editorial_result
from tools.editorial_requests import editorial_requests, request_prompt, REQUEST_SCHEMA
from tools.covers import cover_prompt, validate_cover
from tools import announcements, telegram
from tools.site_publish import SitePublisher, source_hash
from tools.publication_plan import PublicationPlan, PlanConflict
from tools.planning_library import scan_library, catalog_sources
from tools.author_preferences import AuthorPreferences, EVALUATION_SCHEMA
from tools.batch_decisions import decide_batch, undo_batch
from tools.rule_learning import SCHEMA as RULE_PROPOSAL_SCHEMA
from tools.library_locations import LibraryLocations, LocationsConflict
from tools.file_dialog import choose_local_path
TELEGRAM = telegram.Telegram(lambda: ai_provider.STATE)
SITE = SitePublisher(lambda: ai_provider.STATE)
CALENDAR = PublicationPlan(lambda: ai_provider.STATE, lambda: [s for s in library_sources() if s['state']=='draft'])

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SETTINGS = environment.load()
PORT = SETTINGS['server']['port']
HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
LOCATIONS = LibraryLocations(lambda: ai_provider.STATE)
ROOTS = {**environment.roots(), **LOCATIONS.roots()}
CORRECTIONS = environment.path(SETTINGS['corrections_file'])
PREFERENCES = AuthorPreferences(lambda: ai_provider.STATE, lambda: ROOTS, lambda: CORRECTIONS)
TOKEN = secrets.token_hex(16)
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
MAX_BODY = 512 * 1024
MAX_AUDIO = 12 * 1024 * 1024
LOCK = threading.Lock()
STORE = DocumentStore(lambda: ROOTS, LOCK)
revision_of = STORE.revision
safe_resolve = STORE.resolve
mutate = STORE.mutate
DECISION_ACTIONS = {"accept", "reject", "custom", "allergen"}
ANN_TYPES = {"candidate", "protected", "note"}
NOTE_KINDS = {"note", "image", "audio", "voice"}

SPEECH_PYTHON = environment.path(SETTINGS['speech']['python'])
TRANSCRIBE = HERE / "tools" / "transcribe.py"
IIMETER = HERE / "tools" / "iimeter.py"
_meter_cache = {}
JOBS = None
FFMPEG = "ffmpeg"

# каталоги картинок для вставки в статьи
IMAGE_DIRS = [*ROOTS.values(),*(environment.path(p) for p in SETTINGS['image_dirs']),ai_provider.STATE/'generated-images']
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGE_MIME = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
              ".gif":"image/gif", ".webp":"image/webp"}


def resolve_image(value):
    picture = Path(value).resolve()
    if (picture.suffix.lower() not in IMAGE_EXT or
        not any(picture.is_relative_to(base.resolve()) for base in IMAGE_DIRS)):
        raise PermissionError("image outside allowed directories")
    if not picture.is_file():
        raise LookupError("image not found")
    return picture


def job_service():
    global JOBS
    if JOBS is None: JOBS = Jobs(ai_provider.STATE/'jobs')
    return JOBS


def source_document(path, root_id, data):
    if data.get('source'):
        return safe_resolve(root_id,data['source'],suffix='.md')
    return path.with_name(path.name.removesuffix('.granka.json')+'.md')


def enqueue_job(root_id, rel, body):
    path=safe_resolve(root_id,rel)
    kind=body.get('kind')
    if kind not in {'apply','analysis','image','editorial','announcement','benchmark','rules'}: raise ValueError('Неизвестное задание')
    with LOCK:
        raw=path.read_bytes();revision=hashlib.sha256(raw).hexdigest()[:16]
        if body.get('expected_revision')!=revision: return 409,{'error':'conflict','revision':revision}
        data=json.loads(raw)
        errors=contract_errors(data)
        if errors: raise ValueError('Сначала исправьте разметку: '+errors[0])
        version=data['versions'][-1]
        if body.get('version')!=version['id']: raise ValueError('Выберите последнюю версию статьи.')
        md=source_document(path,root_id,data)
        source_hash=hashlib.sha256(md.read_bytes()).hexdigest() if md.exists() else None
    config=ai_provider.settings()
    provider='local' if kind=='apply' else config['image_provider' if kind=='image' else 'analysis_provider']
    prompt=body.get('prompt','')
    if kind=='announcement' and (not isinstance(prompt,str) or len(prompt)>2000):
        raise ValueError('Пожелание к анонсу: до 2000 символов')
    if kind=='image' and (not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000):
        raise ValueError('Опиши картинку: от 1 до 6000 символов.')
    purpose = body.get('purpose', 'inline')
    if kind == 'image' and purpose not in {'inline', 'cover'}:
        raise ValueError('Неизвестное назначение картинки')
    op_id=clean_line(body.get('operation_id',''),64) or uuid.uuid4().hex
    target=None
    if kind=='editorial':
        target=next((a for a in data['annotations'] if a['id']==body.get('annotation_id') and a['version']==version['id']),None)
        if not target or target.get('type')!='candidate' or target.get('decision'):
            raise ValueError('Поручение не найдено или уже имеет решение')
        request=next((r for r in editorial_requests(version['text']) if r['start']==target['start'] and r['end']==target['end']),None)
        if not request or request['kind']!='creative':
            raise ValueError('Творческого поручения здесь нет: требуется ответ автора или источник')

    def work(directory):
        ai_provider.atomic_json(directory/'snapshot.json',data)
        if kind=='rules':
            proposal_source,proposal_prompt=PREFERENCES.learning.proposal_input()
            ai_provider.atomic_json(directory/'rule-evidence.json',proposal_source)
            report,receipt=ai_provider.text_result(proposal_prompt,RULE_PROPOSAL_SCHEMA,directory,config)
            return PREFERENCES.learning.save_proposals(proposal_source,report,receipt,directory.name)
        if kind=='benchmark':
            suite,cases,evaluation_prompt=PREFERENCES.evaluation(body.get('limit',8))
            ai_provider.atomic_json(directory/'benchmark-cases.json',{'suite':suite['fingerprint'],'cases':cases})
            report,receipt=ai_provider.text_result(evaluation_prompt,EVALUATION_SCHEMA,directory,config)
            return PREFERENCES.score(suite,cases,report,receipt,directory.name)
        if kind=='announcement':
            report,receipt=ai_provider.text_result(announcements.prompt(version['text'],prompt),announcements.SCHEMA,directory,config)
            return {'variants':announcements.variants(report),'version':version['id'],'receipt':receipt}
        if kind=='image':
            generation_prompt = cover_prompt(version['text'], prompt.strip()) if purpose == 'cover' else prompt.strip()
            image_config = {**config, 'image_aspect':'16:9'} if purpose == 'cover' else config
            image,receipt=ai_provider.generate_image(generation_prompt,directory,image_config)
            gallery=ai_provider.STATE/'generated-images';gallery.mkdir(parents=True,exist_ok=True)
            image_target=gallery/(directory.name+image.suffix)
            image_target.write_bytes(image.read_bytes())
            return {'path':str(image_target),'name':image_target.name,'prompt':prompt,'purpose':purpose,'provider':provider,'receipt':receipt}
        if kind=='editorial':
            report,receipt=ai_provider.text_result(request_prompt(version['text'],request),REQUEST_SCHEMA,directory,config)
            def fn(current):return add_editorial_result(current,version['id'],target['id'],report,provider)
        elif kind=='analysis':
            protected=[a['quote'] for a in data['annotations'] if a['version']==version['id'] and
                       (a.get('type')=='protected' or (a.get('decision') or {}).get('action')=='reject')]
            feedback=[item for item in data.get('feedback',[]) if not item.get('read_by_agent')][-20:]
            comments=[{'quote':a['quote'],'text':(a.get('decision') or {}).get('text') or a.get('note','')}
                      for a in data['annotations'] if a['version']==version['id'] and a.get('type')=='note' and
                      ((a.get('decision') or {}).get('intent')=='comment' or a.get('note'))]
            comments.extend({'context':item.get('context'),'text':item['text']} for item in feedback)
            preferences=PREFERENCES.context(version['text'])
            ai_provider.atomic_json(directory/'author-preferences.json',preferences)
            report,receipt=ai_provider.text_result(analysis_prompt(version['text'],protected,comments,preferences),ANALYSIS_SCHEMA,directory,config)
            def fn(current):
                result=add_analysis(current,version['id'],report,provider)
                current['analyses'][-1]['preferences']={'fingerprint':preferences['fingerprint'],'rules':len(preferences['rules']),
                    'examples':len(preferences['decisions']),'total_decisions':preferences['total_decisions']}
                result['preferences']=current['analyses'][-1]['preferences']
                ids={item['id'] for item in feedback}
                for item in current.get('feedback',[]):
                    if item['id'] in ids:item['read_by_agent']=True
                return result
        else:
            receipt={'provider':'local'}
            def fn(current): return apply_decisions(current,version['id'])
        code,out=mutate(path,revision,'job-'+directory.name,fn)
        if code==409: raise JobConflict('Статья изменилась во время работы. Результат сохранён в журнале; повтори анализ для новой версии.')
        if kind=='apply':
            with LOCK:
                current=json.loads(path.read_text(encoding='utf-8'))
                final=next(v['text'] for v in current['versions'] if v['id']==out['version'])
                actual=hashlib.sha256(md.read_bytes()).hexdigest() if md.exists() else None
                if actual==source_hash:
                    tmp=md.with_name('.'+md.name+'.'+uuid.uuid4().hex+'.tmp')
                    tmp.write_text(final,encoding='utf-8',newline='\n');tmp.replace(md)
                    out['source_written']=True
                else:
                    out['source_written']=False
                    out['warning']='Версия собрана, но исходный .md изменился снаружи и не был перезаписан.'
        out['receipt']=receipt
        return out
    job=job_service().submit(kind,root_id,rel,op_id,
        {'version':version['id'],'provider':provider, **({'purpose':purpose} if kind=='image' else {})},work)
    return 202,{'ok':True,'job':job}


def current_site_plan(root,rel,identifier):
    plan=SITE.read(identifier)
    if plan['root']!=root or plan['rel']!=rel:raise LookupError('Снимок не принадлежит выбранной статье')
    data,_=STORE.read(safe_resolve(root,rel))
    if source_hash(data)!=plan['source_hash']:raise ValueError('Статья изменилась. Подготовь новый снимок публикации.')
    return plan


GIT_ROOTS = dict(ROOTS)
GIT_ENV = environment.git_environment()
_last_sync = {}


def git_run(root: Path, *args, timeout=60):
    return subprocess.run(["git", "-C", str(root), *environment.git_options(), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=GIT_ENV, timeout=timeout)






HISTORY = GitHistory(lambda: GIT_ROOTS, safe_resolve, git_run)
git_state = HISTORY.state
git_sync = HISTORY.sync

def run_iimeter(text: str, cache_key: str, profile_id="blog"):
    """A corpus change invalidates both the profile and cached measurements."""
    profile = iimeter.load_profile(profile_id)
    cache_key = (cache_key, profile.get("fingerprint") if profile else "uncalibrated")
    if cache_key in _meter_cache:
        return _meter_cache[cache_key]
    res = iimeter.measure(text, profile_id, profile=profile)
    if len(_meter_cache) > 40:
        _meter_cache.clear()
    _meter_cache[cache_key] = res
    return res


def clean_line(s, cap):
    """Одна строка без управляющих символов, с потолком длины."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(s)).strip()
    return s[:cap]


def clean_text(value, cap):
    if not isinstance(value,str):raise ValueError('Текст должен быть строкой')
    if len(value)>cap:raise ValueError(f'Текст длиннее {cap} символов')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',value):raise ValueError('Недопустимые управляющие символы')
    return value.replace('\r\n','\n').replace('\r','\n')






SKIP_DIRS = {".git", "node_modules", "_archive", "_исходники-докстрочники", "covers", "_audit"}


def library_sources(calendar=None):
    selectors = dict(environment.load()['planning']['source_dirs'] or {key: ['.'] for key in ROOTS})
    selectors.update(LOCATIONS.selectors())
    sources = scan_library(ROOTS, selectors, include_published=True)
    return catalog_sources(sources, calendar or [], SITE.directory, SITE.repo, ROOTS)


def list_sources():
    """The article library and calendar use the same configured draft sources."""
    return library_sources(CALENDAR.read()['entries'])


def journal(data, action, **fields):
    """Append-only журнал: что именно поменялось и когда."""
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action}
    ev.update({k: v for k, v in fields.items() if v is not None})
    data.setdefault("history", []).append(ev)




def list_docs():
    docs = []
    seen = set()
    for root_id, root in ROOTS.items():
        if not root.exists():
            continue
        selected = LOCATIONS.selectors().get(root_id, ['.'])
        paths = root.rglob("*.granka.json") if selected == ['.'] else (
            root / Path(name).with_suffix('.granka.json') for name in selected)
        for p in paths:
            if not p.is_file() or not p.resolve().is_relative_to(root.resolve()):
                continue
            if p.resolve() in seen:
                continue
            seen.add(p.resolve())
            row = {"root": root_id, "rel": str(p.relative_to(root)).replace("\\", "/")}
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                errs = contract_errors(data)
                latest = data.get("versions", [{}])[-1].get("id")
                ann = [a for a in data.get("annotations", [])
                       if a.get("type") != "protected" and a.get("version") == latest]
                row.update({
                    "doc": data.get("doc", p.stem),
                    "status": data.get("status", "draft"),
                    "versions": len(data.get("versions", [])),
                    "total": len(ann),
                    "done": len([a for a in ann if a.get("decision")]),
                    "mtime": p.stat().st_mtime,
                    "contract_errors": errs[:5],
                })
            except Exception as e:
                row["error"] = e.__class__.__name__
            docs.append(row)
    docs.sort(key=lambda d: -d.get("mtime", 0))
    return docs




def deliver_feedback(text: str) -> bool:
    """Optional explicitly configured feedback command; disabled by default."""
    try:
        command=environment.load()['feedback']['command']
        if not command:return False
        if not isinstance(command,list) or not all(isinstance(arg,str) for arg in command):return False
        result=subprocess.run([arg.replace('{text}',text) for arg in command],capture_output=True,timeout=10,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return result.returncode==0
    except Exception:
        print('[granka] optional feedback delivery failed')
        return False


def list_images():
    out = []
    for base in IMAGE_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() in IMAGE_EXT and p.is_file():
                out.append({"path": str(p), "name": p.name,
                            "kb": p.stat().st_size // 1024, "mtime": p.stat().st_mtime})
    out.sort(key=lambda x: -x["mtime"])
    return out[:300]


def transcribe_webm(raw: bytes) -> dict:
    if not SPEECH_PYTHON.exists():
        raise RuntimeError("Настрой speech.python для распознавания речи")
    with tempfile.TemporaryDirectory(prefix="granka-stt-") as td:
        webm = Path(td) / "note.webm"
        webm.write_bytes(raw)
        # faster-whisper decodes WebM through PyAV; an external ffmpeg.exe is unnecessary.
        pr = subprocess.run([str(SPEECH_PYTHON), str(TRANSCRIBE), str(webm)],
                            capture_output=True, timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if pr.returncode != 0:
            raise RuntimeError("whisper: " + pr.stderr.decode("utf-8", "replace")[-200:])
        out = pr.stdout.decode("utf-8", "replace").strip()
        try:
            return json.loads(out)
        except json.JSONDecodeError:  # старый формат/мусор в stdout
            return {"text": out, "segments": []}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[granka] {fmt % args}")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass  # A closed/reloaded browser tab is not a failed document operation.

    def _guard(self, mutating: bool) -> bool:
        host = self.headers.get("Host", "")
        if host not in ALLOWED_HOSTS:
            self._send(403, {"error": "bad host"})
            return False
        if mutating:
            if self.headers.get("X-Granka-Token") != TOKEN:
                self._send(403, {"error": "bad token"})
                return False
            ctype = self.headers.get("Content-Type", "")
            if not ctype.startswith("application/json"):
                self._send(415, {"error": "json only"})
                return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_BODY:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _doc_path(self, q):
        return safe_resolve(q.get("root", [""])[0], q.get("f", [""])[0])

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if not self._guard(False):
            return
        try:
            if url.path in ("/", "/index.html", "/workbench"):
                html = (STATIC / "index.html").read_text(encoding="utf-8")
                html = html.replace("__GRANKA_TOKEN__", TOKEN).replace('__GRANKA_VERSION__', (STATIC.parent / 'VERSION').read_text().strip())
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == '/favicon.ico':
                self._send(200, (STATIC / 'brand/granka.ico').read_bytes(), 'image/x-icon')
            elif url.path == '/api/version':
                self._send(200, {'version': (STATIC.parent / 'VERSION').read_text().strip()})
            elif url.path == '/api/preferences':
                self._send(200,PREFERENCES.summary())
            elif url.path == '/api/publication-plan':
                self._send(200,CALENDAR.read())
            elif url.path == '/api/site':
                q=urllib.parse.parse_qs(url.query);data,_=STORE.read(self._doc_path(q))
                root=q.get('root',[''])[0];rel=q.get('f',[''])[0]
                self._send(200,{**SITE.info(data,root,rel),'plans':SITE.plans(root,rel),'source_hash':source_hash(data)})
            elif url.path == '/api/telegram/settings':
                self._send(200, TELEGRAM.settings())
            elif url.path in {'/api/telegram/publications','/api/telegram/photo'}:
                q=urllib.parse.parse_qs(url.query);self._doc_path(q)
                root=q.get('root',[''])[0];rel=q.get('f',[''])[0]
                if url.path.endswith('/publications'):
                    self._send(200, {'publications':TELEGRAM.publications(root,rel)})
                else:
                    record=TELEGRAM.read(q.get('id',[''])[0])
                    if record['root']!=root or record['rel']!=rel or not record.get('photo_path'):
                        raise LookupError('Обложка публикации не найдена')
                    photo=Path(record['photo_path'])
                    self._send(200,photo.read_bytes(),IMAGE_MIME[photo.suffix])
            elif url.path.startswith('/static/'):
                asset = (STATIC / url.path.removeprefix('/static/')).resolve()
                mime = {'.js':'text/javascript; charset=utf-8', '.css':'text/css; charset=utf-8',
                        '.svg':'image/svg+xml', '.png':'image/png', '.ico':'image/x-icon'}
                if not asset.is_relative_to(STATIC.resolve()) or asset.suffix not in mime or not asset.is_file():
                    self._send(404, {'error':'asset not found'});return
                self._send(200, asset.read_bytes(), mime[asset.suffix])
            elif url.path == "/api/article-image":
                q = urllib.parse.parse_qs(url.query)
                doc = self._doc_path(q)
                if not doc.is_file():
                    raise LookupError("document not found")
                rel = urllib.parse.unquote(q.get("src", [""])[0])
                picture = (doc.parent / rel).resolve()
                if (picture.suffix.lower() not in IMAGE_EXT or
                    not any(picture.is_relative_to(base.resolve()) for base in IMAGE_DIRS)):
                    raise PermissionError("image outside allowed directories")
                if not picture.is_file():
                    self._send(404, {"error": "image not found"}); return
                mime = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
                        ".gif":"image/gif", ".webp":"image/webp"}[picture.suffix.lower()]
                self._send(200, picture.read_bytes(), mime)
            elif url.path == "/api/image-preview":
                q = urllib.parse.parse_qs(url.query)
                picture = resolve_image(q.get("path", [""])[0])
                self._send(200, picture.read_bytes(), IMAGE_MIME[picture.suffix.lower()])
            elif url.path == "/api/docs":
                self._send(200, {"docs": list_docs()})
            elif url.path == '/api/settings':
                self._send(200, ai_provider.capabilities())
            elif url.path == '/api/models':
                self._send(200, ai_provider.model_catalog())
            elif url.path == '/api/jobs':
                q=urllib.parse.parse_qs(url.query)
                self._doc_path(q)
                self._send(200,{'jobs':job_service().list(q.get('root',[''])[0],q.get('f',[''])[0])})
            elif url.path == '/api/job':
                q=urllib.parse.parse_qs(url.query)
                self._send(200,{'job':job_service().get(q.get('id',[''])[0])})
            elif url.path == "/api/iimeter":
                q = urllib.parse.parse_qs(url.query)
                pth = self._doc_path(q)
                if not pth.is_file():
                    self._send(404, {"error": "not found"}); return
                data = json.loads(pth.read_text(encoding="utf-8"))
                vid = q.get("version", [""])[0]
                ver = next((v for v in data["versions"] if v["id"] == vid), None)                       or data["versions"][-1]
                key = hashlib.sha256(ver["text"].encode("utf-8")).hexdigest()
                profile_id = q.get("profile", ["blog"])[0]
                self._send(200, run_iimeter(ver["text"], key, profile_id))
            elif url.path == "/api/git":
                q=urllib.parse.parse_qs(url.query)
                self._send(200, git_state(q.get('root',['articles'])[0],q.get('f',[None])[0]))
            elif url.path == "/api/sources":
                self._send(200, {"sources": list_sources()})
            elif url.path == "/api/library-locations":
                self._send(200, LOCATIONS.read())
            elif url.path == "/api/images":
                self._send(200, {"images": list_images()})
            elif url.path == "/api/granka":
                q = urllib.parse.parse_qs(url.query)
                p = self._doc_path(q)
                if not p.is_file():
                    self._send(404, {"error": "not found"})
                    return
                data,revision=STORE.read(p)
                self._send(200, {"revision": revision, "data": data,
                                 "contract_errors": contract_errors(data),
                                 "editorial_requests":{v['id']:editorial_requests(v['text']) for v in data['versions']}})
            else:
                self._send(404, {"error": "not found"})
        except (telegram.TelegramRejected, telegram.TelegramUnknown) as e:
            self._send(502, {'error':str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except (PermissionError, LookupError):
            self._send(403, {"error": "forbidden"})
        except Exception as e:
            print(f"[granka] GET error: {e!r}")
            self._send(500, {"error": "internal"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        # транскрипция: тело — аудио, не JSON; свой лимит
        if url.path == "/api/transcribe":
            if not self._guard(False):
                return
            if self.headers.get("X-Granka-Token") != TOKEN:
                self._send(403, {"error": "bad token"})
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                if not (0 < n <= MAX_AUDIO):
                    self._send(413, {"error": "audio size"})
                    return
                res = transcribe_webm(self.rfile.read(n))
                self._send(200, {"ok": True, "text": res.get("text", ""),
                                 "segments": res.get("segments", []),
                                 "model": res.get("model")})
            except Exception as e:
                print(f"[granka] transcribe error: {e!r}")
                self._send(500, {"error": "transcribe failed"})
            return
        if not self._guard(True):
            return
        try:
            q = urllib.parse.parse_qs(url.query)
            b = self._body()
            op_id = clean_line(b.get("operation_id", ""), 64) or uuid.uuid4().hex

            if url.path == '/api/settings':
                self._send(200,{'ok':True,'settings':ai_provider.save_settings(b.get('settings',{}))})

            elif url.path == '/api/library-locations':
                kind = b.get('kind')
                if b.get('picker'):
                    path = choose_local_path(kind)
                    if not path:
                        self._send(200, {'cancelled': True});return
                else:
                    path = b.get('path')
                result = LOCATIONS.add(path, kind, b.get('expected_revision'))
                with LOCK:
                    extra = LOCATIONS.roots()
                    ROOTS.update(extra)
                    GIT_ROOTS.update(extra)
                    for folder in extra.values():
                        if folder not in IMAGE_DIRS:
                            IMAGE_DIRS.append(folder)
                if result['selection']:
                    chosen = Path(path).resolve()
                    for source in library_sources():
                        base = ROOTS[source['root']]
                        if chosen in {(base / source['source_rel']).resolve(), (base / source['file']).resolve()}:
                            result['selection'] = {'root': source['root'], 'rel': source['source_rel'],
                                                   'granka_rel': source['file'], 'has_granka': source['has_granka']}
                            break
                self._send(200, result)

            elif url.path == '/api/publication-plan':
                value=b.get('entry')
                if isinstance(value,dict) and (value.get('root') or value.get('file')):
                    self._doc_path({'root':[value.get('root','')],'f':[value.get('file','')]})
                self._send(200,CALENDAR.change(b))
            elif url.path in {'/api/publication-plan/preview','/api/publication-plan/apply'}:
                self._send(200,CALENDAR.schedule(b,apply=url.path.endswith('/apply')))
            elif url.path == '/api/site/prepare':
                p=self._doc_path(q);root=q.get('root',[''])[0];rel=q.get('f',[''])[0]
                with LOCK:
                    data,revision=STORE.read(p)
                    if b.get('expected_revision')!=revision:
                        self._send(409,{'error':'conflict','revision':revision});return
                    if b.get('version')!=data['versions'][-1]['id']:raise ValueError('Выбери последнюю версию статьи')
                source_root=ROOTS[root].parent if root=='blog' else ROOTS[root]
                self._send(200,SITE.prepare(root,rel,data,p,source_root,b.get('metadata',{})))
            elif url.path == '/api/site/review':
                root=q.get('root',[''])[0];rel=q.get('f',[''])[0]
                current_site_plan(root,rel,b.get('id',''))
                self._send(200,SITE.review(b['id'],b.get('stage')))
            elif url.path == '/api/site/deploy':
                root=q.get('root',[''])[0];rel=q.get('f',[''])[0]
                plan=current_site_plan(root,rel,b.get('id',''))
                if b.get('confirmed') is not True:raise ValueError('Подтверди публикацию выбранного снимка')
                review=plan.get('review')
                if not review or review['id']!=b.get('review_id'):raise ValueError('Предпросмотр устарел')
                SITE.gate(plan,review['stage'])
                if review['stage']!='nas' and b.get('approved_previous') is not True:raise ValueError('Подтверди просмотр предыдущего контура')
                def work(directory):
                    current_site_plan(root,rel,plan['id'])
                    return SITE.deploy(plan['id'],review['id'],b.get('approved_previous'))
                job=job_service().submit('site',root,rel,review['id'],
                    {'provider':'local','version':plan['version'],'stage':review['stage'],'plan_id':plan['id']},work)
                self._send(202,{'job':job})
            elif url.path == '/api/telegram/settings':
                self._send(200,TELEGRAM.save_settings(b))
            elif url.path == '/api/telegram/check':
                self._send(200,TELEGRAM.connection())
            elif url.path == '/api/announcement':
                p=self._doc_path(q)
                def fn(data):
                    if data['versions'][-1]['id']!=b.get('version'):raise ValueError('Версия статьи изменилась')
                    data['announcement']=announcements.draft(b,b['version'])
                    return {'announcement':data['announcement']}
                code,out=mutate(p,b.get('expected_revision'),op_id,fn);self._send(code,out)
            elif url.path == '/api/telegram/prepare':
                p=self._doc_path(q)
                with LOCK:
                    data,revision=STORE.read(p)
                    if b.get('expected_revision')!=revision:
                        self._send(409,{'error':'conflict','revision':revision});return
                    version=data['versions'][-1]
                    if version['id']!=b.get('version'):raise ValueError('Версия статьи изменилась')
                    value=announcements.draft(b,version['id']);text=announcements.message(value);photo=None
                    if value['include_cover']:
                        cover=validate_cover(version.get('cover'))
                        if not cover:raise ValueError('У статьи нет обложки')
                        photo=(p.parent/urllib.parse.unquote(cover['src'])).resolve()
                        assets=(p.parent/(p.name.removesuffix('.granka.json')+'.assets')).resolve()
                        if not assets.is_relative_to(ROOTS[q.get('root',[''])[0]].resolve()) or not photo.is_relative_to(assets):
                            raise PermissionError('Обложка вне каталога статьи')
                        if not photo.is_file():raise LookupError('Обложка не найдена')
                record=TELEGRAM.prepare(q.get('root',[''])[0],q.get('f',[''])[0],version['id'],text,photo)
                self._send(200,record)
            elif url.path == '/api/telegram/publish':
                self._doc_path(q)
                if b.get('confirmed') is not True:raise ValueError('Сначала проверь предпросмотр и выбери публикацию')
                record=TELEGRAM.read(b.get('id',''))
                if record['root']!=q.get('root',[''])[0] or record['rel']!=q.get('f',[''])[0]:
                    raise LookupError('Публикация не найдена для этой статьи')
                self._send(200,TELEGRAM.publish(record['id']))
            elif url.path == '/api/jobs':
                code,out=enqueue_job(q.get('root',[''])[0],q.get('f',[''])[0],b)
                self._send(code,out)

            elif url.path == "/api/prepare-image":
                p = self._doc_path(q)
                picture = resolve_image(b.get("path", ""))
                if picture.stat().st_size > 25 * 1024 * 1024:
                    raise ValueError("Картинка больше 25 МБ")
                raw = picture.read_bytes()
                with LOCK:
                    if not p.is_file():
                        raise LookupError("document not found")
                    revision = revision_of(p)
                    if revision != b.get("expected_revision"):
                        self._send(409, {"error":"conflict", "revision":revision}); return
                    root = ROOTS[q.get("root", [""])[0]].resolve()
                    folder = (p.parent / (p.name.removesuffix(".granka.json") + ".assets")).resolve()
                    if not folder.is_relative_to(root):
                        raise PermissionError("asset directory outside article root")
                    folder.mkdir(exist_ok=True)
                    dest = (folder / (hashlib.sha256(raw).hexdigest()[:24] + picture.suffix.lower())).resolve()
                    if not dest.is_relative_to(folder):
                        raise PermissionError("asset path outside article directory")
                    if not dest.exists():
                        tmp = dest.with_name("." + dest.name + "." + uuid.uuid4().hex + ".tmp")
                        try:
                            tmp.write_bytes(raw); tmp.replace(dest)
                        finally:
                            tmp.unlink(missing_ok=True)
                self._send(200, {"ok":True, "url":urllib.parse.quote(dest.relative_to(p.parent).as_posix(), safe="/"),
                                 "name":picture.stem, "revision":revision})

            elif url.path == '/api/cover':
                p = self._doc_path(q)
                cover = validate_cover(b.get('cover'))
                def fn(data):
                    if cover:
                        picture = (p.parent / urllib.parse.unquote(cover['src'])).resolve()
                        assets = (p.parent / (p.name.removesuffix('.granka.json') + '.assets')).resolve()
                        root = ROOTS[q.get('root',[''])[0]].resolve()
                        if not assets.is_relative_to(root) or not picture.is_relative_to(assets):
                            raise PermissionError('Обложка вне каталога статьи')
                        if not picture.is_file():
                            raise LookupError('Картинка обложки не найдена')
                    return edit_text(data, b.get('base_version'), data['versions'][-1]['text'], cover=cover)
                code,out = mutate(p,b.get('expected_revision'),op_id,fn)
                self._send(code,out)

            elif url.path == "/api/text":
                p = self._doc_path(q)

                def fn(data):
                    errors = contract_errors(data)
                    if errors:
                        raise ValueError("Сначала исправьте разметку: " + errors[0])
                    fulfilled = b.get("fulfilled_image_notes") or []
                    if not isinstance(fulfilled, list) or len(fulfilled) > 50:
                        raise ValueError("Некорректный список вставленных картинок")
                    for item in fulfilled:
                        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                            raise ValueError("Некорректная вставка картинки")
                        ann = next((a for a in data["annotations"] if a["id"] == item.get("id")), None)
                        if not ann or ann.get("kind") != "image" or ann["version"] != b.get("base_version"):
                            raise ValueError("Пометка картинки не найдена в редактируемой версии")
                        if not isinstance(b.get("text"), str) or "](" + item["url"] + ")" not in b["text"]:
                            raise ValueError("Картинка отсутствует в тексте")
                    result = edit_text(data, b.get("base_version"), b.get("text"))
                    if fulfilled and not result.get("unchanged"):
                        ids = {item["id"] for item in fulfilled}
                        vid = result["version"]
                        data["annotations"] = [a for a in data["annotations"]
                            if not (a["version"] == vid and a.get("source_annotation") in ids)]
                        version = data["versions"][-1]
                        version["fulfilled_image_notes"] = fulfilled
                        version["retired_annotations"] = list(dict.fromkeys(version["retired_annotations"] + list(ids)))
                        version["transferred_annotations"] = sum(a["version"] == vid for a in data["annotations"])
                        result.update(transferred=version["transferred_annotations"], retired=len(version["retired_annotations"]))
                        journal(data, "image-insert", version=vid, notes=sorted(ids))
                    errors = contract_errors(data)
                    if errors:
                        raise ValueError(errors[0])
                    return result
                code, out = mutate(p, b.get("expected_revision"), op_id, fn)
                self._send(code, out)

            elif url.path == '/api/preferences/rule-review':
                self._send(200,PREFERENCES.learning.decide(b.get('id'),b.get('action'),b.get('revision'),b.get('title'),b.get('instruction')))
            elif url.path == '/api/preferences/rule-toggle':
                self._send(200,PREFERENCES.learning.toggle(b.get('id'),b.get('enabled'),b.get('revision')))
            elif url.path == '/api/preferences/benchmark':
                self._send(200,PREFERENCES.build_suite())
            elif url.path in {'/api/decisions-batch','/api/decisions-batch/undo'}:
                p=self._doc_path(q)
                def fn(data):
                    if url.path.endswith('/undo'):return undo_batch(data,b.get('version'),b.get('batch_id'))
                    return decide_batch(data,b.get('version'),b.get('rule_id'),b.get('items'),op_id)
                code,out=mutate(p,b.get('expected_revision'),op_id,fn);self._send(code,out)
            elif url.path == "/api/decision":
                p = self._doc_path(q)
                ann_id = b.get("annotation_id")
                dec = b.get("decision")  # None = переиграть

                def fn(data):
                    ann = next((a for a in data["annotations"] if a["id"] == ann_id), None)
                    if ann is None:
                        raise LookupError("annotation not found")
                    prev = ann.get("decision")
                    if dec is None:
                        ann["decision"] = None
                        journal(data, "undecide", ann=ann_id, quote=ann.get("quote", "")[:60],
                                prev=(prev or {}).get("action"), prev_text=(prev or {}).get("text"))
                        return
                    if dec.get("action") not in DECISION_ACTIONS:
                        raise ValueError("bad action")
                    journal(data, "decide", ann=ann_id, quote=ann.get("quote", "")[:60],
                            cls=ann.get("class"), sev=ann.get("severity"),
                            act=dec.get("action"), reason=dec.get("reason"),
                            text=clean_text(dec.get("text"), 4000) if dec.get("text") else None,
                            prev=(prev or {}).get("action"))
                    ann["decision"] = {
                        "action": dec["action"],
                        "text": (clean_text(dec.get("text"), 4000) if dec.get("text") else None),
                        "reason": clean_line(dec.get("reason", ""), 120) or None,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    if dec.get('intent') in {'replace','comment','delete'}:
                        ann['decision']['intent']=dec['intent']
                code, out = mutate(p, b.get("expected_revision"), op_id, fn)
                self._send(code, out)

            elif url.path == "/api/note":
                p = self._doc_path(q)
                note = b.get("note") or {}

                def fn(data):
                    vid = note.get("version")
                    ver = next((v for v in data["versions"] if v["id"] == vid), None)
                    if ver is None:
                        raise LookupError("version not found")
                    t = ver["text"]
                    s, e = note.get("start"), note.get("end")
                    if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(t)):
                        raise ValueError("bad offsets")
                    if t[s:e] != note.get("quote"):
                        raise ValueError("quote mismatch")
                    for a in data["annotations"]:
                        if a["version"] == vid and not (e <= a["start"] or s >= a["end"]):
                            raise ValueError("overlap with " + a["id"])
                    kind = note.get("kind", "note")
                    if kind not in NOTE_KINDS:
                        raise ValueError("bad kind")
                    aid = "u" + uuid.uuid4().hex[:8]
                    journal(data, "note-add", ann=aid, kind=kind, quote=t[s:e][:60])
                    data["annotations"].append({
                        "id": aid, "version": vid, "start": s, "end": e,
                        "quote": t[s:e], "type": "note", "kind": kind,
                        "class": "🖼" if kind == "image" else "—", "severity": "",
                        "why": "место для картинки" if kind == "image" else "пометка автора",
                        "variants": [],
                        "note": clean_line(note.get("note", ""), 2000), "decision": None,
                    })
                    return {"id": aid}
                code, out = mutate(p, b.get("expected_revision"), op_id, fn)
                self._send(code, out)

            elif url.path == "/api/git-sync":
                root_id = b.get("root") or "articles"
                msg = clean_line(b.get("message", ""), 160) or ("Гранка: правки " + time.strftime("%d.%m %H:%M"))
                self._send(200, git_sync(root_id, msg,relative=b.get('rel')))

            elif url.path == "/api/create-granka":
                md = safe_resolve(q.get("root", [""])[0] or b.get("root", ""),
                                  b.get("rel", ""), suffix=".md")
                if not md.is_file():
                    raise LookupError("md not found")
                g = md.with_name(md.stem + ".granka.json")
                root = ROOTS[b.get("root")]
                rel_g = str(g.relative_to(root)).replace("\\", "/")
                if g.exists():
                    self._send(200, {"ok": True, "existed": True, "root": b.get("root"), "rel": rel_g})
                    return
                text = md.read_text(encoding="utf-8", errors="replace")
                data = {
                    "doc": md.name,
                    "status": "draft",
                    "source": str(md.relative_to(root)).replace("\\", "/"),
                    "versions": [{
                        "id": "v1",
                        "label": "исходник " + time.strftime("%d.%m %H:%M"),
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "text": text,
                    }],
                    "annotations": [],
                }
                with LOCK:
                    if g.exists():
                        self._send(200, {'ok':True,'existed':True,'root':b.get('root'),'rel':rel_g});return
                    tmp = g.with_name(f".{g.name}.{uuid.uuid4().hex[:8]}.tmp")
                    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                    tmp.replace(g)
                self._send(200, {"ok": True, "created": True, "root": b.get("root"), "rel": rel_g})

            elif url.path == "/api/voice-session":
                p = self._doc_path(q)
                sess = b.get("session") or {}
                creates = b.get("create") or []
                if not isinstance(creates, list) or len(creates) > 50:
                    raise ValueError("bad create list")

                def fn(data):
                    vid = sess.get("version")
                    ver = next((v for v in data["versions"] if v["id"] == vid), None)
                    if ver is None:
                        raise LookupError("version not found")
                    t = ver["text"]
                    made = []
                    for c in creates:
                        s, e = c.get("start"), c.get("end")
                        if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(t)):
                            raise ValueError("bad offsets")
                        if t[s:e] != c.get("quote"):
                            raise ValueError("quote mismatch")
                        for a in data["annotations"]:
                            if a["version"] == vid and not (e <= a["start"] or s >= a["end"]):
                                raise ValueError("overlap with " + a["id"])
                        aid = "v" + uuid.uuid4().hex[:8]
                        data["annotations"].append({
                            "id": aid, "version": vid, "start": s, "end": e,
                            "quote": t[s:e], "type": "note", "kind": "voice",
                            "class": "🎤", "severity": "",
                            "why": "сказано голосом при разметке", "variants": [],
                            "note": clean_line(c.get("note", ""), 2000),
                            "at": c.get("at"), "decision": None,
                        })
                        made.append({"id": aid, "start": s, "end": e})
                    journal(data, "voice-session", created=len(made), marks=len(sess.get("marks") or []))
                    data.setdefault("voice_sessions", []).append({
                        "id": "s" + uuid.uuid4().hex[:8],
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "version": vid,
                        "transcript": clean_line(sess.get("transcript", ""), 8000),
                        "segments": (sess.get("segments") or [])[:500],
                        "marks": (sess.get("marks") or [])[:50],
                        "created": [m["id"] for m in made],
                    })
                    return {"created": made}
                code, out = mutate(p, b.get("expected_revision"), op_id, fn)
                self._send(code, out)

            elif url.path == "/api/note-remove":
                p = self._doc_path(q)
                ann_id = str(b.get("annotation_id", ""))

                def fn(data):
                    before = len(data["annotations"])
                    gone = next((a for a in data["annotations"] if a["id"] == ann_id), None)
                    if gone is None:raise LookupError('annotation not found')
                    if gone.get('type')!='note':raise PermissionError('only author notes')
                    journal(data, "note-remove", ann=ann_id,
                            quote=(gone or {}).get("quote", "")[:60])
                    data["annotations"] = [a for a in data["annotations"] if a["id"] != ann_id]
                    if len(data["annotations"]) == before:
                        raise LookupError("annotation not found")
                code, out = mutate(p, b.get("expected_revision"), op_id, fn)
                self._send(code, out)

            elif url.path == "/api/status":
                p = self._doc_path(q)
                new = b.get("status")
                if new not in {"draft", "ready", "applying", "applied"}:
                    raise ValueError("bad status")

                def fn(data):
                    journal(data, "status", prev=data.get("status"), status=new)
                    data["status"] = new
                code, out = mutate(p, b.get("expected_revision"), op_id, fn)
                if code == 200 and new == "ready":
                    doc = p.name.replace(".granka.json", "")
                    out["git"] = git_sync(q.get("root", ["articles"])[0],
                                          "Гранка: решения по «" + doc + "» готовы к применению",relative=q.get('f',[''])[0])
                self._send(code, out)

            elif url.path == "/api/feedback":
                p = self._doc_path(q)
                text = clean_text(b.get("text", ""), 4000)
                if not text:
                    raise ValueError("empty feedback")
                ctx = clean_line(b.get("context", ""), 40) or None
                feedback_id='f'+uuid.uuid4().hex

                def fn(data):
                    data.setdefault("feedback", []).append({
                        "id": feedback_id, "text": text, "context": ctx,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "delivered": False, "read_by_agent": False,
                    })
                code, out = mutate(p, b.get("expected_revision"), op_id, fn)
                if code == 200 and not out.get('idempotent'):
                    delivered=deliver_feedback(text + (f' (к пометке {ctx})' if ctx else ''))
                    if delivered:
                        def record_delivery(data):
                            next(item for item in data['feedback'] if item['id']==feedback_id)['delivered']=True
                        delivery_code,delivery=mutate(p,out['revision'],op_id+'-delivery',record_delivery)
                        if delivery_code==200:out['revision']=delivery['revision']
                    out['delivered']=delivered
                self._send(code, out)

            elif url.path == "/api/allergen":
                pattern = clean_line(b.get("pattern", ""), 120)
                example = clean_line(b.get("example", ""), 240)
                note = clean_line(b.get("note", ""), 240)
                if not pattern:
                    raise ValueError("empty pattern")
                existing = CORRECTIONS.read_text(encoding="utf-8") if CORRECTIONS.exists() else ""
                if example and example in existing:
                    self._send(200, {"ok": True, "dedup": True})
                    return
                with LOCK:
                    CORRECTIONS.parent.mkdir(parents=True,exist_ok=True)
                    with CORRECTIONS.open("a", encoding="utf-8") as f:
                        f.write(f"\n## {time.strftime('%Y-%m-%d')} — {pattern} (из Гранки)\n\n"
                                f"Пример: «{example}». {note or 'Решение автора: не употреблять.'}\n")
                self._send(200, {"ok": True, "appended": pattern})
            else:
                self._send(404, {"error": "not found"})
        except (PermissionError, LookupError) as e:
            self._send(404 if isinstance(e, LookupError) else 403, {"error": str(e)})
        except (telegram.TelegramRejected, telegram.TelegramUnknown) as e:
            self._send(502, {'error':str(e)})
        except RuntimeError as e:
            if url.path.startswith('/api/site/'):
                self._send(502, {'error':str(e)})
            else:
                self._send(500, {'error':'internal'})
        except (PlanConflict, LocationsConflict) as e:
            self._send(409, {'error': str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            print(f"[granka] POST error: {e!r}")
            self._send(500, {"error": "internal"})


def main():
    global JOBS
    for root in ROOTS.values():root.mkdir(parents=True,exist_ok=True)
    JOBS=Jobs(ai_provider.STATE/'jobs',recover=True)
    SITE.recover()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Гранка: {url}")
    print(f"Корни: " + "; ".join(f"{k}={v}" for k, v in ROOTS.items()))
    print(f"Аллергены: {CORRECTIONS}")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Гранка остановлена.")


if __name__ == "__main__":
    main()
