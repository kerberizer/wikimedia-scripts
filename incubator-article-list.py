#!/usr/bin/env python3

import re
import sys

import datetime as dt

import pywikibot as pwb


def main(argv):
    prefix = 'Инкубатор/Статии/'
    wik = pwb.Site(code='bg', fam='wikipedia')
    list_page = pwb.Page(wik, 'Уикипедия:Инкубатор/Списък на статиите')
    list_page_content = []
    list_of_articles = []

    for page in wik.allpages(prefix=prefix, namespace=4):
        since = None
        for rev in page.revisions():
            if rev['timestamp'] < dt.datetime.utcnow()-dt.timedelta(days=360):
                break
            if re.match(
                    r'.+ премести страница(та)? „\[\[.+?\]]“ като „\[\[Уикипедия:Инкубатор/Статии',
                    rev['comment']):
                since = rev['timestamp']
                break
        if not since:
            since = page.oldest_revision.timestamp
        list_of_articles.append((page.title(), since, page.isRedirectPage()))
    list_of_articles.sort(key=lambda x: x[1])
    list_page_content = [
        '{{Уикипедия:Инкубатор/Списък на статиите/Header}}',
        '{| class="wikitable sortable"',
        '! Статия !! Влязла в инкубатора',
        ]
    for page in list_of_articles:
        # page[1] is the timestamp of the article either being created in or moved to the Incubator.
        if dt.datetime.utcnow() - page[1] > dt.timedelta(days=120):
            list_page_content.append('|- style="background-color: red;"')
        elif dt.datetime.utcnow() - page[1] > dt.timedelta(days=90):
            list_page_content.append('|- style="background-color: gold;"')
        # page[2] is the boolean from page.isRedirectPage().
        elif page[2]:
            list_page_content.append('|- style="background-color: magenta;"')
        else:
            list_page_content.append('|-')
        list_page_content.append('| {link} || {timestamp}'.format(
            link='[[' + page[0] + '|' + page[0].rsplit('/', 1)[1] + ']]',
            timestamp=str(page[1])))
    list_page_content.extend([
        '|-',
        '|}',
        ])
    list_page.text = '\n'.join(list_page_content)
    list_page.save(summary='Бот: актуализация на списъка', quiet=True)


if __name__ == '__main__':
    main(sys.argv)

# vim: set ts=4 sts=4 sw=4 et:
