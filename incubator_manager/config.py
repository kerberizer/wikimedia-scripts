from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    site_code: str = "bg"
    site_family: str = "wikipedia"
    expected_username: str = "Eliza Beth"
    list_page_name: str = "Уикипедия:Инкубатор/Списък на статиите"
    task_page_name: str = "Уикипедия:Инкубатор/Задачи"
    article_prefix: str = ""
    article_namespace: str = "Чернова"
    search_moves_days: int = 360
    long_stay_days: int = 150
    critical_days: int = 120
    warning_days: int = 90
    sysops_to_ping: tuple[str, ...] = ("Iliev",)
    legacy_article_fullprefix: str = "Уикипедия:Инкубатор/Статии/"

    @property
    def article_fullprefix(self):
        return f"{self.article_namespace}:{self.article_prefix}"

    @property
    def list_header_template(self):
        return f"{{{{{self.list_page_name}/Header}}}}"

    @property
    def sysop_ping(self):
        return ", ".join(f"@[[User:{name}|{name}]]" for name in self.sysops_to_ping)
