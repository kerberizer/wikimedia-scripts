#!/usr/bin/env python3

import re
import sys

import datetime as dt

import mwparserfromhell as mwp
import pywikibot as pwb

from pywikibot.exceptions import APIError


def is_proposed_for_deletion(article):
    return re.search(r'{{delete|1=Бот:', article.text, flags=re.I)


def delete(site, article_name, reason):
    article = pwb.Page(site, article_name)
    if not is_proposed_for_deletion(article) and article.exists():
        try:
            article.delete(reason=f'Бот: {reason}', prompt=False, mark=True)
        except APIError as e:
            print('APIError exception: {}'.format(str(e)), file=sys.stderr)


def main(argv):
    start_time = dt.datetime.utcnow()
    list_page_fullname = 'Уикипедия:Инкубатор/Списък на статиите'
    article_pageprefix = ''
    article_namespace = 'Чернова'
    days_search_for_move = 360
    days_force_delete = 150
    days_critical = 120
    days_warning = 90

    article_fullprefix = article_namespace + ':' + article_pageprefix
    re_page_move = re.compile(
            r'.+ премести страница(та)? „\[\[.+?\]]“ като „\[\[' + article_fullprefix)
    re_broken_redirect = re.compile(r'\n#(?:пренасочване|redirect)\s*\[\[', flags=re.I)

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
        # Determine the article author.
        author = article.oldest_revision.user
        author_link = '{{{{потребител|{}}}}}'.format(author)
        # Check if the article is a redirect page (e.g. likely moved to main namespace).
        if article.isRedirectPage() or re_broken_redirect.search(article.text):
            timestamp_entered = '0000-00-00T00:00:00Z'
            status = 'redirect'
        else:
            # Parse the revision history to see if the article has been moved to the Incubator.
            oldest_timestamp_to_check = start_time - dt.timedelta(days=days_search_for_move)
            for rev in article.revisions():
                if rev['timestamp'] < oldest_timestamp_to_check:
                    break
                # Temporary check if Kerberizer is the mover because of the mass move to Draft NS.
                if re_page_move.match(rev['comment']) and not rev['user'] == 'Kerberizer':
                    timestamp_entered = rev['timestamp']
                    break
            # Article either created in Incubator or moved there earlier than days_search_for_move.
            if not timestamp_entered:
                timestamp_entered = article.oldest_revision.timestamp
            # Calculate the age of the article in the Incubator and set the status or delete it.
            days_ago_entered = start_time - timestamp_entered
            if days_ago_entered > dt.timedelta(days=days_force_delete):
                status = 'delete'
            elif days_ago_entered > dt.timedelta(days=days_critical):
                status = 'critical'
            elif days_ago_entered > dt.timedelta(days=days_warning):
                status = 'warning'
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
                except APIError as e:
                    print('APIError exception: {}'.format(str(e)), file=sys.stderr)
                except pwb.exceptions.LockedPage as e:
                    print('WARNING: Locked page: {}'.format(str(e)), file=sys.stderr)
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
        try:
            article_name = article['fullname'].rsplit('/', 1)[1]
        except IndexError:
            article_name = article['fullname'].split(':', 1)[1]
        if article['status'] == 'redirect':
            delete(site, article['fullname'],
                   'Излишен остатък след преместване от [[Уикипедия:Инкубатор]]')
            list_page_content.append('|- style="background-color: #ff66ff;"')
            link = '{{без пренасочване|' + article['fullname'] + '|' + article_name + '}}'
        else:
            if article['status'] == 'delete':
                delete(site, article['fullname'], '[[Уикипедия:Инкубатор/Регламент|повече от '
                       + '{days} дни в инкубатора]]'.format(days=days_force_delete))
                list_page_content.append('|- style="background-color: #ff6666;"')
            elif article['status'] == 'critical':
                list_page_content.append('|- style="background-color: #ffb366;"')
            elif article['status'] == 'warning':
                list_page_content.append('|- style="background-color: #ffff66;"')
            elif article['status'] == 'review':
                list_page_content.append('|- style="background-color: #66ff66;"')
            elif article['status'] == 'help':
                list_page_content.append('|- style="background-color: #66ffff;"')
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
