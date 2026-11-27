"""Errors the module shows to people.

Every error carries an HTTP status, a stable code and a message fit to show:
one plain sentence (or the fixed wording the specification gives).
"""

from __future__ import annotations


class ModuleError(Exception):
    """Base for every error that reaches a person."""

    status: int = 500
    code: str = "error"
    default_message: str = "Something went wrong."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class NotFound(ModuleError):
    """Not found, or not theirs: the two are answered identically (BR-8)."""

    status = 404
    code = "not_found"
    default_message = "Not found."


class AlreadyChanged(ModuleError):
    """Optimistic version check failed: someone else changed the record first."""

    status = 409
    code = "already_changed"
    default_message = "This has already changed."


class NotAllowedValue(ModuleError):
    """A value that is not allowed."""

    status = 422
    code = "not_allowed"
    default_message = "This value is not allowed."


class NotSaved(ModuleError):
    """The audit record could not be written, so the change was not made (BR-9)."""

    status = 503
    code = "not_saved"
    default_message = "Not saved. Try again."


class BoundaryUnavailable(ModuleError):
    """A boundary operation failed; the surrounding transaction is rolled back."""

    status = 503
    code = "unavailable"
    default_message = "Flow could not be reached, so nothing was changed."


class NotSignedIn(ModuleError):
    """The boundary could not name the person asking."""

    status = 401
    code = "not_signed_in"
    default_message = "Sign in to continue."
