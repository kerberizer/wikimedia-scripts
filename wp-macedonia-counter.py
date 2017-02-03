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
generatorKey = 'Категория:Портал:Македония/Тематични статии'
countPage = pywikibot.Page(mySite, 'Уикипедия:Македония/Брояч')
datePage = pywikibot.Page(mySite, 'Уикипедия:Македония/Брояч/Дата')
diffPage = pywikibot.Page(mySite, 'Уикипедия:Македония/Брояч/Промени')

locale.setlocale(locale.LC_TIME, 'bg_BG.UTF-8')
currentDateTime = datetime.datetime.now().strftime('%H:%M на %e %B %Y').lower()
locale.resetlocale(locale.LC_TIME)

referringPages = pagegenerators.CategorizedPageGenerator(pywikibot.Category(mySite, generatorKey), recurse = True, namespaces = [0])

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

    listDiff = '__NOEDITSECTION__\n'
    listDiff += '{|\n|<code>' + lastDateTime + '</code>\n|-\n'
    listDiff += '| align="center" | <big>\'\'\'↓\'\'\'</big>\n'
    listDiff += '|-\n|<code>' + currentDateTime + '</code>\n|}\n'
    listDiff += '\n== Добавени ==\n'

    for article in addedTitles:
        listDiff += '* {{статия|' + article + '}}\n'

    listDiff += '\n== Премахнати ==\n'

    for article in removedTitles:
        listDiff += '* {{статия|' + article + '}}\n'

    countPage.text = str(count)
    datePage.text = currentDateTime
    diffPage.text = listDiff
    countPage.save('Бот: актуализация на брояча')
    datePage.save('Бот: актуализация на датата')
    diffPage.save('Бот: актуализация на промените')

# vim:set ts=4 sts=4 sw=4 et:
