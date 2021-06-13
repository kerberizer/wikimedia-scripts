#!/usr/bin/env python3

# wikimedia-scripts - scripts driving the Kerberizer bot on Wikimedia
#
# Written in 2011-2021 by Luchesar ILIEV <luchesar.iliev@gmail.com>
#
# To the extent possible under law, the author(s) have dedicated all
# copyright and related and neighboring rights to this software to the
# public domain worldwide. This software is distributed without any
# warranty.
#
# You should have received a copy of the CC0 Public Domain Dedication
# along with this software. If not, see
# <http://creativecommons.org/publicdomain/zero/1.0/>.


from datetime import datetime as dt
import locale

import pywikibot as pwb


w = pwb.Site()
cat_mk = pwb.Category(w, 'Категория:Портал:Македония/Тематични статии')
page_counter = pwb.Page(w, 'Уикипедия:Македония/Брояч')
page_date = pwb.Page(w, 'Уикипедия:Македония/Брояч/Дата')
locale.setlocale(locale.LC_TIME, 'bg_BG.UTF-8')
page_date.text = dt.now().strftime('%e %B %Y').lower()
page_date.save('Бот: актуализация на датата')
locale.resetlocale(locale.LC_TIME)
page_counter.text = cat_mk.categoryinfo['pages']
page_counter.save('Бот: актуализация на брояча')

# vim:set ts=4 sts=4 sw=4 et:
