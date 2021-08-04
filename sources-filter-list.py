#!/usr/bin/env python3

import locale
import sys

from datetime import datetime as dt

import pywikibot as pwb


def main(argv):
    dump_only = False
    if len(argv) > 1:
        if argv.pop() == '--dump':
            dump_only = True
        else:
            print('Error: Unrecognized option.', file=sys.stderr)
            sys.exit(1)
    wik = pwb.Site(code='bg', fam='wikipedia')
    params = {
            'action': 'query',
            'format': 'json',
            'list': 'abusefilters',
            'formatversion': '2',
            'abfstartid': '12',
            'abfendid': '12',
            'abfprop': 'pattern',
            }
    pattern = pwb.data.api.Request(
            site=wik,
            parameters=params
            ).submit()['query']['abusefilters'][0]['pattern']
    site_list = [_[5:][:-4].replace('\\.', '.') for _ in pattern.splitlines() if _[2:5] == "'\\b"]
    site_list.sort()
    if dump_only:
        for site in site_list:
            print('* {}'.format(site))
    else:
        list_page_name = 'Уикипедия:Патрульори/СФИН'
        list_page = pwb.Page(wik, list_page_name)
        lnum_page = pwb.Page(wik, list_page_name + '/N')
        lupd_page = pwb.Page(wik, list_page_name + '/U')

        list_page.text = '{{' + list_page_name + '/H}}\n'
        site_index = ''
        for site in site_list:
            if site[0] != site_index:
                list_page.text += '\n<h3> {} </h3>\n'.format(site[0].capitalize())
                site_index = site[0]
            list_page.text += '* {}\n'.format(site)
        list_page.text += '\n{{' + list_page_name + '/F}}'
        lnum_page.text = str(len(site_list))

        locale.setlocale(locale.LC_TIME, 'bg_BG.UTF-8')
        lupd_page.text = dt.now().strftime('%H:%M на %e %B %Y').lower()
        locale.resetlocale(locale.LC_TIME)

        list_page.save(summary='Бот: актуализация', quiet=True)
        lnum_page.save(summary='Бот: актуализация', quiet=True)
        lupd_page.save(summary='Бот: актуализация', quiet=True)


if __name__ == '__main__':
    main(sys.argv)

# vim: set ts=4 sts=4 sw=4 tw=100 et:
