import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from tools.public_audit import scan,history
from tools.public_export import export
from tools import ai_provider

ROOT=Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_scanner_reports_locations_without_matched_secret(self):
        secret='sk-'+'a'*40
        hits=scan('sample.py',('key="'+secret+'"').encode())
        self.assertEqual(hits[0]['rule'],'credential')
        self.assertNotIn(secret,json.dumps(hits))
        self.assertTrue(scan('voice-corpus/sample.md',b'personal draft'))
        self.assertTrue(scan('library-locations.json',b'{"locations":[]}'))
        address='.'.join(['192','168','10','20'])
        self.assertTrue(scan('config.py',address.encode()))
        self.assertEqual(scan('config.py',b'http://127.0.0.1:8901/'),[])
        self.assertTrue(scan('README.md',b'private-beacon',['private-beacon']))

    def test_clean_export_does_not_include_unsafe_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'repo';root.mkdir()
            def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
            git('init','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            (root/'private.txt').write_text('private-beacon')
            git('add','private.txt');git('commit','-m','initial')
            git('rm','private.txt');(root/'README.md').write_text('Public project')
            git('add','README.md');git('commit','-m','clean snapshot')
            self.assertTrue(history(root,['private-beacon'])['findings'])
            output=Path(folder)/'public.zip';result=export(root,output,['private-beacon'])
            self.assertFalse(result['git_history_included'])
            with zipfile.ZipFile(output) as archive:self.assertEqual(archive.namelist(),['README.md'])

    def test_fresh_configuration_has_no_private_corpus_or_site(self):
        with tempfile.TemporaryDirectory() as folder:
            env={**os.environ,'GRANKA_STATE_DIR':folder,'GRANKA_CONFIG':str(Path(folder)/'missing.json')}
            code=('import json;from tools.config import load,roots;from tools.iimeter import measure;'
                  'from tools.site_publish import SitePublisher;'
                  'print(json.dumps({"roots":{k:str(v) for k,v in roots().items()},"enabled":load()["site"]["enabled"],'
                  '"meter":measure("Own writing sample."),"site_repo":SitePublisher(lambda:"state").repo}))')
            out=json.loads(subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,env=env))
            self.assertEqual(Path(out['roots']['articles']),Path(folder)/'articles')
            self.assertFalse(out['enabled']);self.assertIsNone(out['site_repo'])
            self.assertIsNone(out['meter']['score']);self.assertEqual(out['meter']['status'],'uncalibrated')

    def test_openrouter_always_saves_response_before_validation(self):
        cases=[({'id':'receipt','usage':{'cost':0.1},'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]},True),
               ({'id':'receipt','choices':[{'finish_reason':'length','message':{'content':'partial'}}]},False),
               ({'choices':[{'finish_reason':'stop','message':{'content':''}}]},False)]
        for data,success in cases:
            with self.subTest(success=success),tempfile.TemporaryDirectory() as folder:
                response=io.BytesIO(json.dumps(data).encode());response.status=200
                with patch.object(ai_provider,'openrouter_key',return_value='fixture'),patch('urllib.request.urlopen',return_value=response) as call:
                    if success:self.assertEqual(ai_provider.run_openrouter_text('test',folder,{'openrouter_text_model':'test/model'})[0],{'ok':True})
                    else:
                        with self.assertRaises(RuntimeError):ai_provider.run_openrouter_text('test',folder,{'openrouter_text_model':'test/model'})
                self.assertEqual(call.call_count,1)
                self.assertEqual(json.loads((Path(folder)/'raw-response.json').read_text()),data)
                self.assertEqual(json.loads((Path(folder)/'receipt.json').read_text())['finish_reason'],data['choices'][0]['finish_reason'])
