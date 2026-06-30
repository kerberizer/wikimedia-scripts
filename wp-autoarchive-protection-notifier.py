#!/usr/bin/env python3

import argparse
import sys

import pywikibot as pwb


TEMPLATE_TITLE = 'Шаблон:Автоматично архивиране'
TALK_PAGE_TITLE = 'Потребител беседа:Iliev'
EDIT_SUMMARY = 'Бот: известяване за защитени страници с автоматично архивиране'
EXCLUDED_PAGE_TITLES = {
    'Шаблон:Искане за чекюзър',
}


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Notify Iliev about fully protected pages using automatic archiving.'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='print the matching pages and message without saving',
    )
    return parser.parse_args(argv[1:])


def is_fully_protected(page):
    edit_protection = page.protection().get('edit')
    return bool(edit_protection) and edit_protection[0] == 'sysop'


def find_fully_protected_pages(site):
    template = pwb.Page(site, TEMPLATE_TITLE)
    pages = [
        page
        for page in template.embeddedin(content=False)
        if page.title() not in EXCLUDED_PAGE_TITLES and is_fully_protected(page)
    ]
    return sorted(pages, key=lambda page: page.title())


def build_message(pages):
    lines = [
        '== Защитени страници с автоматично архивиране ==',
        '',
        (
            'Следните страници използват {{ш|Автоматично архивиране}} '
            'и са напълно защитени:'
        ),
        '',
    ]

    for page in pages:
        lines.append('* {}'.format(page.title(as_link=True, textlink=True)))

    lines.extend(['', '~~~~', ''])
    return '\n'.join(lines)


def append_message(site, message):
    talk_page = pwb.Page(site, TALK_PAGE_TITLE)
    try:
        text = talk_page.get()
    except pwb.exceptions.NoPageError:
        text = ''

    separator = '\n\n' if text.strip() else ''
    talk_page.text = text.rstrip() + separator + message.rstrip() + '\n'
    talk_page.save(summary=EDIT_SUMMARY, minor=False, bot=True, quiet=True)


def main(argv):
    args = parse_args(argv)
    site = pwb.Site(code='bg', fam='wikipedia')
    pages = find_fully_protected_pages(site)

    if not pages:
        print('No fully protected pages transclude {}.'.format(TEMPLATE_TITLE))
        return 0

    message = build_message(pages)
    if args.dry_run:
        print('Found {} fully protected page(s):'.format(len(pages)))
        for page in pages:
            print('* {}'.format(page.title()))
        print('\nMessage to append to {}:\n'.format(TALK_PAGE_TITLE))
        print(message)
        return 0

    site.login()
    append_message(site, message)
    print('Notified {} about {} fully protected page(s).'.format(TALK_PAGE_TITLE, len(pages)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))


# vim: set ts=4 sts=4 sw=4 tw=100 et:
