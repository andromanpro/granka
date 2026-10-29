"""The publication identity must be explicit; manual mode never sends a post."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.telegram import Telegram

TOKEN='123456789:'+('s'*35)

def reply(token,method,payload,photo=None,files=None):
    if method=='getMe':return {'id':123,'username':'sender_fixture_bot'}
    if method=='getChat':return {'id':-100123456789,'type':'channel','title':'Учебный канал','username':'sender_fixture'}
    if method=='getChatMember':return {'status':'administrator','can_post_messages':True}
    if method.startswith(('send','edit')):return {'message_id':42}
    raise AssertionError(method)


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.service=Telegram(lambda:self.directory)
        self.service.save_settings({'chat_id':'@sender_fixture','token':TOKEN})

    def prepare(self,**kwargs):
        with patch('tools.telegram.bot_call',side_effect=reply):
            return self.service.prepare('articles','fixture.granka.json','v1','Учебный пост',**kwargs)

    def test_legacy_settings_keep_channel_mode_and_reviewed_fingerprint(self):
        config=self.service.config();config.pop('publication_mode')
        self.service.persist(self.service.directory/'settings.json',config)
        before=(self.service.directory/'settings.json').read_bytes()
        self.assertEqual(self.service.settings()['publication_mode'],'channel')
        expected=hashlib.sha256(('@sender_fixture|'+TOKEN).encode()).hexdigest()
        self.assertEqual(self.service.fingerprint(),expected)
        self.assertEqual((self.service.directory/'settings.json').read_bytes(),before)

    def test_mode_persists_on_restart_and_credential_updates(self):
        self.service.save_settings({'publication_mode':'manual'})
        restarted=Telegram(lambda:self.directory)
        self.assertEqual(restarted.settings()['publication_mode'],'manual')
        result=restarted.save_settings({'token':'','chat_id':'@sender_fixture'})
        self.assertEqual(result['publication_mode'],'manual')
        self.assertNotIn(TOKEN,json.dumps(result));self.assertEqual(restarted.token(),TOKEN)
        self.assertEqual(restarted.save_settings({'publication_mode':'channel'})['publication_mode'],'channel')

    def test_invalid_mode_never_rewrites_credentials_or_settings(self):
        before=(self.service.directory/'settings.json').read_bytes()
        for value in [None,False,True,{},[],0,'','personal','bot']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.service.save_settings({'publication_mode':value,'chat_id':'@other_fixture','token':'invalid'})
            self.assertEqual((self.service.directory/'settings.json').read_bytes(),before)

    def test_manual_mode_needs_no_bot_and_blocks_preparation_before_network(self):
        self.service.save_settings({'publication_mode':'manual','clear_token':True})
        with patch('tools.telegram.bot_call') as network,self.assertRaisesRegex(ValueError,'ручная публикация'):
            self.service.prepare('articles','fixture.granka.json','v1','Текст')
        network.assert_not_called();self.assertFalse((self.service.directory/'outbox').exists())

    def test_manual_mode_blocks_stale_prepared_post_without_mutating_it(self):
        ready=self.prepare();path=self.service.record_path(ready['id']);before=path.read_bytes()
        self.service.save_settings({'publication_mode':'manual'})
        with patch('tools.telegram.bot_call') as network,self.assertRaisesRegex(ValueError,'ручная публикация'):
            Telegram(lambda:self.directory).publish(ready['id'])
        network.assert_not_called();self.assertEqual(path.read_bytes(),before)

    def test_manual_mode_also_blocks_update_and_keeps_old_receipt(self):
        ready=self.prepare()
        with patch('tools.telegram.bot_call',side_effect=reply):old=self.service.publish(ready['id'])
        with patch('tools.telegram.bot_call',side_effect=reply):
            update=self.service.prepare('articles','fixture.granka.json','v1','Новый текст',action='update',target_id=old['id'])
        self.service.save_settings({'publication_mode':'manual'})
        with patch('tools.telegram.bot_call') as network,self.assertRaises(ValueError):self.service.publish(update['id'])
        network.assert_not_called();self.assertEqual(self.service.read(old['id'])['status'],'sent')

    def test_channel_snapshot_names_sender_but_does_not_invent_bot_api_send_as(self):
        ready=self.prepare();self.assertEqual(ready['sender_mode'],'channel')
        self.assertEqual(ready['channel']['title'],'Учебный канал')
        with patch('tools.telegram.bot_call',side_effect=reply) as network:self.service.publish(ready['id'])
        self.assertEqual(network.call_args.args[2]['chat_id'],'-100123456789')
        self.assertNotIn('send_as',network.call_args.args[2])

    def test_unsupported_snapshot_sender_cannot_fall_back_to_channel(self):
        ready=self.prepare();record=self.service.read(ready['id']);record['sender_mode']='personal'
        self.service.persist(self.service.record_path(ready['id']),record)
        with patch('tools.telegram.bot_call') as network,self.assertRaisesRegex(ValueError,'Отправитель'):
            self.service.publish(ready['id'])
        network.assert_not_called();self.assertEqual(self.service.read(ready['id'])['status'],'prepared')

    def test_legacy_snapshot_still_sends_once_after_settings_are_saved(self):
        ready=self.prepare();record=self.service.read(ready['id']);record.pop('sender_mode')
        self.service.persist(self.service.record_path(ready['id']),record)
        self.service.save_settings({'publication_mode':'channel'})
        with patch('tools.telegram.bot_call',side_effect=reply) as network:
            self.assertEqual(self.service.publish(ready['id'])['status'],'sent')
            self.service.publish(ready['id'])
        self.assertEqual(network.call_count,1)

    def test_manual_mode_does_not_retry_or_hide_a_sent_receipt(self):
        ready=self.prepare()
        with patch('tools.telegram.bot_call',side_effect=reply):self.service.publish(ready['id'])
        self.service.save_settings({'publication_mode':'manual'})
        with patch('tools.telegram.bot_call') as network:
            self.assertEqual(self.service.publish(ready['id'])['status'],'sent')
        network.assert_not_called()

    def test_corrupted_mode_on_disk_fails_closed(self):
        ready=self.prepare();config=self.service.config();config['publication_mode']='personal'
        self.service.persist(self.service.directory/'settings.json',config)
        with patch('tools.telegram.bot_call') as network,self.assertRaises(ValueError):self.service.publish(ready['id'])
        network.assert_not_called()
