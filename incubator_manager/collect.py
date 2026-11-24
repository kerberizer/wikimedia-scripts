import datetime
import re

from .classify import classify_age, inspect_templates, is_broken_redirect
from .model import DraftRecord

REDIRECT_TIMESTAMP = "0000-00-00T00:00:00Z"


def page_move_pattern(config):
    return re.compile(
        r".+ премести страница(та)? „\[\[.+?\]\]“ като „\[\["
        + "(?:"
        + config.article_fullprefix
        + "|"
        + config.legacy_article_fullprefix
        + ")"
    )


def find_entered_timestamp(revisions, start_time, config):
    move_pattern = page_move_pattern(config)
    oldest_timestamp_to_check = start_time - datetime.timedelta(
        days=config.search_moves_days
    )
    for revision in revisions:
        if revision["timestamp"] < oldest_timestamp_to_check:
            break
        if move_pattern.match(revision["comment"]):
            if revision["user"] == "Kerberizer":
                continue
            return revision["timestamp"]
    return None


def collect_record(article, start_time, config):
    oldest_revision = article.oldest_revision

    if article.isRedirectPage():
        return DraftRecord(
            fullname=article.title(),
            timestamp=REDIRECT_TIMESTAMP,
            author=oldest_revision.user,
            is_redirect=True,
        )

    text = article.text
    if is_broken_redirect(text):
        return DraftRecord(
            fullname=article.title(),
            timestamp=REDIRECT_TIMESTAMP,
            author=oldest_revision.user,
            is_redirect=True,
        )

    timestamp = find_entered_timestamp(article.revisions(), start_time, config)
    if timestamp is None:
        timestamp = oldest_revision.timestamp

    template_state = inspect_templates(text)
    age_status = classify_age(start_time - timestamp, config)
    return DraftRecord(
        fullname=article.title(),
        timestamp=timestamp,
        author=oldest_revision.user,
        age_status=age_status,
        reviewer=template_state.reviewer,
        reviewer_diagnostic=template_state.reviewer_diagnostic,
        review_template_name=template_state.review_template_name,
        legacy_reviewer_parameter=template_state.legacy_reviewer_parameter,
        review_requested=template_state.review_requested,
        help_requested=template_state.help_requested,
        needs_incubator_template=not template_state.has_incubator_template,
    )


def iter_records(site, start_time, config):
    for article in site.allpages(
        prefix=config.article_prefix,
        namespace=config.article_namespace,
    ):
        yield article, collect_record(article, start_time, config)
