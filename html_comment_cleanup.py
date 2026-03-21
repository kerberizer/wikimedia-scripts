#!/usr/bin/env python3
# Created with AI assistance using OpenCode and GPT-5.4.

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pywikibot as pwb
from pywikibot import exceptions as pwb_exceptions
from pywikibot.time import Timestamp


COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SECTION_RE = re.compile(r"^###\s+(\d+)\s*$")
REMOVE_RE = re.compile(r"^remove:\s*(yes|no)\s*$", re.IGNORECASE)
DATE_ONLY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATE_TIME_MINUTE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$")
DATE_TIME_SECOND_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$")
RELATIVE_RE = re.compile(
    r"^(\d+)\s+(seconds?|minutes?|hours?|days?|weeks?)\s+ago$",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find HTML comments added by one user that still survive in the "
            "current wikitext, let you review them in an interactive terminal "
            "UI, and then remove only the selected exact comments."
        ),
        epilog=(
            "Use `%(prog)s <command> -h` for command-specific help, for example "
            "`%(prog)s scan -h` or `%(prog)s apply -h`."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser(
        "scan",
        help="scan contributions and build a reviewable report",
    )
    add_scan_arguments(scan)

    apply_parser = subparsers.add_parser(
        "apply",
        help="load an existing report, optionally review it, and apply removals",
    )
    apply_parser.add_argument("report", help="JSON report created by scan")
    add_apply_arguments(apply_parser)

    return parser.parse_args()


def add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user", required=True, help="username to inspect")
    parser.add_argument(
        "--since",
        help=(
            "oldest contribution timestamp to include; examples: "
            "2020-01-01, 2020-01-01 12:34:56, yesterday, 2 weeks ago"
        ),
    )
    parser.add_argument(
        "--until",
        help=(
            "newest contribution timestamp to include; examples: "
            "2021-12-31, 2021-12-31 23:59:59, now"
        ),
    )
    parser.add_argument(
        "--report",
        help="path to write the JSON report (default: <user>-html-comments.json)",
    )
    parser.add_argument(
        "--namespaces",
        help="comma-separated namespace ids, for example 0 or 0,1,3",
    )
    parser.add_argument(
        "--total",
        type=int,
        help="maximum number of contributions to scan (default: all available)",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="open the interactive review UI after scanning so you can mark comments to remove",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the selected removals after scanning (runs review first)",
    )
    parser.add_argument(
        "--no-editor",
        action="store_true",
        help="if the review UI cannot run, skip the $VISUAL/$EDITOR fallback and use the plain terminal prompt instead",
    )
    add_apply_options(parser)


def add_apply_arguments(parser: argparse.ArgumentParser) -> None:
    add_apply_options(parser)
    parser.add_argument(
        "--review",
        action="store_true",
        help="open the interactive review UI before applying changes",
    )
    parser.add_argument(
        "--no-editor",
        action="store_true",
        help="if the review UI cannot run, skip the $VISUAL/$EDITOR fallback and use the plain terminal prompt instead",
    )


def add_apply_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--summary",
        help="edit summary to use when saving pages",
    )
    parser.add_argument(
        "--minor",
        dest="minor",
        action="store_true",
        default=True,
        help="mark edits as minor (default)",
    )
    parser.add_argument(
        "--no-minor",
        dest="minor",
        action="store_false",
        help="do not mark edits as minor",
    )
    parser.add_argument(
        "--bot",
        action="store_true",
        help="save with the bot flag",
    )
    parser.add_argument(
        "--always",
        action="store_true",
        help="skip the final yes/no confirmation before editing",
    )


def parse_namespaces(value: str | None) -> list[int] | None:
    if not value:
        return None
    namespaces = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        namespaces.append(int(item))
    return namespaces or None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_timestamp(value: Timestamp) -> Timestamp:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return Timestamp(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
    )


def timestamp_to_iso(value: Timestamp | None) -> str | None:
    return str(value) if value is not None else None


def parse_relative_timestamp(value: str, now: Timestamp) -> Timestamp | None:
    match = RELATIVE_RE.match(value)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("second"):
        delta = timedelta(seconds=amount)
    elif unit.startswith("minute"):
        delta = timedelta(minutes=amount)
    elif unit.startswith("hour"):
        delta = timedelta(hours=amount)
    elif unit.startswith("day"):
        delta = timedelta(days=amount)
    else:
        delta = timedelta(weeks=amount)
    return now - delta


def parse_time_argument(value: str, *, bound: str) -> Timestamp:
    raw = value.strip()
    lowered = raw.lower()
    now = normalize_timestamp(Timestamp.nowutc().replace(microsecond=0))

    if lowered == "now":
        return now
    if lowered == "today":
        base = now.replace(hour=0, minute=0, second=0)
        if bound == "until":
            return base.replace(hour=23, minute=59, second=59)
        return base
    if lowered == "yesterday":
        base = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0)
        if bound == "until":
            return base.replace(hour=23, minute=59, second=59)
        return base

    relative = parse_relative_timestamp(raw, now)
    if relative is not None:
        return normalize_timestamp(relative.replace(microsecond=0))

    match = DATE_ONLY_RE.match(raw)
    if match:
        year, month, day = map(int, match.groups())
        if bound == "until":
            return Timestamp(year, month, day, 23, 59, 59)
        return Timestamp(year, month, day, 0, 0, 0)

    match = DATE_TIME_MINUTE_RE.match(raw)
    if match:
        year, month, day, hour, minute = map(int, match.groups())
        return Timestamp(year, month, day, hour, minute, 0)

    match = DATE_TIME_SECOND_RE.match(raw)
    if match:
        year, month, day, hour, minute, second = map(int, match.groups())
        return Timestamp(year, month, day, hour, minute, second)

    try:
        return normalize_timestamp(Timestamp.set_timestamp(raw).replace(microsecond=0))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Could not parse --{bound} value {value!r}: {exc}") from exc


def parse_time_range(
    since_value: str | None,
    until_value: str | None,
) -> tuple[Timestamp | None, Timestamp | None]:
    since = parse_time_argument(since_value, bound="since") if since_value else None
    until = parse_time_argument(until_value, bound="until") if until_value else None
    if since is not None and until is not None and since > until:
        raise SystemExit(f"--since must not be later than --until ({since} > {until})")
    return since, until


def preview_comment(text: str, limit: int = 90) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 3] + "..."


def comment_counter(text: str) -> Counter[str]:
    return Counter(COMMENT_RE.findall(text))


def get_revision_texts(
    site: Any,
    title: str,
    revid: int,
    parentid: int,
) -> tuple[pwb.Page, str, str]:
    page = pwb.Page(site, title)
    revids = [revid]
    if parentid:
        revids.append(parentid)
    site.loadrevisions(page, content=True, revids=revids)
    new_text = page.get_revision(revid, content=True).text or ""
    old_text = ""
    if parentid:
        old_text = page.get_revision(parentid, content=True).text or ""
    return page, old_text, new_text


def build_report(
    site: Any,
    user: str,
    namespaces: list[int] | None,
    total: int | None,
    since: Timestamp | None,
    until: Timestamp | None,
) -> dict[str, Any]:
    records: dict[tuple[int, str], dict[str, Any]] = {}
    live_pages: dict[int, pwb.Page] = {}
    current_texts: dict[int, str] = {}
    scan_failures = []
    page_failures = []
    scanned = 0
    revisions_with_added_comments = 0

    contrib_kwargs: dict[str, Any] = {
        "user": user,
        "namespaces": namespaces,
        "total": total,
    }
    if until is not None:
        contrib_kwargs["start"] = until
    if since is not None:
        contrib_kwargs["end"] = since

    contrib_iter = site.usercontribs(**contrib_kwargs)

    if since is not None or until is not None:
        print(
            (
                f"Scanning contributions for {user} "
                f"between {timestamp_to_iso(since) or '-infinity'} and "
                f"{timestamp_to_iso(until) or 'now'}..."
            ),
            file=sys.stderr,
        )
    else:
        print(f"Scanning contributions for {user}...", file=sys.stderr)

    for contrib in contrib_iter:
        scanned += 1
        title = contrib["title"]
        revid = int(contrib["revid"])
        parentid = int(contrib.get("parentid") or 0)
        pageid = int(contrib.get("pageid") or 0)
        timestamp = contrib["timestamp"]

        if scanned % 100 == 0:
            print(
                f"  scanned {scanned} contributions; "
                f"found {len(records)} distinct page/comment pairs so far",
                file=sys.stderr,
            )

        if pageid <= 0:
            continue

        try:
            page, old_text, new_text = get_revision_texts(site, title, revid, parentid)
        except Exception as exc:
            scan_failures.append(
                {
                    "title": title,
                    "pageid": pageid,
                    "revid": revid,
                    "parentid": parentid,
                    "error": str(exc),
                }
            )
            continue

        added_comments = comment_counter(new_text) - comment_counter(old_text)
        if not added_comments:
            continue

        revisions_with_added_comments += 1
        for comment_text, added_count in added_comments.items():
            if added_count <= 0:
                continue
            key = (pageid, comment_text)
            record = records.setdefault(
                key,
                {
                    "id": 0,
                    "pageid": pageid,
                    "page_title": page.title(),
                    "comment_text": comment_text,
                    "comment_preview": preview_comment(comment_text),
                    "current_count": 0,
                    "added_total_count": 0,
                    "latest_revid": 0,
                    "latest_timestamp": "",
                    "additions": [],
                    "selected": False,
                },
            )
            record["page_title"] = page.title()
            record["added_total_count"] += added_count
            record["additions"].append(
                {
                    "revid": revid,
                    "parentid": parentid,
                    "timestamp": timestamp,
                    "count": added_count,
                }
            )
            if timestamp >= record["latest_timestamp"]:
                record["latest_timestamp"] = timestamp
                record["latest_revid"] = revid

    pageids = sorted({pageid for pageid, _comment_text in records})
    for page in site.load_pages_from_pageids(pageids):
        try:
            current_texts[page.pageid] = page.get(get_redirect=True)
            live_pages[page.pageid] = page
        except Exception as exc:
            page_failures.append(
                {
                    "pageid": page.pageid,
                    "title": page.title(),
                    "error": str(exc),
                }
            )

    candidates = []
    for (pageid, _comment_text), record in records.items():
        current_text = current_texts.get(pageid)
        if current_text is None:
            continue
        count = current_text.count(record["comment_text"])
        if count <= 0:
            continue
        page = live_pages[pageid]
        record["page_title"] = page.title()
        record["current_count"] = count
        candidates.append(record)

    candidates.sort(
        key=lambda item: (
            item["latest_timestamp"],
            item["page_title"],
            item["comment_text"],
        ),
        reverse=True,
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["id"] = index

    print(
        (
            f"Finished scanning {scanned} contributions. "
            f"Found {len(candidates)} still-live page/comment pairs "
            f"across {len({item['pageid'] for item in candidates})} pages."
        ),
        file=sys.stderr,
    )

    return {
        "meta": {
            "generated_at": utc_now(),
            "user": user,
            "site": f"{site.code}.{site.family.name}",
            "namespaces": namespaces,
            "total": total,
            "since": timestamp_to_iso(since),
            "until": timestamp_to_iso(until),
            "scan_stats": {
                "contributions_scanned": scanned,
                "revisions_with_added_comments": revisions_with_added_comments,
                "distinct_page_comment_pairs": len(records),
                "live_candidates": len(candidates),
                "pages_with_live_candidates": len(
                    {item["pageid"] for item in candidates}
                ),
                "scan_failures": len(scan_failures),
                "page_failures": len(page_failures),
            },
        },
        "candidates": candidates,
        "scan_failures": scan_failures,
        "page_failures": page_failures,
    }


def load_report(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_report(path: Path, report: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def review_text(report: dict[str, Any]) -> str:
    lines = [
        '# Change only "remove: no" to "remove: yes" for comments you want removed.',
        "# Everything else is informational.",
        "",
    ]
    for candidate in report["candidates"]:
        lines.extend(
            [
                f"### {candidate['id']}",
                f"remove: {'yes' if candidate.get('selected') else 'no'}",
                f"page: {candidate['page_title']}",
                f"pageid: {candidate['pageid']}",
                f"current_count: {candidate['current_count']}",
                f"added_total_count: {candidate['added_total_count']}",
                f"latest_revid: {candidate['latest_revid']}",
                f"latest_timestamp: {candidate['latest_timestamp']}",
                f"addition_revids: {format_addition_revids(candidate)}",
                "comment:",
                candidate["comment_text"],
                "",
            ]
        )
    return "\n".join(lines)


def format_addition_revids(candidate: dict[str, Any], limit: int = 10) -> str:
    revs = ", ".join(str(item["revid"]) for item in candidate["additions"][:limit])
    if len(candidate["additions"]) > limit:
        revs += ", ..."
    return revs


def count_selected(report: dict[str, Any]) -> int:
    return sum(1 for item in report["candidates"] if item.get("selected"))


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def review_report_in_tui(
    report: dict[str, Any],
    run_kwargs: dict[str, Any] | None = None,
) -> int:
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import DataTable, Footer, Header, Static

    candidates = report["candidates"]

    def marker(selected: bool) -> Text:
        return Text("[X]" if selected else "[ ]")

    def render_details(candidate: dict[str, Any]) -> str:
        selected = "yes" if candidate.get("selected") else "no"
        return "\n".join(
            [
                f"Selected: {selected}",
                f"ID: {candidate['id']}",
                f"Page: {candidate['page_title']}",
                f"Page ID: {candidate['pageid']}",
                f"Current count: {candidate['current_count']}",
                f"Added total count: {candidate['added_total_count']}",
                f"Latest revision: {candidate['latest_revid']}",
                f"Latest timestamp: {candidate['latest_timestamp']}",
                f"Addition revisions: {format_addition_revids(candidate)}",
                "",
                "Comment:",
                candidate["comment_text"],
            ]
        )

    class ReviewApp(App[set[int] | None]):
        CSS = """
        #summary {
            height: 1;
            padding: 0 1;
            background: $panel;
            color: $text;
        }

        #main {
            height: 1fr;
        }

        #table {
            width: 2fr;
        }

        #details-pane {
            width: 1fr;
            border: solid $primary;
        }

        #details-title {
            height: auto;
            padding: 0 1;
            background: $boost;
            text-style: bold;
        }

        #details-scroll {
            height: 1fr;
        }

        #details {
            padding: 1;
        }
        """

        BINDINGS = [
            Binding("space", "toggle_current", "Toggle"),
            Binding("enter", "toggle_current", "Toggle"),
            Binding("a", "select_all", "Select All"),
            Binding("n", "select_none", "Select None"),
            Binding("q", "save_and_quit", "Save & Quit"),
            Binding("escape", "cancel", "Cancel"),
        ]

        def __init__(self, rows: list[dict[str, Any]]) -> None:
            super().__init__()
            self.rows = rows
            self.selected_ids = {item["id"] for item in rows if item.get("selected")}

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static(id="summary")
            with Horizontal(id="main"):
                yield DataTable(
                    id="table",
                    cursor_type="row",
                    zebra_stripes=True,
                    show_row_labels=False,
                )
                with Vertical(id="details-pane"):
                    yield Static("Details", id="details-title")
                    with VerticalScroll(id="details-scroll"):
                        yield Static(id="details")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#table", DataTable)
            table.add_columns(
                ("Sel", "sel"),
                ("ID", "id"),
                ("Live", "live"),
                ("Page", "page"),
                ("Timestamp", "timestamp"),
                ("Preview", "preview"),
            )
            for candidate in self.rows:
                table.add_row(
                    marker(candidate["id"] in self.selected_ids),
                    str(candidate["id"]),
                    str(candidate["current_count"]),
                    candidate["page_title"],
                    candidate["latest_timestamp"],
                    candidate["comment_preview"],
                    key=str(candidate["id"]),
                )
            table.focus()
            self._update_summary()
            if self.rows:
                table.move_cursor(row=0, column=0, animate=False, scroll=True)
                self._update_details(self.rows[0])

        def on_data_table_row_highlighted(
            self, event: DataTable.RowHighlighted
        ) -> None:
            candidate = self._candidate_from_row_index(event.cursor_row)
            if candidate is not None:
                self._update_details(candidate)

        def action_toggle_current(self) -> None:
            candidate = self._current_candidate()
            if candidate is None:
                return
            candidate_id = candidate["id"]
            if candidate_id in self.selected_ids:
                self.selected_ids.remove(candidate_id)
                candidate["selected"] = False
            else:
                self.selected_ids.add(candidate_id)
                candidate["selected"] = True
            self._refresh_row(candidate)
            self._update_summary()
            self._update_details(candidate)

        def action_select_all(self) -> None:
            self.selected_ids = {item["id"] for item in self.rows}
            for candidate in self.rows:
                candidate["selected"] = True
                self._refresh_row(candidate)
            self._update_summary()
            current = self._current_candidate()
            if current is not None:
                self._update_details(current)

        def action_select_none(self) -> None:
            self.selected_ids.clear()
            for candidate in self.rows:
                candidate["selected"] = False
                self._refresh_row(candidate)
            self._update_summary()
            current = self._current_candidate()
            if current is not None:
                self._update_details(current)

        def action_save_and_quit(self) -> None:
            self.exit(set(self.selected_ids))

        def action_cancel(self) -> None:
            self.exit(None)

        def _update_summary(self) -> None:
            self.query_one("#summary", Static).update(
                (
                    f"{len(self.selected_ids)} selected / {len(self.rows)} total  "
                    "- arrows to move, space to toggle, a/n to select all/none, q to save"
                )
            )

        def _update_details(self, candidate: dict[str, Any]) -> None:
            self.query_one("#details", Static).update(render_details(candidate))

        def _refresh_row(self, candidate: dict[str, Any]) -> None:
            self.query_one("#table", DataTable).update_cell(
                str(candidate["id"]),
                "sel",
                marker(candidate["id"] in self.selected_ids),
                update_width=False,
            )

        def _current_candidate(self) -> dict[str, Any] | None:
            table = self.query_one("#table", DataTable)
            cursor_row = table.cursor_row
            return self._candidate_from_row_index(cursor_row)

        def _candidate_from_row_index(
            self, row_index: int | None
        ) -> dict[str, Any] | None:
            if row_index is None or row_index < 0 or row_index >= len(self.rows):
                return None
            return self.rows[row_index]

    result = ReviewApp(candidates).run(**(run_kwargs or {}))
    if result is None:
        raise SystemExit("Aborted.")

    for candidate in candidates:
        candidate["selected"] = candidate["id"] in result

    selected = len(result)
    print(f"Marked {selected} candidates for removal.", file=sys.stderr)
    return selected


def apply_review_text(report: dict[str, Any], text: str) -> int:
    selections: dict[int, bool] = {}
    current_id: int | None = None
    in_comment = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        section_match = SECTION_RE.match(line)
        if section_match:
            current_id = int(section_match.group(1))
            in_comment = False
            continue
        if current_id is None:
            continue
        if line == "comment:":
            in_comment = True
            continue
        if in_comment:
            continue
        remove_match = REMOVE_RE.match(line.strip())
        if remove_match:
            selections[current_id] = remove_match.group(1).lower() == "yes"

    changed = 0
    for candidate in report["candidates"]:
        new_value = selections.get(candidate["id"], False)
        if bool(candidate.get("selected")) != new_value:
            changed += 1
        candidate["selected"] = new_value
    return changed


def review_report(path: Path, no_editor: bool) -> int:
    report = load_report(path)
    if not report["candidates"]:
        print("The report has no live candidates to review.", file=sys.stderr)
        return 0

    if is_interactive_terminal():
        try:
            selected = review_report_in_tui(report)
            save_report(path, report)
            return selected
        except ImportError as exc:
            print(f"TUI review unavailable: {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"TUI review failed, falling back: {exc}", file=sys.stderr)

    if not no_editor:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if editor:
            with tempfile.NamedTemporaryFile(
                mode="w+",
                encoding="utf-8",
                prefix="html-comment-review-",
                suffix=".txt",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(review_text(report))

            try:
                command = shlex.split(editor) + [str(temp_path)]
                subprocess.run(command, check=True)
                reviewed_text = temp_path.read_text(encoding="utf-8")
            finally:
                temp_path.unlink(missing_ok=True)

            apply_review_text(report, reviewed_text)
            save_report(path, report)
            selected = count_selected(report)
            print(f"Marked {selected} candidates for removal.", file=sys.stderr)
            return selected

    selected = review_report_in_terminal(report)
    save_report(path, report)
    return selected


def review_report_in_terminal(report: dict[str, Any]) -> int:
    print("No editor configured or editor use disabled.", file=sys.stderr)
    print(
        "Enter items to remove as comma-separated numbers or ranges.", file=sys.stderr
    )
    print("Examples: 1,4,9-12   or   all   or   none", file=sys.stderr)
    print("", file=sys.stderr)

    for candidate in report["candidates"]:
        print(
            (
                f"{candidate['id']:>4}  "
                f"[{candidate['current_count']}] "
                f"{candidate['page_title']} :: {candidate['comment_preview']}"
            ),
            file=sys.stderr,
        )

    answer = input("Remove which items? ").strip().lower()
    selected_ids = parse_selection(answer, len(report["candidates"]))
    for candidate in report["candidates"]:
        candidate["selected"] = candidate["id"] in selected_ids
    selected = len(selected_ids)
    print(f"Marked {selected} candidates for removal.", file=sys.stderr)
    return selected


def parse_selection(value: str, upper_bound: int) -> set[int]:
    if value == "all":
        return set(range(1, upper_bound + 1))
    if value in {"", "none"}:
        return set()

    selected: set[int] = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(chunk))

    invalid = [item for item in selected if item < 1 or item > upper_bound]
    if invalid:
        raise SystemExit(f"Invalid selection ids: {invalid}")
    return selected


def prompt_for_summary() -> str:
    summary = input("Edit summary: ").strip()
    if not summary:
        raise SystemExit("A non-empty edit summary is required for apply.")
    return summary


def confirm_apply(grouped: dict[int, list[dict[str, Any]]]) -> bool:
    pages = len(grouped)
    candidates = sum(len(items) for items in grouped.values())
    answer = (
        input(f"Apply {candidates} selected comment removals on {pages} pages? [y/N] ")
        .strip()
        .lower()
    )
    return answer in {"y", "yes"}


def remove_n_occurrences(text: str, target: str, count: int) -> str:
    if count <= 0:
        return text
    return text.replace(target, "", count)


def apply_report(
    path: Path,
    summary: str | None,
    minor: bool,
    bot: bool,
    always: bool,
    no_editor: bool,
    review_first: bool,
) -> None:
    if review_first:
        review_report(path, no_editor=no_editor)

    report = load_report(path)
    selected = [item for item in report["candidates"] if item.get("selected")]
    if not selected:
        raise SystemExit("No candidates are marked for removal in the report.")

    if not summary:
        summary = prompt_for_summary()

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in selected:
        grouped[item["pageid"]].append(item)

    if not always and not confirm_apply(grouped):
        raise SystemExit("Aborted.")

    site = pwb.Site()
    site.login()

    successes = []
    skips = []
    failures = []

    for page in site.load_pages_from_pageids(grouped):
        items = grouped[page.pageid]
        try:
            text = page.get(get_redirect=True)
        except Exception as exc:
            failures.append(
                {"pageid": page.pageid, "title": page.title(), "error": str(exc)}
            )
            continue

        original_text = text
        page_notes = []

        for item in items:
            live_now = text.count(item["comment_text"])
            if live_now <= 0:
                page_notes.append(f"id {item['id']}: already gone")
                continue
            to_remove = min(live_now, int(item["current_count"]))
            if live_now != item["current_count"]:
                page_notes.append(
                    (
                        f"id {item['id']}: count changed "
                        f"({item['current_count']} -> {live_now}); removing {to_remove}"
                    )
                )
            text = remove_n_occurrences(text, item["comment_text"], to_remove)

        if text == original_text:
            skips.append(
                {"pageid": page.pageid, "title": page.title(), "notes": page_notes}
            )
            continue

        page.text = text
        try:
            page.save(summary=summary, minor=minor, bot=bot, quiet=True)
            successes.append(
                {"pageid": page.pageid, "title": page.title(), "notes": page_notes}
            )
        except Exception as exc:
            failures.append(
                {
                    "pageid": page.pageid,
                    "title": page.title(),
                    "error": str(exc),
                    "notes": page_notes,
                }
            )

    report.setdefault("apply_runs", []).append(
        {
            "applied_at": utc_now(),
            "summary": summary,
            "minor": minor,
            "bot": bot,
            "successes": successes,
            "skips": skips,
            "failures": failures,
        }
    )
    save_report(path, report)

    print(
        (
            f"Applied changes to {len(successes)} pages; "
            f"skipped {len(skips)} pages; "
            f"failed on {len(failures)} pages."
        ),
        file=sys.stderr,
    )
    if failures:
        print("Failures:", file=sys.stderr)
        for failure in failures:
            print(
                f"  - {failure.get('title', failure.get('pageid'))}: {failure['error']}",
                file=sys.stderr,
            )


def default_report_path(user: str) -> Path:
    safe_user = re.sub(r"[^A-Za-z0-9._-]+", "_", user)
    return Path(f"{safe_user}-html-comments.json")


def run_scan(args: argparse.Namespace) -> None:
    site = pwb.Site()
    namespaces = parse_namespaces(args.namespaces)
    since, until = parse_time_range(args.since, args.until)
    report = build_report(site, args.user, namespaces, args.total, since, until)
    report_path = Path(args.report) if args.report else default_report_path(args.user)
    save_report(report_path, report)
    print(f"Report saved to {report_path}", file=sys.stderr)

    if args.review or args.apply:
        review_report(report_path, no_editor=args.no_editor)
    if args.apply:
        apply_report(
            report_path,
            summary=args.summary,
            minor=args.minor,
            bot=args.bot,
            always=args.always,
            no_editor=args.no_editor,
            review_first=False,
        )


def run_apply(args: argparse.Namespace) -> None:
    report_path = Path(args.report)
    if not report_path.exists():
        raise SystemExit(f"Report not found: {report_path}")
    apply_report(
        report_path,
        summary=args.summary,
        minor=args.minor,
        bot=args.bot,
        always=args.always,
        no_editor=args.no_editor,
        review_first=args.review,
    )


def main() -> None:
    args = parse_args()
    try:
        if args.command == "scan":
            run_scan(args)
        elif args.command == "apply":
            run_apply(args)
        else:
            raise SystemExit(f"Unknown command: {args.command}")
    except KeyboardInterrupt:
        raise SystemExit("Interrupted.")
    except pwb_exceptions.Error as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
