from __future__ import annotations


class MutationError(Exception):
    """Base class for user-confirmed Yuque write failures."""


class MarkdownInputError(MutationError):
    """The local Markdown input cannot be safely imported."""


class MutationAuthenticationError(MutationError):
    """The current browser session is not authenticated."""


class MutationProtocolError(MutationError):
    """The Yuque page no longer matches the supported write flow."""


class MutationAccessError(MutationError):
    """Yuque rejected the requested write for the current account."""


class MutationConflictError(MutationError):
    """Yuque reported a duplicate or conflicting resource."""


class MutationTimeoutError(MutationError):
    """The browser write did not reach a confirmed terminal state."""


class MutationConfirmationRequired(MutationError):
    """A mutating operation was requested without explicit confirmation."""
