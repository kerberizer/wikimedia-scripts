import html
from dataclasses import dataclass

from .classify import legacy_timestamp_text
from .render import article_link


@dataclass(frozen=True)
class QueueGroups:
    unclaimed_reviews: tuple
    claimed_reviews: tuple
    review_diagnostics: tuple
    help_requests: tuple
    long_stay: tuple


def record_sort_key(record):
    return legacy_timestamp_text(record.timestamp)


def group_records(records):
    records = tuple(records)
    return QueueGroups(
        unclaimed_reviews=tuple(
            sorted(
                (record for record in records if record.review_state == "unclaimed"),
                key=record_sort_key,
            )
        ),
        claimed_reviews=tuple(
            sorted(
                (record for record in records if record.review_state == "claimed"),
                key=record_sort_key,
            )
        ),
        review_diagnostics=tuple(
            sorted(
                (
                    record
                    for record in records
                    if record.review_state == "diagnostic"
                    or record.legacy_reviewer_parameter
                ),
                key=record_sort_key,
            )
        ),
        help_requests=tuple(
            sorted(
                (record for record in records if record.help_requested),
                key=record_sort_key,
            )
        ),
        long_stay=tuple(
            sorted(
                (record for record in records if record.age_status == "long_stay"),
                key=record_sort_key,
            )
        ),
    )


def age_label(record):
    return {
        "normal": "до 90 дни",
        "warning": "над 90 дни",
        "critical": "над 120 дни",
        "long_stay": "над 150 дни",
    }.get(record.age_status, record.age_status)


def author_link(record):
    return f"{{{{потребител|{record.author}}}}}"


def plain_template_value(value):
    escaped = html.escape(value, quote=False)
    return escaped.translate(
        str.maketrans(
            {
                "[": "&#91;",
                "]": "&#93;",
                "{": "&#123;",
                "}": "&#125;",
                "|": "&#124;",
            }
        )
    )


def review_diagnostic(record):
    messages = []
    if record.legacy_reviewer_parameter:
        messages.append("временен позиционен параметър в {{ш|Инкубатор-проверка}}")
    if record.reviewer_diagnostic:
        messages.append(record.reviewer_diagnostic)
    return "; ".join(messages)


def additional_signals(record):
    signals = []
    if record.review_requested:
        signals.append("преглед")
    if record.help_requested:
        signals.append("помощ")
    return ", ".join(signals) or "—"


def render_table(records, extra_header=None, extra_value=None):
    header = "! Чернова !! Влязла ([[UTC]]) !! Автор !! Възраст"
    if extra_header:
        header += f" !! {extra_header}"
    lines = [
        '{| class="wikitable sortable plainlinks" style="font-size: small;"',
        header,
    ]
    for record in records:
        values = [
            article_link(record),
            legacy_timestamp_text(record.timestamp),
            author_link(record),
            age_label(record),
        ]
        if extra_value:
            values.append(extra_value(record))
        lines.extend(("|-", "| " + " || ".join(values)))
    lines.append("|}")
    return "\n".join(lines)


def render_queue_preview(records):
    groups = group_records(records)
    sections = [
        "<!-- Локален преглед; не публикувайте без отделен преглед. -->",
        "= Опашки на Инкубатора – локален преглед =",
        (
            "''Този временен преглед още не изчислява момента на заявката или "
            "активността на беседата.''"
        ),
        f"== Чакат преглеждащ ({len(groups.unclaimed_reviews)}) ==",
        render_table(groups.unclaimed_reviews),
        f"== Преглеждат се ({len(groups.claimed_reviews)}) ==",
        render_table(
            groups.claimed_reviews,
            "Преглеждащ (непроверена стойност от шаблона)",
            lambda record: plain_template_value(record.reviewer),
        ),
        f"== Диагностики при преглед ({len(groups.review_diagnostics)}) ==",
        render_table(
            groups.review_diagnostics,
            "Диагностика",
            review_diagnostic,
        ),
        f"== Искат помощ ({len(groups.help_requests)}) ==",
        render_table(groups.help_requests),
        f"== Чернови с продължителен престой ({len(groups.long_stay)}) ==",
        render_table(
            groups.long_stay,
            "Други сигнали",
            additional_signals,
        ),
    ]
    return "\n\n".join(sections)
