"""Schedules may only send a reviewed snapshot once; never call a real API."""
from datetime import datetime, timezone
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from tools.telegram import Telegram, TelegramRejected, TelegramUnknown
from tools.publication_scheduler import PublicationScheduler, scheduled_time, policy, zone
from tools.telegram_delivery import PublicationBusy

TOKEN = '123456789:' + 'b' * 35
NOW = datetime(2027, 1, 2, 10, tzinfo=timezone.utc).timestamp()


def fixture(token, method, payload, photo=None, files=None):
    if method == 'getMe':return {'id': 123}
    if method == 'getChat':return {'id': -100123456789, 'type': 'channel', 'title': 'Fixture', 'username': 'fixture_channel'}
    if method == 'getChatMember':return {'status': 'administrator', 'can_post_messages': True}
    return {'message_id': 42}


def child_tick(directory, now, entered, release):
    def sending(*args,**kwargs):
        entered.set();release.wait(12);return {'message_id':42}
    with patch('tools.telegram.bot_call',side_effect=sending):
        PublicationScheduler(Telegram(lambda:Path(directory)),lambda:now).tick()


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.telegram = Telegram(lambda: self.directory)
        self.telegram.save_settings({'chat_id': '@fixture_channel', 'token': TOKEN})
        self.mock = patch('tools.telegram.bot_call', side_effect=fixture).start()
        self.addCleanup(patch.stopall)
        self.now = NOW
        self.queue = PublicationScheduler(self.telegram, lambda: self.now)
        self.ready = self.telegram.prepare('articles', 'fixture.granka.json', 'v1', 'Reviewed text')
        self.mock.reset_mock()

    def values(self, **changes):
        return {'confirmed': True, 'operation_id': str(uuid.uuid4()),
                'when': '2027-01-02T11:00', 'timezone': 'UTC',
                'review_revision': self.ready['review_revision'], **changes}

    def schedule(self, **changes):
        return self.queue.schedule(self.ready['id'], self.values(**changes))

    def due(self):
        self.now = NOW + 3601; self.queue.tick()

    def test_saving_and_schedule_do_not_send(self):
        record = self.schedule(); self.assertEqual(record['schedule']['state'], 'scheduled')
        self.queue.tick(); self.mock.assert_not_called()
        self.assertNotIn(TOKEN, json.dumps(self.queue.read()))

    def test_send_once_and_restore_queue_without_retry(self):
        self.schedule(); self.due(); self.queue.tick()
        PublicationScheduler(Telegram(lambda: self.directory), lambda: self.now).tick()
        self.assertEqual(self.mock.call_count, 1)
        record = self.queue.read()['entries'][0]
        self.assertEqual(record['schedule']['state'], 'sent'); self.assertTrue(record['url'])

    def test_missed_time_is_not_sent_on_restart(self):
        self.schedule(); self.now += 7200
        PublicationScheduler(Telegram(lambda: self.directory), lambda: self.now).tick()
        self.mock.assert_not_called()
        self.assertEqual(self.queue.read()['entries'][0]['schedule']['state'], 'missed')

    def test_no_implicit_scheduling_of_prepared_posts(self):
        self.now += 7200; self.queue.tick()
        self.mock.assert_not_called(); self.assertEqual(self.queue.read()['entries'], [])

    def test_idempotent_unknown_response_to_scheduling(self):
        values = self.values(); first = self.queue.schedule(self.ready['id'], values)
        self.assertEqual(self.queue.schedule(self.ready['id'], values)['schedule'], first['schedule'])
        with self.assertRaises(ValueError):self.queue.schedule(self.ready['id'], {**values, 'when': '2027-01-03T11:00'})

    def test_stale_schedule_revision_cannot_replace_or_cancel(self):
        self.schedule()
        with self.assertRaises(ValueError):self.schedule()
        with self.assertRaises(ValueError):self.queue.cancel(self.ready['id'], 'stale')
        self.mock.assert_not_called()

    def test_cancel_before_due_and_resume_manual_publish(self):
        record = self.schedule()
        self.queue.cancel(record['id'], record['schedule']['revision'])
        self.due(); self.mock.assert_not_called()
        self.assertEqual(self.telegram.publish(record['id'])['status'], 'sent')

    def test_reschedule_changes_generation_and_old_wakeup_cannot_send(self):
        record = self.schedule()
        new = self.schedule(when='2027-01-03T11:00', expected_schedule_revision=record['schedule']['revision'])
        self.now += 3601
        self.telegram.publish(record['id'], schedule_id=record['schedule']['id'], now=self.now)
        self.mock.assert_not_called(); self.assertNotEqual(record['schedule']['id'], new['schedule']['id'])

    def test_manual_publish_cannot_bypass_queue(self):
        self.schedule()
        with self.assertRaises(ValueError):self.telegram.publish(self.ready['id'])
        self.mock.assert_not_called()

    def test_changing_source_or_old_preparation_does_not_replace_snapshot(self):
        self.schedule()
        with self.assertRaises(ValueError):self.telegram.prepare('articles', 'fixture.granka.json', 'v1', 'Reviewed text')
        self.due(); self.assertEqual(self.mock.call_args.args[2]['text'], 'Reviewed text')

    def test_uncertain_delivery_never_retries(self):
        self.schedule(); self.mock.side_effect = TelegramUnknown('Lost response')
        self.due(); self.queue.tick()
        self.assertEqual(self.mock.call_count, 1)
        self.assertEqual(self.queue.read()['entries'][0]['status'], 'unknown')

    def test_explicit_rejection_is_paused_not_retried(self):
        self.schedule(); self.mock.side_effect = TelegramRejected('Denied')
        self.due(); self.queue.tick()
        self.assertEqual(self.mock.call_count, 1)
        self.assertEqual(self.queue.read()['entries'][0]['schedule']['state'], 'failed')

    def test_crash_after_sending_marker_is_unknown(self):
        record = self.schedule(); private = self.telegram.read(record['id'])
        private.update(status='sending'); private['schedule']['state'] = 'sending'
        self.telegram.persist(self.telegram.record_path(record['id']), private)
        self.queue.tick(); self.mock.assert_not_called()
        self.assertEqual(self.queue.read()['entries'][0]['status'], 'unknown')

    def test_changed_configuration_and_manual_mode_pause_without_network(self):
        for changes in ({'publication_mode': 'manual'}, {'chat_id': '@different_channel'}):
            with self.subTest(changes=changes):
                self.telegram.save_settings({'chat_id': '@fixture_channel', 'publication_mode': 'channel'})
                self.now = NOW
                self.ready = self.telegram.prepare('articles', str(uuid.uuid4()) + '.granka.json', 'v1', 'Reviewed')
                self.schedule(); self.telegram.save_settings(changes); self.mock.reset_mock()
                self.due(); self.queue.tick(); self.mock.assert_not_called()
                self.assertEqual(self.telegram.read(self.ready['id'])['schedule']['state'], 'error')

    def test_modified_text_or_missing_media_cannot_be_sent(self):
        record = self.schedule(); private = self.telegram.read(record['id'])
        private['text'] = 'Changed after confirmation'
        self.telegram.persist(self.telegram.record_path(record['id']), private)
        self.due(); self.mock.assert_not_called()
        self.assertEqual(self.telegram.read(record['id'])['schedule']['state'], 'error')

    def test_confirmation_snapshot_and_future_date_required(self):
        for changes in ({'confirmed': False}, {'review_revision': 'stale'},
                        {'when': '2020-01-01T11:00'}, {'when': '2030-01-01T11:00'},
                        {'timezone': 'not/a-zone'}, {'operation_id': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):self.schedule(**changes)
        self.mock.assert_not_called()

    def test_allowed_days_hours_and_fixed_timezone(self):
        self.assertEqual(scheduled_time('2027-01-02T14:00', 'UTC+03:00'), NOW + 3600)
        for changes in ({'windows': {'days': [0]}}, {'windows': {'start': '12:00'}},
                        {'windows': {'days': [True]}}, {'windows': {'start': '25:00'}},
                        {'when': '2027-02-30T12:00'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):self.schedule(**changes)

    def test_timezone_offset_validation(self):
        for name in ('UTC+15:00', 'UTC+14:01', 'UTC+03:99', [], '../UTC'):
            with self.subTest(name=name), self.assertRaises(ValueError):zone(name)

    def test_clock_change_is_rejected_when_named_zone_database_is_available(self):
        try:zone('America/New_York')
        except ValueError:
            # A clean portable runtime may not have IANA data: fixed offsets
            # remain available and the named zone must fail explicitly.
            self.assertEqual(scheduled_time('2027-01-02T14:00','UTC-05:00'),NOW+9*3600)
            return
        for when in ('2026-11-01T01:30','2027-03-14T02:30'):
            with self.subTest(when=when),self.assertRaisesRegex(ValueError,'перевод часов'):
                scheduled_time(when,'America/New_York')

    def test_failed_queue_write_has_no_external_effect(self):
        with patch.object(self.telegram, 'persist', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.schedule()
        self.mock.assert_not_called(); self.assertEqual(self.queue.read()['entries'], [])

    def test_legacy_cover_is_frozen_and_modified_snapshot_is_not_sent(self):
        from PIL import Image
        photo=self.directory/'cover.png';Image.new('RGB',(4,4),'red').save(photo)
        self.ready=self.telegram.prepare('articles','photo.granka.json','v1','Photo',photo=photo)
        private=self.telegram.read(self.ready['id']);private.pop('photo_sha256')
        self.telegram.persist(self.telegram.record_path(private['id']),private)
        self.ready=self.telegram.public(private);record=self.schedule()
        self.assertTrue(self.telegram.read(record['id'])['photo_sha256'])
        Path(private['photo_path']).write_bytes(b'changed');self.mock.reset_mock()
        self.due();self.mock.assert_not_called()
        self.assertEqual(self.telegram.read(record['id'])['schedule']['state'],'error')

    def process_case(self, crash=False):
        record=self.schedule();self.now=NOW+3601
        ctx=mp.get_context('spawn');entered=ctx.Event();release=ctx.Event()
        process=ctx.Process(target=child_tick,args=(str(self.directory),self.now,entered,release));process.start()
        try:
            self.assertTrue(entered.wait(10),'worker did not enter the fake API')
            self.queue.tick();self.mock.assert_not_called()
            with self.assertRaises(PublicationBusy):self.queue.cancel(record['id'],record['schedule']['revision'])
            if crash:
                process.terminate();process.join(5);self.queue.tick()
                self.assertEqual(self.telegram.read(record['id'])['status'],'unknown')
                self.queue.tick();self.mock.assert_not_called()
            else:
                release.set();process.join(10);self.queue.tick()
                self.assertEqual(self.telegram.read(record['id'])['status'],'sent');self.mock.assert_not_called()
        finally:
            if process.is_alive():process.terminate()
            process.join(3)

    def test_two_processes_cannot_send_twice_and_cancel_cannot_claim_to_stop_active_send(self):self.process_case()
    def test_killed_worker_recovers_as_unknown_without_a_second_request(self):self.process_case(crash=True)


if __name__ == '__main__':unittest.main()
