# -*- coding: utf-8 -*-
"""
Classes for achieving the name mangling effect.
"""

import logging
from operator import itemgetter
from itertools import count
from itertools import product

from calmjs.parse.asttypes import Identifier
from calmjs.parse.ruletypes import SourceChunk
from calmjs.parse.ruletypes import PushScope
from calmjs.parse.ruletypes import PopScope
from calmjs.parse.ruletypes import Declare
from calmjs.parse.ruletypes import Resolve
from calmjs.parse.layout import rule_handler_noop

from calmjs.parse.unparsers.walker import Dispatcher
from calmjs.parse.unparsers.walker import walk

logger = logging.getLogger(__name__)
logger.level = logging.WARNING

ID_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_'


def token_handler_unobfuscate(token, dispatcher, node, subnode):
    """
    A token handler that will return the original identifier
    value.
    """

    original = (
        node.value
        if isinstance(node, Identifier) and node.value != subnode else
        None
    )

    if isinstance(token.pos, int):
        _, lineno, colno = node.getpos(original or subnode, token.pos)
    else:
        lineno, colno = None, None

    yield SourceChunk(subnode, lineno, colno, original)


class NameGenerator(object):
    """
    A name generator.  It can accept one argument for values that should
    be skipped.

    It is also a constructor so that further names can be skipped.
    """

    def __init__(self, skip=None, charset=ID_CHARS):
        self.skip = set(skip or [])
        self.charset = charset
        self.__iterself = iter(self)

    def __call__(self, skip):
        return type(self)(set(skip) | set(self.skip), self.charset)

    def __iter__(self):
        for n in count(1):
            for chars in product(self.charset, repeat=n):
                symbol = ''.join(chars)
                if symbol in self.skip:
                    continue
                yield symbol

    def __next__(self):
        return next(self.__iterself)

    # python2.7 compatibility.
    next = __next__


# TODO provide an option to memoize all properties to reduce computation

class Scope(object):
    """
    For tracking the symbols.
    """

    def __init__(self, node, parent=None):
        self._closed = False
        self.node = node
        self.parent = parent
        self.children = []

        # Local names is for this scope only, the variable name will be
        # the key, with a value of how many times it has been referenced
        # anywhere in this scope.
        self.referenced_symbols = {}

        # This is a set of names that have been declared (i.e. via var
        # or function)
        self.local_declared_symbols = set()

        # All symbols remapped to be remapped to a different name will
        # be stored here, for the resolved method to make use of.
        self.remapped_symbols = {}

    @property
    def declared_symbols(self):
        """
        Return all local symbols here, and also of the parents
        """

        return self.local_declared_symbols | (
            self.parent.declared_symbols if self.parent else set())

    @property
    def global_symbols(self):
        """
        These are symbols that have been referenced, but not declared
        within this scope or any parent scopes.
        """

        declared_symbols = self.declared_symbols
        return set(
            s for s in self.referenced_symbols if s not in declared_symbols)

    @property
    def global_symbols_in_children(self):
        """
        This is based on all children referenced symbols that have not
        been declared.

        The intended use case is to ban the symbols from being used as
        remapped symbol values.
        """

        result = set()
        for child in self.children:
            result |= (
                child.global_symbols |
                child.global_symbols_in_children)
        return result

    @property
    def leaked_referenced_symbols(self):
        return {
            k: v for k, v in self.referenced_symbols.items()
            if k not in self.local_declared_symbols
        }

    def declare(self, symbol):
        self.local_declared_symbols.add(symbol)
        # simply create the reference, if not already there.
        self.referenced_symbols[symbol] = self.referenced_symbols.get(
            symbol, 0)

    def reference(self, symbol):
        # increment reference counter, declare one if not already did.
        self.referenced_symbols[symbol] = self.referenced_symbols.get(
            symbol, 0) + 1

    def close(self):
        """
        Mark the scope as closed, i.e. all symbols have been declared,
        and no changes should be done.
        """

        if self._closed:
            raise ValueError('scope is already marked as closed')

        # the key thing that needs to be done is to claim all references
        # to symbols done by all children that they have not declared.

        for child in self.children:
            for k, v in child.leaked_referenced_symbols.items():
                self.referenced_symbols[k] = self.referenced_symbols.get(
                    k, 0) + v

        self._closed = True

    def close_all(self):
        """
        Recursively close everything.
        """

        for child in self.children:
            child.close_all()
        self.close()

    def build_remap_symbols(self, name_generator, children_only=True):
        """
        This builds the replacement table for all the defined symbols
        for all the children, and this scope, if the children_only
        argument is False.
        """

        if not children_only:
            parent_remapped_symbols = (
                self.parent.remapped_symbols if self.parent else {})

            replacement = name_generator(skip=(
                # block implicit children globals.
                self.global_symbols_in_children | set(
                    # resolve the remapped symbols
                    v for k, v in parent_remapped_symbols.items()
                    # only skip over referenced symbols and not
                    # already redefined locally
                    if (k in self.referenced_symbols and
                        k not in self.local_declared_symbols)
                ) | self.global_symbols
            ))

            for symbol, c in reversed(sorted(
                    self.referenced_symbols.items(), key=itemgetter(1))):
                if symbol not in self.local_declared_symbols:
                    continue
                self.remapped_symbols[symbol] = next(replacement)

        for child in self.children:
            child.build_remap_symbols(name_generator, False)

    def resolve(self, symbol):
        result = None
        scope = self
        while result is None and scope:
            result = scope.remapped_symbols.get(symbol)
            scope = scope.parent
        return result or symbol

    def nest(self, node):
        """
        Create a new nested scope that is within this instance, binding
        the provided node to it.
        """

        nested_scope = type(self)(node, self)
        self.children.append(nested_scope)
        return nested_scope


class Obfuscator(object):
    """
    The name obfuscator.
    """

    def __init__(self, obfuscate_globals=False, reserved_keywords=()):
        """
        Arguments

        obfuscate_globals
            Also obfuscate variables declared on the global scope.  Do
            not enable this option unless there is an explicit need to
            do so as this will likely result in code that no longer
            provide the global variable names at where they were
            expected.

            Defaults to False for the reason above.

        reserved_keywords
            A list of reserved keywords for the input AST that should
            not be used as an obfuscated identifier.  Defaults to an
            empty tuple.
        """

        # this is a mapping of Identifier nodes to the scope
        self.identifiers = {}
        self.scopes = {}
        self.stack = []
        self.obfuscate_globals = obfuscate_globals
        self.reserved_keywords = reserved_keywords
        # global scope is in the ether somewhere so it isn't exactly
        # bounded to any specific node that gets passed in.
        self.global_scope = Scope(None)
        self.stack.append(self.global_scope)

    @property
    def current_scope(self):
        return self.stack[-1]

    def push_scope(self, dispatcher, node, *a, **kw):
        scope = self.current_scope.nest(node)
        self.scopes[node] = scope
        self.stack.append(scope)

    def pop_scope(self, dispatcher, node, *a, **kw):
        scope = self.stack.pop()
        scope.close()
        # TODO figure out whether/how to check that the scope that just
        # got popped is indeed of this node.

    def declare(self, dispatcher, node):
        """
        Declare the value of the Identifier of the node that was passed
        in as used in the current scope.
        """

        self.current_scope.declare(node.value)

    def reference(self, dispatcher, node):
        """
        Register this identifier to the current scope.
        """

        # the identifier node itself will be mapped to the current scope
        # for the resolve to work
        self.identifiers[node] = self.current_scope
        self.current_scope.reference(node.value)

    def resolve(self, dispatcher, node):
        """
        For the given node, resolve it into the scope it was declared
        at, and if one was found, return its value.
        """

        scope = self.identifiers.get(node)
        if not scope:
            return node.value
        return scope.resolve(node.value)

    def walk(self, dispatcher, node):
        """
        Walk through the node with a custom dispatcher for extraction of
        details that are required.
        """

        local_dispatcher = Dispatcher(
            definitions=dict(dispatcher),
            token_handler=rule_handler_noop,
            layout_handlers={
                PushScope: self.push_scope,
                PopScope: self.pop_scope,
            },
            deferrable_handlers={
                Declare: self.declare,
                Resolve: self.reference,
            },
        )
        return list(walk(local_dispatcher, node))

    def finalize(self):
        """
        Finalize the run - build the name generator and use it to build
        the remap symbol tables.
        """

        self.global_scope.close()
        name_generator = NameGenerator(skip=self.reserved_keywords)
        self.global_scope.build_remap_symbols(
            name_generator,
            children_only=not self.obfuscate_globals,
        )

    def prewalk_hook(self, dispatcher, node):
        """
        This is for the Unparser to use as a prewalk hook.
        """

        self.walk(dispatcher, node)
        self.finalize()
        return node


def obfuscate(obfuscate_globals=False, reserved_keywords=()):
    def name_obfuscation_rules():
        inst = Obfuscator(
            obfuscate_globals=obfuscate_globals,
            reserved_keywords=reserved_keywords,
        )
        return {
            'token_handler': token_handler_unobfuscate,
            'deferrable_handlers': {
                Resolve: inst.resolve,
            },
            'prewalk_hooks': [
                inst.prewalk_hook,
            ],
        }
    return name_obfuscation_rules
