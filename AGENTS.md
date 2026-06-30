# AGENTS.md

## Repo Shape
- Standalone executable Python 3 scripts for the Kerberizer Wikimedia bot; there is no package manifest, lockfile, CI, or configured test runner.
- Do not import or run scripts casually: several perform live `pywikibot` saves/deletes, send email, or update local state at top level.

## Runtime Assumptions
- External dependencies are inferred from imports: `pywikibot`, `mwparserfromhell`, `python-dateutil`; `html_comment_cleanup.py --review` also needs `rich` and `textual`.
- Pywikibot config/auth is intentionally caller-managed; scripts using bare `pywikibot.Site()` depend on the caller's local default site.
- Some scripts explicitly target `bg.wikipedia`; preserve that unless the task is to change the target wiki.
- `sources-filter-list.py` and `wp-macedonia-counter.py` require the `bg_BG.UTF-8` locale for date formatting.

## Verification
- Lint with `python -m flake8 .`; `.flake8` only sets `max-line-length = 100`.
- No tests are configured. Do not invent `pytest` or package commands unless adding the needed config.
- Prefer static review or targeted linting over executing scripts, because most entrypoints touch Wikimedia or local operational files.

## Script Gotchas
- `sources-filter-gen.py <file>` is offline and prints generated AbuseFilter regex source.
- `sources-filter-list.py --dump` fetches AbuseFilter 12 and prints the site list; without `--dump` it saves wiki pages.
- `html_comment_cleanup.py scan ...` writes a JSON report; `apply` edits wiki pages after review/confirmation, and `--always` skips the final confirmation.
- `wp-admin-notifier.py` reads/writes `~/.wp-admin-notifier/datetime-last.dat` and sends mail through localhost SMTP.
- `wp-sandbox-cleaner.py`, `wp-villagepump-scrub.py`, `wp-macedonia-counter.py`, `thanksmeter.py`, and `incubator-article-list.py` can save or delete live wiki content.

## Style
- Preserve Bulgarian page names, edit summaries, and bot-facing text exactly unless the task is to change them.
- Use `{{ш|Име на шаблон}}` when wiki text should conveniently link to a template.
- Existing scripts use executable shebangs, 4-space indentation, and a 100-column lint limit.
