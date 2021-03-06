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

import datetime, re, pywikibot

gracePeriod = datetime.timedelta(minutes=15)
pubSandboxTitle = 'Уикипедия:Пясъчник'
cleanTemplateTitle = 'Шаблон:Чист пясък'
cleanText = '{{замест:Чист пясък}}'

myWikiSite = pywikibot.Site()
pubSandbox = pywikibot.Page(myWikiSite, pubSandboxTitle)
cleanTemplate = pywikibot.Page(myWikiSite, cleanTemplateTitle)

reIncOnly = re.compile('<includeonly>(.*?)<\/includeonly>', re.DOTALL)
cleanTemplateIncOnly = reIncOnly.findall(cleanTemplate.text)
pubSandboxIsClean = ''.join(cleanTemplateIncOnly)

if not pubSandbox.userName() == 'Kerberizer' and not pubSandbox.text == pubSandboxIsClean:
    currentTime = datetime.datetime.utcnow()
    lastModified = datetime.datetime.strptime(str(pubSandbox.editTime()), '%Y-%m-%dT%H:%M:%SZ')
    if not (currentTime - lastModified) < gracePeriod:
        pubSandbox.text = cleanText
        pubSandbox.save('Бот: почистване на пясъчника')

# vim:set ts=4 sts=4 sw=4 et:
