#!/usr/bin/env python3

# wikimedia-scripts - scripts driving the Kerberizer bot on Wikimedia
#
# Written in 2011-2017 by Luchesar V. ILIEV <luchesar.iliev@gmail.com>
#
# To the extent possible under law, the author(s) have dedicated all
# copyright and related and neighboring rights to this software to the
# public domain worldwide. This software is distributed without any
# warranty.
#
# You should have received a copy of the CC0 Public Domain Dedication
# along with this software. If not, see
# <http://creativecommons.org/publicdomain/zero/1.0/>.

import pywikibot

villagePumpTitle = 'Уикипедия:Разговори'
textToClean = '<!-- ВЪВЕДЕТЕ СЪОБЩЕНИЕТО СИ НАД ТОЗИ РЕД! НЕ ПИШЕТЕ ПОД НЕГО! НЕ ГО ИЗТРИВАЙТЕ! -->'

myWikiSite = pywikibot.Site()
villagePumpPage = pywikibot.Page(myWikiSite, villagePumpTitle)

villagePumpOrigText = villagePumpPage.text
villagePumpPage.text = villagePumpPage.text.replace(textToClean, '')

if not villagePumpPage.text == villagePumpOrigText:
    villagePumpPage.save('Бот: премахване на излишни HTML коментари', force=True)

# vim:set ts=4 sts=4 sw=4 et:
