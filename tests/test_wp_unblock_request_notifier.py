import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / 'wp-unblock-request-notifier.py'
SPEC = importlib.util.spec_from_file_location('unblock_request_notifier', SCRIPT)
notifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notifier)


UTC = dt.timezone.utc
ALIASES = {'отблокиране'}


def revision(revid, text, user='Blocked user', second=0, parentid=0, comment=''):
    return SimpleNamespace(
        revid=revid,
        text=text,
        user=user,
        timestamp=dt.datetime(2026, 10, 19, 12, 0, second, tzinfo=UTC),
        parentid=parentid,
        comment=comment,
    )


class FakeHistoryPage:
    def __init__(self, revisions):
        self._revisions = revisions

    def revisions(self, content=False):
        assert content
        yield from self._revisions


class ActiveRequestTests(unittest.TestCase):
    def test_template_is_active_only_without_resolution_or_demo(self):
        self.assertEqual(notifier.active_request_count('{{Отблокиране|защото}}', ALIASES), 1)
        self.assertEqual(notifier.active_request_count('{{Шаблон:Отблокиране|x|2=}}', ALIASES), 1)
        self.assertEqual(
            notifier.active_request_count('{{Отблокиране|x|2=<!-- later -->}}', ALIASES),
            1,
        )
        self.assertEqual(notifier.active_request_count('{{Отблокиране|x|2=отказано}}', ALIASES), 0)
        self.assertEqual(notifier.active_request_count('{{Отблокиране|demo=1}}', ALIASES), 0)

    def test_two_active_templates_are_not_a_valid_request(self):
        text = '{{Отблокиране|one}}\n{{Отблокиране|two}}'
        self.assertEqual(notifier.active_request_count(text, ALIASES), 2)
        page = FakeHistoryPage([revision(3, text), revision(2, '{{Отблокиране|one}}')])
        self.assertIsNone(notifier.find_introduction_revision(page, ALIASES))

    def test_reason_edits_keep_the_original_introduction_revision(self):
        page = FakeHistoryPage([
            revision(13, '{{Отблокиране|updated}}', second=3),
            revision(12, '{{Отблокиране|initial}}', second=2),
            revision(11, 'ordinary talk', second=1),
        ])
        found = notifier.find_introduction_revision(page, ALIASES)
        self.assertEqual(found.revid, 12)

    def test_resolved_request_followed_by_new_request_gets_new_revision(self):
        page = FakeHistoryPage([
            revision(15, '{{Отблокиране|new}}', second=5),
            revision(14, '{{Отблокиране|old|2=отказано}}', second=4),
            revision(13, '{{Отблокиране|old}}', second=3),
        ])
        found = notifier.find_introduction_revision(page, ALIASES)
        self.assertEqual(found.revid, 15)


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.request = notifier.Request(
            page_title='Потребител беседа:Blocked user',
            username='Blocked user',
            pageid=123,
            revision_id=456,
            timestamp=dt.datetime(2026, 10, 19, 14, 37, 22, tzinfo=UTC),
        )

    def test_request_id_heading_and_message_are_stable(self):
        self.assertEqual(self.request.request_id, '123:456')
        self.assertEqual(
            self.request.heading,
            'Заявка за отблокиране: Blocked user (2026-10-19 14:37:22 UTC)',
        )
        self.assertIn('[[Special:Diff/456|заявка за отблокиране]]', self.request.message)
        self.assertIn('{{потребител|Blocked user}}', self.request.message)
        self.assertNotIn('123:456', self.request.message)

    def test_anomaly_report_is_bounded(self):
        titles = ['Page {}'.format(index) for index in range(15)]
        message = notifier.anomaly_message(titles)
        self.assertEqual(message.count('* [['), notifier.MAX_ANOMALY_LINKS)
        self.assertIn('и още 5 страници', message)


class JournalTests(unittest.TestCase):
    def test_replay_accepts_valid_unterminated_last_line(self):
        event = {'v': 1, 'event': 'notified', 'request_id': '1:2'}
        state = notifier.replay_journal(json.dumps(event))
        self.assertIn('1:2', state.notified)

    def test_replay_ignores_only_invalid_unterminated_last_line(self):
        good = json.dumps({'v': 1, 'event': 'notified', 'request_id': '1:2'}) + '\n'
        state = notifier.replay_journal(good + '{"v": 1')
        self.assertIn('1:2', state.notified)

    def test_replay_rejects_corruption_before_last_line(self):
        data = '{bad}\n' + json.dumps({'v': 1, 'event': 'notified', 'request_id': '1:2'})
        with self.assertRaisesRegex(ValueError, 'line 1'):
            notifier.replay_journal(data)

    def test_replay_rejects_unknown_event_types(self):
        data = json.dumps({'v': 1, 'event': 'typo'})
        with self.assertRaisesRegex(ValueError, 'unknown state-journal event'):
            notifier.replay_journal(data)

    def test_append_is_durable_and_updates_replayed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.jsonl'
            with notifier.Journal(path, writable=True) as journal:
                journal.append({'event': 'notified', 'request_id': '7:8'})
                self.assertIn('7:8', journal.state.notified)
            state = notifier.replay_journal(path.read_text(encoding='utf-8'))
            self.assertIn('7:8', state.notified)

    def test_append_repairs_an_invalid_unterminated_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.jsonl'
            first = {'v': 1, 'event': 'notified', 'request_id': '1:2'}
            path.write_text(json.dumps(first) + '\n{"v": 1', encoding='utf-8')
            with notifier.Journal(path, writable=True) as journal:
                journal.append({'event': 'notified', 'request_id': '3:4'})
            state = notifier.replay_journal(path.read_text(encoding='utf-8'))
            self.assertEqual(set(state.notified), {'1:2', '3:4'})

    def test_append_terminates_a_valid_unterminated_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.jsonl'
            first = {'v': 1, 'event': 'notified', 'request_id': '1:2'}
            path.write_text(json.dumps(first), encoding='utf-8')
            with notifier.Journal(path, writable=True) as journal:
                journal.append({'event': 'notified', 'request_id': '3:4'})
            state = notifier.replay_journal(path.read_text(encoding='utf-8'))
            self.assertEqual(set(state.notified), {'1:2', '3:4'})


class AnomalyTests(unittest.TestCase):
    def test_anomaly_requires_minimum_age_and_repeats_daily(self):
        now = dt.datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
        state = notifier.JournalState(
            anomaly_first_seen={'Page': now - dt.timedelta(minutes=31)}
        )
        self.assertEqual(
            notifier.reportable_anomalies(
                state, ['Page'], now, dt.timedelta(minutes=30), dt.timedelta(hours=24)
            ),
            ['Page'],
        )
        state.anomaly_reported['Page'] = now - dt.timedelta(hours=23)
        self.assertEqual(
            notifier.reportable_anomalies(
                state, ['Page'], now, dt.timedelta(minutes=30), dt.timedelta(hours=24)
            ),
            [],
        )

    def test_cleared_anomaly_loses_its_first_seen_time(self):
        now = dt.datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            with notifier.Journal(Path(directory) / 'state.jsonl', writable=True) as journal:
                notifier.sync_anomalies(journal, ['One'], now)
                self.assertIn('One', journal.state.anomaly_first_seen)
                notifier.sync_anomalies(journal, [], now + dt.timedelta(minutes=1))
                self.assertNotIn('One', journal.state.anomaly_first_seen)

    def test_recovered_daily_report_is_not_posted_twice(self):
        now = dt.datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            with notifier.Journal(Path(directory) / 'state.jsonl', writable=True) as journal:
                journal.append(
                    {'event': 'anomaly_first_seen', 'page': 'Unexpected'},
                    now=now - dt.timedelta(minutes=31),
                )
                with mock.patch.object(
                        notifier, 'unexpected_inclusions', return_value=['Unexpected']), \
                        mock.patch.object(
                            notifier, 'find_section_addition', return_value=777
                        ), \
                        mock.patch.object(notifier, 'post_new_section') as post:
                    result = notifier.process_anomalies(
                        mock.sentinel.site,
                        journal,
                        'Потребител беседа:Iliev',
                        'Eliza Beth',
                        now,
                        30,
                    )
                self.assertEqual(result, 0)
                post.assert_not_called()
                self.assertEqual(journal.state.last_anomaly_report, now)

    def test_daily_limit_applies_to_the_whole_digest(self):
        now = dt.datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            with notifier.Journal(Path(directory) / 'state.jsonl', writable=True) as journal:
                journal.append({
                    'event': 'anomaly_reported',
                    'pages': ['Earlier'],
                    'notification_revid': 123,
                }, now=now - dt.timedelta(hours=1))
                journal.append(
                    {'event': 'anomaly_first_seen', 'page': 'New'},
                    now=now - dt.timedelta(minutes=31),
                )
                with mock.patch.object(
                        notifier, 'unexpected_inclusions', return_value=['New']), \
                        mock.patch.object(notifier, 'find_section_addition') as find, \
                        mock.patch.object(notifier, 'post_new_section') as post:
                    result = notifier.process_anomalies(
                        mock.sentinel.site,
                        journal,
                        'Потребител беседа:Iliev',
                        'Eliza Beth',
                        now,
                        30,
                    )
                self.assertEqual(result, 0)
                find.assert_not_called()
                post.assert_not_called()

    def test_started_digest_is_recovered_across_utc_midnight(self):
        started = dt.datetime(2026, 10, 19, 23, 59, tzinfo=UTC)
        now = dt.datetime(2026, 10, 20, 0, 1, tzinfo=UTC)
        old_heading = 'Неочаквани включвания на „Отблокиране“ (2026-10-19)'
        with tempfile.TemporaryDirectory() as directory:
            with notifier.Journal(Path(directory) / 'state.jsonl', writable=True) as journal:
                journal.append(
                    {'event': 'anomaly_first_seen', 'page': 'Unexpected'},
                    now=started - dt.timedelta(minutes=31),
                )
                journal.append({
                    'event': 'anomaly_report_started',
                    'heading': old_heading,
                    'pages': ['Unexpected'],
                }, now=started)
                with mock.patch.object(
                        notifier, 'unexpected_inclusions', return_value=['Unexpected']), \
                        mock.patch.object(
                            notifier, 'find_section_addition', return_value=888
                        ) as find, \
                        mock.patch.object(notifier, 'post_new_section') as post:
                    result = notifier.process_anomalies(
                        mock.sentinel.site,
                        journal,
                        'Потребител беседа:Iliev',
                        'Eliza Beth',
                        now,
                        30,
                    )
                self.assertEqual(result, 0)
                self.assertEqual(find.call_args.args[3], old_heading)
                post.assert_not_called()
                self.assertEqual(journal.state.last_anomaly_report, started)
                self.assertNotIn(old_heading, journal.state.anomaly_report_started)


class NotificationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.request = notifier.Request(
            page_title='Потребител беседа:Blocked user',
            username='Blocked user',
            pageid=123,
            revision_id=456,
            timestamp=dt.datetime(2026, 10, 19, 14, 37, 22, tzinfo=UTC),
        )

    def test_recovery_after_wiki_edit_does_not_post_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            with notifier.Journal(Path(directory) / 'state.jsonl', writable=True) as journal:
                journal.append({
                    'event': 'notify_started',
                    **notifier.request_event(self.request),
                })
                with mock.patch.object(notifier, 'find_existing_notification', return_value=999), \
                        mock.patch.object(notifier, 'post_new_section') as post:
                    result = notifier.notify_request(
                        mock.sentinel.site,
                        ALIASES,
                        journal,
                        'У:ЗА',
                        'Eliza Beth',
                        self.request,
                    )
                self.assertEqual(result, 'recovered')
                post.assert_not_called()
                self.assertEqual(
                    journal.state.notified[self.request.request_id]['notification_revid'],
                    999,
                )

    def test_recovery_query_is_server_filtered_and_follows_continuation(self):
        class Page:
            def title(self):
                return 'У:ЗА'

            def get_revision(self, revision_id, content=False):
                self.assert_content = content
                text = {
                    900: 'before',
                    901: self_request.message,
                }[revision_id]
                return SimpleNamespace(text=text)

        self_request = self.request
        responses = [
            {
                'continue': {'rvcontinue': 'token', 'continue': '||'},
                'query': {'pages': {'1': {'revisions': []}}},
            },
            {
                'query': {'pages': {'1': {'revisions': [{
                    'revid': 901,
                    'parentid': 900,
                    'comment': '/* {} */'.format(self.request.heading),
                }]}}},
            },
        ]
        submitted_parameters = []

        class Site:
            def simple_request(self, **parameters):
                submitted_parameters.append(parameters)
                return SimpleNamespace(submit=lambda: responses.pop(0))

        with mock.patch.object(notifier.pwb, 'Page', return_value=Page()):
            found = notifier.find_existing_notification(
                Site(), 'У:ЗА', 'Eliza Beth', self.request
            )

        self.assertEqual(found, 901)
        self.assertEqual(submitted_parameters[0]['rvuser'], 'Eliza Beth')
        self.assertEqual(submitted_parameters[0]['rvend'], '2026-10-19T14:37:22Z')
        self.assertEqual(submitted_parameters[1]['rvcontinue'], 'token')

    def test_inactive_request_is_rechecked_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            with notifier.Journal(Path(directory) / 'state.jsonl', writable=True) as journal:
                with mock.patch.object(notifier, 'find_existing_notification', return_value=None), \
                        mock.patch.object(notifier, 'same_request_is_active', return_value=False), \
                        mock.patch.object(notifier, 'post_new_section') as post:
                    result = notifier.notify_request(
                        mock.sentinel.site,
                        ALIASES,
                        journal,
                        'У:ЗА',
                        'Eliza Beth',
                        self.request,
                    )
                self.assertEqual(result, 'no-longer-active')
                post.assert_not_called()


class CommandLineTests(unittest.TestCase):
    def test_default_is_read_only(self):
        args = notifier.parse_args(['script'])
        self.assertFalse(args.write)

    def test_live_test_requires_explicit_write(self):
        with self.assertRaises(SystemExit):
            notifier.parse_args(['script', '--test-notification'])


if __name__ == '__main__':
    unittest.main()
