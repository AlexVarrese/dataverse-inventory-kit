"""Regressões de equivalência e observabilidade para o render em grande escala.

Rodar: python -m unittest discover -s tests -p 'test_render_performance.py' -v
Não acessa Dataverse e não usa dados de cliente.
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from dvinv.util import field_refs, field_tokens
from dvinv import dependencies


class TokenMatchingRegression(unittest.TestCase):
    def test_same_matches_as_original_field_refs(self):
        snippets = [
            "const a = 'xc7_cliente'; const b = 'xc7_cliente_old';",
            "entity.xv2_contrato, getAttribute(\"XS3_STATUS\"), xx3_status_extra",
            "",
            "abc_1 abc_1.abc fooBar tokená xn_a",
        ]
        tables = [
            {"xc7_cliente", "xs3_status", "not_present"},
            {"xv2_contrato", "xx3_status", "xc7_cliente_old"},
            {"abc_1", "fooBar", "xn_a"},
        ]
        for code in snippets:
            tokens = field_tokens(code)
            for cols in tables:
                self.assertEqual(field_refs(code, cols), sorted(tokens & cols))

    def test_inverted_index_gives_same_table_column_pairs(self):
        cols_of = {
            "t_a": {"xc7_cliente", "xv2_contrato"},
            "t_b": {"xc7_cliente", "xs3_status"},
            "t_c": {"absent", "xv2_contrato"},
        }
        content = "entity.xc7_cliente; entity.xv2_contrato; 'xs3_status';"
        expected = {(tbl, fld) for tbl, known in cols_of.items()
                    for fld in field_refs(content, known)}
        inv = {}
        for tbl, fields in cols_of.items():
            for fld in fields:
                inv.setdefault(fld, []).append(tbl)
        actual = {(tbl, fld) for fld in field_tokens(content)
                  for tbl in inv.get(fld, ())}
        self.assertEqual(expected, actual)


class LargeGraphProgressRegression(unittest.TestCase):
    def test_empty_graph_build_remains_valid(self):
        with contextlib.redirect_stdout(io.StringIO()):
            g = dependencies.build({})
        self.assertEqual(len(g.edges), 0)
        self.assertEqual(len(g.nodes), 0)


if __name__ == "__main__":
    unittest.main()
