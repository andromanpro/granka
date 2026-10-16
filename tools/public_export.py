"""Export an audited, history-free snapshot. Does not create or push a repository."""
import argparse
import datetime
import json
from pathlib import Path
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.public_audit import git,scan


def export(root,output,policy=()):
    root=Path(root).resolve();output=Path(output).resolve()
    if git(root,'status','--porcelain').strip():raise ValueError('Commit reviewed changes before public export')
    files={}
    for entry in git(root,'ls-tree','-rz','HEAD').decode('utf-8').split('\0'):
        if not entry:continue
        info,name=entry.split('\t',1);mode,kind,oid=info.split()
        if kind!='blob' or mode=='120000':raise ValueError('Unsupported file type in export: '+name)
        raw=git(root,'cat-file','blob',oid)
        if scan(name,raw,policy):raise ValueError('Public audit rejected '+name+'; run public_audit.py for details')
        files[name]=raw
    if '.git' in files:raise ValueError('Git metadata cannot be exported')
    if output.exists():raise ValueError('Export destination already exists; choose another filename')
    output.parent.mkdir(parents=True,exist_ok=True)
    stamp=int(git(root,'show','-s','--format=%ct','HEAD').decode())
    date=datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(name,date);info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            archive.writestr(info,raw)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert all(not scan(name,archive.read(name),policy) for name in archive.namelist())
    return {'files':len(files),'output':str(output),'commit':git(root,'rev-parse','HEAD').decode().strip(),
            'license_present':'LICENSE' in files,'git_history_included':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--policy',type=Path)
    args=parser.parse_args();policy=json.loads(args.policy.read_text(encoding='utf-8')) if args.policy else []
    print(json.dumps(export(args.root,args.output,policy),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
