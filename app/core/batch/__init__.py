"""Batch input: read a messy xlsx that nobody cleaned up first.

* :mod:`core.batch.reader` - finds the header under title rows, tolerates mixed identifier
  columns and Excel-eaten leading zeros, and keeps the input columns for echoing back.

The reader still recognises IČO and name columns - it came with the Czech-company batch
that has since moved out of this repository. Roadmap epic E6 generalises it to ISIN and
name columns for the Tool 1 batch; until then nothing in the app calls it.
"""

from core.batch.reader import BatchInput, BatchInputError, InputRow, read_batch_input

__all__ = [
    "BatchInput",
    "BatchInputError",
    "InputRow",
    "read_batch_input",
]
