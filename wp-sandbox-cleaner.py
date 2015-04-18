#!/usr/local/bin/python
# -*- coding: utf-8  -*-

import datetime, re, pywikibot

gracePeriod = datetime.timedelta(minutes=15)
pubSandboxTitle = u'Уикипедия:Пясъчник'
cleanTemplateTitle = u'Шаблон:Чист пясък'
cleanText = u'{{замест:Чист пясък}}'

myWikiSite = pywikibot.Site()
pubSandbox = pywikibot.Page(myWikiSite, pubSandboxTitle)
cleanTemplate = pywikibot.Page(myWikiSite, cleanTemplateTitle)

reIncOnly = re.compile('<includeonly>(.*?)<\/includeonly>', re.DOTALL)
cleanTemplateIncOnly = reIncOnly.findall(cleanTemplate.text)
pubSandboxIsClean = u''.join(cleanTemplateIncOnly)

if not pubSandbox.userName() == 'Kerberizer' and not pubSandbox.text == pubSandboxIsClean:
  currentTime = datetime.datetime.utcnow()
  lastModified = datetime.datetime.strptime(str(pubSandbox.editTime()), '%Y-%m-%dT%H:%M:%SZ')
  if not (currentTime - lastModified) < gracePeriod:
    pubSandbox.text = cleanText
    pubSandbox.save(u'Бот: почистване на пясъчника')

# vim: ts=2 =2 sw=2 et
