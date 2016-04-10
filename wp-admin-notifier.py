#!/usr/bin/env python3
# -*- coding: utf-8  -*-

import datetime, difflib, os.path, pickle, pywikibot, smtplib
from pywikibot import pagegenerators
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

fname_lastDateTime = os.path.expanduser('~/.wp-admin-notifier/datetime-last.dat')

f = open(fname_lastDateTime, 'rb')
lastDateTime = pickle.load(f)
f.close()

currentDateTime = datetime.datetime.utcnow()

mailFrom = 'WikiBG Admin Notifier <admin-notifier@wikimedia.bg>'
mailRcpt = 'admin-notify@wikimedia.bg'
mailSubj = u'Промени в "Заявки към администраторите"'
mailBody = u''

mail = MIMEMultipart('alternative')
mail.set_charset('utf-8')
mail['From'] = mailFrom
mail['To'] = mailRcpt
mail['Subject'] = Header(mailSubj.encode('utf-8'), 'utf-8')

adminReqPagesPrevYear = pagegenerators.PrefixingPageGenerator(
                            u'Заявки към администраторите/' + str(currentDateTime.year - 1),
                            namespace=u'Уикипедия',
                            includeredirects=False)

adminReqPagesCurrYear = pagegenerators.PrefixingPageGenerator(
                            u'Заявки към администраторите/' + str(currentDateTime.year),
                            namespace=u'Уикипедия',
                            includeredirects=False)

adminReqPages = pagegenerators.CombinedPageGenerator(
                            [adminReqPagesPrevYear, adminReqPagesCurrYear]) 

adminReqPagesRecent = pagegenerators.EdittimeFilterPageGenerator(
                            adminReqPages,
                            last_edit_start=lastDateTime)

revisionCount = 0
for reqPage in adminReqPagesRecent:
    mailBody += '##############################################################################\n\n'
    mailBody += u'https://bg.wikipedia.org/wiki/' + reqPage.title().replace(' ', '_') + '\n\n'
    mailBody += '##############################################################################\n\n'
    for revision in reqPage.revisions(reverse=True, starttime=lastDateTime, content=True):
        revisionCount += 1
        mailBody += u'Дата и час: %s' % revision.timestamp + '\n'
        mailBody += u'Потребител: %s' % revision.user + '\n'
        mailBody += u'Резюме: %s' % revision.comment + '\n\n'
        if revision._parent_id:
            try:
                diff = difflib.unified_diff(
                            reqPage.getOldVersion(revision._parent_id).splitlines(),
                            revision.text.splitlines(),
                            n=0)
                # We don't need the diff headers, so iterate with .next() twice over them.
                # However, if the edit has been empty (e.g. changed only the protection level),
                # a StopIteration exception will be raised that we need to catch properly.
                try:
                    next(diff)
                    next(diff)
                    for line in diff:
                        mailBody += line + '\n'
                except StopIteration:
                    mailBody += u'НЯМА РАЗЛИКА (ПРОМЯНА НА ЗАЩИТАТА И Т.Н.)\n'
            except AttributeError:
                mailBody += u'СКРИТ ТЕКСТ НА РЕДАКЦИЯ\n'
        else:
            try:
                mailBody += revision.text + '\n'
            except AttributeError:
                mailBody += u'СКРИТ ТЕКСТ НА РЕДАКЦИЯ\n'
        mailBody += '\n- - - 8< - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -\n\n'

if revisionCount:
    mailBody = u'НОВИ РЕДАКЦИИ: %i\n\n' % revisionCount + mailBody + u'КРАЙ НА СЪОБЩЕНИЕТО\n'

    mail.attach(MIMEText(mailBody.encode('utf-8'), 'plain', 'utf-8'))

    mailer = smtplib.SMTP('localhost')
    mailer.sendmail(mailFrom, mailRcpt, mail.as_string())
    mailer.quit()

f = open(fname_lastDateTime, 'wb')
pickle.dump(currentDateTime, f)
f.close()
