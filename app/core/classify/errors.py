"""Failures of the classification path.

The split matters to the caller: an *unavailable* model is a transient outage the UI should
report as "try again", while a *bad response* is a defect in the prompt, the schema or the
provider adapter and should be investigated. Neither may ever be turned into a guess.
"""

from __future__ import annotations


class ClassifyError(Exception):
    """Base class for every classification failure."""


class LlmError(ClassifyError):
    """Base class for failures of the model call."""


class LlmNotConfiguredError(LlmError):
    """No API key or provider is configured, so no call can be made."""


class LlmUnavailableError(LlmError):
    """The model could not be reached: timeout, connection error, 5xx, rate limit."""


class LlmResponseError(LlmError):
    """The model answered, but not in a form that can be used.

    Covers a non-JSON body, JSON that does not match the schema, and a payload missing the
    fields the adapter needs. Never raised for a model that legitimately abstained - that is
    a valid answer, not an error.
    """
