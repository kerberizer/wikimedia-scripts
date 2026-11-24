from dataclasses import dataclass

MALFORMED_REVIEW_TEMPLATE = (
    '<span style="color: red; font-weight: bold;">Грешно попълнен шаблон '
    "{{ш|инкубатор-проверка}}.</span>"
)
POSITIONAL_REVIEW_PARAMETER = (
    '<span style="color: red; font-weight: bold;">Грешно попълнен шаблон '
    "{{ш|Чернова за преглед}}: използвайте именувания параметър "
    "<code>преглеждащ=</code>.</span>"
)


@dataclass(frozen=True)
class TemplateState:
    has_incubator_template: bool = False
    review_requested: bool = False
    help_requested: bool = False
    reviewer: str = ""
    reviewer_diagnostic: str = ""
    review_template_name: str = ""
    legacy_reviewer_parameter: bool = False


@dataclass(frozen=True)
class DraftRecord:
    fullname: str
    timestamp: object
    author: str
    age_status: str = "normal"
    reviewer: str = ""
    reviewer_diagnostic: str = ""
    review_template_name: str = ""
    legacy_reviewer_parameter: bool = False
    review_requested: bool = False
    help_requested: bool = False
    is_redirect: bool = False
    needs_incubator_template: bool = False

    @property
    def status(self):
        if self.is_redirect:
            return "redirect"
        if self.age_status in ("long_stay", "critical"):
            return self.age_status
        if self.review_requested:
            return "review"
        if self.age_status == "warning":
            return "warning"
        if self.help_requested:
            return "help"
        return "normal"

    @property
    def review_state(self):
        if not self.review_requested:
            return "none"
        if self.reviewer_diagnostic:
            return "diagnostic"
        if self.reviewer:
            return "claimed"
        return "unclaimed"

    @property
    def reviewer_display(self):
        return self.reviewer_diagnostic or self.reviewer
