import copy
import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
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
NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
ALIASES = {'отблокиране'}


def member(pageid=123, title='Потребител беседа:Blocked user', second=0):
    return notifier.CategoryMember(
        pageid, title, 3, NOW + dt.timedelta(seconds=second), ''
    )


def inclusion(pageid=123, title='Потребител беседа:Blocked user', revid=20, ns=3):
    return notifier.Inclusion(pageid, title, ns, revid, False)


def revision(revid, text, user='Blocked user', userid=77, parentid=1, second=0):
    return {
        'revid': revid,
        'parentid': parentid,
        'timestamp': notifier.serialize_timestamp(NOW + dt.timedelta(seconds=second)),
        'user': user,
        'userid': userid,
        'slots': {'main': {'content': text}},
    }


def args(write=False, test=False, state_file=None):
    return SimpleNamespace(
        write=write,
        expected_user='Eliza Beth' if not test else 'Kerberiel',
        notification_page='У:ЗА' if not test else 'Потребител беседа:Kerberiel/test',
        anomaly_report_page='Потребител беседа:Iliev' if not test else None,
        anomaly_min_age_minutes=30,
        test_unblocked_requests=test,
        state_file=state_file,
    )


def scope():
    return notifier.state_scope(args())


def ready_record(status='ready', lastrevid=20):
    return {
        'kind': 'category',
        'title': 'Потребител беседа:Blocked user',
        'namespace': 3,
        'category_timestamp': notifier.serialize_timestamp(NOW),
        'sortkeyprefix': '',
        'lastrevid': lastrevid,
        'included': True,
        'first_seen': notifier.serialize_timestamp(NOW),
        'reported': False,
        'username': 'Blocked user',
        'owner_userid': 77,
        'intro_revid': 10,
        'intro_timestamp': notifier.serialize_timestamp(NOW),
        'intro_user': 'Blocked user',
        'status': status,
        'reason': None,
    }


class TemplateSemanticsTests(unittest.TestCase):
    def test_active_request_matches_the_live_bulgarian_parameters(self):
        active = [
            '{{Отблокиране|защото}}',
            '{{Отблокиране|x|2=<!-- later -->}}',
            '{{Отблокиране|x|демо=да}}',
            '{{Отблокиране|x|demo=1}}',
        ]
        inactive = [
            '{{Отблокиране|x|2=отказано}}',
            '{{Отблокиране|x|демо=1}}',
            '{{Отблокиране|x|демо=01}}',
            '{{Отблокиране|x|демо=1.0}}',
            '{{Отблокиране|x|демо=+1}}',
        ]
        for text in active:
            with self.subTest(text=text):
                self.assertEqual(notifier.active_request_count(text, ALIASES), 1)
        for text in inactive:
            with self.subTest(text=text):
                self.assertEqual(notifier.active_request_count(text, ALIASES), 0)

    def test_demo_numeric_comparison_uses_php_style_ascii_strings(self):
        self.assertTrue(notifier.mediawiki_numeric_one('&#49;'))
        self.assertTrue(notifier.mediawiki_numeric_one(' 1e0\n'))
        self.assertFalse(notifier.mediawiki_numeric_one('１'))
        self.assertFalse(notifier.mediawiki_numeric_one('١'))
        self.assertFalse(notifier.mediawiki_numeric_one('1_'))

    def test_nested_demo_value_is_expanded_by_mediawiki(self):
        expand = mock.Mock(return_value='1')
        text = '{{Отблокиране|демо={{#expr: 1}}}}'
        self.assertEqual(notifier.active_request_count(text, ALIASES, expand), 0)
        expand.assert_called_once_with('{{#expr: 1}}')

    def test_more_than_one_active_template_is_still_one_page_episode(self):
        text = '{{Отблокиране|one}}\n{{Отблокиране|two}}'
        self.assertEqual(notifier.active_request_count(text, ALIASES), 2)

    def test_template_name_normalization_matches_mediawiki_semantics(self):
        active = [
            '{{Шаблон : Отблокиране}}',
            '{{:Шаблон:Отблокиране}}',
            '{{Отблокиране<!-- comment -->}}',
        ]
        for text in active:
            with self.subTest(text=text):
                self.assertEqual(notifier.active_request_count(text, ALIASES), 1)
        self.assertEqual(notifier.active_request_count('{{:Отблокиране}}', ALIASES), 0)

    def test_message_uses_explicit_parameter_and_escapes_tildes(self):
        request = notifier.Request('Talk', 'User~=name', 1, 456, NOW)
        self.assertIn('{{потребител|1=User&#126;=name}}', request.message)
        self.assertIn('[[Special:Diff/456|', request.message)
        self.assertNotIn('1:456', request.message)


class DiscoveryTests(unittest.TestCase):
    def test_category_members_uses_all_namespaces_and_continuation(self):
        responses = [
            {
                'continue': {'cmcontinue': 'next', 'continue': '-||'},
                'query': {'categorymembers': [{
                    'pageid': 1, 'ns': 0, 'title': 'Article',
                    'timestamp': '2026-10-20T12:00:00Z', 'sortkeyprefix': 'A',
                }]},
            },
            {'query': {'categorymembers': [{
                'pageid': 2, 'ns': 3, 'title': 'Потребител беседа:User',
                'timestamp': '2026-10-20T12:01:00Z', 'sortkeyprefix': '',
            }]}},
        ]
        site = mock.Mock()
        site.simple_request.side_effect = [
            SimpleNamespace(submit=lambda response=response: response) for response in responses
        ]
        result = notifier.category_members(site)
        self.assertEqual(set(result), {1, 2})
        first = site.simple_request.call_args_list[0].kwargs
        second = site.simple_request.call_args_list[1].kwargs
        self.assertNotIn('cmnamespace', first)
        self.assertEqual(second['cmcontinue'], 'next')

    def test_inclusions_merge_canonical_template_and_redirects(self):
        responses = [
            {'query': {'pages': {'1': {
                'pageid': 1, 'ns': 3, 'title': 'Talk:A', 'lastrevid': 10,
            }}}},
            {'query': {'pages': {'2': {
                'pageid': 2, 'ns': 1, 'title': 'Talk:B', 'lastrevid': 11,
            }}}},
        ]
        with mock.patch.object(notifier, 'submit_query', side_effect=responses) as submit:
            result = notifier.template_inclusions(
                mock.sentinel.site, ['Шаблон:Отблокиране', 'Шаблон:Unblock']
            )
        self.assertEqual(set(result), {1, 2})
        self.assertEqual(submit.call_count, 2)


class HistoryClassificationTests(unittest.TestCase):
    def test_history_starts_near_category_time_and_pages_two_then_five(self):
        responses = [
            {
                'continue': {'rvcontinue': 'older', 'continue': '||'},
                'query': {'pages': {'123': {'revisions': [
                    revision(12, '{{Отблокиране|updated}}', second=2),
                    revision(11, '{{Отблокиране|initial}}', second=1),
                ]}}},
            },
            {'query': {'pages': {'123': {'revisions': [revision(10, 'ordinary')]}}}},
        ]
        with mock.patch.object(notifier, 'submit_query', side_effect=responses) as submit:
            found = notifier.find_introduction_revision(
                mock.sentinel.site, member(), ALIASES
            )
        self.assertEqual(found['revid'], 11)
        first = submit.call_args_list[0].args[1]
        second = submit.call_args_list[1].args[1]
        self.assertEqual(first['rvlimit'], 2)
        self.assertEqual(
            first['rvstart'], notifier.serialize_timestamp(NOW + dt.timedelta(seconds=60))
        )
        self.assertEqual(second['rvlimit'], 5)
        self.assertEqual(second['rvcontinue'], 'older')

    def test_first_page_revision_can_be_the_introduction(self):
        response = {
            'query': {'pages': {'123': {'revisions': [
                revision(1, '{{Отблокиране}}', parentid=0)
            ]}}}
        }
        with mock.patch.object(notifier, 'submit_query', return_value=response):
            self.assertEqual(
                notifier.find_introduction_revision(mock.sentinel.site, member(), ALIASES)[
                    'revid'
                ],
                1,
            )

    def test_hidden_content_is_a_permanent_anomaly(self):
        response = {'query': {'pages': {'123': {'revisions': [{
            'revid': 2, 'parentid': 1, 'timestamp': '2026-10-20T12:00:00Z',
            'user': 'Blocked user', 'userid': 77, 'slots': {'main': {'contenthidden': True}},
        }]}}}}
        with mock.patch.object(notifier, 'submit_query', return_value=response):
            with self.assertRaises(notifier.PermanentClassificationError):
                notifier.find_introduction_revision(mock.sentinel.site, member(), ALIASES)

    def test_window_that_starts_inactive_is_retried(self):
        response = {
            'query': {'pages': {'123': {'revisions': [revision(10, 'ordinary')]}}}
        }
        with mock.patch.object(notifier, 'submit_query', return_value=response):
            with self.assertRaises(notifier.RetryClassification):
                notifier.find_introduction_revision(mock.sentinel.site, member(), ALIASES)

    def test_late_template_on_manual_category_retries_from_current_revision(self):
        current = revision(30, '{{Отблокиране}}', second=3600)
        intro = revision(29, '{{Отблокиране}}', second=3599)
        with (
            mock.patch.object(notifier, 'fetch_page_revision', return_value=current),
            mock.patch.object(notifier, 'is_own_base_talk_page', return_value=True),
            mock.patch.object(
                notifier,
                'find_introduction_revision',
                side_effect=[notifier.RetryClassification('old category'), intro],
            ) as find,
            mock.patch.object(notifier, 'account_info', return_value=(77, True)),
        ):
            record = notifier.classify_category_page(
                mock.sentinel.site, member(), inclusion(revid=30), ALIASES, True, NOW
            )
        self.assertEqual(record['intro_revid'], 29)
        self.assertIsNone(find.call_args_list[0].kwargs.get('start'))
        self.assertEqual(find.call_args_list[1].kwargs['start'], NOW + dt.timedelta(hours=1))

    def test_recovery_ignores_later_edit_and_finds_actual_section_addition(self):
        request = notifier.Request('Talk', 'User', 1, 456, NOW)
        revisions = [
            {'revid': 902, 'parentid': 901,
             'comment': 'Заявка за отблокиране: later bot edit'},
            {'revid': 901, 'parentid': 900,
             'comment': 'Заявка за отблокиране: new request'},
        ]
        with mock.patch.object(
                notifier, 'revisions_by_user_since', return_value=revisions), \
                mock.patch.object(
                    notifier, 'revision_content',
                    side_effect=[request.message, request.message,
                                 request.message, 'before'],
                ):
            self.assertEqual(
                notifier.find_existing_notification(
                    mock.sentinel.site, 'У:ЗА', 'Eliza Beth', request
                ),
                901,
            )

    def test_recovery_fails_closed_when_candidate_content_is_hidden(self):
        request = notifier.Request('Talk', 'User', 1, 456, NOW)
        revisions = [{
            'revid': 901, 'parentid': 900,
            'comment': 'Заявка за отблокиране: new request',
        }]
        with mock.patch.object(
                notifier, 'revisions_by_user_since', return_value=revisions), \
                mock.patch.object(
                    notifier, 'revision_content',
                    side_effect=notifier.PermanentClassificationError('hidden'),
                ):
            with self.assertRaises(notifier.PermanentClassificationError):
                notifier.find_existing_notification(
                    mock.sentinel.site, 'У:ЗА', 'Eliza Beth', request
                )


