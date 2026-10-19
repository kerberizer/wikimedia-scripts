#!/usr/bin/env python3

"""Notify Bulgarian Wikipedia administrators about unblock requests."""

import argparse
import datetime as dt
import fcntl
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import mwparserfromhell as mwp
import pywikibot as pwb

from pywikibot.exceptions import APIError


SITE_CODE = 'bg'
SITE_FAMILY = 'wikipedia'
TEMPLATE_TITLE = 'Шаблон:Отблокиране'
CATEGORY_TITLE = 'Категория:Заявки за отблокиране'
DEFAULT_NOTIFICATION_PAGE = 'Уикипедия:Заявки към администраторите'
DEFAULT_EXPECTED_USER = 'Eliza Beth'
JOURNAL_VERSION = 1
MAX_ANOMALY_LINKS = 10


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def format_timestamp(value):
    return as_utc(value).strftime('%Y-%m-%d %H:%M:%S UTC')


def serialize_timestamp(value):
    return as_utc(value).isoformat().replace('+00:00', 'Z')


def parse_timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(dt.timezone.utc)


def default_state_file():
    state_home = os.environ.get('XDG_STATE_HOME')
    if not state_home:
        state_home = os.path.join(os.path.expanduser('~'), '.local', 'state')
    return Path(state_home) / 'wikimedia-scripts' / 'unblock-request-notifier.jsonl'


@dataclass(frozen=True)
class Request:
    page_title: str
    username: str
    pageid: int
    revision_id: int
    timestamp: dt.datetime

    @property
    def request_id(self):
        return '{}:{}'.format(self.pageid, self.revision_id)

    @property
    def heading(self):
        return 'Заявка за отблокиране: {} ({})'.format(
            self.username, format_timestamp(self.timestamp)
        )

    @property
    def marker(self):
        return '[[Special:Diff/{}|'.format(self.revision_id)

    @property
    def message(self):
        return (
            'Нова [[Special:Diff/{revision}|заявка за отблокиране]] от\n'
            '{{{{потребител|{username}}}}} очаква преглед.\n~~~~'
        ).format(revision=self.revision_id, username=self.username)


@dataclass
class JournalState:
    started: dict = field(default_factory=dict)
    notified: dict = field(default_factory=dict)
    anomaly_first_seen: dict = field(default_factory=dict)
    anomaly_reported: dict = field(default_factory=dict)
    anomaly_report_started: dict = field(default_factory=dict)
    last_anomaly_report: dt.datetime | None = None

    def apply(self, event):
        event_type = event.get('event')
        if event_type == 'notify_started':
            self.started[event['request_id']] = event
        elif event_type == 'notified':
            self.notified[event['request_id']] = event
        elif event_type == 'anomaly_first_seen':
            self.anomaly_first_seen[event['page']] = parse_timestamp(event['timestamp'])
        elif event_type == 'anomaly_cleared':
            self.anomaly_first_seen.pop(event['page'], None)
            self.anomaly_reported.pop(event['page'], None)
        elif event_type == 'anomaly_reported':
            timestamp = parse_timestamp(event['timestamp'])
            for title in event['pages']:
                self.anomaly_reported[title] = timestamp
            self.last_anomaly_report = timestamp
            heading = event.get('heading')
            if heading:
                self.anomaly_report_started.pop(heading, None)
        elif event_type == 'anomaly_report_started':
            self.anomaly_report_started[event['heading']] = event
        elif event_type == 'anomaly_report_cancelled':
            self.anomaly_report_started.pop(event['heading'], None)
        else:
            raise ValueError('unknown state-journal event: {!r}'.format(event_type))


class Journal:
    def __init__(self, path, writable):
        self.path = Path(path)
        self.writable = writable
        self.file = None
        self.state = JournalState()

    def __enter__(self):
        try:
            if self.writable:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.file = self.path.open('a+b')
                os.chmod(self.path, 0o600)
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.file.seek(0)
                data = self.file.read()
                self.state, repaired = replay_and_repair_writable_journal(data)
                if repaired != data:
                    if len(repaired) < len(data):
                        os.ftruncate(self.file.fileno(), len(repaired))
                    else:
                        self.file.seek(0, os.SEEK_END)
                        self.file.write(repaired[len(data):])
                    self.file.flush()
                    os.fsync(self.file.fileno())
            else:
                data = self.path.read_text(encoding='utf-8') if self.path.exists() else ''
                self.state = replay_journal(data)
            return self
        except Exception:
            if self.file is not None:
                self.file.close()
                self.file = None
            raise

    def append(self, event, now=None):
        if not self.writable or self.file is None:
            raise RuntimeError('attempted to write a read-only journal')
        record = dict(event)
        record['v'] = JOURNAL_VERSION
        record.setdefault('timestamp', serialize_timestamp(now or utc_now()))
        self.file.seek(0, os.SEEK_END)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n'
        self.file.write(line.encode('utf-8'))
        self.file.flush()
        os.fsync(self.file.fileno())
        self.state.apply(record)
        return record

    def __exit__(self, exc_type, exc_value, traceback):
        if self.file is not None:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()


def replay_journal(data):
    state = JournalState()
    lines = data.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            raise ValueError('empty line {} in state journal'.format(index + 1))
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            is_unterminated_last_line = index == len(lines) - 1 and not line.endswith('\n')
            if is_unterminated_last_line:
                logging.warning('Ignoring a truncated final state-journal line')
                break
            raise ValueError(
                'invalid JSON on state-journal line {}'.format(index + 1)
            )
        if event.get('v') != JOURNAL_VERSION:
            raise ValueError('unsupported state-journal version on line {}'.format(index + 1))
        state.apply(event)
    return state


def replay_and_repair_writable_journal(data):
    if not data:
        return JournalState(), data
    if data.endswith(b'\n'):
        return replay_journal(data.decode('utf-8')), data

    prefix_end = data.rfind(b'\n') + 1
    prefix = data[:prefix_end]
    tail = data[prefix_end:]
    state = replay_journal(prefix.decode('utf-8'))
    try:
        event = json.loads(tail.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logging.warning('Truncating a torn final state-journal line')
        return state, prefix
    if event.get('v') != JOURNAL_VERSION:
        raise ValueError('unsupported state-journal version on final line')
    state.apply(event)
    return state, data + b'\n'


def normalize_template_name(name):
    normalized = ' '.join(str(name).replace('_', ' ').strip().split())
    namespace, separator, remainder = normalized.partition(':')
    if separator and namespace.casefold() in {'шаблон', 'template'}:
        normalized = remainder
    return normalized.casefold()


def template_aliases(site):
    template = pwb.Page(site, TEMPLATE_TITLE)
    aliases = {normalize_template_name(template.title(with_ns=False))}
    aliases.update(
        normalize_template_name(page.title(with_ns=False)) for page in template.redirects()
    )
    return aliases


def parameter_has_value(template, name):
    if not template.has(name, ignore_empty=False):
        return False
    value = mwp.parse(str(template.get(name).value))
    for comment in value.filter_comments(recursive=True):
        value.remove(comment)
    return bool(str(value).strip())


def active_request_count(text, aliases):
    count = 0
    for template in mwp.parse(text).filter_templates(recursive=True):
        if normalize_template_name(template.name) not in aliases:
            continue
        if parameter_has_value(template, 'demo'):
            continue
        if not parameter_has_value(template, 2):
            count += 1
    return count


def revision_value(revision, name):
    if hasattr(revision, name):
        return getattr(revision, name)
    return revision[name]


def find_introduction_revision(page, aliases):
    introduction = None
    for revision in page.revisions(content=True):
        text = revision_value(revision, 'text') or ''
        if active_request_count(text, aliases) != 1:
            break
        introduction = revision
    return introduction


def is_own_base_talk_page(page, username):
    if page.namespace() != 3:
        return False
    return pwb.User(page.site, username).getUserTalkPage().title() == page.title()


def has_local_block(site, username):
    return next(iter(site.blocks(users=username, total=1)), None) is not None


def discover_request(page, aliases):
    username = page.title(with_ns=False)
    if not is_own_base_talk_page(page, username):
        raise ValueError('not a base user-talk page')
    if page.isRedirectPage():
        raise ValueError('the user-talk page is a redirect')
    if pwb.tools.is_ip_address(username):
        raise ValueError('IP-address requests are outside the supported workflow')
    introduction = find_introduction_revision(page, aliases)
    if introduction is None:
        raise ValueError('the page does not currently contain exactly one active request')
    author = revision_value(introduction, 'user')
    if author != username:
        raise ValueError('the current request was introduced by {!r}, not the owner'.format(author))
    if not has_local_block(page.site, username):
        raise ValueError('the requester is not currently blocked on bgwiki')
    return Request(
        page_title=page.title(),
        username=username,
        pageid=page.pageid,
        revision_id=revision_value(introduction, 'revid'),
        timestamp=as_utc(revision_value(introduction, 'timestamp')),
    )


def category_requests(site, aliases):
    category = pwb.Category(site, CATEGORY_TITLE)
    requests = []
    for page in category.members(namespaces=[3]):
        try:
            requests.append(discover_request(page, aliases))
        except (APIError, ValueError) as error:
            logging.warning('Skipping %s: %s', page.title(), error)
    return requests


def unexpected_inclusions(site):
    template = pwb.Page(site, TEMPLATE_TITLE)
    unexpected = []
    for page in template.embeddedin(content=False):
        if page.title() == TEMPLATE_TITLE or page.title().startswith(TEMPLATE_TITLE + '/'):
            continue
        username = page.title(with_ns=False)
        if is_own_base_talk_page(page, username):
            continue
        unexpected.append(page.title())
    return sorted(set(unexpected))


def revisions_by_user_since(site, page_title, expected_user, since):
    page = pwb.Page(site, page_title)
    parameters = {
        'action': 'query',
        'prop': 'revisions',
        'titles': page.title(),
        'rvprop': 'ids|timestamp|user|comment',
        'rvuser': expected_user,
        'rvend': serialize_timestamp(since),
        'rvlimit': 'max',
    }
    while True:
        response = site.simple_request(**parameters).submit()
        pages = response.get('query', {}).get('pages', {})
        for page_data in pages.values():
            for revision in page_data.get('revisions', []):
                yield page, revision
        continuation = response.get('continue')
        if not continuation:
            break
        parameters.update(continuation)


def find_section_addition(site, page_title, expected_user, heading, marker, since):
    for page, revision in revisions_by_user_since(
            site, page_title, expected_user, since):
        if heading not in (revision.get('comment') or ''):
            continue
        revision_id = revision['revid']
        text = page.get_revision(revision_id, content=True).text or ''
        if marker not in text:
            continue
        parent_id = revision.get('parentid')
        parent_text = (page.get_revision(parent_id, content=True).text or '') if parent_id else ''
        if marker not in parent_text:
            return revision_id
    return None


def find_existing_notification(site, page_title, expected_user, request):
    return find_section_addition(
        site,
        page_title,
        expected_user,
        request.heading,
        request.marker,
        request.timestamp,
    )


def assert_write_identity(site, expected_user):
    site.login()
    actual_user = site.user()
    if actual_user != expected_user:
        raise RuntimeError(
            'refusing to write as {!r}; expected {!r}'.format(actual_user, expected_user)
        )


def post_new_section(site, page_title, heading, message, expected_user):
    assert_write_identity(site, expected_user)
    page = pwb.Page(site, page_title)
    base_revision = page.latest_revision_id
    parameters = dict(
        action='edit',
        title=page.title(),
        section='new',
        sectiontitle=heading,
        text=message,
        token=site.tokens['csrf'],
        notminor=True,
        nocreate=True,
        watchlist='nochange',
        baserevid=base_revision,
        assertuser=expected_user,
        maxlag=5,
    )
    parameters['assert'] = 'user'
    request = site.simple_request(**parameters)
    response = request.submit()
    edit = response.get('edit', {})
    if edit.get('result') != 'Success' or 'newrevid' not in edit:
        raise RuntimeError('unexpected edit response: {!r}'.format(response))
    return int(edit['newrevid'])


def request_event(request):
    return {
        'request_id': request.request_id,
        'page': request.page_title,
        'request_revid': request.revision_id,
        'request_timestamp': serialize_timestamp(request.timestamp),
        'heading': request.heading,
    }


def same_request_is_active(site, aliases, expected):
    current = discover_request(pwb.Page(site, expected.page_title), aliases)
    return current.request_id == expected.request_id


def notify_request(site, aliases, journal, notification_page, expected_user, request):
    if request.request_id in journal.state.notified:
        return 'already-notified'

    existing_revision = find_existing_notification(
        site, notification_page, expected_user, request
    )
    if existing_revision:
        journal.append({
            'event': 'notified',
            'request_id': request.request_id,
            'notification_revid': existing_revision,
            'recovered': True,
        })
        return 'recovered'

    if not same_request_is_active(site, aliases, request):
        return 'no-longer-active'

    if request.request_id not in journal.state.started:
        event = request_event(request)
        event['event'] = 'notify_started'
        journal.append(event)

    try:
        notification_revision = post_new_section(
            site, notification_page, request.heading, request.message, expected_user
        )
    except APIError as error:
        if error.code != 'editconflict':
            raise
        recovered = find_existing_notification(site, notification_page, expected_user, request)
        if recovered:
            notification_revision = recovered
        else:
            if not same_request_is_active(site, aliases, request):
                return 'no-longer-active'
            notification_revision = post_new_section(
                site, notification_page, request.heading, request.message, expected_user
            )

    journal.append({
        'event': 'notified',
        'request_id': request.request_id,
        'notification_revid': notification_revision,
    })
    return 'notified'


def sync_anomalies(journal, current_pages, now):
    current = set(current_pages)
    known = set(journal.state.anomaly_first_seen)
    for title in sorted(current - known):
        journal.append({'event': 'anomaly_first_seen', 'page': title}, now=now)
    for title in sorted(known - current):
        journal.append({'event': 'anomaly_cleared', 'page': title}, now=now)


def reportable_anomalies(state, current_pages, now, minimum_age, repeat_after):
    result = []
    for title in sorted(current_pages):
        first_seen = state.anomaly_first_seen.get(title)
        if first_seen is None or now - first_seen < minimum_age:
            continue
        reported = state.anomaly_reported.get(title)
        if reported is None or now - reported >= repeat_after:
            result.append(title)
    return result


def anomaly_message(titles):
    shown = titles[:MAX_ANOMALY_LINKS]
    lines = [
        'При периодичната проверка намерих неочаквани включвания на',
        '{{ш|Отблокиране}} извън основни потребителски беседи:',
        '',
    ]
    lines.extend('* [[{}]]'.format(title) for title in shown)
    if len(titles) > len(shown):
        lines.append('* и още {} страници'.format(len(titles) - len(shown)))
    lines.extend(['', '~~~~'])
    return '\n'.join(lines)


def recover_started_anomaly_reports(site, journal, report_page, expected_user, now):
    for heading, event in list(journal.state.anomaly_report_started.items()):
        started_at = parse_timestamp(event['timestamp'])
        existing = find_section_addition(
            site,
            report_page,
            expected_user,
            heading,
            '== {} =='.format(heading),
            started_at,
        )
        if existing:
            journal.append({
                'event': 'anomaly_reported',
                'heading': heading,
                'pages': event['pages'],
                'notification_revid': existing,
                'recovered': True,
            }, now=started_at)
        else:
            journal.append({
                'event': 'anomaly_report_cancelled',
                'heading': heading,
            }, now=now)


def process_anomalies(site, journal, report_page, expected_user, now, min_age_minutes):
    current = unexpected_inclusions(site)
    sync_anomalies(journal, current, now)
    if report_page:
        recover_started_anomaly_reports(site, journal, report_page, expected_user, now)
    last_report = journal.state.last_anomaly_report
    if last_report is not None and now - last_report < dt.timedelta(hours=24):
        return 0
    reportable = reportable_anomalies(
        journal.state,
        current,
        now,
        dt.timedelta(minutes=min_age_minutes),
        dt.timedelta(hours=24),
    )
    if not reportable:
        return 0
    if not report_page:
        logging.warning('Unexpected inclusions: %s', ', '.join(reportable))
        return 0
    heading = 'Неочаквани включвания на „Отблокиране“ ({})'.format(
        now.strftime('%Y-%m-%d')
    )
    section_marker = '== {} =='.format(heading)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    existing = find_section_addition(
        site, report_page, expected_user, heading, section_marker, start_of_day
    )
    if existing:
        journal.append({
            'event': 'anomaly_reported',
            'heading': heading,
            'pages': reportable,
            'notification_revid': existing,
            'recovered': True,
        }, now=now)
        return 0
    journal.append({
        'event': 'anomaly_report_started',
        'heading': heading,
        'pages': reportable,
    }, now=now)
    revision = post_new_section(
        site, report_page, heading, anomaly_message(reportable), expected_user
    )
    journal.append({
        'event': 'anomaly_reported',
        'heading': heading,
        'pages': reportable,
        'notification_revid': revision,
    }, now=now)
    return len(reportable)


def validate_test_target(site, page_title, expected_user):
    page = pwb.Page(site, page_title)
    if page.namespace() not in {2, 3}:
        raise ValueError('test target must be in the expected user\'s own user space')
    local_title = page.title(with_ns=False)
    if local_title != expected_user and not local_title.startswith(expected_user + '/'):
        raise ValueError('test target must be in the expected user\'s own user space')


def run_test_notification(site, page_title, expected_user):
    validate_test_target(site, page_title, expected_user)
    now = utc_now()
    heading = 'Тестово уведомление за заявка за отблокиране ({})'.format(
        format_timestamp(now)
    )
    message = (
        'Това е контролиран тест на механизма за нов раздел, без реална '
        'заявка за отблокиране.\n~~~~'
    )
    return post_new_section(site, page_title, heading, message, expected_user)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Notify bgwiki administrators about new unblock requests.'
    )
    parser.add_argument(
        '--write',
        action='store_true',
        help='enable wiki writes and state-journal updates (default: read-only dry run)',
    )
    parser.add_argument('--expected-user', default=DEFAULT_EXPECTED_USER)
    parser.add_argument('--notification-page', default=DEFAULT_NOTIFICATION_PAGE)
    parser.add_argument('--state-file', type=Path, default=default_state_file())
    parser.add_argument(
        '--anomaly-report-page',
        help='optional page for daily reports about persistent unexpected transclusions',
    )
    parser.add_argument('--anomaly-min-age-minutes', type=int, default=30)
    parser.add_argument(
        '--test-notification',
        action='store_true',
        help='post one controlled test section to --notification-page and exit',
    )
    args = parser.parse_args(argv[1:])
    if args.test_notification and not args.write:
        parser.error('--test-notification requires --write')
    if args.anomaly_min_age_minutes < 0:
        parser.error('--anomaly-min-age-minutes cannot be negative')
    return args


