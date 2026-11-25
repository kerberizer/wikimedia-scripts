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


def record_sort_key(record, timestamp_name):
    timestamp = getattr(record, timestamp_name) or record.timestamp
    return legacy_timestamp_text(timestamp)


def group_records(records):
    records = tuple(records)
    return QueueGroups(
        unclaimed_reviews=tuple(
            sorted(
                (record for record in records if record.review_state == "unclaimed"),
                key=lambda record: record_sort_key(record, "review_request_timestamp"),
            )
        ),
        claimed_reviews=tuple(
            sorted(
                (record for record in records if record.review_state == "claimed"),
                key=lambda record: record_sort_key(record, "review_request_timestamp"),
            )
        ),
        review_diagnostics=tuple(
            sorted(
                (
                    record
                    for record in records
                    if record.review_state == "diagnostic"
                    or record.legacy_reviewer_parameter
                    or record.same_user_reviewer_claim
                ),
                key=lambda record: record_sort_key(record, "review_request_timestamp"),
            )
        ),
        help_requests=tuple(
            sorted(
                (record for record in records if record.help_requested),
                key=lambda record: record_sort_key(record, "help_request_timestamp"),
            )
        ),
        long_stay=tuple(
            sorted(
                (record for record in records if record.age_status == "long_stay"),
                key=lambda record: record_sort_key(record, "timestamp"),
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


def user_link(username):
    return f"{{{{потребител|{username}}}}}"


def author_link(record):
    return user_link(record.author)


def reviewer_link(record):
    if not record.reviewer_claim_user:
        return "неизвестно"
    return user_link(record.reviewer_claim_user)


def timestamp_text(value):
    if value is None:
        return "неизвестно"
    return legacy_timestamp_text(value)


def talk_activity(record):
    if not record.talk_exists:
        return "няма беседа"
    return timestamp_text(record.talk_timestamp)


def review_diagnostic(record):
    messages = []
    if record.legacy_reviewer_parameter:
        messages.append("временен позиционен параметър в {{ш|Инкубатор-проверка}}")
    if record.same_user_reviewer_claim:
        messages.append("прегледът изглежда поет от първоначалния автор")
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


def render_table(records, columns):
    lines = [
        '<div style="overflow-x: auto;">',
        '{| class="wikitable sortable plainlinks" style="font-size: small;"',
        "! " + " !! ".join(header for header, _value in columns),
    ]
    for record in records:
        values = [value(record) for _header, value in columns]
        lines.extend(("|-", "| " + " || ".join(values)))
    lines.append("|}")
    lines.append("</div>")
    return "\n".join(lines)


BASE_COLUMNS = (
    ("Чернова", article_link),
    ("Влязла ([[UTC]])", lambda record: timestamp_text(record.timestamp)),
    ("Автор", author_link),
    ("Възраст", age_label),
)

TALK_COLUMN = ("Последна активност по беседата ([[UTC]])", talk_activity)
REVIEW_REQUEST_COLUMN = (
    "Заявка за преглед ([[UTC]])",
    lambda record: timestamp_text(record.review_request_timestamp),
)
HELP_REQUEST_COLUMN = (
    "Заявка за помощ ([[UTC]])",
    lambda record: timestamp_text(record.help_request_timestamp),
)


def render_task_page(records):
    groups = group_records(records)
    sections = [
        (
            "<!-- Страницата се обновява автоматично. Обсъждайте конкретната "
            "чернова на нейната беседа. -->"
        ),
        (
            "Тук са събрани черновите, които чакат преглед или помощ, както "
            "и тези с продължителен престой. Моментът на заявката е началото "
            "на текущото непрекъснато присъствие на съответния шаблон. "
            "Опашката осигурява видимост, но не обещава срок за отговор."
        ),
        f"== Чакат преглеждащ ({len(groups.unclaimed_reviews)}) ==",
        render_table(
            groups.unclaimed_reviews,
            (
                BASE_COLUMNS[0],
                REVIEW_REQUEST_COLUMN,
                TALK_COLUMN,
                *BASE_COLUMNS[1:],
            ),
        ),
        f"== Преглеждат се ({len(groups.claimed_reviews)}) ==",
        render_table(
            groups.claimed_reviews,
            (
                BASE_COLUMNS[0],
                REVIEW_REQUEST_COLUMN,
                (
                    "Преглеждащ (редактор на ревизията)",
                    reviewer_link,
                ),
                (
                    "Поета ([[UTC]])",
                    lambda record: timestamp_text(record.reviewer_claim_timestamp),
                ),
                TALK_COLUMN,
                *BASE_COLUMNS[1:],
            ),
        ),
        f"== Диагностики при преглед ({len(groups.review_diagnostics)}) ==",
        render_table(
            groups.review_diagnostics,
            (
                BASE_COLUMNS[0],
                REVIEW_REQUEST_COLUMN,
                ("Диагностика", review_diagnostic),
                TALK_COLUMN,
                *BASE_COLUMNS[1:],
            ),
        ),
        f"== Искат помощ ({len(groups.help_requests)}) ==",
        render_table(
            groups.help_requests,
            (
                BASE_COLUMNS[0],
                HELP_REQUEST_COLUMN,
                TALK_COLUMN,
                *BASE_COLUMNS[1:],
            ),
        ),
        f"== Чернови с продължителен престой ({len(groups.long_stay)}) ==",
        render_table(
            groups.long_stay,
            (*BASE_COLUMNS, TALK_COLUMN, ("Други сигнали", additional_signals)),
        ),
    ]
    return "\n\n".join(sections)


def render_queue_preview(records):
    return render_task_page(records)
