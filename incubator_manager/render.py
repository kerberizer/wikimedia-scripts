from .classify import legacy_timestamp_text

ROW_COLORS = {
    "redirect": "#ff66ff",
    "long_stay": "#eaecf0",
    "critical": "#ffb366",
    "warning": "#ffff66",
    "review": "#66ff66",
    "help": "#66ffff",
}


def article_name(fullname):
    if "/" in fullname:
        return fullname.rsplit("/", 1)[1]
    return fullname.split(":", 1)[1]


def article_link(record):
    name = article_name(record.fullname)
    if record.status == "redirect":
        return f"{{{{без пренасочване|{record.fullname}|{name}}}}}"
    # Keep this literal: the brace-heavy legacy wikitext is easy to alter by mistake.
    parts = [
        "[[",
        record.fullname,
        "|",
        name,
        "]]",
        " ([[{{TALKPAGENAME:",
        record.fullname,
        "}}|беседа]] - [{{fullurl:",
        record.fullname,
        "|action=history}} история])",
    ]
    return "".join(parts)


def render_row(record):
    color = ROW_COLORS.get(record.status)
    row_start = f'|- style="background-color: {color};"' if color else "|-"
    return (
        f"{row_start}\n"
        f"| {article_link(record)} || {legacy_timestamp_text(record.timestamp)} || "
        f"{{{{потребител|{record.author}}}}} || {record.reviewer}"
    )


def render_list(records, config):
    content = [
        config.list_header_template,
        '{| class="wikitable sortable plainlinks" style="font-size: small;"',
        "! Статия !! Влязла ([[UTC]]) !! Автор !! Проверяващ",
    ]
    content.extend(
        render_row(record)
        for record in sorted(
            records, key=lambda item: legacy_timestamp_text(item.timestamp)
        )
    )
    content.append("|-\n|}")
    return "\n".join(content)
