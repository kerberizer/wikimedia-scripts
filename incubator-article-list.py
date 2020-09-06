#!/usr/bin/env python3

import re
import sys

import datetime as dt

import mwparserfromhell as mwp
import pywikibot as pwb


def main(argv):
    start_time = dt.datetime.utcnow()
    list_page_fullname = 'Уикипедия:Инкубатор/Списък на статиите'
    article_pageprefix = 'Инкубатор/Статии/'
    article_namespace = 'Уикипедия'
    days_search_for_move = 360
    days_force_delete = 150
    days_critical = 120
    days_warning = 90

    article_fullprefix = article_namespace + ':' + article_pageprefix
    re_page_move = re.compile(
            r'.+ премести страница(та)? „\[\[.+?\]]“ като „\[\[' + article_fullprefix)

    site = pwb.Site(code='bg', fam='wikipedia')
    list_page = pwb.Page(site, list_page_fullname)
    list_page_content = [
        '{{' + list_page_fullname + '/Header}}',
        '{| class="wikitable sortable plainlinks" style="font-size: small;"',
        '! Статия !! Влязла ([[UTC]]) !! Автор !! Проверяващ',
        ]

    list_of_articles = []
    for article in site.allpages(prefix=article_pageprefix, namespace=article_namespace):
        timestamp_entered = None
        status = 'normal'
        reviewer = ''
        # Check if the article is a redirect page (e.g. likely moved to main namespace).
        if article.isRedirectPage():
            timestamp_entered = '0000-00-00T00:00:00Z'
            status = 'redirect'
        else:
            # Parse the revision history to see if the article has been moved to the Incubator.
            oldest_timestamp_to_check = start_time - dt.timedelta(days=days_search_for_move)
            for rev in article.revisions():
                if rev['timestamp'] < oldest_timestamp_to_check:
                    break
                if re_page_move.match(rev['comment']):
                    timestamp_entered = rev['timestamp']
                    break
            # Article either created in Incubator or moved there earlier than days_search_for_move.
            if not timestamp_entered:
                timestamp_entered = article.oldest_revision.timestamp
            # Calculate the age of the article in the Incubator and set the status or delete it.
            days_ago_entered = start_time - timestamp_entered
            if days_ago_entered > dt.timedelta(days=days_force_delete):
                try:
                    article.delete(reason='Автоматично изтриване: [[Уикипедия:Инкубатор/Регламент|'
                                   + 'повече от 120 дни в инкубатора]]', prompt=False)
                except pwb.data.api.APIError as e:
                    print('APIError exception: {}'.format(str(e)), file=sys.stderr)
                else:
                    continue
            elif days_ago_entered > dt.timedelta(days=days_critical):
                status = 'critical'
            elif days_ago_entered > dt.timedelta(days=days_warning):
                status = 'warning'
            # Determine the article author.
            author = article.oldest_revision.user
            author_link = '{{{{потребител|{}}}}}'.format(author)
            # Check if the article has the {{в инкубатора}} template and, if not, add it. While at
            # it, see also if a review is requested and if somebody is doing it. Finally, check if
            # help is being requested. Status priority: critical > review > warning > help > normal.
            has_incubator_template = False
            for template in mwp.parse(article.text).filter_templates():
                if template.name.matches('в инкубатора'):
                    has_incubator_template = True
                elif template.name.matches('инкубатор-проверка'):
                    if status in ('normal', 'help', 'warning'):
                        status = 'review'
                    if template.params:
                        reviewer = template.get(1).value
                elif template.name.matches('помощ'):
                    if status in ('normal'):
                        status = 'help'
            if not has_incubator_template:
                try:
                    article.text = '{{в инкубатора}}\n' + article.text
                    article.save(summary='Бот: добавяне на {{в инкубатора}}', quiet=True)
                except pwb.data.api.APIError as e:
                    print('APIError exception: {}'.format(str(e)), file=sys.stderr)
        # Add an associative array for each article.
        list_of_articles.append({
            'fullname': article.title(),
            'timestamp': str(timestamp_entered).replace('T', ' ')[:-1],
            'status': status,
            'author': author_link,
            'reviewer': reviewer
            })

    list_of_articles.sort(key=lambda _: _['timestamp'])

    # Populate the table of articles.
    for article in list_of_articles:
        article_name = article['fullname'].rsplit('/', 1)[1]
        if article['status'] == 'redirect':
            list_page_content.append('|- style="background-color: magenta;"')
            link = '{{без пренасочване|' + article['fullname'] + '|' + article_name + '}}'
        else:
            if article['status'] == 'critical':
                list_page_content.append('|- style="background-color: red;"')
            elif article['status'] == 'warning':
                list_page_content.append('|- style="background-color: gold;"')
            elif article['status'] == 'review':
                list_page_content.append('|- style="background-color: lightgreen;"')
            elif article['status'] == 'help':
                list_page_content.append('|- style="background-color: cyan;"')
            else:
                list_page_content.append('|-')
            link = [
                    '[[',
                    article['fullname'],
                    '|',
                    article_name,
                    ']]',
                    ' ([[{{TALKPAGENAME:',
                    article['fullname'],
                    '}}|беседа]] - [{{fullurl:',
                    article['fullname'],
                    '|action=history}} история])',
                    ]
        list_page_content.append('| {link} || {timestamp} || {author} || {reviewer}'.format(
            link=''.join(link), timestamp=article['timestamp'], author=article['author'],
            reviewer=article['reviewer']))

    list_page_content.append('|-\n|}')
    list_page.text = '\n'.join(list_page_content)
    list_page.save(summary='Бот: актуализация на списъка', quiet=True)


if __name__ == '__main__':
    main(sys.argv)

# vim: set ts=4 sts=4 sw=4 tw=100 et:
