#!/usr/local/bin/python
# -*- coding: utf-8  -*-

import pywikibot

villagePumpTitle = u'Уикипедия:Разговори'
textToClean = u'<!-- ВЪВЕДЕТЕ СЪОБЩЕНИЕТО СИ НАД ТОЗИ РЕД! НЕ ПИШЕТЕ ПОД НЕГО! НЕ ГО ИЗТРИВАЙТЕ! -->'

myWikiSite = pywikibot.Site()
villagePumpPage = pywikibot.Page(myWikiSite, villagePumpTitle)

villagePumpOrigText = villagePumpPage.text
villagePumpPage.text = villagePumpPage.text.replace(textToClean, u'')

if not villagePumpPage.text == villagePumpOrigText:
	villagePumpPage.save(u'Бот: премахване на излишни HTML коментари', force=True)
