#!/usr/bin/env python3

from datetime import datetime as dt
from json import loads as jload

from dateutil.relativedelta import relativedelta as rd
from pywikibot import Page, Site, User
from pywikibot.exceptions import APIError


class ThanksMeter:

    def __init__(self):
        self._site = Site('bg', fam='wikipedia')
        self._page = Page(self._site, 'Потребител:Iliev/Мерсиметър')
        self._settings_page = Page(self._site, 'Потребител:Iliev/Мерсиметър/settings.json')
        self._settings = jload(self._settings_page.text)

    def _ignore_user(self, user):
        if user in self._settings['ignored_users']:
            return True
        elif User(self._site, user).is_blocked():
            self._settings['ignored_users'].append(user)
            return True
        return False

    def _get_thanks(self, since_datetime):
        thanks = dict(r=dict(), s=dict(), c=0)
        for thank in self._site.logevents(logtype='thanks', end=since_datetime):
            if self._ignore_user(thank.user()):
                continue
            try:
                thanks['r'][thank.page().title(with_ns=False)] += 1
            except KeyError:
                thanks['r'][thank.page().title(with_ns=False)] = 1
            try:
                thanks['s'][thank.user()] += 1
            except KeyError:
                thanks['s'][thank.user()] = 1
            thanks['c'] += 1
        return self._sort_user_thanks(thanks)

    def _sort_user_thanks(self, user_thanks_dict):
        presort = dict(
                r=dict(sorted(user_thanks_dict['r'].items())),
                s=dict(sorted(user_thanks_dict['s'].items())),
                c=user_thanks_dict['c']
                )
        return dict(
                r=dict(sorted(presort['r'].items(), key=lambda _: _[1], reverse=True)),
                s=dict(sorted(presort['s'].items(), key=lambda _: _[1], reverse=True)),
                c=presort['c']
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
        self._page.text += "{{subst:CURRENTMONTHNAME}} {{subst:CURRENTYEAR}}''' г.''\n"

    def save_page(self):
        try:
            self._page.save(summary='Бот: актуализация', minor=False)
        except APIError as e:
            print('ERROR: Cannot save page: APIError: ' + str(e))

    def draw_tables(self, since_datetime, group_title):
        thanks_help = ':en:Help:Notifications/Thanks'
        thanks = self._get_thanks(since_datetime)
        self._page.text += f'== {group_title} ==\n'
        self._page.text += f": ''Общо [[{thanks_help}|благодарности]]: "
        self._page.text += f"'''{thanks['c']}'''''\n"
        self._page.text += '<div style="float: left;">\n'
        self._draw_table(thanks['r'], 'Получени')
        self._page.text += '</div><div style="float: left;">\n'
        self._draw_table(thanks['s'], 'Изпратени')
        self._page.text += '</div>{{br}}\n'


def main():
    NOW = dt.utcnow()
    TABLE_CONFIG = [
            (NOW - rd(days=1), 'За последния ден'),
            (NOW - rd(weeks=1), 'За последната седмица'),
            (NOW - rd(months=1), 'За последния месец'),
            (NOW - rd(months=3), 'За последните три месеца'),
            (NOW - rd(years=1), 'За последната година'),
            ]
    thanksmeter = ThanksMeter()
    thanksmeter.init_page()
    for since_datetime, group_title in TABLE_CONFIG:
        thanksmeter.draw_tables(since_datetime, group_title)
    thanksmeter.save_page()


if __name__ == '__main__':
    main()

# vim: set ts=4 sts=4 sw=4 et:
