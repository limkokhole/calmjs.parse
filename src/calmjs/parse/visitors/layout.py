# -*- coding: utf-8 -*-
"""
Various helpers for plugging into the pprint visitor.

Combining the following layout handlers with the pptypes definition and
pprint framework can be done in myriad of ways such that new output
formats can be constructed very trivially by simply changing how or what
of the following layouts to use to plug into the pprint visitor setup.
This finally removes the annoyance of having to write an entire new
visitor class for every kind of desired output.  Good riddance to the
visit_* methods.
"""

from calmjs.parse.pptypes import Dedent
from calmjs.parse.pptypes import Indent
from calmjs.parse.pptypes import Newline


class Indentation(object):
    """
    For tracking indent/dedents.
    """

    def __init__(self, indent=2):
        """
        Arguments

        indent
            The spaces to indent a line with; defaults to 2.
        """
        self.indent = indent
        self._level = 0

    def layout_handler_indent(self, state, node, before, after):
        self._level += 1

    def layout_handler_dedent(self, state, node, before, after):
        self._level -= 1

    def layout_handler_newline(self, state, node, before, after):
        # simply render the newline with an implicit sourcemap line/col
        yield ('\n', 0, 0, None)
        indents = ' ' * (self.indent * self._level)
        if indents:
            yield (indents, None, None, None)


def indentation(indent=2):
    def make_layout():
        inst = Indentation(indent)
        return {
            Indent: inst.layout_handler_indent,
            Dedent: inst.layout_handler_dedent,
            Newline: inst.layout_handler_newline,
        }
    return make_layout


# other standalone handlers

def token_handler_str_default(token, state, node, subnode):
    # TODO the mangler could provide an implementation of this that will
    # fill out the last element of the yielded tuple.
    if isinstance(token.pos, int):
        _, lineno, colno = node.getpos(subnode, token.pos)
    else:
        lineno, colno = None, None
    yield (subnode, lineno, colno, None)


def layout_handler_space_imply(state, node, before, after):
    # default layout handler where the space will be rendered, with the
    # line/column set to 0 for sourcemap to generate the implicit value.
    yield (' ', 0, 0, None)


def layout_handler_space_drop(state, node, before, after):
    # default layout handler where the space will be rendered, with the
    # line/column set to None for sourcemap to terminate the position.
    yield (' ', None, None, None)


def layout_handler_newline_simple(state, node, before, after):
    # simply render the newline with an implicit sourcemap line/col
    yield ('\n', 0, 0, None)
