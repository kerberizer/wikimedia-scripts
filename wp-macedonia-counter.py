#!/usr/local/bin/python
# -*- coding: utf-8  -*-

import os, pickle, locale, datetime, pywikibot
from pywikibot import pagegenerators

fname_pageTitlesLast = os.path.expanduser('~/.wp-macedonia-counter/titles-last.dat')
fname_lastDateTime = os.path.expanduser('~/.wp-macedonia-counter/datetime-last.dat')

f = open(fname_pageTitlesLast, 'r')
pageTitlesLast = pickle.load(f)
f.close()

f = open(fname_lastDateTime, 'r')
lastDateTime = pickle.load(f)
f.close()

mySite = pywikibot.Site()
myTemplate = u'Шаблон:Портал Македония'
countPage = pywikibot.Page(mySite, u'Уикипедия:Македония/Брояч')
datePage = pywikibot.Page(mySite, u'Уикипедия:Македония/Брояч/Дата')
diffPage = pywikibot.Page(mySite, u'Уикипедия:Македония/Брояч/Промени')

locale.setlocale(locale.LC_TIME, 'bg_BG.UTF-8')
currentDateTime = datetime.datetime.now().strftime('%H:%M на %e %B %Y').decode('utf8').lower()
locale.resetlocale(locale.LC_TIME)

referringPages = pagegenerators.ReferringPageGenerator(pywikibot.Page(mySite, myTemplate), onlyTemplateInclusion = True)
referringPages = pagegenerators.NamespaceFilterPageGenerator(referringPages, [0])

pageTitles=set()
count = 0
for page in referringPages:
  count += 1
  pageTitles.add(page.title())

addedTitles = pageTitles.difference(pageTitlesLast)
removedTitles = pageTitlesLast.difference(pageTitles)

if addedTitles or removedTitles:

  f = open(fname_lastDateTime, 'w')
  pickle.dump(currentDateTime, f)
  f.close()
  f = open(fname_pageTitlesLast, 'w')
  pickle.dump(pageTitles, f)
  f.close()

  listDiff = u'__NOEDITSECTION__\n'
  listDiff += u'{|\n|<code>' + lastDateTime + '</code>\n|-\n'
  listDiff += u'| align="center" | <big>\'\'\'↓\'\'\'</big>\n'
  listDiff += u'|-\n|<code>' + currentDateTime + '</code>\n|}\n'
  listDiff += u'\n== Добавени ==\n'

  for article in addedTitles:
    listDiff += u'* {{статия|' + article + '}}\n'

  listDiff += u'\n== Премахнати ==\n'

  for article in removedTitles:
    listDiff += u'* {{статия|' + article + '}}\n'

  countPage.text = str(count)
  datePage.text = currentDateTime
  diffPage.text = listDiff
  countPage.save(u'Бот: актуализация на брояча')
  datePage.save(u'Бот: актуализация на датата')
  diffPage.save(u'Бот: актуализация на промените')
