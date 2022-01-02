#!/usr/bin/env python3

from datetime import datetime as dt

from dateutil.relativedelta import relativedelta as rd
from pywikibot import Page, Site
from pywikibot.exceptions import APIError


class ThanksMeter:

    def __init__(self):
        self._site = Site('bg', fam='wikipedia')
        self._page = Page(self._site, 'Потребител:Iliev/Мерсиметър')

    def _get_thanks_since(self, date):
        thanks = dict(r=dict(), s=dict())
        for thank in self._site.logevents(logtype='thanks', end=date):
            try:
                thanks['r'][thank.page().title(with_ns=False)] += 1
            except KeyError:
                thanks['r'][thank.page().title(with_ns=False)] = 1
            try:
                thanks['s'][thank.user()] += 1
            except KeyError:
                thanks['s'][thank.user()] = 1

        return dict(
                r=dict(sorted(thanks['r'].items(), key=lambda _: _[1], reverse=True)),
                s=dict(sorted(thanks['s'].items(), key=lambda _: _[1], reverse=True))
                )

    def _draw_table(self, user_thanks_dict, title):
        self._page.text += '{| class="wikitable sortable col-2-right"\n'
        self._page.text += f'|+ {title}\n'
        self._page.text += '! Редактор !! Брой\n|-\n'
        for user, thanks in user_thanks_dict.items():
            self._page.text += f'| [[Потребител:{user}|{user}]] || {thanks}\n|-\n'
        self._page.text += '|}\n'

    def init_page(self):
        script_url = 'https://github.com/kerberizer/wikimedia-scripts/blob/master/thanksmeter.py'
        self._page.text = '\'\'Тази страница е генерирана автоматично в {{subst:CURRENTTIME}} на '
        self._page.text += '{{subst:CURRENTDAY}} {{subst:CURRENTMONTHNAME}} {{subst:CURRENTYEAR}} '
        self._page.text += f'от [{script_url} скрипт].\'\'\n'

    def save_page(self):
        try:
            self._page.save(summary='Бот: актуализация', minor=False)
        except APIError as e:
            print('ERROR: Cannot save page: APIError: ' + str(e))

    def draw_tables_since(self, date, title):
        thanks = self._get_thanks_since(date)
        self._page.text += f'== {title} ==\n'
        self._page.text += '<div style="float: left;">\n'
        self._draw_table(thanks['r'], 'Получени благодарности')
        self._page.text += '</div><div style="float: left;">\n'
        self._draw_table(thanks['s'], 'Изпратени благодарности')
        self._page.text += '</div>{{br}}\n'


def main():
    thanksmeter = ThanksMeter()
    thanksmeter.init_page()
    since = dt.utcnow() - rd(days=1)
    thanksmeter.draw_tables_since(since, 'За последния ден')
    since = dt.utcnow() - rd(weeks=1)
    thanksmeter.draw_tables_since(since, 'За последната седмица')
    since = dt.utcnow() - rd(months=1)
    thanksmeter.draw_tables_since(since, 'За последния месец')
    since = dt.utcnow() - rd(months=3)
    thanksmeter.draw_tables_since(since, 'За последните три месеца')
    thanksmeter.save_page()


if __name__ == '__main__':
    main()

# vim: set ts=4 sts=4 sw=4 et:
