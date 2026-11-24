import datetime
import re

import mwparserfromhell

from .model import (
    MALFORMED_REVIEW_TEMPLATE,
    POSITIONAL_REVIEW_PARAMETER,
    TemplateState,
)

CANONICAL_REVIEW_TEMPLATE = "чернова за преглед"
LEGACY_REVIEW_TEMPLATE = "инкубатор-проверка"
BASE_TEMPLATE_NAMES = ("в инкубатора", "чернова")
REVIEW_TEMPLATE_NAMES = (LEGACY_REVIEW_TEMPLATE, CANONICAL_REVIEW_TEMPLATE)
HELP_TEMPLATE_NAMES = ("помощ", "чернова за помощ")

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
    if elapsed > datetime.timedelta(days=config.long_stay_days):
        return "long_stay"
    if elapsed > datetime.timedelta(days=config.critical_days):
        return "critical"
    if elapsed > datetime.timedelta(days=config.warning_days):
        return "warning"
    return "normal"


def matches_any(template, names):
    return next((name for name in names if template.name.matches(name)), None)


def inspect_reviewer(template, matched_name):
    parameters = {
        str(parameter.name).strip(): parameter for parameter in template.params
    }
    unknown_parameters = set(parameters) - {"1", "преглеждащ"}
    named_reviewer = parameters.get("преглеждащ")
    positional_reviewer = parameters.get("1")

    if matched_name == CANONICAL_REVIEW_TEMPLATE:
        if unknown_parameters or positional_reviewer is not None:
            return "", POSITIONAL_REVIEW_PARAMETER, False
        if named_reviewer is not None:
            return str(named_reviewer.value).strip(), "", False
        return "", "", False

    if unknown_parameters:
        return "", MALFORMED_REVIEW_TEMPLATE, False
    if named_reviewer is not None and positional_reviewer is not None:
        return "", MALFORMED_REVIEW_TEMPLATE, False
    if named_reviewer is not None:
        return str(named_reviewer.value).strip(), "", False
    if positional_reviewer is not None:
        return str(positional_reviewer.value).strip(), "", True
    return "", "", False


def inspect_templates(text):
    parsed = mwparserfromhell.parse(text)

    has_incubator_template = False
    review_requested = False
    help_requested = False
    reviewer = ""
    reviewer_diagnostic = ""
    review_template_name = ""
    legacy_reviewer_parameter = False

    for template in parsed.filter_templates():
        base_name = matches_any(template, BASE_TEMPLATE_NAMES)
        review_name = matches_any(template, REVIEW_TEMPLATE_NAMES)
        help_name = matches_any(template, HELP_TEMPLATE_NAMES)

        if base_name:
            has_incubator_template = True
        elif review_name:
            review_requested = True
            review_template_name = review_name
            current_reviewer, current_diagnostic, legacy_parameter = inspect_reviewer(
                template, review_name
            )
            if current_reviewer:
                reviewer = current_reviewer
            if current_diagnostic:
                reviewer_diagnostic = current_diagnostic
            legacy_reviewer_parameter = legacy_reviewer_parameter or legacy_parameter
            if review_name == CANONICAL_REVIEW_TEMPLATE:
                has_incubator_template = True
        elif help_name:
            help_requested = True
            if help_name == "чернова за помощ":
                has_incubator_template = True

    return TemplateState(
        has_incubator_template=has_incubator_template,
        review_requested=review_requested,
        help_requested=help_requested,
        reviewer=reviewer,
        reviewer_diagnostic=reviewer_diagnostic,
        review_template_name=review_template_name,
        legacy_reviewer_parameter=legacy_reviewer_parameter,
    )


def legacy_timestamp_text(value):
    return str(value).replace("T", " ")[:-1]
