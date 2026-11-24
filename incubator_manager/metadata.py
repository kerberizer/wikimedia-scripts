from dataclasses import dataclass, replace

from .classify import inspect_templates


@dataclass(frozen=True)
class RequestTimestamps:
    review_request: object = None
    reviewer_claim: object = None
    reviewer_claim_user: str = ""
    help_request: object = None


def needs_queue_metadata(record):
    return (
        record.review_requested
        or record.help_requested
        or record.age_status == "long_stay"
    )


def find_current_request_timestamps(revisions, record):
    review_open = record.review_requested
    claim_open = record.review_state == "claimed"
    help_open = record.help_requested
    review_timestamp = None
    claim_timestamp = None
    claim_user = ""
    help_timestamp = None

    for revision in revisions:
        if not any((review_open, claim_open, help_open)):
            break

        text = revision.get("text")
        if text is None:
            if review_open:
                review_timestamp = None
            if claim_open:
                claim_timestamp = None
                claim_user = ""
            if help_open:
                help_timestamp = None
            break

        state = inspect_templates(text)
        timestamp = revision["timestamp"]

        if review_open:
            if state.review_requested:
                review_timestamp = timestamp
            else:
                review_open = False

        if claim_open:
            if (
                state.review_requested
                and not state.reviewer_diagnostic
                and state.reviewer == record.reviewer
            ):
                claim_timestamp = timestamp
                claim_user = revision.get("user", "")
            else:
                claim_open = False

        if help_open:
            if state.help_requested:
                help_timestamp = timestamp
            else:
                help_open = False

    return RequestTimestamps(
        review_request=review_timestamp,
        reviewer_claim=claim_timestamp,
        reviewer_claim_user=claim_user,
        help_request=help_timestamp,
    )


def latest_talk_activity(article):
    talk_page = article.toggleTalkPage()
    if not talk_page.exists():
        return False, None
    return True, talk_page.latest_revision.timestamp


def enrich_queue_record(article, record):
    if not needs_queue_metadata(record):
        return record

    timestamps = RequestTimestamps()
    if record.review_requested or record.help_requested:
        timestamps = find_current_request_timestamps(
            article.revisions(content=True),
            record,
        )
    talk_exists, talk_timestamp = latest_talk_activity(article)
    return replace(
        record,
        review_request_timestamp=timestamps.review_request,
        reviewer_claim_timestamp=timestamps.reviewer_claim,
        reviewer_claim_user=timestamps.reviewer_claim_user,
        help_request_timestamp=timestamps.help_request,
        talk_exists=talk_exists,
        talk_timestamp=talk_timestamp,
    )
