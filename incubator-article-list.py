#!/usr/bin/env python3

import re
import sys

import datetime as dt

import pywikibot as pwb


def main(argv):
    list_page_fullname = 'Уикипедия:Инкубатор/Списък на статиите'
    article_pageprefix = 'Инкубатор/Статии/'
    article_namespace = 'Уикипедия'
    days_search_for_move = 360
    days_critical = 120
    days_warning = 90

    article_fullprefix = article_namespace + ':' + article_pageprefix
    re_page_move = re.compile(
            r'.+ премести страница(та)? „\[\[.+?\]]“ като „\[\[' + article_fullprefix)

    site = pwb.Site(code='bg', fam='wikipedia')
    list_page = pwb.Page(site, list_page_fullname)
    list_page_content = [
        '{{' + list_page_fullname + '/Header}}',
        '{| class="wikitable sortable"',
        '! Статия !! Влязла в инкубатора',
        ]

    list_of_articles = []
    for article in site.allpages(prefix=article_pageprefix, namespace=article_namespace):
        timestamp_entered_incubator = None
        # Parse the revision history to see if the article has been moved to the Incubator.
        oldest_timestamp_to_check = dt.datetime.utcnow() - dt.timedelta(days=days_search_for_move)
        for rev in article.revisions():
            if rev['timestamp'] < oldest_timestamp_to_check:
                break
            if re_page_move.match(rev['comment']):
                timestamp_entered_incubator = rev['timestamp']
                break
        # Article either created in Incubator or moved there earlier than days_search_for_move.
        if not timestamp_entered_incubator:
            timestamp_entered_incubator = article.oldest_revision.timestamp
        # Add an associative array for each article.
        list_of_articles.append({
            'fullname': article.title(),
            'timestamp': timestamp_entered_incubator,
            'is_redirect': article.isRedirectPage()
            })

    list_of_articles.sort(key=lambda x: x['timestamp'])

    # Populate the table of articles.
    for article in list_of_articles:
        article_name = article['fullname'].rsplit('/', 1)[1]
        days_ago_entered = dt.datetime.utcnow() - article['timestamp']
        if article['is_redirect']:
            list_page_content.append('|- style="background-color: magenta;"')
            link = '{{без пренасочване|' + article['fullname'] + '|' + article_name + '}}'
        else:
            if days_ago_entered > dt.timedelta(days=days_critical):
                list_page_content.append('|- style="background-color: red;"')
            elif days_ago_entered > dt.timedelta(days=days_warning):
                list_page_content.append('|- style="background-color: gold;"')
            else:
                list_page_content.append('|-')
            link = '[[' + article['fullname'] + '|' + article_name + ']]'
        list_page_content.append('| {link} || {timestamp}'.format(
            link=link, timestamp=str(article['timestamp'])))

    list_page_content.append('|-\n|}')
    list_page.text = '\n'.join(list_page_content)
    list_page.save(summary='Бот: актуализация на списъка', quiet=True)


if __name__ == '__main__':
    main(sys.argv)

# vim: set ts=4 sts=4 sw=4 tw=100 et:
