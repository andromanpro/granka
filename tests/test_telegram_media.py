import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from tools import announcements, telegram_media
from tools.site_engine import render_markdown
from tools.telegram import Telegram, TelegramUnknown, bot_call
import test_site_publish as site_fixtures

TOKEN='123456789:'+('b'*35)


def png():
    output=io.BytesIO();Image.new('RGB',(12,9),'navy').save(output,format='PNG');return output.getvalue()


def reply(token,method,payload,photo=None,files=None):
    if method=='getMe':return {'id':123,'username':'fixture_bot'}
    if method=='getChat':return {'id':-100123456789,'type':'channel','title':'Fixture','username':'fixture_channel'}
    if method=='getChatMember':return {'status':'administrator','can_post_messages':True}
    if method=='sendMediaGroup':return [{'message_id':71+i}for i in range(len(payload['media']))]
    return {'message_id':71}


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.doc=self.root/'article.granka.json';self.doc.write_text('{}')
        self.image=telegram_media.import_asset(self.doc,self.root,'frame.png',png())
        self.video=telegram_media.import_asset(self.doc,self.root,'film.mp4',b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00')
        self.service=Telegram(lambda:self.root/'state');self.service.save_settings({'chat_id':'@fixture_channel','token':TOKEN})

    def tearDown(self):self.temp.cleanup()
    def path(self,item):return telegram_media.resolve_asset(self.doc,self.root,item['src'])

    def test_upload_validates_bytes_and_cannot_escape_article_assets(self):
        with self.assertRaises(ValueError):telegram_media.import_asset(self.doc,self.root,'film.mp4',b'notvideo')
        with self.assertRaises(ValueError):telegram_media.import_asset(self.doc,self.root,'frame.png',b'notpng')
        with self.assertRaises(PermissionError):telegram_media.resolve_asset(self.doc,self.root,'../secret.mp4')
        with self.assertRaises(ValueError):telegram_media.resolve_asset(self.doc,self.root,'file:///secret.mp4')
        repeated=telegram_media.import_asset(self.doc,self.root,'renamed.png',png())
        self.assertEqual(self.image['src'],repeated['src'])

    def test_caption_limit_and_separate_rich_article_limit(self):
        draft=announcements.draft({'text':'я'*1025,'attachments':[self.video]},'v1')
        with self.assertRaises(ValueError):announcements.message(draft)
        article=announcements.draft({'text':'я'*5000,'mode':'article'},'v1')
        self.assertEqual(len(announcements.message(article)),5000)
        with self.assertRaises(ValueError):announcements.draft({'text':'x','mode':'article','attachments':[self.image]},'v1')

    def test_one_video_and_album_snapshot_are_sent_once(self):
        for items,method in [([self.video],'sendVideo'),([self.video,self.image],'sendMediaGroup')]:
            with self.subTest(method=method),patch('tools.telegram.bot_call',side_effect=reply)as send:
                ready=self.service.prepare('a','article.granka.json','v1',method,media=[self.path(i)for i in items])
                self.assertEqual(sum(c.args[1].startswith('send')for c in send.call_args_list),0)
                public=json.dumps(ready);self.assertNotIn(str(self.root),public)
                out=self.service.publish(ready['id']);self.assertEqual(out['status'],'sent');self.service.publish(ready['id'])
                sent=[c for c in send.call_args_list if c.args[1]==method];self.assertEqual(len(sent),1)
                self.assertEqual(set(sent[0].kwargs['files']),{'video'}if len(items)==1 else{'m0','m1'})
                if len(items)==1:self.assertNotIn('video',sent[0].args[2])  # The multipart file itself is the video parameter.
                if len(items)==2:self.assertEqual(out['message_ids'],[71,72])

    def test_rich_media_order_and_local_paths_are_replaced(self):
        text=f'# Тема\n\nДо.\n\n![Видео]({self.video["src"]})\n\nМежду.\n\n![Фото]({self.image["src"]})\n\nПосле.'
        rich,files=telegram_media.rich_input(text,lambda s:telegram_media.resolve_asset(self.doc,self.root,s))
        self.assertIn('tg://video?id=m0',rich['markdown']);self.assertIn('tg://photo?id=m1',rich['markdown'])
        self.assertLess(rich['markdown'].index('tg://video'),rich['markdown'].index('Между.'))
        self.assertNotIn('.assets/',rich['markdown'])
        with patch('tools.telegram.bot_call',side_effect=reply)as send:
            ready=self.service.prepare('a','article.granka.json','v2',text,rich_message=rich,rich_files=files)
            originals=[p.read_bytes()for p in files.values()]
            self.path(self.video).write_bytes(b'changed source')
            out=self.service.publish(ready['id']);self.assertEqual(out['status'],'sent')
            sent=next(c for c in send.call_args_list if c.args[1]=='sendRichMessage')
            self.assertEqual(sent.args[2]['rich_message'],rich)
            self.assertEqual([p.read_bytes()for p in sent.kwargs['files'].values()],originals)
        with self.assertRaises(ValueError):telegram_media.rich_input('Внутри ![x](article.assets/x.png) текста',lambda s:self.path(self.image))

    def test_unknown_album_does_not_repeat_and_partial_response_is_unknown(self):
        with patch('tools.telegram.bot_call',side_effect=reply):ready=self.service.prepare('a','article.granka.json','v1','Подпись',media=[self.path(self.video),self.path(self.image)])
        with patch('tools.telegram.bot_call',return_value=[{'message_id':71}])as send:
            self.assertEqual(self.service.publish(ready['id'])['status'],'unknown')
            self.assertEqual(Telegram(lambda:self.root/'state').publish(ready['id'])['status'],'unknown');self.assertEqual(send.call_count,1)

    def test_changed_snapshot_is_blocked_before_send(self):
        with patch('tools.telegram.bot_call',side_effect=reply):ready=self.service.prepare('a','article.granka.json','v1','Подпись',media=[self.path(self.video)])
        stored=self.service.read(ready['id']);Path(stored['media_paths']['m0']).write_bytes(b'corrupted')
        with patch('tools.telegram.bot_call')as send,self.assertRaises(ValueError):self.service.publish(ready['id'])
        send.assert_not_called();self.assertEqual(self.service.read(ready['id'])['status'],'prepared')

    def test_legacy_id_is_preserved_and_multipart_nested_fields_are_json(self):
        with patch('tools.telegram.bot_call',side_effect=reply):ready=self.service.prepare('a','article.granka.json','v1','Текст')
        identity=json.dumps(['a','article.granka.json','-100123456789','Текст',hashlib.sha256(b'').hexdigest()],ensure_ascii=False)
        self.assertEqual(ready['id'],hashlib.sha256(identity.encode()).hexdigest()[:32])
        result=io.BytesIO(b'{"ok":true,"result":{"message_id":71}}')
        payload={'chat_id':'-100123456789','rich_message':{'markdown':'Текст','media':[{'id':'m0','media':{'type':'video','media':'attach://m0'}}]}}
        with patch('urllib.request.urlopen',return_value=result)as send:
            bot_call(TOKEN,'sendRichMessage',payload,files={'m0':self.path(self.video)})
            raw=send.call_args.args[0].data;self.assertIn(json.dumps(payload['rich_message'],ensure_ascii=False).encode(),raw);self.assertIn(b'name="m0"',raw);self.assertIn(b'video/mp4',raw)

    def test_markdown_video_stays_video(self):
        html=render_markdown('До.\n\n![Полёт](article.assets/film.mp4)\n\nПосле.')
        self.assertIn('<video',html);self.assertIn('controls',html);self.assertNotIn('<img',html)


class VideoPublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=site_fixtures.SiteTests('test_bundle_preserves_markdown_and_copies_cover_and_reference_images');self.fixture.setUp()
    def tearDown(self):self.fixture.tearDown()
    def test_site_bundle_copies_video_and_preserves_markdown_destination(self):
        fixture=self.fixture;folder=fixture.source/'article.assets';folder.mkdir();video=folder/'film.mp4';video.write_bytes(b'movie')
        fixture.data['versions'][0]['text']='# Статья\n\n![Видео](article.assets/film.mp4)\n\nПосле.'
        plan=fixture.prepare();self.assertEqual(len(plan['assets']),1)
        bundle=fixture.service.path(plan['id'])/'bundle';content=(bundle/plan['file']).read_text(encoding='utf-8')
        self.assertIn('](../covers/',content);self.assertIn('.mp4)',content);self.assertEqual((bundle/plan['assets'][0]).read_bytes(),b'movie')
