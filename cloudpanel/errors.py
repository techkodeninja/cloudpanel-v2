"""Errors the CLI turns into a clean message and a non-zero exit code."""


class CloudPanelError(Exception):
    """Something went wrong; the message is shown to the person as-is."""


class UsageError(CloudPanelError):
    """The command was called wrongly (missing or invalid option)."""


class CommandError(CloudPanelError):
    """An external command (clpctl, wp, certbot, ...) failed or is missing."""
