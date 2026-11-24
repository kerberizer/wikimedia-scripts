import argparse
import datetime
import sys

import pywikibot

from .actions import (
    add_incubator_template,
    apply_maintenance_actions,
    planned_actions,
    save_page_if_changed,
)
from .collect import iter_records
from .config import Config
from .identity import assert_write_identity
from .metadata import enrich_queue_record
from .queues import render_queue_preview
from .render import render_list


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Maintain the Bulgarian Wikipedia Incubator draft list.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="read the wiki and print the planned actions and generated list without saving",
    )
    mode.add_argument(
        "--preview-queues",
        action="store_true",
        help="read the wiki and print local review, help, diagnostic, and long-stay queues",
    )
    return parser.parse_args(argv[1:])


def run(dry_run=False, preview_queues=False, config=None):
    config = config or Config()
    start_time = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    site = pywikibot.Site(code=config.site_code, fam=config.site_family)
    read_only = dry_run or preview_queues
    if not read_only:
        assert_write_identity(site, config.expected_username)
    list_page = pywikibot.Page(site, config.list_page_name)
    records = []

    for article, record in iter_records(site, start_time, config):
        if record.needs_incubator_template and not read_only:
            add_incubator_template(article)
        if preview_queues:
            record = enrich_queue_record(article, record)
        records.append(record)

    if preview_queues:
        print(render_queue_preview(records))
        return 0

    list_text = render_list(records, config)
    if dry_run:
        for action in planned_actions(records, config):
            print(f"DRY-RUN: {action}", file=sys.stderr)
        print(list_text)
        return 0

    apply_maintenance_actions(site, records, config)
    save_page_if_changed(
        list_page,
        list_text,
        summary="Бот: актуализация на списъка",
    )
    return 0


def main(argv=None):
    argv = argv or sys.argv
    args = parse_args(argv)
    return run(dry_run=args.dry_run, preview_queues=args.preview_queues)