class CategoryEligibilityTests(unittest.TestCase):
    def test_mismatched_discovery_titles_are_retried_before_content_is_read(self):
        moved = inclusion(title='Потребител беседа:Moved user')
        with mock.patch.object(notifier, 'fetch_page_revision') as fetch:
            with self.assertRaisesRegex(notifier.RetryClassification, 'identity changed'):
                notifier.classify_category_page(
                    mock.sentinel.site, member(), moved, ALIASES, True, NOW
                )
        fetch.assert_not_called()

    def test_ip_talk_page_is_an_anomaly_without_history_or_account_lookup(self):
        ip_member = member(title='Потребител беседа:192.0.2.1')
        ip_inclusion = inclusion(title=ip_member.title)
        with (
            mock.patch.object(
                notifier, 'fetch_page_revision',
                return_value=revision(20, '{{Отблокиране}}'),
            ),
            mock.patch.object(notifier, 'find_introduction_revision') as history,
            mock.patch.object(notifier, 'account_info') as account,
        ):
            record = notifier.classify_category_page(
                mock.sentinel.site, ip_member, ip_inclusion, ALIASES, True, NOW
            )
        self.assertEqual(record['status'], 'anomaly')
        self.assertIn('IP', record['reason'])
        history.assert_not_called()
        account.assert_not_called()

    def test_user_talk_subpage_is_rejected_before_history_or_account_lookup(self):
        subpage = member(title='Потребител беседа:Blocked user/request')
        subpage_inclusion = inclusion(title=subpage.title)
        with (
            mock.patch.object(notifier, 'fetch_page_revision') as fetch,
            mock.patch.object(notifier, 'find_introduction_revision') as history,
            mock.patch.object(notifier, 'account_info') as account,
        ):
            record = notifier.classify_category_page(
                mock.sentinel.site, subpage, subpage_inclusion, ALIASES, True, NOW
            )
        self.assertEqual(record['status'], 'anomaly')
        self.assertIn('base user-talk page', record['reason'])
        fetch.assert_not_called()
        history.assert_not_called()
        account.assert_not_called()

    def test_owner_is_compared_by_user_id_and_partial_block_counts(self):
        intro = revision(10, '{{Отблокиране}}', user='Canonical user', userid=77)
        with (
            mock.patch.object(
                notifier, 'fetch_page_revision',
                return_value=revision(20, '{{Отблокиране}}'),
            ),
            mock.patch.object(notifier, 'is_own_base_talk_page', return_value=True),
            mock.patch.object(notifier, 'find_introduction_revision', return_value=intro),
            mock.patch.object(notifier, 'account_info', return_value=(77, True)),
        ):
            record = notifier.classify_category_page(
                mock.sentinel.site, member(), inclusion(), ALIASES, True, NOW
            )
        self.assertEqual(record['status'], 'ready')

    def test_wrong_author_is_an_anomaly(self):
        intro = revision(10, '{{Отблокиране}}', user='Other', userid=88)
        with (
            mock.patch.object(
                notifier, 'fetch_page_revision',
                return_value=revision(20, '{{Отблокиране}}'),
            ),
            mock.patch.object(notifier, 'is_own_base_talk_page', return_value=True),
            mock.patch.object(notifier, 'find_introduction_revision', return_value=intro),
            mock.patch.object(notifier, 'account_info', return_value=(77, True)),
        ):
            record = notifier.classify_category_page(
                mock.sentinel.site, member(), inclusion(), ALIASES, True, NOW
            )
        self.assertEqual(record['status'], 'anomaly')
        self.assertIn('another account', record['reason'])


class SnapshotTests(unittest.TestCase):
    def test_missing_snapshot_is_read_without_creating_any_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = notifier.load_snapshot(path, scope())
            self.assertEqual(state['pages'], {})
            self.assertFalse(path.exists())

    def test_store_writes_atomic_snapshot_and_reloads_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with notifier.StateStore(path, scope()) as store:
                store.state['pages']['1'] = {
                    'kind': 'stray', 'title': 'Template:X', 'namespace': 10,
                    'category_timestamp': None, 'lastrevid': 5, 'included': True,
                    'status': 'inactive', 'reason': None,
                }
                self.assertTrue(store.save())
                self.assertFalse(store.save())
            self.assertEqual(notifier.load_snapshot(path, scope())['pages']['1']['status'],
                             'inactive')
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_store_round_trips_category_and_unclassified_stray_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = notifier.empty_snapshot(scope())
            state['pages']['123'] = ready_record()
            state['pages']['8'] = {
                'kind': 'stray', 'title': 'Файл:Unexpected.svg', 'namespace': 6,
                'category_timestamp': None, 'lastrevid': 5, 'included': True,
                'status': 'unclassified', 'reason': 'temporary failure',
                'first_seen': notifier.serialize_timestamp(NOW), 'reported': False,
            }
            with notifier.StateStore(path, scope()) as store:
                store.state = state
                self.assertTrue(store.save())
            self.assertEqual(notifier.load_snapshot(path, scope()), state)

    def test_corruption_and_scope_mismatch_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text('{bad', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'valid JSON'):
                notifier.load_snapshot(path, scope())
            path.write_text(json.dumps(notifier.empty_snapshot({'different': True})),
                            encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'scope'):
                notifier.load_snapshot(path, scope())

    def test_valid_json_with_an_incomplete_notified_record_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = notifier.empty_snapshot(scope())
            state['pages']['1'] = {
                'kind': 'category', 'title': 'Потребител беседа:User',
                'namespace': 3, 'category_timestamp': notifier.serialize_timestamp(NOW),
                'sortkeyprefix': '', 'lastrevid': 5, 'included': True,
                'status': 'notified',
            }
            path.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'missing'):
                notifier.load_snapshot(path, scope())

    def test_valid_json_with_an_incomplete_ready_record_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = notifier.empty_snapshot(scope())
            state['pages']['1'] = {
                'kind': 'category', 'title': 'Потребител беседа:User',
                'namespace': 3, 'category_timestamp': notifier.serialize_timestamp(NOW),
                'sortkeyprefix': '', 'lastrevid': 5, 'included': True,
                'status': 'ready',
            }
            path.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'missing'):
                notifier.load_snapshot(path, scope())

    def test_valid_json_with_an_incomplete_inactive_stray_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = notifier.empty_snapshot(scope())
            state['pages']['1'] = {
                'kind': 'stray', 'title': 'Шаблон:X', 'namespace': 10,
                'lastrevid': 5, 'included': True, 'status': 'inactive',
            }
            path.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'missing'):
                notifier.load_snapshot(path, scope())

    def test_lock_contention_is_cleanly_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with notifier.StateStore(path, scope()):
                with self.assertRaises(notifier.LockUnavailable):
                    with notifier.StateStore(path, scope()):
                        pass


class ReconciliationTests(unittest.TestCase):
    def test_unchanged_notified_page_reads_no_history_text_or_block(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = ready_record(status='notified')
        with mock.patch.object(notifier, 'classify_category_page') as classify, \
                mock.patch.object(notifier, 'fetch_page_text') as text, \
                mock.patch.object(notifier, 'account_info') as account, \
                mock.patch.object(notifier, 'notify_record') as notify:
            failures = notifier.reconcile_world(
                mock.sentinel.site, state, {123: member()}, {123: inclusion()},
                ALIASES, True, NOW, args()
            )
        self.assertEqual(failures, [])
        classify.assert_not_called()
        text.assert_not_called()
        account.assert_not_called()
        notify.assert_not_called()

    def test_ordinary_edit_of_notified_page_does_not_reclassify(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = ready_record(status='notified', lastrevid=20)
        with mock.patch.object(notifier, 'classify_category_page') as classify:
            notifier.reconcile_world(
                mock.sentinel.site, state, {123: member()},
                {123: inclusion(revid=21)}, ALIASES, True, NOW, args()
            )
        classify.assert_not_called()

    def test_changed_category_timestamp_reclassifies_but_same_intro_stays_notified(self):
        state = notifier.empty_snapshot(scope())
        old = ready_record(status='notified')
        old['notification_revid'] = 999
        state['pages']['123'] = old
        with mock.patch.object(
                notifier, 'classify_category_page', return_value=ready_record()), \
                mock.patch.object(notifier, 'notify_record') as notify:
            notifier.reconcile_world(
                mock.sentinel.site, state, {123: member(second=1)},
                {123: inclusion()}, ALIASES, True, NOW, args()
            )
        self.assertEqual(state['pages']['123']['status'], 'notified')
        self.assertEqual(state['pages']['123']['notification_revid'], 999)
        notify.assert_not_called()

    def test_new_category_episode_with_different_intro_is_not_suppressed(self):
        state = notifier.empty_snapshot(scope())
        old = ready_record(status='notified')
        old['notification_revid'] = 999
        state['pages']['123'] = old
        new = ready_record()
        new['category_timestamp'] = notifier.serialize_timestamp(
            NOW + dt.timedelta(seconds=1)
        )
        new['intro_revid'] = 11
        with mock.patch.object(
                notifier, 'classify_category_page', return_value=new), \
                mock.patch.object(
                    notifier, 'notify_record', return_value='would-notify'
                ) as notify:
            notifier.reconcile_world(
                mock.sentinel.site, state, {123: member(second=1)},
                {123: inclusion()}, ALIASES, True, NOW, args()
            )
        self.assertEqual(state['pages']['123']['status'], 'ready')
        self.assertNotIn('notification_revid', state['pages']['123'])
        notify.assert_called_once()

    def test_category_only_anomaly_is_reclassified_when_template_appears(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = {
            'kind': 'category', 'title': member().title, 'namespace': 3,
            'category_timestamp': notifier.serialize_timestamp(NOW),
            'sortkeyprefix': '', 'lastrevid': None, 'included': False,
            'status': 'anomaly', 'reason': 'no transclusion',
            'first_seen': notifier.serialize_timestamp(NOW), 'reported': False,
        }
        with mock.patch.object(
                notifier, 'classify_category_page', return_value=ready_record()) as classify, \
                mock.patch.object(notifier, 'notify_record', return_value='would-notify'):
            notifier.reconcile_world(
                mock.sentinel.site, state, {123: member()}, {123: inclusion()},
                ALIASES, True, NOW, args()
            )
        classify.assert_called_once()
        self.assertEqual(state['pages']['123']['status'], 'ready')

    def test_waiting_page_only_rechecks_block_until_it_becomes_ready(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = ready_record(status='waiting')
        with mock.patch.object(notifier, 'account_info', return_value=(77, True)) as account, \
                mock.patch.object(notifier, 'classify_category_page') as classify, \
                mock.patch.object(notifier, 'notify_record', return_value='would-notify') as notify:
            notifier.reconcile_world(
                mock.sentinel.site, state, {123: member()}, {123: inclusion()},
                ALIASES, True, NOW, args()
            )
        account.assert_called_once()
        classify.assert_not_called()
        notify.assert_called_once()

    def test_persisted_ready_page_is_not_posted_after_unblock(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = ready_record()
        with mock.patch.object(
                notifier, 'find_existing_notification', return_value=None), \
                mock.patch.object(notifier, 'account_info', return_value=(77, False)), \
                mock.patch.object(notifier, 'request_is_current') as current, \
                mock.patch.object(notifier, 'post_new_section') as post:
            notifier.reconcile_world(
                mock.sentinel.site, state, {123: member()}, {123: inclusion()},
                ALIASES, True, NOW, args(write=True)
            )
        self.assertEqual(state['pages']['123']['status'], 'waiting')
        current.assert_not_called()
        post.assert_not_called()

    def test_crash_recovery_precedes_changed_block_eligibility(self):
        record = ready_record()
        with mock.patch.object(
                notifier, 'find_existing_notification', return_value=999), \
                mock.patch.object(notifier, 'account_info') as account:
            result = notifier.notify_record(
                mock.sentinel.site, record, '123', 'У:ЗА', 'Eliza Beth',
                True, True, eligibility_checked=False,
            )
        self.assertEqual(result, 'recovered')
        self.assertEqual(record['status'], 'notified')
        account.assert_not_called()

    def test_ambiguous_first_post_stops_before_a_second_ready_page(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = ready_record()
        second = ready_record()
        second['title'] = 'Потребител беседа:Second'
        second['username'] = 'Second'
        second['owner_userid'] = 88
        state['pages']['124'] = second
        members = {123: member(), 124: member(124, second['title'])}
        inclusions = {123: inclusion(), 124: inclusion(124, second['title'])}
        with mock.patch.object(
                notifier, 'account_info', side_effect=[(77, True), (88, True)]), \
                mock.patch.object(
                    notifier, 'notify_record', side_effect=notifier.AmbiguousWriteError('maybe')
                ) as notify:
            with self.assertRaises(notifier.AmbiguousWriteError):
                notifier.reconcile_world(
                    mock.sentinel.site, state, members, inclusions,
                    ALIASES, True, NOW, args(write=True)
                )
        self.assertEqual(notify.call_count, 1)

    def test_changed_stray_failure_starts_a_new_observation_window(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['8'] = {
            'kind': 'stray', 'title': 'User talk:X/sub', 'namespace': 3,
            'category_timestamp': None, 'lastrevid': 5, 'included': True,
            'status': 'anomaly', 'reason': 'stray',
            'first_seen': notifier.serialize_timestamp(NOW - dt.timedelta(hours=1)),
            'reported': False,
        }
        with mock.patch.object(
                notifier, 'make_stray_record', side_effect=TimeoutError('temporary')):
            failures = notifier.reconcile_world(
                mock.sentinel.site, state, {},
                {8: inclusion(8, 'User talk:X/sub', 6)}, ALIASES, True, NOW, args()
            )
        self.assertEqual(len(failures), 1)
        record = state['pages']['8']
        self.assertEqual(record['status'], 'unclassified')
        self.assertEqual(record['lastrevid'], 6)
        self.assertEqual(record['first_seen'], notifier.serialize_timestamp(NOW))
        self.assertFalse(record['reported'])
        self.assertEqual(notifier.report_anomalies_dry_run(state, args(), NOW), [])
        with mock.patch.object(
                notifier, 'make_stray_record', side_effect=TimeoutError('temporary')):
            notifier.reconcile_world(
                mock.sentinel.site, state, {},
                {8: inclusion(8, 'User talk:X/sub', 6)}, ALIASES, True,
                NOW + dt.timedelta(minutes=31), args()
            )
        self.assertEqual(
            notifier.report_anomalies_dry_run(
                state, args(), NOW + dt.timedelta(minutes=31)
            ),
            [state['pages']['8']],
        )

    def test_category_failure_does_not_inherit_an_old_episode_age(self):
        state = notifier.empty_snapshot(scope())
        old = ready_record(status='anomaly')
        old['reason'] = 'old anomaly'
        old['first_seen'] = notifier.serialize_timestamp(NOW - dt.timedelta(hours=1))
        state['pages']['123'] = old
        with mock.patch.object(
                notifier, 'classify_category_page', side_effect=TimeoutError('temporary')):
            notifier.reconcile_world(
                mock.sentinel.site, state, {123: member(second=1)},
                {123: inclusion()}, ALIASES, True, NOW, args()
            )
        record = state['pages']['123']
        self.assertEqual(record['status'], 'unclassified')
        self.assertEqual(record['first_seen'], notifier.serialize_timestamp(NOW))
        self.assertEqual(notifier.report_anomalies_dry_run(state, args(), NOW), [])

    def test_known_category_failures_reset_a_new_episode(self):
        cases = [
            (notifier.RetryClassification('retry'), 'unclassified'),
            (notifier.PermanentClassificationError('permanent'), 'anomaly'),
        ]
        for error, expected_status in cases:
            with self.subTest(error=type(error).__name__):
                state = notifier.empty_snapshot(scope())
                old = ready_record(status='anomaly')
                old['reason'] = 'old anomaly'
                old['first_seen'] = notifier.serialize_timestamp(
                    NOW - dt.timedelta(hours=1)
                )
                old['reported'] = True
                for field in ('username', 'owner_userid', 'intro_revid',
                              'intro_timestamp', 'intro_user'):
                    old.pop(field, None)
                state['pages']['123'] = old
                with mock.patch.object(
                        notifier, 'classify_category_page', side_effect=error):
                    notifier.reconcile_world(
                        mock.sentinel.site, state, {123: member(second=1)},
                        {123: inclusion()}, ALIASES, True, NOW, args()
                    )
                record = state['pages']['123']
                self.assertEqual(record['status'], expected_status)
                self.assertEqual(
                    record['first_seen'], notifier.serialize_timestamp(NOW)
                )
                self.assertFalse(record['reported'])

    def test_definite_refusal_does_not_prevent_later_notifications(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = ready_record()
        second = ready_record()
        second['title'] = 'Потребител беседа:Second'
        second['username'] = 'Second'
        second['owner_userid'] = 88
        state['pages']['124'] = second
        members = {123: member(), 124: member(124, second['title'])}
        inclusions = {123: inclusion(), 124: inclusion(124, second['title'])}
        with mock.patch.object(
                notifier, 'account_info', side_effect=[(77, True), (88, True)]), \
                mock.patch.object(
                    notifier, 'notify_record',
                    side_effect=[notifier.DefiniteWriteError('abusefilter-disallowed'),
                                 'would-notify'],
                ) as notify:
            failures = notifier.reconcile_world(
                mock.sentinel.site, state, members, inclusions,
                ALIASES, True, NOW, args(write=True)
            )
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(len(failures), 1)
        self.assertEqual(state['pages']['123']['status'], 'unclassified')
        self.assertIn('abusefilter-disallowed', state['pages']['123']['reason'])

    def test_stray_text_is_fetched_only_when_lastrevid_changes(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['8'] = {
            'kind': 'stray', 'title': 'User talk:X/sub', 'namespace': 3,
            'category_timestamp': None, 'lastrevid': 5, 'included': True,
            'status': 'anomaly', 'reason': 'stray',
            'first_seen': notifier.serialize_timestamp(NOW), 'reported': True,
        }
        stray = inclusion(8, 'User talk:X/sub', 5)
        with mock.patch.object(notifier, 'fetch_page_text') as fetch:
            notifier.reconcile_world(
                mock.sentinel.site, state, {}, {8: stray}, ALIASES, True, NOW, args()
            )
        fetch.assert_not_called()
        with mock.patch.object(
                notifier, 'fetch_page_text', return_value='resolved') as fetch:
            notifier.reconcile_world(
                mock.sentinel.site, state, {},
                {8: inclusion(8, 'User talk:X/sub', 6)}, ALIASES, True, NOW, args()
            )
        fetch.assert_called_once()
        self.assertEqual(state['pages']['8']['status'], 'inactive')
        with mock.patch.object(notifier, 'fetch_page_text') as fetch:
            notifier.reconcile_world(
                mock.sentinel.site, state, {},
                {8: inclusion(8, 'User talk:X/sub', 6)}, ALIASES, True, NOW, args()
            )
        fetch.assert_not_called()

    def test_exact_documentation_page_is_the_only_expected_stray(self):
        doc = inclusion(8, notifier.TEMPLATE_TITLE + '/doc', 5, 10)
        with mock.patch.object(notifier, 'fetch_page_text') as fetch:
            self.assertIsNone(
                notifier.make_stray_record(mock.sentinel.site, doc, ALIASES, None, NOW)
            )
        fetch.assert_not_called()
        sub = inclusion(9, notifier.TEMPLATE_TITLE + '/doc/test', 6, 10)
        with mock.patch.object(
                notifier, 'fetch_page_text', return_value='{{Отблокиране}}'):
            self.assertEqual(
                notifier.make_stray_record(
                    mock.sentinel.site, sub, ALIASES, None, NOW
                )['status'],
                'anomaly',
            )

    def test_disappearing_page_removes_its_episode(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['123'] = ready_record(status='notified')
        notifier.reconcile_world(
            mock.sentinel.site, state, {}, {}, ALIASES, True, NOW, args()
        )
        self.assertEqual(state['pages'], {})


class NotificationTests(unittest.TestCase):
    def post_with_error(self, error):
        site = mock.Mock()
        site.user.return_value = 'Eliza Beth'
        site.tokens = {'csrf': 'token'}
        request = mock.Mock()
        request.submit.side_effect = error
        with mock.patch.object(notifier, 'ensure_destination', return_value=50), \
                mock.patch.object(notifier.api, 'Request', return_value=request):
            return notifier.post_new_section(
                site, 'У:ЗА', 'heading', 'message', 'Eliza Beth'
            )

    def test_existing_exact_marker_recovers_without_post(self):
        record = ready_record()
        with mock.patch.object(
                notifier, 'find_existing_notification', return_value=999), \
                mock.patch.object(notifier, 'request_is_current') as current, \
                mock.patch.object(notifier, 'post_new_section') as post:
            result = notifier.notify_record(
                mock.sentinel.site, record, '123', 'У:ЗА', 'Eliza Beth', True, True
            )
        self.assertEqual(result, 'recovered')
        self.assertEqual(record['notification_revid'], 999)
        current.assert_not_called()
        post.assert_not_called()

    def test_recovery_uses_ten_minute_margin_and_exact_marker(self):
        request = notifier.Request('Talk', 'User', 1, 456, NOW)
        revisions = [{'revid': 901, 'parentid': 900,
                      'comment': 'Нова тема /* Заявка за отблокиране: very long */'}]
        with mock.patch.object(
                notifier, 'revisions_by_user_since', return_value=revisions) as listed, \
                mock.patch.object(
                    notifier, 'revision_content', side_effect=[request.message, 'before']
                ):
            found = notifier.find_existing_notification(
                mock.sentinel.site, 'У:ЗА', 'Eliza Beth', request
            )
        self.assertEqual(found, 901)
        self.assertEqual(listed.call_args.args[-1], NOW - dt.timedelta(minutes=10))

    def test_post_disables_transport_retries(self):
        site = mock.Mock()
        site.user.return_value = 'Eliza Beth'
        site.tokens = {'csrf': 'token'}
        api_request = mock.Mock()
        api_request.submit.return_value = {'edit': {'result': 'Success', 'newrevid': 99}}
        with mock.patch.object(notifier, 'ensure_destination', return_value=50), \
                mock.patch.object(notifier.api, 'Request', return_value=api_request) as request:
            result = notifier.post_new_section(
                site, 'У:ЗА', 'heading', 'message', 'Eliza Beth'
            )
        self.assertEqual(result, 99)
        self.assertEqual(request.call_args.kwargs['max_retries'], 0)
        self.assertEqual(request.call_args.kwargs['parameters']['baserevid'], 50)
        self.assertEqual(api_request.submit.call_count, 1)

    def test_explicit_api_refusal_is_a_definite_write_error(self):
        with self.assertRaises(notifier.DefiniteWriteError) as raised:
            self.post_with_error(
                notifier.APIError('abusefilter-disallowed', 'rejected')
            )
        self.assertEqual(raised.exception.code, 'abusefilter-disallowed')

    def test_mediawiki_internal_exception_has_an_ambiguous_outcome(self):
        with self.assertRaises(notifier.AmbiguousWriteError):
            self.post_with_error(notifier.APIMWError('DBQueryError', 'database failed'))

    def test_transport_exception_has_an_ambiguous_outcome(self):
        with self.assertRaises(notifier.AmbiguousWriteError):
            self.post_with_error(TimeoutError('network timeout'))

    def test_ambiguous_write_is_not_retried_in_same_run(self):
        record = ready_record()
        with mock.patch.object(notifier, 'find_existing_notification', return_value=None), \
                mock.patch.object(notifier, 'request_is_current', return_value=True), \
                mock.patch.object(
                    notifier, 'post_new_section',
                    side_effect=notifier.AmbiguousWriteError('ambiguous')
                ) as post:
            with self.assertRaises(notifier.AmbiguousWriteError):
                notifier.notify_record(
                    mock.sentinel.site, record, '123', 'У:ЗА', 'Eliza Beth',
                    True, True, eligibility_checked=True,
                )
        self.assertEqual(post.call_count, 1)
        self.assertEqual(record['status'], 'ready')

    def test_final_guard_rejects_a_page_moved_after_discovery(self):
        response = {'query': {'pages': {'123': {
            'pageid': 123, 'ns': 3, 'title': 'Потребител беседа:Moved user',
            'lastrevid': 20,
            'categories': [{'title': notifier.CATEGORY_TITLE}],
        }}}}
        request = notifier.request_from_record('123', ready_record())
        with mock.patch.object(notifier, 'submit_query', return_value=response):
            self.assertFalse(
                notifier.request_is_current(mock.sentinel.site, request, 20)
            )


class FakeStore:
    def __init__(self, state):
        self.state = state
        self.saved = []

    def save(self):
        notifier.validate_snapshot(self.state)
        self.saved.append(copy.deepcopy(self.state))
        return True


class AnomalyDigestTests(unittest.TestCase):
    def anomaly_state(self, count=1):
        state = notifier.empty_snapshot(scope())
        for index in range(count):
            pageid = index + 1
            state['pages'][str(pageid)] = {
                'kind': 'stray', 'title': 'Page {}'.format(index), 'namespace': 0,
                'category_timestamp': None, 'lastrevid': pageid, 'included': True,
                'status': 'anomaly', 'reason': 'reason',
                'first_seen': notifier.serialize_timestamp(NOW - dt.timedelta(hours=1)),
                'reported': False,
            }
        notifier.validate_snapshot(state)
        return state

    def test_digest_persists_intent_then_marks_every_case_reported(self):
        store = FakeStore(self.anomaly_state(15))
        with mock.patch.object(notifier, 'ensure_destination', return_value=10), \
                mock.patch.object(notifier, 'post_new_section', return_value=99) as post:
            count = notifier.report_anomalies(
                mock.sentinel.site, store, args(write=True), NOW
            )
        self.assertEqual(count, 15)
        self.assertIsNotNone(store.saved[0]['pending_digest'])
        self.assertIsNone(store.state['pending_digest'])
        self.assertTrue(all(record['reported'] for record in store.state['pages'].values()))
        message = post.call_args.args[3]
        self.assertEqual(message.count('* [[:'), notifier.MAX_ANOMALY_LINKS)
        self.assertIn('и още 5 страници', message)

    def test_anomaly_links_do_not_categorize_or_embed_the_report_page(self):
        records = [
            {'title': 'Категория:Неочаквана', 'first_seen': '2026-10-20T11:00:00Z'},
            {'title': 'Файл:Неочакван.svg', 'first_seen': '2026-10-20T11:01:00Z'},
        ]
        message = notifier.anomaly_message(records)
        self.assertIn('[[:Категория:Неочаквана]]', message)
        self.assertIn('[[:Файл:Неочакван.svg]]', message)
        self.assertNotIn('* [[Категория:', message)
        self.assertNotIn('* [[Файл:', message)

    def test_digest_cap_prevents_a_second_report_for_24_hours(self):
        state = self.anomaly_state()
        state['last_digest'] = notifier.serialize_timestamp(NOW - dt.timedelta(hours=1))
        store = FakeStore(state)
        with mock.patch.object(notifier, 'post_new_section') as post:
            self.assertEqual(
                notifier.report_anomalies(mock.sentinel.site, store, args(write=True), NOW), 0
            )
        post.assert_not_called()

    def test_pending_digest_is_recovered_after_an_ambiguous_write(self):
        state = self.anomaly_state()
        state['pending_digest'] = {
            'created_at': notifier.serialize_timestamp(NOW - dt.timedelta(minutes=15)),
            'heading': 'Old heading', 'marker': '== Old heading ==',
            'pages': [{'pageid': 1, 'first_seen': state['pages']['1']['first_seen']}],
        }
        store = FakeStore(state)
        with mock.patch.object(notifier, 'find_section_addition', return_value=88):
            self.assertTrue(notifier.recover_pending_digest(
                mock.sentinel.site, store, 'Report', 'Eliza Beth'
            ))
        self.assertIsNone(store.state['pending_digest'])
        self.assertTrue(store.state['pages']['1']['reported'])

    def test_missing_pending_digest_is_cleared_then_reposted(self):
        state = self.anomaly_state()
        state['pending_digest'] = {
            'created_at': notifier.serialize_timestamp(NOW - dt.timedelta(minutes=15)),
            'heading': 'Old heading', 'marker': '== Old heading ==',
            'pages': [{'pageid': 1, 'first_seen': state['pages']['1']['first_seen']}],
        }
        store = FakeStore(state)
        with mock.patch.object(notifier, 'find_section_addition', return_value=None), \
                mock.patch.object(notifier, 'ensure_destination', return_value=10), \
                mock.patch.object(notifier, 'post_new_section', return_value=99) as post:
            self.assertEqual(
                notifier.report_anomalies(mock.sentinel.site, store, args(write=True), NOW),
                1,
            )
        post.assert_called_once()
        self.assertIsNone(store.state['pending_digest'])
        self.assertTrue(store.state['pages']['1']['reported'])

    def test_ambiguous_digest_post_leaves_the_pending_intent_saved(self):
        store = FakeStore(self.anomaly_state())
        with mock.patch.object(notifier, 'ensure_destination', return_value=10), \
                mock.patch.object(
                    notifier, 'post_new_section',
                    side_effect=notifier.AmbiguousWriteError('unknown'),
                ):
            with self.assertRaises(notifier.AmbiguousWriteError):
                notifier.report_anomalies(
                    mock.sentinel.site, store, args(write=True), NOW
                )
        self.assertIsNotNone(store.saved[0]['pending_digest'])
        self.assertIsNotNone(store.state['pending_digest'])


class CommandLineTests(unittest.TestCase):
    def test_defaults_use_json_snapshot_not_jsonl(self):
        production = notifier.parse_args(['script'])
        test = notifier.parse_args(['script', '--write', '--test-unblocked-requests'])
        self.assertFalse(production.write)
        self.assertEqual(production.state_file.name, 'unblock-request-notifier.json')
        self.assertEqual(test.state_file.name, 'unblock-request-notifier-test.json')
        self.assertNotEqual(production.state_file, test.state_file)

    def test_test_mode_requires_write_and_has_a_distinct_scope(self):
        with self.assertRaises(SystemExit):
            notifier.parse_args(['script', '--test-unblocked-requests'])
        self.assertNotEqual(notifier.state_scope(args()), notifier.state_scope(args(test=True)))

    def test_dry_run_prints_each_reportable_anomaly_and_reason(self):
        state = notifier.empty_snapshot(scope())
        state['pages']['8'] = {
            'kind': 'stray', 'title': 'Файл:Unexpected.svg', 'namespace': 6,
            'category_timestamp': None, 'lastrevid': 5, 'included': True,
            'status': 'unclassified', 'reason': 'temporary classification failure',
            'first_seen': notifier.serialize_timestamp(NOW - dt.timedelta(hours=1)),
            'reported': False,
        }
        command_args = args(state_file=Path('/unused/state.json'))
        output = io.StringIO()
        with (
            mock.patch.object(notifier.pwb, 'Site', return_value=mock.sentinel.site),
            mock.patch.object(notifier, 'template_aliases', return_value=(['Template'], ALIASES)),
            mock.patch.object(notifier, 'category_members', return_value={}),
            mock.patch.object(notifier, 'template_inclusions', return_value={}),
            mock.patch.object(notifier, 'utc_now', return_value=NOW),
            mock.patch.object(notifier, 'load_snapshot', return_value=state),
            mock.patch.object(notifier, 'reconcile_world', return_value=[]),
            contextlib.redirect_stdout(output),
        ):
            result = notifier.run(command_args)
        self.assertEqual(result, 0)
        self.assertIn(
            'ANOMALY: Файл:Unexpected.svg — temporary classification failure',
            output.getvalue(),
        )

    def test_run_reports_anomalies_after_a_definite_notification_failure(self):
        store = FakeStore(notifier.empty_snapshot(scope()))
        manager = mock.MagicMock()
        manager.__enter__.return_value = store
        manager.__exit__.return_value = False
        command_args = args(write=True, state_file=Path('/unused/state.json'))
        with (
            mock.patch.object(notifier.pwb, 'Site', return_value=mock.sentinel.site),
            mock.patch.object(notifier, 'template_aliases', return_value=(['Template'], ALIASES)),
            mock.patch.object(notifier, 'category_members', return_value={}),
            mock.patch.object(notifier, 'template_inclusions', return_value={}),
            mock.patch.object(notifier, 'StateStore', return_value=manager),
            mock.patch.object(
                notifier, 'reconcile_world', return_value=['notification refused']
            ),
            mock.patch.object(notifier, 'report_anomalies', return_value=1) as report,
        ):
            result = notifier.run(command_args)
        self.assertEqual(result, 1)
        report.assert_called_once()

    def test_run_skips_anomaly_post_after_ambiguous_normal_post(self):
        store = FakeStore(notifier.empty_snapshot(scope()))
        manager = mock.MagicMock()
        manager.__enter__.return_value = store
        manager.__exit__.return_value = False
        command_args = args(write=True, state_file=Path('/unused/state.json'))
        with (
            mock.patch.object(notifier.pwb, 'Site', return_value=mock.sentinel.site),
            mock.patch.object(notifier, 'template_aliases', return_value=(['Template'], ALIASES)),
            mock.patch.object(notifier, 'category_members', return_value={}),
            mock.patch.object(notifier, 'template_inclusions', return_value={}),
            mock.patch.object(notifier, 'StateStore', return_value=manager),
            mock.patch.object(
                notifier, 'reconcile_world',
                side_effect=notifier.AmbiguousWriteError('unknown'),
            ),
            mock.patch.object(notifier, 'report_anomalies') as report,
        ):
            result = notifier.run(command_args)
        self.assertEqual(result, 1)
        report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
