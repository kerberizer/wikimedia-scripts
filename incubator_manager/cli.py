import argparse
import datetime
import sys

import pywikibot

from .actions import add_incubator_template, apply_maintenance_actions, planned_actions
from .collect import iter_records
from .config import Config
from .render import render_list


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Maintain the Bulgarian Wikipedia Incubator draft list.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the wiki and print the planned actions and generated list without saving",
    )
    return parser.parse_args(argv[1:])


def run(dry_run=False, config=None):
    config = config or Config()
    start_time = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    site = pywikibot.Site(code=config.site_code, fam=config.site_family)
    list_page = pywikibot.Page(site, config.list_page_name)
    records = []

    for article, record in iter_records(site, start_time, config):
        if record.needs_incubator_template and not dry_run:
            add_incubator_template(article)
        records.append(record)

    list_text = render_list(records, config)
    if dry_run:
        for action in planned_actions(records, config):
            print(f"DRY-RUN: {action}", file=sys.stderr)
        print(list_text)
        return 0

    apply_maintenance_actions(site, records, config)
    list_page.text = list_text
    list_page.save(summary="Бот: актуализация на списъка", quiet=True)
    return 0


def main(argv=None):
    argv = argv or sys.argv
    args = parse_args(argv)
    return run(dry_run=args.dry_run)
