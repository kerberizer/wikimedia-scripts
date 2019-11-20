#!/usr/bin/env python3

import sys

import pywikibot as pwb


def main(argv):
    wik = pwb.Site(code='bg', fam='wikipedia')
    list_page_name = 'Уикипедия:Патрульори/СФИН'
    list_page = pwb.Page(wik, list_page_name)
    lnum_page = pwb.Page(wik, list_page_name + '/N')
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
    site_list = [_[3:][:-4].replace('\\.', '.') for _ in pattern.splitlines() if _[0:3] == "'\\b"]
    site_list.sort()
    style = ';'.join([
            '-webkit-column-width: 20em',
            '-moz-column-width: 20em',
            'column-width: 20em',
            'font-family: monospace',
            'font-size: small',
            ])
    list_page.text = '\n'.join([
        '__NOINDEX____NOTOC__',
        '{{съкр|У:СФИН}}',
        '<div class="hilite" style="text-align: center; margin-bottom: 1em;">',
        'Автоматично генериран списък на сайтове от ' +
        '[[Специални:Филтър срещу злоупотреби/12|ФИН]]. За повече информация, вижте [[У:ФИН]].',
        '<br>',
        '<big>Моля, не редактирайте страницата!</big>',
        '<br>',
        'За обсъждане на добавяне и изваждане на сайтове, моля използвайте ' +
        '[[{{TALKPAGENAME}}|беседата]].',
        '</div>',
        '<div style="' + style + '">',
        ])
    site_index = ''
    for site in site_list:
        if site[0] != site_index:
            list_page.text += '\n=== {} ===\n'.format(site[0].capitalize())
            site_index = site[0]
        list_page.text += '* {}\n'.format(site)
    list_page.text += '</div>'
    lnum_page.text = str(len(site_list))
    list_page.save(summary='Бот: актуализация', quiet=True)
    lnum_page.save(summary='Бот: актуализация', quiet=True)


if __name__ == '__main__':
    main(sys.argv)

# vim: set ts=4 sts=4 sw=4 tw=100 et:
