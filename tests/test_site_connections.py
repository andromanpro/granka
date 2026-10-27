import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlunsplit
from tools import ai_provider,site_connections
from tools.file_lock import file_lock
from tools.site_publish import SitePublisher

BAD_PROXY=urlunsplit(('http','name:secret@host:5','','',''))

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'machine.json'
        self.data={'ai':{'codex_model':'before'},'private_key':'untouched',
                   'site':{'environments':{k:{'url':'https://'+k+'.example.invalid','ssh':['user@host.example.invalid'],'wp':['wp']}for k in site_connections.STAGES}}}
        self.path.write_text(json.dumps(self.data),encoding='utf-8')

    def save(self,routes,revision=None,values=None):
        with patch.object(ai_provider,'CONFIG',self.path):
            return ai_provider.save_settings(values or {},connections=routes,expected_revision=revision or site_connections.snapshot(self.path)['revision'])

    def test_routes_and_ai_saved_together_preserve_other_machine_fields(self):
        self.save({'nas':{'mode':'direct'},'staging':{'mode':'system'},'prod':{'mode':'custom','proxy':'socks5://127.0.0.1:1080'}},values={'codex_model':'after'})
        saved=json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['private_key'],'untouched');self.assertEqual(saved['ai']['codex_model'],'after')
        self.assertNotIn('ssh_proxy',saved['site']['environments']['nas']);self.assertEqual(saved['site']['environments']['staging']['ssh_proxy'],'system')
        self.assertEqual(saved['site']['environments']['prod']['ssh_proxy'],'socks5://127.0.0.1:1080')
        self.assertEqual(saved['site']['environments']['nas'],self.data['site']['environments']['nas'])

    def test_stale_revision_does_not_overwrite_either_setting(self):
        revision=site_connections.snapshot(self.path)['revision'];self.save({'prod':{'mode':'system'}})
        before=self.path.read_bytes()
        with self.assertRaises(site_connections.SettingsConflict):self.save({'prod':{'mode':'direct'}},revision,{'codex_model':'lost'})
        self.assertEqual(self.path.read_bytes(),before)

    def test_validation_never_partially_saves(self):
        before=self.path.read_bytes()
        for routes,values in [({'prod':{'mode':'custom','proxy':BAD_PROXY}},{}),
                              ({'prod':{'mode':'system'}},{'image_aspect':'bad'}),
                              ({'other':{'mode':'direct'}},{}),({'prod':{'mode':'direct','ssh':['bad']}},{})]:
            with self.subTest(routes=routes),self.assertRaises(ValueError):self.save(routes,values=values)
            self.assertEqual(self.path.read_bytes(),before)

    def test_public_snapshot_omits_keys_and_invalid_proxy_secrets(self):
        self.data['site']['environments']['prod']['ssh_proxy']=BAD_PROXY
        self.path.write_text(json.dumps(self.data),encoding='utf-8')
        with patch('tools.site_connections.urllib.request.getproxies',return_value={'https':BAD_PROXY}):
            public=site_connections.snapshot(self.path)
        raw=json.dumps(public);self.assertNotIn('secret',raw);self.assertNotIn('untouched',raw)
        self.assertFalse(public['system_proxy']['available']);self.assertEqual(public['environments'][2]['proxy'],'')

    def test_direct_clears_proxy_and_old_ai_settings_save_keeps_routes(self):
        self.save({'prod':{'mode':'system'}})
        with patch.object(ai_provider,'CONFIG',self.path):ai_provider.save_settings({'codex_model':'legacy'})
        self.assertEqual(site_connections.snapshot(self.path)['environments'][2]['mode'],'system')
        self.save({'prod':{'mode':'direct'}});self.assertNotIn('ssh_proxy',json.loads(self.path.read_text())['site']['environments']['prod'])

    def test_second_writer_cannot_acquire_config_file_lock(self):
        before=self.path.read_bytes()
        with file_lock(self.path.with_name(self.path.name+'.lock'),'busy'):
            with self.assertRaisesRegex(ValueError,'другом окне'):self.save({})
        self.assertEqual(self.path.read_bytes(),before)

    def test_new_wordpress_operation_gets_new_route_old_operation_keeps_snapshot(self):
        config={'site':{'enabled':True,'repository':self.temp.name,'remote':'mock','environments':copy.deepcopy(self.data['site']['environments'])}}
        with patch('tools.config.load',side_effect=lambda:copy.deepcopy(config)):
            publisher=SitePublisher(lambda:Path(self.temp.name))
            old=publisher.wordpress('prod',self.temp.name)
            config['site']['environments']['prod']['ssh_proxy']='system'
            publisher.require_config()
            new=publisher.wordpress('prod',self.temp.name)
        self.assertNotIn('ssh_proxy',old.env);self.assertEqual(new.env['ssh_proxy'],'system')
