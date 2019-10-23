#!/usr/bin/env python3

import re
import sys

import datetime as dt

import pywikibot as pwb


def main(argv):
    wik = pwb.Site(code='bg', fam='wikipedia')
    prefix = 'Инкубатор/Статии/'
    print('Getting the list of articles...')
    lop = []
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
        lop.append((page.title(), since, page.isRedirectPage()))
    lop.sort(key=lambda x: x[1])
    print('{{Уикипедия:Инкубатор/Списък на статиите/Header}}')
    print('{| class="wikitable sortable"')
    print('! Статия !! Влязла в инкубатора')
    for page in lop:
        if dt.datetime.utcnow() - page[1] > dt.timedelta(days=90):
            print('|- style="background-color: gold;"')
        elif page[2]:
            print('|- style="background-color: red;"')
        else:
            print('|-')
        print('| {link} || {timestamp}'.format(
            link='[[' + page[0] + '|' + page[0].rsplit('/', 1)[1] + ']]',
            timestamp=str(page[1])))
    print('|-')
    print('|}')


if __name__ == '__main__':
    main(sys.argv)

# vim: set ts=4 sts=4 sw=4 et:
