#!/usr/bin/env python3

# wikimedia-scripts - scripts driving the Kerberizer bot on Wikimedia
#
# Written in 2011-2026 by Luchesar V. ILIEV <luchesar.iliev@gmail.com>
#
# To the extent possible under law, the author(s) have dedicated all
# copyright and related and neighboring rights to this software to the
# public domain worldwide. This software is distributed without any
# warranty.
#
# You should have received a copy of the CC0 Public Domain Dedication
# along with this software. If not, see
# <http://creativecommons.org/publicdomain/zero/1.0/>.

from datetime import timedelta
import re

import pywikibot


GRACE_PERIOD = timedelta(minutes=15)
SANDBOX_TITLE = 'Уикипедия:Пясъчник'
CLEAN_TEMPLATE_TITLE = 'Шаблон:Чист пясък'
CLEAN_WIKITEXT = '{{замест:Чист пясък}}'
EDIT_SUMMARY = 'Бот: почистване на пясъчника'
INCLUDEONLY_RE = re.compile(r'<includeonly>(.*?)</includeonly>', re.DOTALL | re.IGNORECASE)


def main():
    site = pywikibot.Site(code='bg', fam='wikipedia')
    sandbox = pywikibot.Page(site, SANDBOX_TITLE)
    revision = sandbox.latest_revision

    if revision.user == site.username():
        return

    clean_template = pywikibot.Page(site, CLEAN_TEMPLATE_TITLE)
    clean_sandbox_text = ''.join(INCLUDEONLY_RE.findall(clean_template.text))
    if not clean_sandbox_text.strip():
        raise RuntimeError(f'{CLEAN_TEMPLATE_TITLE} has no nonempty <includeonly> content')

    if revision.text == clean_sandbox_text:
        return

    if site.server_time() - revision.timestamp < GRACE_PERIOD:
        return

    sandbox.text = CLEAN_WIKITEXT
    sandbox.save(summary=EDIT_SUMMARY, nocreate=True)


if __name__ == '__main__':
    main()

# vim:set ts=4 sts=4 sw=4 et:
