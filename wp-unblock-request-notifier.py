#!/usr/bin/env python3

"""Notify Bulgarian Wikipedia administrators about unblock requests."""

import argparse
import copy
import datetime as dt
import decimal
import fcntl
import html
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mwparserfromhell as mwp
import pywikibot as pwb

from pywikibot.data import api
from pywikibot.exceptions import APIError, APIMWError


SITE_CODE = 'bg'
SITE_FAMILY = 'wikipedia'
TEMPLATE_TITLE = 'Шаблон:Отблокиране'
CATEGORY_TITLE = 'Категория:Заявки за отблокиране'
EXPECTED_ANOMALY_TITLES = frozenset({TEMPLATE_TITLE + '/doc'})
DEFAULT_NOTIFICATION_PAGE = 'Уикипедия:Заявки към администраторите'
DEFAULT_EXPECTED_USER = 'Eliza Beth'
STATE_VERSION = 1
MAX_ANOMALY_LINKS = 10
MAX_HISTORY_REVISIONS = 20
HISTORY_START_SLACK = dt.timedelta(seconds=60)
RECOVERY_START_SLACK = dt.timedelta(minutes=10)


class PermanentClassificationError(ValueError):
    """The current episode cannot safely become a normal notification."""


class RetryClassification(RuntimeError):
    """Classification should be attempted again on the next run."""


class LockUnavailable(RuntimeError):
    """Another notifier process owns the state lock."""


class AmbiguousWriteError(RuntimeError):
    """A wiki write may have succeeded; no further writes are safe this run."""


class DefiniteWriteError(RuntimeError):
    """MediaWiki explicitly refused a write without saving a revision."""

    def __init__(self, code):
        self.code = code
        super().__init__('MediaWiki refused the edit ({})'.format(code))


@dataclass(frozen=True)
class CategoryMember:
    pageid: int
    title: str
    namespace: int
    timestamp: dt.datetime
    sortkeyprefix: str


@dataclass(frozen=True)
class Inclusion:
    pageid: int
    title: str
    namespace: int
    lastrevid: int
    redirect: bool


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
        username = self.username.replace('~', '&#126;')
        return (
            'Нова [[Special:Diff/{revision}|заявка за отблокиране]] от\n'
            '{{{{потребител|1={username}}}}} очаква преглед.\n~~~~'
        ).format(revision=self.revision_id, username=username)


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
    return Path(state_home) / 'wikimedia-scripts' / 'unblock-request-notifier.json'


def default_test_state_file():
    return default_state_file().with_name('unblock-request-notifier-test.json')


def state_scope(args):
    return {
        'site': '{}.{}'.format(SITE_CODE, SITE_FAMILY),
        'mode': 'test-unblocked' if args.test_unblocked_requests else 'production',
        'expected_user': args.expected_user,
        'notification_page': args.notification_page,
        'anomaly_report_page': args.anomaly_report_page,
    }


def empty_snapshot(scope):
    return {
        'version': STATE_VERSION,
        'scope': scope,
        'pages': {},
        'pending_digest': None,
        'last_digest': None,
    }


def require_fields(value, fields, context):
    missing = sorted(set(fields) - set(value))
    if missing:
        raise ValueError('{} is missing {}'.format(context, ', '.join(missing)))


def validate_snapshot_timestamp(value, context, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str):
        raise ValueError('{} must be a timestamp string'.format(context))
    try:
        parse_timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError('{} is not a valid timestamp'.format(context)) from error


def validate_page_record(pageid, record):
    context = 'page record {}'.format(pageid)
    if not isinstance(record, dict):
        raise ValueError('{} must be an object'.format(context))
    require_fields(
        record,
        {
            'kind', 'title', 'namespace', 'category_timestamp', 'lastrevid',
            'included', 'status', 'reason',
        },
        context,
    )
    if record['kind'] not in {'category', 'stray'}:
        raise ValueError('{} has an unknown kind'.format(context))
    if record['status'] not in {
            'inactive', 'anomaly', 'unclassified', 'waiting', 'ready', 'notified'}:
        raise ValueError('{} has an unknown status'.format(context))
    if not isinstance(record['title'], str) or not record['title']:
        raise ValueError('{} has an invalid title'.format(context))
    if type(record['namespace']) is not int or type(record['included']) is not bool:
        raise ValueError('{} has invalid namespace/included fields'.format(context))
    if record['lastrevid'] is not None and type(record['lastrevid']) is not int:
        raise ValueError('{} has an invalid lastrevid'.format(context))
    if record['reason'] is not None and not isinstance(record['reason'], str):
        raise ValueError('{} has an invalid reason'.format(context))
    if record['included'] != (record['lastrevid'] is not None):
        raise ValueError('{} has inconsistent inclusion fields'.format(context))

    if record['kind'] == 'category':
        require_fields(
            record,
            {'category_timestamp', 'sortkeyprefix', 'first_seen', 'reported', 'reason'},
            context,
        )
        validate_snapshot_timestamp(record['category_timestamp'], context + '.category_timestamp')
        if not isinstance(record['sortkeyprefix'], str):
            raise ValueError('{} has an invalid sortkeyprefix'.format(context))
        validate_snapshot_timestamp(record['first_seen'], context + '.first_seen')
        if type(record['reported']) is not bool or (
                record['reason'] is not None and not isinstance(record['reason'], str)):
            raise ValueError('{} has invalid episode fields'.format(context))
    else:
        if record['category_timestamp'] is not None:
            raise ValueError('{} has a category timestamp for a stray'.format(context))
        if record['status'] not in {'inactive', 'anomaly', 'unclassified'}:
            raise ValueError('{} has an invalid status for a stray'.format(context))
        if not record['included']:
            raise ValueError('{} records a stray without an inclusion'.format(context))

    if record['status'] in {'anomaly', 'unclassified', 'waiting'}:
        require_fields(record, {'first_seen', 'reported', 'reason'}, context)
        validate_snapshot_timestamp(record['first_seen'], context + '.first_seen')
        if type(record['reported']) is not bool or not isinstance(record['reason'], str):
            raise ValueError('{} has invalid anomaly fields'.format(context))
    if record['status'] in {'waiting', 'ready', 'notified'}:
        if record['kind'] != 'category':
            raise ValueError('{} has a request status on a stray'.format(context))
        require_fields(
            record,
            {
                'username', 'owner_userid', 'intro_revid', 'intro_timestamp',
                'intro_user', 'lastrevid',
            },
            context,
        )
        if not isinstance(record['username'], str) or not record['username']:
            raise ValueError('{} has an invalid username'.format(context))
        if type(record['owner_userid']) is not int or type(record['intro_revid']) is not int:
            raise ValueError('{} has invalid user/revision IDs'.format(context))
        if type(record['lastrevid']) is not int or not isinstance(record['intro_user'], str):
            raise ValueError('{} has invalid request fields'.format(context))
        validate_snapshot_timestamp(record['intro_timestamp'], context + '.intro_timestamp')
    if record['status'] == 'notified' and type(record.get('notification_revid')) is not int:
        raise ValueError('{} has no valid notification revision'.format(context))
    if record['status'] == 'inactive':
        if record['kind'] != 'stray':
            raise ValueError('{} has inactive status on a category member'.format(context))
        if record['reason'] is not None:
            raise ValueError('{} has a reason for an inactive inclusion'.format(context))


def validate_pending_digest(pending):
    if pending is None:
        return
    if not isinstance(pending, dict):
        raise ValueError('pending_digest must be an object or null')
    require_fields(pending, {'created_at', 'heading', 'marker', 'pages'}, 'pending_digest')
    validate_snapshot_timestamp(pending['created_at'], 'pending_digest.created_at')
    if (
            not isinstance(pending['heading'], str) or not pending['heading']
            or pending['marker'] != '== {} =='.format(pending['heading'])):
        raise ValueError('pending_digest has invalid heading/marker fields')
    if not isinstance(pending['pages'], list):
        raise ValueError('pending_digest.pages must be an array')
    for index, item in enumerate(pending['pages']):
        context = 'pending_digest.pages[{}]'.format(index)
        if not isinstance(item, dict):
            raise ValueError('{} must be an object'.format(context))
        require_fields(item, {'pageid', 'first_seen'}, context)
        if type(item['pageid']) is not int:
            raise ValueError('{} has an invalid pageid'.format(context))
        validate_snapshot_timestamp(item['first_seen'], context + '.first_seen')


def validate_snapshot(state):
    for pageid, record in state['pages'].items():
        if not isinstance(pageid, str) or not re.fullmatch(r'[1-9][0-9]*', pageid):
            raise ValueError('state snapshot has an invalid page-ID key')
        validate_page_record(pageid, record)
    validate_pending_digest(state['pending_digest'])
    validate_snapshot_timestamp(state['last_digest'], 'last_digest', optional=True)


def load_snapshot(path, scope):
    path = Path(path)
    if not path.exists():
        return empty_snapshot(scope)
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('cannot read valid JSON snapshot {}: {}'.format(path, error))
    if not isinstance(state, dict) or state.get('version') != STATE_VERSION:
        raise ValueError('unsupported or missing state snapshot version in {}'.format(path))
    if state.get('scope') != scope:
        raise ValueError('state snapshot scope does not match this invocation')
    if not isinstance(state.get('pages'), dict):
        raise ValueError('invalid pages object in state snapshot')
    if 'pending_digest' not in state or 'last_digest' not in state:
        raise ValueError('incomplete state snapshot')
    validate_snapshot(state)
    return state


class StateStore:
    """Locked, atomically replaced JSON state snapshot."""

    def __init__(self, path, scope):
        self.path = Path(path)
        self.scope = scope
        self.lock_file = None
        self.state = None
        self.serialized = None

    @property
    def lock_path(self):
        return self.path.with_name(self.path.name + '.lock')

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_file = self.lock_path.open('a+')
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.lock_file.close()
            self.lock_file = None
            raise LockUnavailable('another notifier run owns {}'.format(self.lock_path)) from error
        try:
            self.state = load_snapshot(self.path, self.scope)
            self.serialized = self._serialize(self.state)
        except Exception:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None
            raise
        return self

    @staticmethod
    def _serialize(state):
        return (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode(
            'utf-8'
        )

    def save(self):
        validate_snapshot(self.state)
        data = self._serialize(self.state)
        if data == self.serialized:
            return False
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'wb') as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
            directory_descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        self.serialized = data
        return True

    def __exit__(self, exc_type, exc_value, traceback):
        if self.lock_file is not None:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()


def normalize_template_name(name):
    parsed = mwp.parse(str(name))
    for comment in parsed.filter_comments(recursive=True):
        parsed.remove(comment)
    normalized = ' '.join(str(parsed).replace('_', ' ').strip().split())
    leading_colon = normalized.startswith(':')
    if leading_colon:
        normalized = normalized[1:].lstrip()
    namespace, separator, remainder = normalized.partition(':')
    if separator and namespace.strip().casefold() in {'шаблон', 'template'}:
        return remainder.strip().casefold()
    if leading_colon:
        return (':' + normalized).casefold()
    return normalized.casefold()


def parameter_text(template, name):
    if not template.has(name, ignore_empty=False):
        return ''
    value = mwp.parse(str(template.get(name).value))
    for comment in value.filter_comments(recursive=True):
        value.remove(comment)
    return str(value).strip()


def mediawiki_numeric_one(value):
    value = html.unescape(value)
    numeric = re.fullmatch(
        r'[\x00\x09-\x0d ]*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
        r'(?:[eE][+-]?[0-9]+)?[\x00\x09-\x0d ]*',
        value,
        flags=re.ASCII,
    )
    if numeric is None:
        return False
    try:
        return decimal.Decimal(value) == decimal.Decimal(1)
    except decimal.InvalidOperation:
        return False


def active_request_count(text, aliases, expand_demo=None):
    count = 0
    for template in mwp.parse(text).filter_templates(recursive=True):
        if normalize_template_name(template.name) not in aliases:
            continue
        demo = parameter_text(template, 'демо')
        if expand_demo is not None and '{{' in demo:
            demo = expand_demo(demo)
        if mediawiki_numeric_one(demo):
            continue
        if not parameter_text(template, 2):
            count += 1
    return count


def expand_wikitext(site, page_title, text):
    response = submit_query(site, {
        'action': 'expandtemplates',
        'title': page_title,
        'text': text,
        'prop': 'wikitext',
    })
    expanded = response.get('expandtemplates', {})
    if 'wikitext' in expanded:
        return expanded['wikitext']
    if '*' in expanded:
        return expanded['*']
    raise RetryClassification('MediaWiki did not return expanded template text')


def submit_query(site, parameters):
    return site.simple_request(**parameters).submit()


def response_pages(response):
    pages = response.get('query', {}).get('pages', {})
    return pages.values() if isinstance(pages, dict) else pages


def template_aliases(site):
    titles = [TEMPLATE_TITLE]
    parameters = {
        'action': 'query',
        'prop': 'redirects',
        'titles': TEMPLATE_TITLE,
        'rdnamespace': 10,
        'rdlimit': 'max',
    }
    while True:
        response = submit_query(site, dict(parameters))
        for page in response_pages(response):
            titles.extend(redirect['title'] for redirect in page.get('redirects', []))
        continuation = response.get('continue')
        if not continuation:
            break
        parameters.update(continuation)
    aliases = {normalize_template_name(title) for title in titles}
    return titles, aliases


def category_members(site):
    members = {}
    parameters = {
        'action': 'query',
        'list': 'categorymembers',
        'cmtitle': CATEGORY_TITLE,
        'cmprop': 'ids|title|timestamp|sortkeyprefix',
        'cmlimit': 'max',
    }
    while True:
        response = submit_query(site, parameters)
        for item in response.get('query', {}).get('categorymembers', []):
            member = CategoryMember(
                pageid=int(item['pageid']),
                title=item['title'],
                namespace=int(item['ns']),
                timestamp=parse_timestamp(item['timestamp']),
                sortkeyprefix=item.get('sortkeyprefix', ''),
            )
            members[member.pageid] = member
        continuation = response.get('continue')
        if not continuation:
            return members
        parameters.update(continuation)


def template_inclusions(site, template_titles):
    inclusions = {}
    for template_title in template_titles:
        parameters = {
            'action': 'query',
            'generator': 'embeddedin',
            'geititle': template_title,
            'geilimit': 'max',
            'prop': 'info',
        }
        while True:
            response = submit_query(site, parameters)
            for item in response_pages(response):
                if 'missing' in item:
                    continue
                inclusion = Inclusion(
                    pageid=int(item['pageid']),
                    title=item['title'],
                    namespace=int(item['ns']),
                    lastrevid=int(item.get('lastrevid', 0)),
                    redirect='redirect' in item,
                )
                inclusions[inclusion.pageid] = inclusion
            continuation = response.get('continue')
            if not continuation:
                break
            parameters.update(continuation)
    return inclusions


def revision_text(revision):
    main = revision.get('slots', {}).get('main', {})
    if 'contenthidden' in revision or 'texthidden' in revision or 'contenthidden' in main:
        raise PermanentClassificationError('revision content is hidden')
    if 'content' in main:
        return main['content']
    if '*' in main:
        return main['*']
    raise PermanentClassificationError(
        'revision {} has no readable main-slot content'.format(revision.get('revid', 'unknown'))
    )


def fetch_page_revision(site, inclusion):
    response = submit_query(site, {
        'action': 'query',
        'prop': 'revisions',
        'pageids': inclusion.pageid,
        'rvprop': 'ids|timestamp|content',
        'rvslots': 'main',
        'rvlimit': 1,
    })
    pages = list(response_pages(response))
    if len(pages) != 1 or 'missing' in pages[0]:
        raise RetryClassification('page disappeared while its inclusion was inspected')
    revisions = pages[0].get('revisions', [])
    if not revisions or int(revisions[0]['revid']) != inclusion.lastrevid:
        raise RetryClassification('page changed while its inclusion was inspected')
    return revisions[0]


def fetch_page_text(site, inclusion):
    return revision_text(fetch_page_revision(site, inclusion))


def history_revisions(site, member, start=None):
    parameters = {
        'action': 'query',
        'prop': 'revisions',
        'pageids': member.pageid,
        'rvprop': 'ids|timestamp|user|userid|content',
        'rvslots': 'main',
        'rvstart': serialize_timestamp((start or member.timestamp) + HISTORY_START_SLACK),
        'rvdir': 'older',
        'rvlimit': 2,
    }
    seen = 0
    while seen < MAX_HISTORY_REVISIONS:
        response = submit_query(site, dict(parameters))
        pages = list(response_pages(response))
        if len(pages) != 1 or 'missing' in pages[0]:
            raise RetryClassification('category member disappeared during classification')
        for revision in pages[0].get('revisions', []):
            if seen >= MAX_HISTORY_REVISIONS:
                raise PermanentClassificationError('history search exceeded its revision limit')
            seen += 1
            yield revision
        continuation = response.get('continue')
        if not continuation:
            return
        if seen >= MAX_HISTORY_REVISIONS:
            raise PermanentClassificationError('history search exceeded its revision limit')
        parameters.update(continuation)
        parameters['rvlimit'] = min(5, MAX_HISTORY_REVISIONS - seen)


def find_introduction_revision(site, member, aliases, start=None):
    newer_active = None
    saw_revision = False
    for revision in history_revisions(site, member, start=start):
        saw_revision = True
        text = revision_text(revision)
        if active_request_count(
                text,
                aliases,
                expand_demo=lambda value: expand_wikitext(site, member.title, value)):
            newer_active = revision
            if not int(revision.get('parentid', 0)):
                break
            continue
        if newer_active is None:
            raise RetryClassification('history window begins before an active request')
        break
    if not saw_revision or newer_active is None:
        raise RetryClassification('could not locate an active request near the category timestamp')
    if 'userhidden' in newer_active or 'user' not in newer_active:
        raise PermanentClassificationError('the introducing revision author is hidden')
    if 'userid' not in newer_active:
        raise PermanentClassificationError('the introducing revision has no registered user ID')
    return newer_active


def account_info(site, username):
    response = submit_query(site, {
        'action': 'query',
        'list': 'users',
        'ususers': username,
        'usprop': 'blockinfo',
    })
    users = response.get('query', {}).get('users', [])
    if len(users) != 1 or 'missing' in users[0] or 'invalid' in users[0]:
        raise PermanentClassificationError('the page owner is not a registered account')
    user = users[0]
    if 'userid' not in user:
        raise PermanentClassificationError('the page owner has no user ID')
    blocked = 'blockedby' in user or 'blockid' in user
    return int(user['userid']), blocked


def is_own_base_talk_page(site, title, namespace, username):
    if namespace != 3 or '/' in username:
        return False
    return pwb.User(site, username).getUserTalkPage().title() == title


def classify_category_page(site, member, inclusion, aliases, expected_blocked, now):
    base = {
        'kind': 'category',
        'title': member.title,
        'namespace': member.namespace,
        'category_timestamp': serialize_timestamp(member.timestamp),
        'sortkeyprefix': member.sortkeyprefix,
        'lastrevid': inclusion.lastrevid if inclusion else None,
        'included': inclusion is not None,
        'first_seen': serialize_timestamp(now),
        'reported': False,
    }
    if inclusion is None:
        return {**base, 'status': 'anomaly', 'reason': 'category member has no transclusion'}
    if (member.title, member.namespace) != (inclusion.title, inclusion.namespace):
        raise RetryClassification('page identity changed between discovery queries')
    if inclusion.redirect:
        return {**base, 'status': 'anomaly', 'reason': 'the user-talk page is a redirect'}
    username = member.title.partition(':')[2]
    if pwb.tools.is_ip_address(username):
        return {**base, 'status': 'anomaly', 'reason': 'IP talk pages are outside the workflow'}
    if not is_own_base_talk_page(site, member.title, member.namespace, username):
        return {**base, 'status': 'anomaly', 'reason': 'not the owner\'s base user-talk page'}
    current_revision = fetch_page_revision(site, inclusion)
    text = revision_text(current_revision)
    if not active_request_count(
            text,
            aliases,
            expand_demo=lambda value: expand_wikitext(site, member.title, value)):
        return {**base, 'status': 'anomaly', 'reason': 'no active request in current text'}
    try:
        introduction = find_introduction_revision(site, member, aliases)
    except RetryClassification:
        current_timestamp = parse_timestamp(current_revision['timestamp'])
        if current_timestamp <= member.timestamp + HISTORY_START_SLACK:
            raise
        introduction = find_introduction_revision(
            site, member, aliases, start=current_timestamp
        )
    owner_userid, blocked = account_info(site, username)
    author_userid = int(introduction['userid'])
    if author_userid != owner_userid:
        return {
            **base,
            'status': 'anomaly',
            'reason': 'request introduced by another account',
            'intro_revid': int(introduction['revid']),
        }
    record = {
        **base,
        'username': username,
        'owner_userid': owner_userid,
        'intro_revid': int(introduction['revid']),
        'intro_timestamp': serialize_timestamp(parse_timestamp(introduction['timestamp'])),
        'intro_user': introduction['user'],
    }
    if blocked == expected_blocked:
        return {**record, 'status': 'ready', 'reason': None}
    expectation = 'blocked' if expected_blocked else 'unblocked'
    return {
        **record,
        'status': 'waiting',
        'reason': 'requester is not currently {}'.format(expectation),
    }


def request_from_record(pageid, record):
    return Request(
        page_title=record['title'],
        username=record['username'],
        pageid=int(pageid),
        revision_id=int(record['intro_revid']),
        timestamp=parse_timestamp(record['intro_timestamp']),
    )


def refresh_waiting(site, record, expected_blocked):
    owner_userid, blocked = account_info(site, record['username'])
    if owner_userid != int(record['owner_userid']):
        record['status'] = 'anomaly'
        record['reason'] = 'the page owner account changed unexpectedly'
    elif blocked == expected_blocked:
        record['status'] = 'ready'
        record['reason'] = None


def revalidate_ready(site, record, expected_blocked):
    owner_userid, blocked = account_info(site, record['username'])
    if owner_userid != int(record['owner_userid']):
        record['status'] = 'anomaly'
        record['reason'] = 'the page owner account changed unexpectedly'
        return False
    if blocked != expected_blocked:
        record['status'] = 'waiting'
        expectation = 'blocked' if expected_blocked else 'unblocked'
        record['reason'] = 'requester is not currently {}'.format(expectation)
        return False
    return True


def record_source(record):
    return (
        record.get('title'),
        record.get('category_timestamp'),
        record.get('lastrevid'),
        record.get('included'),
    )


def current_source(member, inclusion):
    return (
        member.title if member else inclusion.title,
        serialize_timestamp(member.timestamp) if member else None,
        inclusion.lastrevid if inclusion else None,
        inclusion is not None,
    )


def preserve_episode_state(old, new):
    if not old:
        return new
    if old.get('intro_revid') != new.get('intro_revid'):
        return new
    if old.get('intro_revid') is None and (
            old.get('kind') != new.get('kind')
            or old.get('title') != new.get('title')
            or old.get('category_timestamp') != new.get('category_timestamp')):
        return new
    new['first_seen'] = old.get('first_seen', new['first_seen'])
    new['reported'] = old.get('reported', False)
    if old.get('status') == 'notified' and new.get('status') == 'ready':
        new['status'] = 'notified'
        new['notification_revid'] = old.get('notification_revid')
    return new


def make_stray_record(site, inclusion, aliases, old, now):
    if inclusion.title in EXPECTED_ANOMALY_TITLES:
        return None
    if (
            old and old.get('kind') == 'stray'
            and old.get('lastrevid') == inclusion.lastrevid
            and old.get('status') != 'unclassified'):
        old['title'] = inclusion.title
        old['namespace'] = inclusion.namespace
        return old
    text = fetch_page_text(site, inclusion)
    if not active_request_count(
            text,
            aliases,
            expand_demo=lambda value: expand_wikitext(site, inclusion.title, value)):
        return {
            'kind': 'stray',
            'title': inclusion.title,
            'namespace': inclusion.namespace,
            'category_timestamp': None,
            'lastrevid': inclusion.lastrevid,
            'included': True,
            'status': 'inactive',
            'reason': None,
        }
    if (
            old and old.get('kind') == 'stray'
            and old.get('title') == inclusion.title
            and old.get('lastrevid') == inclusion.lastrevid
            and old.get('status') in {'anomaly', 'unclassified'}):
        first_seen = old.get('first_seen', serialize_timestamp(now))
        reported = old.get('reported', False)
    else:
        first_seen = serialize_timestamp(now)
        reported = False
    return {
        'kind': 'stray',
        'title': inclusion.title,
        'namespace': inclusion.namespace,
        'category_timestamp': None,
        'lastrevid': inclusion.lastrevid,
        'included': True,
        'status': 'anomaly',
        'reason': 'active transclusion outside the normal request workflow',
        'first_seen': first_seen,
        'reported': reported,
    }


def revisions_by_user_since(site, page_title, expected_user, since):
    parameters = {
        'action': 'query',
        'prop': 'revisions',
        'titles': page_title,
        'rvprop': 'ids|timestamp|user|comment',
        'rvuser': expected_user,
        'rvend': serialize_timestamp(since),
        'rvlimit': 'max',
    }
    while True:
        response = submit_query(site, parameters)
        for page in response_pages(response):
            for revision in page.get('revisions', []):
                yield revision
        continuation = response.get('continue')
        if not continuation:
            break
        parameters.update(continuation)


def revision_content(site, page_title, revision_id):
    response = submit_query(site, {
        'action': 'query',
        'prop': 'revisions',
        'titles': page_title,
        'rvprop': 'ids|content',
        'rvslots': 'main',
        'rvstartid': revision_id,
        'rvendid': revision_id,
    })
    for page in response_pages(response):
        revisions = page.get('revisions', [])
        if revisions:
            return revision_text(revisions[0])
    raise RetryClassification('could not read destination revision {}'.format(revision_id))


def find_section_addition(site, page_title, expected_user, summary_terms, marker, since):
    for revision in revisions_by_user_since(site, page_title, expected_user, since):
        comment = revision.get('comment') or ''
        if not all(term in comment for term in summary_terms):
            continue
        revision_id = int(revision['revid'])
        text = revision_content(site, page_title, revision_id)
        if marker not in text:
            continue
        parent_id = int(revision.get('parentid', 0))
        parent_text = revision_content(site, page_title, parent_id) if parent_id else ''
        if marker not in parent_text:
            return revision_id
    return None


def find_existing_notification(site, page_title, expected_user, request):
    return find_section_addition(
        site,
        page_title,
        expected_user,
        ['Заявка за отблокиране'],
        request.marker,
        request.timestamp - RECOVERY_START_SLACK,
    )


def assert_write_identity(site, expected_user):
    site.login()
    actual_user = site.user()
    if actual_user != expected_user:
        raise RuntimeError(
            'refusing to write as {!r}; expected {!r}'.format(actual_user, expected_user)
        )


def ensure_destination(site, page_title):
    response = submit_query(site, {'action': 'query', 'prop': 'info', 'titles': page_title})
    pages = list(response_pages(response))
    if len(pages) != 1 or 'missing' in pages[0]:
        raise ValueError('destination page does not exist: {}'.format(page_title))
    if 'redirect' in pages[0]:
        raise ValueError('destination page is a redirect: {}'.format(page_title))
    return int(pages[0]['lastrevid'])


def post_new_section(site, page_title, heading, message, expected_user):
    assert_write_identity(site, expected_user)
    base_revision = ensure_destination(site, page_title)
    parameters = {
        'action': 'edit', 'title': page_title, 'section': 'new',
        'sectiontitle': heading, 'text': message, 'token': site.tokens['csrf'],
        'notminor': True, 'nocreate': True, 'watchlist': 'nochange',
        'baserevid': base_revision, 'assertuser': expected_user,
        'maxlag': 5, 'assert': 'user',
    }
    try:
        response = api.Request(site=site, parameters=parameters, max_retries=0).submit()
    except APIMWError as error:
        raise AmbiguousWriteError('wiki edit outcome is unknown') from error
    except APIError as error:
        if (
                error.code == 'readonly' or error.code == 'maxlag'
                or error.code.startswith('internal_api_error_')):
            raise AmbiguousWriteError('wiki edit outcome is unknown') from error
        raise DefiniteWriteError(error.code) from error
    except Exception as error:
        raise AmbiguousWriteError('wiki edit outcome is unknown') from error
    edit = response.get('edit', {})
    if edit.get('result') != 'Success' or 'newrevid' not in edit:
        raise AmbiguousWriteError('ambiguous edit response: {!r}'.format(response))
    return int(edit['newrevid'])


def request_is_current(site, request, expected_lastrevid):
    response = submit_query(site, {
        'action': 'query', 'prop': 'info|categories', 'pageids': request.pageid,
        'clcategories': CATEGORY_TITLE, 'cllimit': 'max',
    })
    pages = list(response_pages(response))
    if len(pages) != 1 or 'missing' in pages[0]:
        return False
    page = pages[0]
    categories = {item['title'] for item in page.get('categories', [])}
    return (
        int(page.get('lastrevid', 0)) == expected_lastrevid
        and page.get('title') == request.page_title
        and int(page.get('ns', -1)) == 3
        and CATEGORY_TITLE in categories
    )


def notify_record(
        site, record, pageid, notification_page, expected_user, write,
        expected_blocked, eligibility_checked=False):
    request = request_from_record(pageid, record)
    existing = find_existing_notification(site, notification_page, expected_user, request)
    if existing:
        record['status'] = 'notified'
        record['notification_revid'] = existing
        return 'recovered'
    if not eligibility_checked and not revalidate_ready(site, record, expected_blocked):
        return 'no-longer-eligible'
    if not request_is_current(site, request, int(record['lastrevid'])):
        return 'changed-before-write'
    if not write:
        return 'would-notify'
    revision = post_new_section(
        site, notification_page, request.heading, request.message, expected_user
    )
    record['status'] = 'notified'
    record['notification_revid'] = revision
    return 'notified'


def anomaly_message(records):
    shown = records[:MAX_ANOMALY_LINKS]
    lines = [
        'При периодичната проверка намерих активни включвания на',
        '{{ш|Отблокиране}}, които не отговарят на условията за обичайна заявка:',
        '',
    ]
    lines.extend('* [[:{title}]]'.format(**record) for record in shown)
    if len(records) > len(shown):
        lines.append('* и още {} страници'.format(len(records) - len(shown)))
    lines.extend(['', '~~~~'])
    return '\n'.join(lines)


def digest_heading(now):
    return 'Неочаквани включвания на „Отблокиране“ ({})'.format(
        now.strftime('%Y-%m-%d %H:%M UTC')
    )


def finish_digest(state, pending, revision_id):
    for item in pending['pages']:
        record = state['pages'].get(str(item['pageid']))
        if record and record.get('first_seen') == item['first_seen']:
            record['reported'] = True
    state['last_digest'] = pending['created_at']
    state['pending_digest'] = None
    return revision_id


def recover_pending_digest(site, store, report_page, expected_user):
    pending = store.state.get('pending_digest')
    if not pending:
        return False
    existing = find_section_addition(
        site, report_page, expected_user, [pending['heading']], pending['marker'],
        parse_timestamp(pending['created_at']) - RECOVERY_START_SLACK,
    )
    if existing:
        finish_digest(store.state, pending, existing)
        store.save()
        return True
    store.state['pending_digest'] = None
    store.save()
    return False


def report_anomalies(site, store, args, now):
    if not args.anomaly_report_page:
        return 0
    recover_pending_digest(site, store, args.anomaly_report_page, args.expected_user)
    last_digest = store.state.get('last_digest')
    if last_digest and now - parse_timestamp(last_digest) < dt.timedelta(hours=24):
        return 0
    minimum_age = dt.timedelta(minutes=args.anomaly_min_age_minutes)
    reportable = []
    for pageid, record in store.state['pages'].items():
        if record.get('status') not in {'anomaly', 'waiting', 'unclassified'}:
            continue
        if record.get('reported'):
            continue
        if now - parse_timestamp(record['first_seen']) < minimum_age:
            continue
        reportable.append({
            'pageid': int(pageid), 'title': record['title'],
            'reason': record.get('reason', 'не може да бъде класифицирано'),
            'first_seen': record['first_seen'],
        })
    reportable.sort(key=lambda item: item['title'])
    if not reportable:
        return 0
    ensure_destination(site, args.anomaly_report_page)
    heading = digest_heading(now)
    pending = {
        'created_at': serialize_timestamp(now), 'heading': heading,
        'marker': '== {} =='.format(heading),
        'pages': [
            {'pageid': item['pageid'], 'first_seen': item['first_seen']}
            for item in reportable
        ],
    }
    store.state['pending_digest'] = pending
    store.save()
    revision = post_new_section(
        site, args.anomaly_report_page, heading, anomaly_message(reportable), args.expected_user
    )
    finish_digest(store.state, pending, revision)
    store.save()
    return len(reportable)


def report_anomalies_dry_run(state, args, now):
    minimum_age = dt.timedelta(minutes=args.anomaly_min_age_minutes)
    return [
        record for pageid, record in state['pages'].items()
        if record.get('status') in {'anomaly', 'waiting', 'unclassified'}
        and not record.get('reported')
        and now - parse_timestamp(record['first_seen']) >= minimum_age
    ]


def same_category_episode(old, member):
    return bool(
        old and old.get('kind') == 'category'
        and old.get('title') == member.title
        and old.get('category_timestamp') == serialize_timestamp(member.timestamp)
    )


def classification_failure_record(member, inclusion, old, now, status, reason):
    same_episode = same_category_episode(old, member)
    return {
        'kind': 'category', 'title': member.title, 'namespace': member.namespace,
        'category_timestamp': serialize_timestamp(member.timestamp),
        'sortkeyprefix': member.sortkeyprefix,
        'lastrevid': inclusion.lastrevid if inclusion else None,
        'included': inclusion is not None, 'status': status, 'reason': reason,
        'first_seen': old.get('first_seen', serialize_timestamp(now))
        if same_episode else serialize_timestamp(now),
        'reported': old.get('reported', False) if same_episode else False,
    }


def stray_classification_failure_record(inclusion, old, now, reason):
    same_source = bool(
        old and old.get('kind') == 'stray'
        and old.get('title') == inclusion.title
        and old.get('lastrevid') == inclusion.lastrevid
    )
    return {
        'kind': 'stray', 'title': inclusion.title, 'namespace': inclusion.namespace,
        'category_timestamp': None, 'lastrevid': inclusion.lastrevid,
        'included': True, 'status': 'unclassified', 'reason': reason,
        'first_seen': old.get('first_seen', serialize_timestamp(now))
        if same_source else serialize_timestamp(now),
        'reported': old.get('reported', False) if same_source else False,
    }


def reconcile_world(site, state, members, inclusions, aliases, expected_blocked, now, args):
    failures = []
    fresh_eligibility = set()
    current_ids = set(members) | set(inclusions)
    for stale_pageid in set(state['pages']) - {str(pageid) for pageid in current_ids}:
        del state['pages'][stale_pageid]

    for pageid in sorted(current_ids):
        key = str(pageid)
        member = members.get(pageid)
        inclusion = inclusions.get(pageid)
        old = state['pages'].get(key)
        try:
            if member is None:
                new = make_stray_record(site, inclusion, aliases, old, now)
                if new is None:
                    state['pages'].pop(key, None)
                else:
                    state['pages'][key] = new
                continue

            changed = old is None or record_source(old) != current_source(member, inclusion)
            if old and old.get('status') == 'notified':
                identity_changed = (
                    old.get('title') != member.title
                    or old.get('category_timestamp') != serialize_timestamp(member.timestamp)
                )
                if not identity_changed:
                    continue
            elif old and old.get('status') == 'waiting' and not changed:
                refresh_waiting(site, old, expected_blocked)
                fresh_eligibility.add(key)
                continue
            elif old and old.get('status') not in {'unclassified', 'ready'} and not changed:
                continue
            elif old and old.get('status') == 'ready' and not changed:
                continue

            try:
                new = classify_category_page(
                    site, member, inclusion, aliases, expected_blocked, now
                )
            except PermanentClassificationError as error:
                new = classification_failure_record(
                    member, inclusion, old, now, 'anomaly', str(error)
                )
            except RetryClassification as error:
                new = classification_failure_record(
                    member, inclusion, old, now, 'unclassified', str(error)
                )
            state['pages'][key] = preserve_episode_state(old, new)
            if new.get('status') in {'ready', 'waiting'}:
                fresh_eligibility.add(key)
        except Exception as error:
            logging.exception('Failed to classify %s', (member or inclusion).title)
            failures.append('{}: {}'.format((member or inclusion).title, error))
            if member is not None:
                state['pages'][key] = classification_failure_record(
                    member, inclusion, old, now, 'unclassified', str(error)
                )
            else:
                state['pages'][key] = stray_classification_failure_record(
                    inclusion, old, now, str(error)
                )

    for pageid, record in sorted(state['pages'].items()):
        if record.get('status') != 'ready':
            continue
        try:
            result = notify_record(
                site, record, pageid, args.notification_page,
                args.expected_user, args.write, expected_blocked,
                eligibility_checked=pageid in fresh_eligibility,
            )
            print('{}:{}: {}'.format(pageid, record['intro_revid'], result))
        except AmbiguousWriteError:
            raise
        except DefiniteWriteError as error:
            record['status'] = 'unclassified'
            record['reason'] = 'administrator notification refused ({})'.format(error.code)
            logging.exception('Notification refused for %s', record.get('title'))
            failures.append('{}: {}'.format(record.get('title'), error))
        except Exception as error:
            logging.exception('Failed to notify for %s', record.get('title'))
            failures.append('{}: {}'.format(record.get('title'), error))
    return failures


def validate_test_target(site, page_title, expected_user):
    page = pwb.Page(site, page_title)
    if page.namespace() not in {2, 3}:
        raise ValueError('test target must be in the expected user\'s own user space')
    local_title = page.title(with_ns=False)
    if local_title != expected_user and not local_title.startswith(expected_user + '/'):
        raise ValueError('test target must be in the expected user\'s own user space')


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Notify bgwiki administrators about new unblock requests.'
    )
    parser.add_argument(
        '--write', action='store_true',
        help='enable wiki writes and atomic state-snapshot updates (default: dry run)',
    )
    parser.add_argument('--expected-user', default=DEFAULT_EXPECTED_USER)
    parser.add_argument('--notification-page', default=DEFAULT_NOTIFICATION_PAGE)
    parser.add_argument('--state-file', type=Path)
    parser.add_argument(
        '--anomaly-report-page',
        help='optional page for one-time reports about unexpected active transclusions',
    )
    parser.add_argument('--anomaly-min-age-minutes', type=int, default=30)
    parser.add_argument(
        '--test-unblocked-requests', action='store_true',
        help=(
            'run the complete workflow for otherwise eligible unblocked users; '
            'destinations must be in --expected-user user space'
        ),
    )
    args = parser.parse_args(argv[1:])
    if args.test_unblocked_requests and not args.write:
        parser.error('--test-unblocked-requests requires --write')
    if args.anomaly_min_age_minutes < 0:
        parser.error('--anomaly-min-age-minutes cannot be negative')
    if args.state_file is None:
        args.state_file = (
            default_test_state_file() if args.test_unblocked_requests else default_state_file()
        )
    return args


def run(args):
    site = pwb.Site(code=SITE_CODE, fam=SITE_FAMILY)
    if args.test_unblocked_requests:
        validate_test_target(site, args.notification_page, args.expected_user)
        if args.anomaly_report_page:
            validate_test_target(site, args.anomaly_report_page, args.expected_user)

    template_titles, aliases = template_aliases(site)
    members = category_members(site)
    inclusions = template_inclusions(site, template_titles)
    scope = state_scope(args)
    expected_blocked = not args.test_unblocked_requests
    now = utc_now()

    if not args.write:
        state = copy.deepcopy(load_snapshot(args.state_file, scope))
        failures = reconcile_world(
            site, state, members, inclusions, aliases, expected_blocked, now, args
        )
        anomalies = report_anomalies_dry_run(state, args, now)
        for record in anomalies:
            print('ANOMALY: {} — {}'.format(record['title'], record['reason']))
        print(
            'Read-only dry run: {} category member(s), {} inclusion(s), '
            '{} reportable anomaly/anomalies.'.format(
                len(members), len(inclusions), len(anomalies)
            )
        )
        return 1 if failures else 0

    try:
        with StateStore(args.state_file, scope) as store:
            ambiguous_write = None
            try:
                failures = reconcile_world(
                    site, store.state, members, inclusions, aliases,
                    expected_blocked, now, args,
                )
            except AmbiguousWriteError as error:
                ambiguous_write = error
                failures = ['ambiguous wiki write: {}'.format(error)]
                logging.exception('Stopping after an ambiguous wiki write')
            store.save()
            if ambiguous_write is None:
                try:
                    reported = report_anomalies(site, store, args, now)
                    if reported:
                        print('Reported {} unexpected transclusion(s).'.format(reported))
                except Exception as error:
                    logging.exception('Failed to report unexpected transclusions')
                    failures.append('unexpected transclusions: {}'.format(error))
            store.save()
    except LockUnavailable as error:
        logging.info('%s; exiting without work', error)
        return 0

    for failure in failures:
        print('ERROR: {}'.format(failure), file=sys.stderr)
    return 1 if failures else 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    return run(parse_args(argv or sys.argv))


if __name__ == '__main__':
    sys.exit(main())


# vim: set ts=4 sts=4 sw=4 tw=100 et:
