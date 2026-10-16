"""Scan distributable files or Git history without printing matched secret values."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

RULES={
    'server_address':r'(?<![\d.])(?!(?:127\.0\.0\.1|0\.0\.0\.0)(?![\d.]))(?:\d{1,3}\.){3}\d{1,3}(?![\d.])',
    'private_network':r'(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])',
    'personal_path':r'(?i)(?:[a-z]:[\\/](?:users|workai|home)[\\/][^\s"\']+|/(?:Users|home)/[a-z][^/\s"\']+/)',
    'credential':r'(?<![\w-])(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{30,}|\d{7,15}:[A-Za-z0-9_-]{30,})(?![\w-])',
    'private_key':r'-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----',
    'credential_url':r'''https?://[^\s/@:'"<>]+:[^\s/@'"<>]+@''',
}
PRIVATE_DIRS={'_audit','_dist','private','voice-corpus','node_modules','__pycache__','.git'}
PRIVATE_NAMES={'granka-config.json','library-locations.json','.env','excalidraw.log','voice-profile.json','voice-profile-talks.json'}


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)


def scan(path,raw,policy=()):
    findings=[];parts=Path(path).parts;name=Path(path).name
    if any(p in PRIVATE_DIRS for p in parts) or name in PRIVATE_NAMES or (name.startswith('.env.') and name!='.env.example') or name.endswith(('.pyc','.pyo','.log')):
        findings.append({'file':path,'rule':'private_file','line':1})
    text=raw.decode('utf-8','ignore')
    for rule,pattern in RULES.items():
        for match in re.finditer(pattern,text):
            findings.append({'file':path,'rule':rule,'line':text.count('\n',0,match.start())+1})
    folded=text.casefold()
    for value in policy:
        start=folded.find(value.casefold())
        if start>=0:findings.append({'file':path,'rule':'private_policy','line':text.count('\n',0,start)+1})
    return findings


def worktree(root,policy=()):
    paths=set(git(root,'ls-files','-z').decode('utf-8').split('\0'))
    paths.update(git(root,'ls-files','--others','--exclude-standard','-z').decode('utf-8').split('\0'))
    findings=[];count=0
    for name in sorted(paths):
        if not name or not (root/name).is_file():continue
        if (root/name).is_symlink():findings.append({'file':name,'rule':'symlink','line':1});continue
        count+=1;findings.extend(scan(name,(root/name).read_bytes(),policy))
    return {'scope':'worktree','files_checked':count,'findings':findings,'license_present':(root/'LICENSE').is_file()}


def history(root,policy=()):
    objects=[]
    for line in git(root,'rev-list','--objects','--all').decode('utf-8').splitlines():
        oid,_,path=line.partition(' ');objects.append((oid,path))
    process=subprocess.Popen(['git','-C',str(root),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    findings=[];count=0
    try:
        for oid,path in objects:
            process.stdin.write((oid+'\n').encode());process.stdin.flush()
            header=process.stdout.readline().decode().split();size=int(header[2]);raw=process.stdout.read(size);process.stdout.read(1)
            if header[1] not in {'blob','commit','tag'}:continue
            count+=1
            findings.extend({**item,'object':oid} for item in scan(path or '['+header[1]+']',raw,policy))
    finally:
        process.stdin.close();process.stdout.close();process.wait()
    return {'scope':'history','objects_checked':count,'findings':findings}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--history',action='store_true')
    parser.add_argument('--policy',type=Path,help='Private JSON list of additional literal strings to reject')
    parser.add_argument('--report',type=Path,help='Write a report outside the public repository')
    args=parser.parse_args();policy=json.loads(args.policy.read_text(encoding='utf-8')) if args.policy else []
    if not isinstance(policy,list) or not all(isinstance(s,str) and s for s in policy):parser.error('Policy must be a list of nonempty strings')
    result=history(args.root,policy) if args.history else worktree(args.root,policy)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    counts={}
    for item in result['findings']:counts[item['rule']]=counts.get(item['rule'],0)+1
    print(json.dumps({'scope':result['scope'],'findings':len(result['findings']),'by_rule':counts,'report_saved':bool(args.report)},ensure_ascii=False))
    if not args.report:
        for item in result['findings'][:30]:print(f"{item['file']}:{item['line']} [{item['rule']}]")
    return 1 if result['findings'] else 0


if __name__=='__main__':sys.exit(main())
