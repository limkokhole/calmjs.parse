# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import unittest
import json
from io import StringIO
from os.path import join
from tempfile import mktemp

from calmjs.parse.asttypes import Node
from calmjs.parse.ruletypes import Attr
from calmjs.parse.unparsers.base import BaseUnparser
from calmjs.parse import io


class IOTestCase(unittest.TestCase):

    def test_read(self):
        # given that there is no generic parser, emulate one like so
        def parser(text):
            result = Node()
            result.raw = text
            return result

        stream = StringIO('var foo = "bar";')
        node = io.read(parser, stream)
        self.assertIsNone(node.sourcepath)

        stream.name = 'somefile.js'
        node = io.read(parser, stream)
        self.assertEqual(node.sourcepath, 'somefile.js')

    def test_write_no_sourcemap(self):
        root = mktemp()
        definitions = {'Node': (
            Attr(attr='left'), Attr(attr='op'), Attr(attr='right'),)}

        # the program node; attributes are assigned to mimic a real one
        program = Node()
        program.left, program.op, program.right = ('foo', '=', 'true')
        program.sourcepath = join(root, 'original.js')
        program._token_map = {
            'foo': [(0, 1, 1)],
            '=': [(4, 1, 5)],
            'true': [(6, 1, 7)],
        }

        output_stream = StringIO()
        output_stream.name = join(root, 'processed.js')

        unparser = BaseUnparser(definitions)
        io.write(unparser, program, output_stream)
        self.assertEqual('foo=true', output_stream.getvalue())

    def test_write_sourcemap(self):
        root = mktemp()
        definitions = {'Node': (
            Attr(attr='left'), Attr(attr='op'), Attr(attr='right'),)}

        # the program node; attributes are assigned to mimic a real one
        program = Node()
        program.left, program.op, program.right = ('foo', '=', 'true')
        program.sourcepath = join(root, 'original.js')
        program._token_map = {
            'foo': [(0, 1, 1)],
            '=': [(4, 1, 5)],
            'true': [(6, 1, 7)],
        }

        # streams
        output_stream = StringIO()
        output_stream.name = join(root, 'processed.js')
        sourcemap_stream = StringIO()
        sourcemap_stream.name = join(root, 'processed.js.map')

        unparser = BaseUnparser(definitions)
        io.write(unparser, program, output_stream, sourcemap_stream)

        sourcemap = json.loads(sourcemap_stream.getvalue())
        self.assertEqual({
            "version": 3,
            "sources": ["original.js"],
            "names": [],
            "mappings": "AAAA,GAAI,CAAE",
            "file": "processed.js"
        }, sourcemap)
        self.assertEqual(
            'foo=true\n//# sourceMappingURL=processed.js.map\n',
            output_stream.getvalue())
