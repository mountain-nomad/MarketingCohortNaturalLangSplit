"""Crawler errors."""


class CrawlScopeError(Exception):
    """The crawl would see no tables, or a configured schema is missing or not usable.

    Raised before anything is stored, so a misconfiguration or revoked grant can never
    wipe generated docs or de-confirm reviewed use cases. The message is actionable
    and contains no credentials.
    """
