# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""
Routines for printing columnar output.  See ``colify()`` for more information.
"""

from __future__ import division

import fcntl
import os
import struct
import sys
import termios
from io import StringIO

from .color import cextra
from .color import clen


class ColumnConfig:
    def __init__(self, cols):
        self.cols = cols
        self.line_length = 0
        self.valid = True
        self.widths = [0] * cols  # does not include ansi colors

    def __repr__(self):
        attrs = [(a, getattr(self, a)) for a in dir(self) if not a.startswith("__")]
        return "<Config: %s>" % ", ".join("%s: %r" % a for a in attrs)


def config_variable_cols(elts, console_width, padding, cols=0):
    """Variable-width column fitting algorithm.

    This function determines the most columns that can fit in the
    screen width.  Unlike uniform fitting, where all columns take
    the width of the longest element in the list, each column takes
    the width of its own longest element. This packs elements more
    efficiently on screen.

    If cols is nonzero, force

    """
    if cols < 0:
        raise ValueError("cols must be non-negative.")

    # Get a bound on the most columns we could possibly have.
    # 'clen' ignores length of ansi color sequences.
    lengths = [clen(e) for e in elts]
    max_cols = max(1, console_width // (min(lengths) + padding))
    max_cols = min(len(elts), max_cols)

    # Range of column counts to try.  If forced, use the supplied value.
    col_range = [cols] if cols else range(1, max_cols + 1)

    # Determine the most columns possible for the console width.
    configs = [ColumnConfig(c) for c in col_range]
    for i, length in enumerate(lengths):
        for conf in configs:
            if conf.valid:
                col = i // ((len(elts) + conf.cols - 1) // conf.cols)
                p = padding if col < (conf.cols - 1) else 0

                if conf.widths[col] < (length + p):
                    conf.line_length += length + p - conf.widths[col]
                    conf.widths[col] = length + p
                    conf.valid = conf.line_length < console_width

    try:
        config = next(conf for conf in reversed(configs) if conf.valid)
    except StopIteration:
        # If nothing was valid the screen was too narrow -- just use 1 col.
        config = configs[0]

    config.widths = [w for w in config.widths if w != 0]
    config.cols = len(config.widths)
    return config


def config_uniform_cols(elts, console_width, padding, cols=0):
    """Uniform-width column fitting algorithm.

    Determines the longest element in the list, and determines how
    many columns of that width will fit on screen.  Returns a
    corresponding column config.

    """
    if cols < 0:
        raise ValueError("cols must be non-negative.")

    # 'clen' ignores length of ansi color sequences.
    max_len = max(clen(e) for e in elts) + padding
    if cols == 0:
        cols = max(1, console_width // max_len)
        cols = min(len(elts), cols)

    config = ColumnConfig(cols)
    config.widths = [max_len] * cols

    return config


def colify(elts, **options):
    """Takes a list of elements as input and finds a good columnization
    of them, similar to how gnu ls does. This supports both
    uniform-width and variable-width (tighter) columns.

    If elts is not a list of strings, each element is first conveted
    using ``str()``.

    Keyword Arguments:
      output : A file object to write to. Default is ``sys.stderr``
      indent : Optionally indent all columns by some number of spaces
      padding : Spaces between columns. Default is 2
      width : Width of the output. Default is 80 if tty not detected
      cols : Force number of columns. Default is to size to terminal, or
        single-column if no tty
      tty : Whether to attempt to write to a tty. Default is to autodetect a tty.
        Set to False to force single-column output
      method : Method to use to fit columns. Options are variable or uniform.
        Variable-width columns are tighter, uniform columns are all the same
        width and fit less data on the screen

    """
    # Get keyword arguments or set defaults
    cols = options.pop("cols", 0)
    output = options.pop("output", sys.stdout)
    indent = options.pop("indent", 0)
    padding = options.pop("padding", 2)
    tty = options.pop("tty", None)
    method = options.pop("method", "variable")
    console_cols = options.pop("width", None)

    if options:
        raise TypeError(
            "'%s' is an invalid keyword argument for this function." % next(x for x in options)
        )

    # elts needs to be an array of strings so we can count the elements
    elts = [str(elt) for elt in elts]
    if not elts:
        return (0, ())

    # environment size is of the form "<rows>x<cols>"
    env_size = os.environ.get("COLIFY_SIZE")
    if env_size:
        try:
            r, c = env_size.split("x")
            console_rows, console_cols = int(r), int(c)
            tty = True
        except BaseException:
            pass

    # Use only one column if not a tty.
    if not console_cols and not tty:
        if tty is False or not output.isatty():
            cols = 1

    # Specify the number of character columns to use.
    if not console_cols:
        console_rows, console_cols = terminal_size()
    elif not isinstance(console_cols, int):
        raise ValueError("Number of columns must be an int")
    console_cols = max(1, console_cols - indent)

    # Choose a method.  Variable-width colums vs uniform-width.
    if method == "variable":
        config = config_variable_cols(elts, console_cols, padding, cols)
    elif method == "uniform":
        config = config_uniform_cols(elts, console_cols, padding, cols)
    else:
        raise ValueError("method must be either 'variable' or 'uniform'")

    cols = config.cols
    rows = (len(elts) + cols - 1) // cols
    rows_last_col = len(elts) % rows

    for row in range(rows):
        output.write(" " * indent)
        for col in range(cols):
            elt = col * rows + row
            width = config.widths[col] + cextra(elts[elt])
            if col < cols - 1:
                fmt = "%%-%ds" % width
                output.write(fmt % elts[elt])
            else:
                # Don't pad the rightmost column (sapces can wrap on
                # small teriminals if one line is overlong)
                output.write(elts[elt])

        output.write("\n")
        row += 1
        if row == rows_last_col:
            cols -= 1

    return (config.cols, tuple(config.widths))


def colify_table(table, **options):
    """Version of ``colify()`` for data expressed in rows, (list of lists).

    Same as regular colify but takes a list of lists, where each
    sub-list must be the same length, and each is interpreted as a
    row in a table.  Regular colify displays a sequential list of
    values in columns.

    """
    if table is None:
        raise TypeError("Can't call colify_table on NoneType")
    elif not table or not table[0]:
        raise ValueError("Table is empty in colify_table!")

    columns = len(table[0])

    def transpose():
        for i in range(columns):
            for row in table:
                yield row[i]

    if "cols" in options:
        raise ValueError("Cannot override columsn in colify_table.")
    options["cols"] = columns

    colify(transpose(), **options)


def colified(elts, **options):
    """Invokes the ``colify()`` function but returns the result as a string
    instead of writing it to an output string.

    """
    sio = StringIO()
    options["output"] = sio
    colify(elts, **options)
    return sio.getvalue()


def terminal_size():
    """Gets the dimensions of the console: (rows, cols)."""

    def ioctl_gwinsz(fd):
        try:
            rc = struct.unpack("hh", fcntl.ioctl(fd, termios.TIOCGWINSZ, "1234"))
        except BaseException:
            return
        return rc

    rc = ioctl_gwinsz(0) or ioctl_gwinsz(1) or ioctl_gwinsz(2)
    if not rc:
        try:
            fd = os.open(os.ctermid(), os.O_RDONLY)
            rc = ioctl_gwinsz(fd)
            os.close(fd)
        except BaseException:
            pass

    if not rc:
        rc = (os.environ.get("LINES", 25), os.environ.get("COLUMNS", 80))

    return int(rc[0]) or 25, int(rc[1]) or 80
