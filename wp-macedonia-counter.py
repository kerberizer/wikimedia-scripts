#!/usr/bin/env python3
# -*- coding: utf-8  -*-

# wikimedia-scripts - scripts driving the Kerberizer bot on Wikimedia
#
# Written in 2011-2016 by Luchesar V. ILIEV <luchesar.iliev@gmail.com>
#
# To the extent possible under law, the author(s) have dedicated all
# copyright and related and neighboring rights to this software to the
# public domain worldwide. This software is distributed without any
# warranty.
#
# You should have received a copy of the CC0 Public Domain Dedication
# along with this software. If not, see
# <http://creativecommons.org/publicdomain/zero/1.0/>.

import os.path, pickle, locale, datetime, pywikibot
from pywikibot import pagegenerators

fname_pageTitlesLast = os.path.expanduser('~/.wp-macedonia-counter/titles-last.dat')
fname_lastDateTime = os.path.expanduser('~/.wp-macedonia-counter/datetime-last.dat')

f = open(fname_pageTitlesLast, 'rb')
pageTitlesLast = pickle.load(f)
f.close()

f = open(fname_lastDateTime, 'rb')
lastDateTime = pickle.load(f)
f.close()

mySite = pywikibot.Site()
myTemplate = u'Шаблон:Портал Македония'
countPage = pywikibot.Page(mySite, u'Уикипедия:Македония/Брояч')
datePage = pywikibot.Page(mySite, u'Уикипедия:Македония/Брояч/Дата')
diffPage = pywikibot.Page(mySite, u'Уикипедия:Македония/Брояч/Промени')

locale.setlocale(locale.LC_TIME, 'bg_BG.UTF-8')
currentDateTime = datetime.datetime.now().strftime('%H:%M на %e %B %Y').lower()
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

  f = open(fname_lastDateTime, 'wb')
  pickle.dump(currentDateTime, f)
  f.close()
  f = open(fname_pageTitlesLast, 'wb')
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