def run(args):
    site = pwb.Site(code=SITE_CODE, fam=SITE_FAMILY)
    if args.test_notification:
        revision = run_test_notification(site, args.notification_page, args.expected_user)
        print('Posted test notification in revision {}.'.format(revision))
        return 0

    aliases = template_aliases(site)
    requests = category_requests(site, aliases)
    if not args.write:
        print('Read-only dry run: {} eligible active request(s).'.format(len(requests)))
        for request in requests:
            print('* {} -> {}'.format(request.request_id, request.heading))
        anomalies = unexpected_inclusions(site)
        print('Read-only dry run: {} unexpected transclusion(s).'.format(len(anomalies)))
        for title in anomalies:
            print('* {}'.format(title))
        return 0

    failures = []
    with Journal(args.state_file, writable=True) as journal:
        for request in requests:
            try:
                result = notify_request(
                    site,
                    aliases,
                    journal,
                    args.notification_page,
                    args.expected_user,
                    request,
                )
                print('{}: {}'.format(request.request_id, result))
            except Exception as error:
                logging.exception('Failed to process request %s', request.request_id)
                failures.append('{}: {}'.format(request.request_id, error))
        try:
            reported = process_anomalies(
                site,
                journal,
                args.anomaly_report_page,
                args.expected_user,
                utc_now(),
                args.anomaly_min_age_minutes,
            )
            if reported:
                print('Reported {} unexpected transclusion(s).'.format(reported))
        except Exception as error:
            logging.exception('Failed to process unexpected transclusions')
            failures.append('unexpected transclusions: {}'.format(error))
    if failures:
        for failure in failures:
            print('ERROR: {}'.format(failure), file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    return run(parse_args(argv or sys.argv))


if __name__ == '__main__':
    sys.exit(main())


# vim: set ts=4 sts=4 sw=4 tw=100 et:
