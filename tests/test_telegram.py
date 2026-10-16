import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import announcements
from tools.telegram import Telegram, TelegramRejected, TelegramUnknown, bot_call

TOKEN='123456789:'+('a'*35)
def response(token,method,payload,photo=None):
    return {'getMe':{'id':123,'username':'granka_test_bot'},
            'getChat':{'id':-100123456789,'type':'channel','title':'Тестовый канал','username':'granka_test'},
            'getChatMember':{'status':'administrator','can_post_messages':True},
            'sendMessage':{'message_id':42},'sendPhoto':{'message_id':43}}[method]


class AnnouncementTests(unittest.TestCase):
    def test_message_has_exact_text_and_separate_link(self):
        draft=announcements.draft({'text':'Текст <прямо> и & без разметки.','url':'https://example.org/post','include_cover':False},'v1')
        self.assertEqual(announcements.message(draft),'Текст <прямо> и & без разметки.\n\nhttps://example.org/post')
        with self.assertRaises(ValueError):announcements.link('javascript:alert(1)')
        with self.assertRaises(ValueError):announcements.link('https://'+'secret:password'+'@example.org/')

    def test_photo_and_text_limits_include_link_and_unicode(self):
        for text,cover in [('я'*1025,True),('😀'*2050,False)]:
            with self.subTest(cover=cover),self.assertRaises(ValueError):
                announcements.message(announcements.draft({'text':text,'include_cover':cover},'v1'))
        draft=announcements.draft({'text':'я'*1000,'url':'https://example.org/longer-post','include_cover':True},'v1')
        with self.assertRaises(ValueError):announcements.message(draft)

    def test_generation_removes_unfilled_author_instructions(self):
        prompt=announcements.prompt('# Тема\nФакт. [ЛИЧНОЕ: придумай биографию автора]','Сухо и коротко')
        self.assertNotIn('придумай биографию',prompt)
        self.assertIn('Факт.',prompt);self.assertIn('Сухо и коротко',prompt)
        with self.assertRaises(ValueError):announcements.variants({'variants':[{'label':'Тест','text':'[придумай анонс]'}]})


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=Telegram(lambda:self.root)
        self.service.save_settings({'chat_id':'@granka_test','token':TOKEN})

    def tearDown(self):self.temp.cleanup()

    def prepare(self,photo=None):
        return self.service.prepare('articles','article.granka.json','v1','Точный текст\n\nhttps://example.org',photo)

    def test_settings_do_not_return_token_and_blank_preserves_it(self):
        result=self.service.save_settings({'chat_id':'@granka_test','token':''})
        self.assertNotIn(TOKEN,json.dumps(result));self.assertTrue(result['has_token'])
        self.assertEqual(self.service.token(),TOKEN)

    def test_preparation_does_not_publish_and_repeated_send_is_idempotent(self):
        with patch('tools.telegram.bot_call',side_effect=response) as call:
            ready=self.prepare();self.assertEqual(call.call_count,3)
            self.assertEqual(ready['status'],'prepared');self.assertNotIn('config_fingerprint',ready)
            result=self.service.publish(ready['id']);again=self.service.publish(ready['id'])
            self.assertEqual(result['status'],'sent');self.assertEqual(result,again)
            self.assertEqual(sum(c.args[1]=='sendMessage' for c in call.call_args_list),1)
            sent=next(c for c in call.call_args_list if c.args[1]=='sendMessage')
            self.assertEqual(sent.args[2]['text'],ready['text'])
            self.assertEqual(sent.args[2]['chat_id'],'-100123456789')
            self.assertNotIn('parse_mode',sent.args[2]);self.assertEqual(result['url'],'https://t.me/granka_test/42')
            self.assertEqual(self.prepare()['status'],'sent')

    def test_unknown_delivery_is_never_retried_even_after_restart(self):
        with patch('tools.telegram.bot_call',side_effect=response):ready=self.prepare()
        with patch('tools.telegram.bot_call',side_effect=TelegramUnknown('timeout')) as call:
            out=self.service.publish(ready['id']);self.assertEqual(out['status'],'unknown')
            restarted=Telegram(lambda:self.root)
            self.assertEqual(restarted.publish(ready['id'])['status'],'unknown');self.assertEqual(call.call_count,1)

    def test_restart_turns_interrupted_send_into_unknown(self):
        with patch('tools.telegram.bot_call',side_effect=response):ready=self.prepare()
        path=self.service.record_path(ready['id']);record=json.loads(path.read_text(encoding='utf-8'));record['status']='sending'
        path.write_text(json.dumps(record),encoding='utf-8')
        self.assertEqual(self.service.publications('articles','article.granka.json')[0]['status'],'unknown')

    def test_definite_rejection_requires_explicit_retry(self):
        with patch('tools.telegram.bot_call',side_effect=response):ready=self.prepare()
        with patch('tools.telegram.bot_call',side_effect=TelegramRejected('Нет права публикации')) as call:
            self.assertEqual(self.service.publish(ready['id'])['status'],'failed');self.assertEqual(call.call_count,1)
        with patch('tools.telegram.bot_call',side_effect=response):self.assertEqual(self.service.publish(ready['id'])['status'],'sent')

    def test_cover_snapshot_cannot_change_after_review(self):
        photo=self.root/'cover.png';photo.write_bytes(b'original-png')
        with patch('tools.telegram.bot_call',side_effect=response) as call:
            ready=self.prepare(photo);photo.write_bytes(b'changed-png')
            out=self.service.publish(ready['id']);self.assertEqual(out['status'],'sent')
            sent=next(c for c in call.call_args_list if c.args[1]=='sendPhoto')
            self.assertEqual(sent.args[3].read_bytes(),b'original-png')
            self.assertEqual(sent.args[2]['caption'],ready['text'])

    def test_changed_channel_cannot_receive_prepared_post(self):
        with patch('tools.telegram.bot_call',side_effect=response):ready=self.prepare()
        self.service.save_settings({'chat_id':'@another_channel'})
        with patch('tools.telegram.bot_call') as call,self.assertRaises(ValueError):self.service.publish(ready['id'])
        call.assert_not_called()

    def test_transport_errors_never_reveal_token(self):
        import urllib.error
        error=urllib.error.URLError('https://api.telegram.org/bot'+TOKEN+'/sendMessage')
        with patch('urllib.request.urlopen',side_effect=error),self.assertRaises(TelegramUnknown) as caught:
            bot_call(TOKEN,'sendMessage',{'chat_id':'@granka_test','text':'test'})
        self.assertNotIn(TOKEN,str(caught.exception))

    def test_bot_transport_uses_utf8_json_and_multipart_photo(self):
        import io
        result=b'{"ok":true,"result":{"message_id":42}}'
        with patch('urllib.request.urlopen',return_value=io.BytesIO(result)) as send:
            bot_call(TOKEN,'sendMessage',{'chat_id':'-100123456789','text':'Текст & <точно>'})
            req=send.call_args.args[0]
            self.assertEqual(json.loads(req.data),{'chat_id':'-100123456789','text':'Текст & <точно>'})
            self.assertEqual(req.get_header('Content-type'),'application/json')
        photo=self.root/'cover.png';photo.write_bytes(b'png-binary')
        with patch('urllib.request.urlopen',return_value=io.BytesIO(result)) as send:
            bot_call(TOKEN,'sendPhoto',{'chat_id':'-100123456789','caption':'Подпись'},photo)
            req=send.call_args.args[0]
            self.assertIn('multipart/form-data',req.get_header('Content-type'))
            self.assertIn(b'name="photo"',req.data);self.assertIn(b'png-binary',req.data)
            self.assertIn('Подпись'.encode('utf-8'),req.data)
