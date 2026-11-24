def assert_write_identity(site, expected_user):
    """Refuse live mutations unless Pywikibot is using the expected account."""
    site.login()
    actual_user = site.user()
    if actual_user != expected_user:
        raise RuntimeError(
            f"refusing to write as {actual_user!r}; expected {expected_user!r}"
        )
