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
special `--test-notification` mode refuses to write outside the expected
account's own user or user-talk space.

State is an append-only, locked JSONL journal. By default it is stored at
`$XDG_STATE_HOME/wikimedia-scripts/unblock-request-notifier.jsonl`, falling back
to `~/.local/state/wikimedia-scripts/unblock-request-notifier.jsonl`. A
`notify_started` event is synced before the wiki edit and a `notified` event
after MediaWiki returns the new revision ID. If the process stops between the
two, it recovers from the notification page's revision history. The recovery
query is server-filtered by the expected account and bounded by the request
timestamp; it never scans the account's complete contribution history.

Exact template transclusions are checked separately for unexpected placement.
An anomaly must persist through the configured grace period (30 minutes by
default) before it can be reported. At most one digest is posted per 24 hours,
with no more than ten displayed links. Without `--anomaly-report-page`,
persistent anomalies are only logged.

Run the offline tests with:

```sh
python -m unittest discover -s tests -v
```
