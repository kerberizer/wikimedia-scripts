import datetime
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pywikibot

from incubator_manager.actions import apply_maintenance_actions, planned_actions
from incubator_manager.classify import (
    classify_age,
    inspect_templates,
    is_broken_redirect,
    is_proposed_for_deletion,
    legacy_timestamp_text,
)
from incubator_manager.cli import run
from incubator_manager.collect import find_entered_timestamp
from incubator_manager.config import Config
from incubator_manager.identity import assert_write_identity
from incubator_manager.model import MALFORMED_REVIEW_TEMPLATE, DraftRecord
from incubator_manager.render import render_list


class IncubatorClassificationTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()

    def test_age_thresholds_remain_strict(self):
        self.assertEqual(
            classify_age(
                datetime.timedelta(days=self.config.warning_days), self.config
            ),
            "normal",
        )
        self.assertEqual(
            classify_age(
                datetime.timedelta(days=self.config.warning_days, seconds=1),
                self.config,
            ),
            "warning",
        )
        self.assertEqual(
            classify_age(
                datetime.timedelta(days=self.config.critical_days), self.config
            ),
            "warning",
        )
        self.assertEqual(
            classify_age(
                datetime.timedelta(days=self.config.critical_days, seconds=1),
                self.config,
            ),
            "critical",
        )
        self.assertEqual(
            classify_age(
                datetime.timedelta(days=self.config.long_stay_days), self.config
            ),
            "critical",
        )
        self.assertEqual(
            classify_age(
                datetime.timedelta(days=self.config.long_stay_days, seconds=1),
                self.config,
            ),
            "long_stay",
        )

    def test_current_templates_and_positional_reviewer_are_recognized(self):
        state = inspect_templates(
            "{{В инкубатора}}\n{{инкубатор-проверка|Example}}\n{{помощ}}"
        )
        self.assertTrue(state.has_incubator_template)
        self.assertTrue(state.review_requested)
        self.assertTrue(state.help_requested)
        self.assertEqual(state.reviewer, "Example")

    def test_named_reviewer_is_reported_as_malformed_under_current_rules(self):
        state = inspect_templates("{{инкубатор-проверка|проверяващ=Example}}")
        self.assertEqual(state.reviewer, MALFORMED_REVIEW_TEMPLATE)

    def test_redirect_and_deletion_patterns_remain_compatible(self):
        self.assertTrue(is_broken_redirect("text\n#ПРЕНАСОЧВАНЕ [[Статия]]"))
        self.assertFalse(is_broken_redirect("#ПРЕНАСОЧВАНЕ [[Статия]]"))
        self.assertTrue(is_proposed_for_deletion("{{delete|1=Бот: причина}}"))
        self.assertTrue(is_proposed_for_deletion("{{DELETE|1=бот: причина}}"))
        self.assertFalse(is_proposed_for_deletion("{{бързо изтриване|Бот: причина}}"))

    def test_status_priority_remains_unchanged(self):
        self.assertEqual(
            DraftRecord(
                "Чернова:A", "x", "Author", age_status="critical", review_requested=True
            ).status,
            "critical",
        )
        self.assertEqual(
            DraftRecord(
                "Чернова:A",
                "x",
                "Author",
                age_status="long_stay",
                review_requested=True,
            ).status,
            "long_stay",
        )
        self.assertEqual(
            DraftRecord(
                "Чернова:A", "x", "Author", age_status="warning", review_requested=True
            ).status,
            "review",
        )
        self.assertEqual(
            DraftRecord("Чернова:A", "x", "Author", help_requested=True).status,
            "help",
        )
        self.assertEqual(
            DraftRecord("Чернова:A", "x", "Author", is_redirect=True).status,
            "redirect",
        )

    def test_legacy_timestamp_format_is_preserved(self):
        self.assertEqual(
            legacy_timestamp_text(pywikibot.Timestamp(2026, 11, 25, 12, 34, 56)),
            "2026-11-25 12:34:56",
        )
        self.assertEqual(
            legacy_timestamp_text("0000-00-00T00:00:00Z"),
            "0000-00-00 00:00:00",
        )


class IncubatorMoveTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.start = datetime.datetime(2026, 11, 25, 12, 0, 0)

    def revision(self, days, user, comment):
        return {
            "timestamp": self.start - datetime.timedelta(days=days),
            "user": user,
            "comment": comment,
        }

    def test_move_to_current_prefix_is_found_and_kerberizer_move_is_skipped(self):
        revisions = [
            self.revision(
                2,
                "Kerberizer",
                "X премести страницата „[[Стара]]“ като „[[Чернова:Пример]]“",
            ),
            self.revision(
                10,
                "Mover",
                "X премести страница „[[Стара]]“ като „[[Чернова:Пример]]“",
            ),
        ]
        self.assertEqual(
            find_entered_timestamp(revisions, self.start, self.config),
            self.start - datetime.timedelta(days=10),
        )

    def test_move_to_legacy_prefix_is_found(self):
        revision = self.revision(
            20,
            "Mover",
            "X премести страницата „[[Стара]]“ като „[[Уикипедия:Инкубатор/Статии/Пример]]“",
        )
        self.assertEqual(
            find_entered_timestamp([revision], self.start, self.config),
            revision["timestamp"],
        )

    def test_search_stops_before_revisions_older_than_window(self):
        revision = self.revision(
            361,
            "Mover",
            "X премести страницата „[[Стара]]“ като „[[Чернова:Пример]]“",
        )
        self.assertIsNone(find_entered_timestamp([revision], self.start, self.config))


class IncubatorOutputTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()

    def test_generated_table_keeps_current_wikitext_shape(self):
        records = [
            DraftRecord(
                fullname="Чернова:По-нова",
                timestamp=pywikibot.Timestamp(2026, 11, 2, 3, 4, 5),
                author="Author Two",
                age_status="warning",
                review_requested=True,
                reviewer="Reviewer",
            ),
            DraftRecord(
                fullname="Чернова:Група/По-стара",
                timestamp=pywikibot.Timestamp(2026, 11, 1, 2, 3, 4),
                author="Author One",
                age_status="long_stay",
            ),
        ]
        expected = (
            "{{Уикипедия:Инкубатор/Списък на статиите/Header}}\n"
            '{| class="wikitable sortable plainlinks" style="font-size: small;"\n'
            "! Статия !! Влязла ([[UTC]]) !! Автор !! Проверяващ\n"
            '|- style="background-color: #eaecf0;"\n'
            "| [[Чернова:Група/По-стара|По-стара]] "
            "([[{{TALKPAGENAME:Чернова:Група/По-стара}}|беседа]] - "
            "[{{fullurl:Чернова:Група/По-стара|action=history}} история]) || "
            "2026-11-01 02:03:04 || {{потребител|Author One}} || \n"
            '|- style="background-color: #66ff66;"\n'
            "| [[Чернова:По-нова|По-нова]] "
            "([[{{TALKPAGENAME:Чернова:По-нова}}|беседа]] - "
            "[{{fullurl:Чернова:По-нова|action=history}} история]) || "
            "2026-11-02 03:04:05 || {{потребител|Author Two}} || Reviewer\n"
            "|-\n"
            "|}"
        )
        self.assertEqual(render_list(records, self.config), expected)

    def test_empty_table_has_no_extra_blank_row(self):
        expected = (
            "{{Уикипедия:Инкубатор/Списък на статиите/Header}}\n"
            '{| class="wikitable sortable plainlinks" style="font-size: small;"\n'
            "! Статия !! Влязла ([[UTC]]) !! Автор !! Проверяващ\n"
            "|-\n"
            "|}"
        )
        self.assertEqual(render_list([], self.config), expected)

    def test_planned_actions_keep_mutation_order(self):
        records = [
            DraftRecord(
                "Чернова:Нова",
                datetime.datetime(2026, 11, 2),
                "Author",
                needs_incubator_template=True,
            ),
            DraftRecord(
                "Чернова:Стара",
                datetime.datetime(2026, 11, 1),
                "Author",
                age_status="long_stay",
                needs_incubator_template=True,
            ),
            DraftRecord(
                "Чернова:Пренасочване",
                "0000-00-00T00:00:00Z",
                "Author",
                is_redirect=True,
            ),
        ]
        self.assertEqual(
            planned_actions(records, self.config),
            [
                "add-template\tЧернова:Нова",
                "add-template\tЧернова:Стара",
                "delete-or-mark-page-and-talk\tЧернова:Пренасочване",
                "update-list\tУикипедия:Инкубатор/Списък на статиите",
            ],
        )

    def test_long_stay_draft_has_no_maintenance_action(self):
        long_stay = DraftRecord(
            "Чернова:Стара",
            pywikibot.Timestamp(2026, 1, 1),
            "Author",
            age_status="long_stay",
        )
        with mock.patch("incubator_manager.actions.delete_or_mark") as delete_or_mark:
            apply_maintenance_actions(mock.Mock(), [long_stay], self.config)

        delete_or_mark.assert_not_called()


class IncubatorCliTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.article = mock.Mock()
        self.record = DraftRecord(
            "Чернова:Пример",
            pywikibot.Timestamp(2026, 11, 25),
            "Author",
            needs_incubator_template=True,
        )
        self.site = mock.Mock()
        self.list_page = mock.Mock()

    def test_dry_run_performs_no_writes(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch("incubator_manager.cli.pywikibot.Site", return_value=self.site),
            mock.patch(
                "incubator_manager.cli.pywikibot.Page", return_value=self.list_page
            ),
            mock.patch(
                "incubator_manager.cli.iter_records",
                return_value=[(self.article, self.record)],
            ),
            mock.patch("incubator_manager.cli.assert_write_identity") as identity,
            mock.patch("incubator_manager.cli.add_incubator_template") as add_template,
            mock.patch(
                "incubator_manager.cli.apply_maintenance_actions"
            ) as maintenance,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = run(dry_run=True, config=self.config)

        self.assertEqual(result, 0)
        identity.assert_not_called()
        add_template.assert_not_called()
        maintenance.assert_not_called()
        self.list_page.save.assert_not_called()
        self.assertIn("DRY-RUN: add-template\tЧернова:Пример", stderr.getvalue())
        self.assertIn(self.config.list_header_template, stdout.getvalue())

    def test_live_mode_retains_existing_write_sequence(self):
        with (
            mock.patch("incubator_manager.cli.pywikibot.Site", return_value=self.site),
            mock.patch(
                "incubator_manager.cli.pywikibot.Page", return_value=self.list_page
            ),
            mock.patch(
                "incubator_manager.cli.iter_records",
                return_value=[(self.article, self.record)],
            ),
            mock.patch("incubator_manager.cli.assert_write_identity") as identity,
            mock.patch("incubator_manager.cli.add_incubator_template") as add_template,
            mock.patch(
                "incubator_manager.cli.apply_maintenance_actions"
            ) as maintenance,
        ):
            result = run(dry_run=False, config=self.config)

        self.assertEqual(result, 0)
        identity.assert_called_once_with(self.site, "Eliza Beth")
        add_template.assert_called_once_with(self.article)
        maintenance.assert_called_once_with(self.site, [self.record], self.config)
        self.list_page.save.assert_called_once_with(
            summary="Бот: актуализация на списъка",
            quiet=True,
        )


class IncubatorIdentityTests(unittest.TestCase):
    def test_expected_account_is_accepted(self):
        site = mock.Mock()
        site.user.return_value = "Eliza Beth"

        assert_write_identity(site, "Eliza Beth")

        site.login.assert_called_once_with()
        site.user.assert_called_once_with()

    def test_unexpected_account_is_rejected(self):
        site = mock.Mock()
        site.user.return_value = "Kerberizer"

        with self.assertRaisesRegex(
            RuntimeError,
            "refusing to write as 'Kerberizer'; expected 'Eliza Beth'",
        ):
            assert_write_identity(site, "Eliza Beth")


if __name__ == "__main__":
    unittest.main()
