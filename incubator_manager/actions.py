import sys

import pywikibot
from pywikibot.exceptions import APIError

from .classify import is_proposed_for_deletion


def save_page_if_changed(page, text, summary):
    if page.text == text:
        return False
    page.text = text
    page.save(summary=summary, quiet=True)
    return True


def delete_or_mark(site, article_name, reason, delete_talk=False):
    page = pywikibot.Page(site, article_name)
    pages = [page]
    if delete_talk:
        pages.append(page.toggleTalkPage())

    for item in pages:
        if is_proposed_for_deletion(item.text) or not item.exists():
            continue
        try:
            item.delete(reason=f"Бот: {reason}", prompt=False, mark=True)
        except APIError as error:
            print(f"APIError exception: {error}", file=sys.stderr)


def add_incubator_template(article):
    article.text = "{{Чернова}}\n" + article.text
    try:
        article.save(summary="Бот: добавяне на помощен шаблон за чернови", quiet=True)
    except APIError as error:
        print(f"APIError exception: {error}", file=sys.stderr)
    except pywikibot.exceptions.LockedPage as error:
        print(f"WARNING: Locked page: {error}", file=sys.stderr)


def apply_maintenance_actions(site, records, config):
    for record in sorted(
        records, key=lambda item: str(item.timestamp).replace("T", " ")[:-1]
    ):
        if record.status == "redirect":
            delete_or_mark(
                site,
                record.fullname,
                "Излишен остатък след преместване от [[Уикипедия:Инкубатор]] "
                + config.sysop_ping,
                delete_talk=True,
            )


def planned_actions(records, config):
    actions = []
    for record in records:
        if record.needs_incubator_template:
            actions.append(f"add-template\t{record.fullname}")

    for record in sorted(
        records, key=lambda item: str(item.timestamp).replace("T", " ")[:-1]
    ):
        if record.status == "redirect":
            actions.append(f"delete-or-mark-page-and-talk\t{record.fullname}")
    actions.append(f"update-list\t{config.list_page_name}")
    return actions
