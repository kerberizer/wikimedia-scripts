import datetime
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pywikibot

from incubator_manager.actions import (
    add_incubator_template,
    apply_maintenance_actions,
    planned_actions,
    save_page_if_changed,
)
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
from incubator_manager.metadata import (
    enrich_queue_record,
    find_current_request_timestamps,
    latest_talk_activity,
)
from incubator_manager.model import (
    MALFORMED_REVIEW_TEMPLATE,
    POSITIONAL_REVIEW_PARAMETER,
    DraftRecord,
)
from incubator_manager.queues import (
    group_records,
    render_queue_preview,
    render_task_page,
)
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

    def test_new_template_family_is_recognized_as_managed_draft_states(self):
        review = inspect_templates("{{Чернова за преглед}}")
        help_request = inspect_templates("{{Чернова за помощ}}")
        base = inspect_templates("{{Чернова}}")

        self.assertTrue(review.has_incubator_template)
        self.assertTrue(review.review_requested)
        self.assertTrue(help_request.has_incubator_template)
        self.assertTrue(help_request.help_requested)
        self.assertTrue(base.has_incubator_template)

    def test_named_reviewer_is_accepted_for_new_and_legacy_templates(self):
        canonical = inspect_templates("{{Чернова за преглед|преглеждащ=Example}}")
        legacy = inspect_templates("{{инкубатор-проверка|преглеждащ=Example}}")

        self.assertEqual(canonical.reviewer, "Example")
        self.assertEqual(canonical.review_template_name, "чернова за преглед")
        self.assertFalse(canonical.legacy_reviewer_parameter)
        self.assertEqual(legacy.reviewer, "Example")
        self.assertEqual(legacy.review_template_name, "инкубатор-проверка")
        self.assertFalse(legacy.legacy_reviewer_parameter)

    def test_legacy_positional_reviewer_remains_compatible_during_migration(self):
        state = inspect_templates("{{инкубатор-проверка|Example}}")

        self.assertEqual(state.reviewer, "Example")
        self.assertTrue(state.legacy_reviewer_parameter)
        self.assertEqual(state.reviewer_diagnostic, "")

    def test_canonical_positional_reviewer_gets_explicit_diagnostic(self):
        state = inspect_templates("{{Чернова за преглед|Example}}")

        self.assertEqual(state.reviewer, "")
        self.assertEqual(state.reviewer_diagnostic, POSITIONAL_REVIEW_PARAMETER)

    def test_unknown_review_parameter_is_reported_as_malformed(self):
        state = inspect_templates("{{инкубатор-проверка|друг=Example}}")

        self.assertEqual(state.reviewer, "")
        self.assertEqual(state.reviewer_diagnostic, MALFORMED_REVIEW_TEMPLATE)

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

    def test_review_state_distinguishes_unclaimed_claimed_and_diagnostic(self):
        self.assertEqual(
            DraftRecord("Чернова:A", "x", "Author", review_requested=True).review_state,
            "unclaimed",
        )
        self.assertEqual(
            DraftRecord(
                "Чернова:A",
                "x",
                "Author",
                reviewer="Reviewer",
                review_requested=True,
            ).review_state,
            "claimed",
        )
        self.assertEqual(
            DraftRecord(
                "Чернова:A",
                "x",
                "Author",
                reviewer_diagnostic="bad parameter",
                review_requested=True,
            ).review_state,
            "diagnostic",
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


class IncubatorQueueMetadataTests(unittest.TestCase):
    def setUp(self):
        self.start = pywikibot.Timestamp(2026, 11, 25, 12)

    def revision(self, hours, text, user="Editor"):
        return {
            "timestamp": self.start - datetime.timedelta(hours=hours),
            "text": text,
            "user": user,
        }

    def test_current_review_and_claim_episodes_have_separate_start_times(self):
        record = DraftRecord(
            "Чернова:Пример",
            self.start,
            "Author",
            review_requested=True,
            reviewer="Reviewer",
        )
        revisions = [
            self.revision(0, "{{Инкубатор-проверка|Reviewer}}", "Reviewer"),
            self.revision(1, "{{Инкубатор-проверка|Reviewer}}", "Reviewer"),
            self.revision(2, "{{Инкубатор-проверка}}"),
            self.revision(3, "{{В инкубатора}}"),
        ]

        timestamps = find_current_request_timestamps(revisions, record)

        self.assertEqual(timestamps.review_request, revisions[2]["timestamp"])
        self.assertEqual(timestamps.reviewer_claim, revisions[1]["timestamp"])
        self.assertEqual(timestamps.reviewer_claim_user, "Reviewer")
        self.assertIsNone(timestamps.help_request)

    def test_current_help_episode_stops_at_last_revision_without_template(self):
        record = DraftRecord(
            "Чернова:Пример",
            self.start,
            "Author",
            help_requested=True,
        )
        revisions = [
            self.revision(0, "{{Помощ}}"),
            self.revision(1, "{{Помощ}}"),
            self.revision(2, "{{В инкубатора}}"),
            self.revision(3, "{{Помощ}}"),
        ]

        timestamps = find_current_request_timestamps(revisions, record)

        self.assertEqual(timestamps.help_request, revisions[1]["timestamp"])

    def test_hidden_revision_makes_an_open_episode_timestamp_unknown(self):
        record = DraftRecord(
            "Чернова:Пример",
            self.start,
            "Author",
            review_requested=True,
        )
        revisions = [
            self.revision(0, "{{Инкубатор-проверка}}"),
            self.revision(1, None),
        ]

        timestamps = find_current_request_timestamps(revisions, record)

        self.assertIsNone(timestamps.review_request)

    def test_latest_talk_activity_distinguishes_a_missing_talk_page(self):
        article = mock.Mock()
        talk_page = article.toggleTalkPage.return_value
        talk_page.exists.return_value = False

        self.assertEqual(latest_talk_activity(article), (False, None))

    def test_enrichment_reads_history_and_talk_without_mutating_page(self):
        article = mock.Mock()
        article.revisions.return_value = [
            self.revision(0, "{{Инкубатор-проверка}}", "Requester")
        ]
        talk_page = article.toggleTalkPage.return_value
        talk_page.exists.return_value = True
        talk_page.latest_revision.timestamp = self.start - datetime.timedelta(hours=2)
        record = DraftRecord(
            "Чернова:Пример",
            self.start,
            "Author",
            review_requested=True,
        )

        enriched = enrich_queue_record(article, record)

        article.revisions.assert_called_once_with(content=True)
        self.assertEqual(enriched.review_request_timestamp, self.start)
        self.assertTrue(enriched.talk_exists)
        self.assertEqual(
            enriched.talk_timestamp,
            self.start - datetime.timedelta(hours=2),
        )


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
            "! Статия !! Влязла ([[UTC]]) !! Автор !! Преглеждащ\n"
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
            "! Статия !! Влязла ([[UTC]]) !! Автор !! Преглеждащ\n"
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

    def test_missing_base_template_is_added_with_canonical_name(self):
        article = mock.Mock()
        article.text = "Съдържание"

        add_incubator_template(article)

        self.assertEqual(article.text, "{{Чернова}}\nСъдържание")
        article.save.assert_called_once_with(
            summary="Бот: добавяне на помощен шаблон за чернови",
            quiet=True,
        )

    def test_unchanged_generated_page_is_not_saved(self):
        page = mock.Mock()
        page.text = "same"

        changed = save_page_if_changed(page, "same", "summary")

        self.assertFalse(changed)
        page.save.assert_not_called()

    def test_changed_generated_page_is_saved_once(self):
        page = mock.Mock()
        page.text = "old"

        changed = save_page_if_changed(page, "new", "summary")

        self.assertTrue(changed)
        self.assertEqual(page.text, "new")
        page.save.assert_called_once_with(summary="summary", quiet=True)


class IncubatorQueuePreviewTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            DraftRecord(
                "Чернова:Чака",
                pywikibot.Timestamp(2026, 11, 1),
                "Author One",
                review_requested=True,
                help_requested=True,
                review_request_timestamp=pywikibot.Timestamp(2026, 11, 10),
                help_request_timestamp=pywikibot.Timestamp(2026, 11, 11),
            ),
            DraftRecord(
                "Чернова:Преглежда се",
                pywikibot.Timestamp(2026, 11, 2),
                "Author Two",
                reviewer="[[User:Reviewer|Reviewer]] {{unsafe}} <tag>",
                review_requested=True,
                review_request_timestamp=pywikibot.Timestamp(2026, 11, 12),
                reviewer_claim_timestamp=pywikibot.Timestamp(2026, 11, 13),
                reviewer_claim_user="Reviewer",
                talk_exists=True,
                talk_timestamp=pywikibot.Timestamp(2026, 11, 14),
            ),
            DraftRecord(
                "Чернова:Стара",
                pywikibot.Timestamp(2026, 1, 1),
                "Author Three",
                age_status="long_stay",
                reviewer="Legacy Reviewer",
                review_requested=True,
                legacy_reviewer_parameter=True,
                reviewer_claim_user="Author Three",
            ),
        ]

    def test_queue_groups_overlap_without_collapsing_states(self):
        groups = group_records(self.records)

        self.assertEqual(
            [record.fullname for record in groups.unclaimed_reviews], ["Чернова:Чака"]
        )
        self.assertEqual(
            [record.fullname for record in groups.claimed_reviews],
            ["Чернова:Стара", "Чернова:Преглежда се"],
        )
        self.assertEqual(
            [record.fullname for record in groups.review_diagnostics],
            ["Чернова:Стара"],
        )
        self.assertEqual(
            [record.fullname for record in groups.help_requests], ["Чернова:Чака"]
        )
        self.assertEqual(
            [record.fullname for record in groups.long_stay], ["Чернова:Стара"]
        )

    def test_queue_preview_uses_claim_revision_user_not_parameter_wikitext(self):
        preview = render_queue_preview(self.records)

        self.assertIn("== Чакат преглеждащ (1) ==", preview)
        self.assertIn("== Преглеждат се (2) ==", preview)
        self.assertIn("== Диагностики при преглед (1) ==", preview)
        self.assertIn("== Искат помощ (1) ==", preview)
        self.assertIn("== Чернови с продължителен престой (1) ==", preview)
        self.assertIn("{{потребител|Reviewer}}", preview)
        self.assertNotIn("[[User:Reviewer|Reviewer]]", preview)
        self.assertNotIn("{{unsafe}}", preview)
        self.assertIn("прегледът изглежда поет от първоначалния автор", preview)
        self.assertIn("2026-11-10 00:00:00", preview)
        self.assertIn("2026-11-13 00:00:00", preview)
        self.assertIn("2026-11-14 00:00:00", preview)
        self.assertIn("няма беседа", preview)

    def test_task_page_has_public_explanation_and_automation_notice(self):
        page = render_task_page(self.records)

        self.assertIn("Страницата се обновява автоматично", page)
        self.assertIn("Опашката осигурява видимост, но не обещава срок", page)
        self.assertEqual(page.count('<div style="overflow-x: auto;">'), 5)
        self.assertNotIn("локален преглед", page.casefold())


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
            mock.patch("incubator_manager.cli.save_page_if_changed") as save_page,
        ):
            result = run(dry_run=False, config=self.config)

        self.assertEqual(result, 0)
        identity.assert_called_once_with(self.site, "Eliza Beth")
        add_template.assert_called_once_with(self.article)
        maintenance.assert_called_once_with(self.site, [self.record], self.config)
        save_page.assert_called_once_with(
            self.list_page,
            mock.ANY,
            summary="Бот: актуализация на списъка",
        )

    def test_queue_preview_performs_no_writes(self):
        stdout = io.StringIO()
        with (
            mock.patch("incubator_manager.cli.pywikibot.Site", return_value=self.site),
            mock.patch(
                "incubator_manager.cli.pywikibot.Page", return_value=self.list_page
            ),
            mock.patch(
                "incubator_manager.cli.iter_records",
                return_value=[(self.article, self.record)],
            ),
            mock.patch(
                "incubator_manager.cli.enrich_queue_record",
                return_value=self.record,
            ) as enrich,
            mock.patch("incubator_manager.cli.assert_write_identity") as identity,
            mock.patch("incubator_manager.cli.add_incubator_template") as add_template,
            mock.patch(
                "incubator_manager.cli.apply_maintenance_actions"
            ) as maintenance,
            redirect_stdout(stdout),
        ):
            result = run(preview_queues=True, config=self.config)

        self.assertEqual(result, 0)
        identity.assert_not_called()
        add_template.assert_not_called()
        maintenance.assert_not_called()
        self.list_page.save.assert_not_called()
        self.assertIn("Страницата се обновява автоматично", stdout.getvalue())
        enrich.assert_called_once_with(self.article, self.record)

    def test_explicit_queue_publication_saves_guarded_task_page(self):
        task_page = mock.Mock()
        enriched_record = mock.Mock()
        with (
            mock.patch("incubator_manager.cli.pywikibot.Site", return_value=self.site),
            mock.patch(
                "incubator_manager.cli.pywikibot.Page",
                side_effect=[self.list_page, task_page],
            ),
            mock.patch(
                "incubator_manager.cli.iter_records",
                return_value=[(self.article, self.record)],
            ),
            mock.patch(
                "incubator_manager.cli.enrich_queue_record",
                return_value=enriched_record,
            ) as enrich,
            mock.patch("incubator_manager.cli.assert_write_identity") as identity,
            mock.patch("incubator_manager.cli.add_incubator_template"),
            mock.patch("incubator_manager.cli.apply_maintenance_actions"),
            mock.patch("incubator_manager.cli.render_list", return_value="list"),
            mock.patch("incubator_manager.cli.render_task_page", return_value="tasks"),
            mock.patch("incubator_manager.cli.save_page_if_changed") as save_page,
        ):
            result = run(publish_queues=True, config=self.config)

        self.assertEqual(result, 0)
        identity.assert_called_once_with(self.site, "Eliza Beth")
        enrich.assert_called_once_with(self.article, self.record)
        save_page.assert_has_calls(
            [
                mock.call(
                    self.list_page,
                    "list",
                    summary="Бот: актуализация на списъка",
                ),
                mock.call(
                    task_page,
                    "tasks",
                    summary="Бот: актуализация на задачите в Инкубатора",
                ),
            ]
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
