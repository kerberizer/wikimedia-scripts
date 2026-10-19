# wikimedia-scripts

These scripts drive [Kerberizer](https://meta.wikimedia.org/wiki/User:Kerberizer), a bot that I run on the Wikimedia projects, but mainly on the Bulgarian language Wikipedia.

The code is likely crude and mostly undocumented. It's released in the public domain under the [CC0](http://creativecommons.org/publicdomain/zero/1.0/) license.

## Unblock-request notifier

`wp-unblock-request-notifier.py` watches
`Категория:Заявки за отблокиране` on the Bulgarian Wikipedia. For each eligible
new `{{Отблокиране}}` request it opens a short section on
`Уикипедия:Заявки към администраторите`, linking to the revision that introduced
the request. It deliberately does not copy the requester's reason.

The default invocation is read-only:

```sh
./wp-unblock-request-notifier.py
```

Production writes require an explicit `--write`. The script also verifies the
logged-in account before every edit:

```sh
./wp-unblock-request-notifier.py --write \
  --expected-user 'Eliza Beth' \
  --anomaly-report-page 'Потребител беседа:Iliev'
```

Notification destinations, the expected account, and the state-file path are
command-line options so that a controlled test can use a user sandbox. The
special `--test-unblocked-requests` mode runs the complete discovery and
notification workflow for all otherwise eligible requests made by unblocked
users in the category. This deliberately inverts, rather than skips, the block
check. It posts the normal notification text and refuses to write outside the
expected account's own user or user-talk space:

```sh
./wp-unblock-request-notifier.py --write --test-unblocked-requests \
  --expected-user Kerberiel \
  --notification-page 'Потребител беседа:Kerberiel/пясъчник'
```

Test mode defaults to a separate `unblock-request-notifier-test.json` snapshot.
The request must still be on the requester's base user-talk page, be introduced
by that account (compared by user ID), and be discovered through the live
category. Running the command again also exercises duplicate suppression.

State is a locked, atomically replaced JSON snapshot. By default it is stored
at `$XDG_STATE_HOME/wikimedia-scripts/unblock-request-notifier.json`, falling
back to `~/.local/state/wikimedia-scripts/unblock-request-notifier.json`. Its
scope records the mode, expected account, and both destinations; reusing it
with different settings is refused. The snapshot only retains current
category/transclusion episodes, their classification, and a pending anomaly
digest. An unchanged notified episode causes no talk-page history, content, or
block lookup. Losing the snapshot may repeat a diagnostic anomaly digest. A
normal notification is protected against duplication by searching the
destination history for its exact `Special:Diff` marker before each write.
That recovery assumes that the destination retains its history; moving and
recreating the destination can make an old notification unverifiable. An
unreadable candidate revision also fails closed instead of risking a duplicate.

The old `.jsonl` journals are neither imported nor read. They may be archived
or removed during deployment after the old timer has been stopped.

For a new or structurally changed category episode, the introduction lookup
starts near the category-membership timestamp, reads history in batches of two
and then five revisions, stops at the first revision without an active request,
and has a hard 20-revision limit. Once an episode is notified, ordinary later
talk-page edits do not trigger that lookup again. Transport retries are
disabled for new-section writes; after an ambiguous response, the next timer
run consults the wiki history before deciding whether another write is needed.
A definite API refusal is retained as an unclassified anomaly, while later
requests in the same run are still processed.

Exact template transclusions are checked separately against the complete
production eligibility rules. Every active transclusion which would not
produce a normal administrator notification is anomalous: this includes a
wrong namespace or user-talk subpage, an IP talk page, a redirect, a request
introduced by someone other than the page owner, or a request from an
unblocked user. More than one active template on the same page still represents
one page-level request episode. Resolved requests and Bulgarian `демо` values
which MediaWiki compares numerically equal to `1` are ignored; the unrelated
English `demo` parameter has no special meaning. The deliberate example on
`Шаблон:Отблокиране/doc` is also ignored as an expected anomaly; the exception
is exact and does not cover other template subpages. An anomaly must persist
through the configured grace period (30 minutes by default) before it can be
reported. Each anomaly is reported only once per active episode. At most one
digest is posted per 24 hours, with no more than ten displayed links; all
cases in a longer digest are nevertheless marked as reported. Without
`--anomaly-report-page`, anomalies remain visible only in the local state and
dry-run output; the latter prints each reportable page and its reason.

Run the offline tests with:

```sh
python -m unittest discover -s tests -v
```
