import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.telegram import Telegram,TelegramRejected,TelegramUnknown
from tools.telegram_delivery import PublicationBusy

TOKEN='123456789:'+('b'*35)


def reply(token,method,payload,photo=None,files=None):
    if method=='getMe':return {'id':123,'username':'fixture_bot'}
    if method=='getChat':return {'id':-100123456789,'type':'channel','title':'Fixture','username':'fixture_channel'}
    if method=='getChatMember':return {'status':'administrator','can_post_messages':True}
    if method=='sendMediaGroup':return [{'message_id':71},{'message_id':72}]
    return {'message_id':payload.get('message_id',42)}


def child_publish(directory,identifier,entered,release,results):
    def blocking(*args,**kwargs):
        entered.set();release.wait(12);return {'message_id':42}
    try:
        with patch('tools.telegram.bot_call',side_effect=blocking):
            results.put(Telegram(lambda:Path(directory)).publish(identifier)['status'])
    except Exception as e:results.put(type(e).__name__)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.service=Telegram(lambda:self.root);self.service.save_settings({'chat_id':'@fixture_channel','token':TOKEN})
        self.mock=patch('tools.telegram.bot_call',side_effect=reply);self.calls=self.mock.start();self.addCleanup(self.mock.stop)

    def prepare(self,text='First',**kwargs):return self.service.prepare('a','article.granka.json','v1',text,**kwargs)
    def sent(self,**kwargs):return self.service.publish(self.prepare(**kwargs)['id'])
    def operations(self):return [c for c in self.calls.call_args_list if c.args[1].startswith(('send','edit'))]
    def picture(self,name):
        from PIL import Image
        p=self.root/name;Image.new('RGB',(4,4),(len(name)*11%255,sum(name.encode())%255,80)).save(p);return p

    def test_text_update_keeps_id_url_and_repeated_apply_does_not_send(self):
        old=self.sent();ready=self.prepare('Changed',action='update',target_id=old['id']);new=self.service.publish(ready['id'])
        self.assertEqual(new['message_id'],old['message_id']);self.assertEqual(new['url'],old['url']);self.assertEqual(new['action'],'update')
        self.service.publish(ready['id']);self.assertEqual([c.args[1]for c in self.operations()],['sendMessage','editMessageText'])

    def test_changed_content_without_explicit_action_is_rejected(self):
        self.sent()
        with self.assertRaisesRegex(ValueError,'Выбери'):self.prepare('Changed')
        self.assertEqual(len(self.operations()),1)

    def test_explicit_create_after_publication_is_new_and_idempotent(self):
        old=self.sent();ready=self.prepare(action='create');self.assertNotEqual(ready['id'],old['id'])
        self.assertEqual(self.prepare(action='create')['id'],ready['id'])
        self.service.publish(ready['id']);self.service.publish(ready['id']);self.assertEqual(len(self.operations()),2)

    def test_unchanged_update_is_existing_receipt(self):
        old=self.sent();value=self.prepare(action='update',target_id=old['id']);self.assertTrue(value['unchanged']);self.assertEqual(value['id'],old['id']);self.assertEqual(len(self.operations()),1)

    def test_two_different_prepared_creates_cannot_both_publish(self):
        a=self.prepare('A',action='create');b=self.prepare('B',action='create');self.service.publish(a['id'])
        with self.assertRaisesRegex(ValueError,'другом окне'):self.service.publish(b['id'])
        self.assertEqual(len(self.operations()),1)

    def test_stale_window_cannot_prepare_an_unintended_second_post(self):
        old=self.sent()
        with self.assertRaisesRegex(ValueError,'История публикаций изменилась'):
            self.prepare('Second',action='create',expected_source_id=None)
        self.assertEqual(self.service.publications('a','article.granka.json')[0]['is_source_head'],True)
        self.assertEqual(len(self.operations()),1)

    def test_stale_update_does_not_overwrite_newer_edit_even_equal_timestamps(self):
        with patch('tools.telegram.time.time',return_value=123):
            old=self.sent();a=self.prepare('A',action='update',target_id=old['id']);b=self.prepare('B',action='update',target_id=old['id']);self.service.publish(a['id'])
            with self.assertRaises(ValueError):self.service.publish(b['id'])
            with self.assertRaisesRegex(ValueError,'уже обновлён'):self.prepare('C',action='update',target_id=old['id'])
        self.assertEqual(len(self.operations()),2)

    def test_rich_update_replaces_inline_files_without_send_fallback(self):
        p=self.picture('one.png');old=self.sent(rich_message={'markdown':'![x](tg://photo?id=attach://m0)'},rich_files={'m0':p})
        p2=self.picture('two.png');rich={'markdown':'New\n\n![y](tg://photo?id=attach://m0)'}
        ready=self.prepare('Changed',rich_message=rich,rich_files={'m0':p2},action='update',target_id=old['id']);out=self.service.publish(ready['id'])
        call=self.operations()[-1];self.assertEqual(call.args[1],'editMessageText');self.assertEqual(call.args[2]['rich_message'],rich);self.assertEqual(call.kwargs['files']['m0'].read_bytes(),p2.read_bytes());self.assertEqual(out['message_id'],42)

    def test_single_photo_changes_media_in_place(self):
        old=self.sent(media=[self.picture('one.png')]);ready=self.prepare('Caption',media=[self.picture('two.png')],action='update',target_id=old['id']);out=self.service.publish(ready['id'])
        self.assertEqual(self.operations()[-1].args[1],'editMessageMedia');self.assertEqual(out['message_id'],old['message_id'])

    def test_album_preserves_all_message_numbers_and_rejects_count_change(self):
        items=[self.picture('one.png'),self.picture('two.png')];old=self.sent(media=items)
        with self.assertRaisesRegex(ValueError,'число вложений'):self.prepare('Wrong',media=[items[0]],action='update',target_id=old['id'])
        ready=self.prepare('Changed',media=list(reversed(items)),action='update',target_id=old['id']);out=self.service.publish(ready['id'])
        edits=[c for c in self.operations()if c.args[1].startswith('edit')];self.assertEqual([c.args[2]['message_id']for c in edits],[71,72]);self.assertEqual(out['message_ids'],[71,72])

    def test_failed_update_never_sends_new_message(self):
        old=self.sent();ready=self.prepare('Changed',action='update',target_id=old['id'])
        with patch('tools.telegram.bot_call',side_effect=TelegramRejected('No edit permission'))as call:
            self.assertEqual(self.service.publish(ready['id'])['status'],'failed')
        self.assertEqual(call.call_count,1);self.assertEqual(call.call_args.args[1],'editMessageText')

    def test_not_modified_is_success_but_other_rejections_are_not(self):
        old=self.sent();ready=self.prepare('Changed',action='update',target_id=old['id'])
        with patch('tools.telegram.bot_call',side_effect=TelegramRejected('Bad Request: message is not modified')):
            self.assertEqual(self.service.publish(ready['id'])['status'],'sent')

    def test_partial_album_failure_is_unknown_and_never_retried(self):
        items=[self.picture('one.png'),self.picture('two.png')];old=self.sent(media=items);ready=self.prepare('Changed',media=items,action='update',target_id=old['id'])
        with patch('tools.telegram.bot_call',side_effect=[{'message_id':71},TelegramRejected('Denied')])as call:
            out=self.service.publish(ready['id']);self.assertEqual(out['status'],'unknown');self.assertEqual(out['completed_edits'],[71]);self.service.publish(ready['id']);self.assertEqual(call.call_count,2)
        with self.assertRaisesRegex(ValueError,'неизвестным'):self.prepare('Other',action='create')

    def test_foreign_article_cannot_be_edit_target(self):
        old=self.service.publish(self.service.prepare('other','other.granka.json','v1','Other')['id'])
        with self.assertRaises(ValueError):self.prepare(action='update',target_id=old['id'])

    def process_case(self,terminate=False):
        ready=self.prepare(action='create');ctx=mp.get_context('spawn');entered=ctx.Event();release=ctx.Event();results=ctx.Queue();p=ctx.Process(target=child_publish,args=(str(self.root),ready['id'],entered,release,results));p.start()
        try:
            self.assertTrue(entered.wait(10),'child did not enter API call');other=Telegram(lambda:self.root)
            with self.assertRaises(PublicationBusy),patch('tools.telegram.bot_call')as calls:other.publish(ready['id'])
            calls.assert_not_called();self.assertEqual(other.publications('a','article.granka.json')[0]['status'],'sending')
            if terminate:
                p.terminate();p.join(5);self.assertEqual(other.publications('a','article.granka.json')[0]['status'],'unknown')
                self.assertEqual(other.publish(ready['id'])['status'],'unknown')
            else:
                release.set();p.join(10);self.assertEqual(results.get(timeout=3),'sent');self.assertEqual(other.publish(ready['id'])['status'],'sent')
        finally:
            # A killed Event waiter can leave notify_all's accounting incomplete.
            # Never touch its synchronization objects after termination.
            if terminate:
                if p.is_alive():p.terminate()
            else:release.set()
            p.join(2)
            if p.is_alive():p.terminate();p.join(3)
            results.close()

    def test_second_real_process_cannot_send_and_history_does_not_corrupt_active_status(self):self.process_case()
    def test_crashed_process_releases_os_lock_but_does_not_allow_retry(self):self.process_case(terminate=True)
