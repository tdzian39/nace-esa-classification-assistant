"""Batch form of Tool 2: a messy xlsx of Czech companies in, one result row per line out.

* :mod:`core.batch.reader` - reads an input sheet that nobody cleaned up first.
* :mod:`core.batch.runner` - resolves every row and writes the workbook.
* ``python -m core.batch INPUT.xlsx`` - the command line entry point.

The output row shape lives in :mod:`core.export.columns`, shared with the single-lookup CLI.
"""

from core.batch.reader import BatchInput, BatchInputError, InputRow, read_batch_input
from core.batch.runner import BatchReport, build_columns, build_row, default_output_path, run_batch

__all__ = [
    "BatchInput",
    "BatchInputError",
    "BatchReport",
    "InputRow",
    "build_columns",
    "build_row",
    "default_output_path",
    "read_batch_input",
    "run_batch",
]
