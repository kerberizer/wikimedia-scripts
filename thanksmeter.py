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
        return self._sort_user_thanks(thanks)

    def _sort_user_thanks(self, user_thanks_dict):
        presort = dict(
                r=dict(sorted(user_thanks_dict['r'].items())),
                s=dict(sorted(user_thanks_dict['s'].items()))
                )
        return dict(
                r=dict(sorted(presort['r'].items(), key=lambda _: _[1], reverse=True)),
                s=dict(sorted(presort['s'].items(), key=lambda _: _[1], reverse=True))
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
        self._page.text = f"''Тази страница е генерирана автоматично от [{script_url} скрипт] в "
        self._page.text += "'''{{subst:CURRENTTIME}}''' [[UTC]] на '''{{subst:CURRENTDAY}} "
        self._page.text += "{{subst:CURRENTMONTHNAME}} {{subst:CURRENTYEAR}}'''.''\n"

    def save_page(self):
        try:
            self._page.save(summary='Бот: актуализация', minor=False)
        except APIError as e:
            print('ERROR: Cannot save page: APIError: ' + str(e))

    def draw_tables_since(self, date, title):
        thanks = self._get_thanks_since(date)
        self._page.text += f'== {title} ==\n'
        self._page.text += '<div style="float: left;">\n'
        self._draw_table(thanks['r'], 'Получени благодарности')
        self._page.text += '</div><div style="float: left;">\n'
        self._draw_table(thanks['s'], 'Изпратени благодарности')
        self._page.text += '</div>{{br}}\n'


def main():
    thanksmeter = ThanksMeter()
    thanksmeter.init_page()
    thanksmeter.draw_tables_since(
            dt.utcnow() - rd(days=1), 'За последния ден')
    thanksmeter.draw_tables_since(
            dt.utcnow() - rd(weeks=1), 'За последната седмица')
    thanksmeter.draw_tables_since(
            dt.utcnow() - rd(months=1), 'За последния месец')
    thanksmeter.draw_tables_since(
            dt.utcnow() - rd(months=3), 'За последните три месеца')
    thanksmeter.draw_tables_since(
            dt.utcnow() - rd(years=1), 'За последната година')
    thanksmeter.save_page()


if __name__ == '__main__':
    main()

# vim: set ts=4 sts=4 sw=4 et:
