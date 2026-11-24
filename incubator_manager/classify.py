import datetime
import re

import mwparserfromhell

from .model import MALFORMED_REVIEW_TEMPLATE, TemplateState

BROKEN_REDIRECT_PATTERN = re.compile(
    r"\n#(?:пренасочване|redirect)\s*\[\[",
    flags=re.IGNORECASE,
)
BOT_DELETION_PATTERN = re.compile(r"{{delete\|1=Бот:", flags=re.IGNORECASE)


def is_broken_redirect(text):
    return BROKEN_REDIRECT_PATTERN.search(text) is not None


def is_proposed_for_deletion(text):
    return BOT_DELETION_PATTERN.search(text) is not None


def classify_age(elapsed, config):
    if elapsed > datetime.timedelta(days=config.force_delete_days):
        return "delete"
    if elapsed > datetime.timedelta(days=config.critical_days):
        return "critical"
    if elapsed > datetime.timedelta(days=config.warning_days):
        return "warning"
    return "normal"


def inspect_templates(text):
    parsed = mwparserfromhell.parse(text)

    has_incubator_template = False
    review_requested = False
    help_requested = False
    reviewer = ""

    for template in parsed.filter_templates():
        if template.name.matches("в инкубатора"):
            has_incubator_template = True
        elif template.name.matches("инкубатор-проверка"):
            review_requested = True
            if template.params:
                try:
                    reviewer = str(template.get(1).value)
                except ValueError:
                    reviewer = MALFORMED_REVIEW_TEMPLATE
        elif template.name.matches("помощ"):
            help_requested = True

    return TemplateState(
        has_incubator_template=has_incubator_template,
        review_requested=review_requested,
        help_requested=help_requested,
        reviewer=reviewer,
    )


def legacy_timestamp_text(value):
    return str(value).replace("T", " ")[:-1]
