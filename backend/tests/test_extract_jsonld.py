"""Unit tests for JSON-LD recipe parsing edge cases."""

from __future__ import annotations

import unittest

from app.extract_service import _parse_html, _types_include_recipe


class TestRecipeJsonLd(unittest.TestCase):
    def test_schema_org_https_iri_recipe_type(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"https://schema.org/Recipe","name":"IRI",
         "recipeIngredient":["1 cup flour"],"recipeInstructions":[{"@type":"HowToStep","text":"Mix."}]}
        </script>
        """
        recipe, method, _w = _parse_html(html, "https://example.com/r")
        self.assertEqual(method, "json-ld")
        self.assertEqual(recipe["title"], "IRI")
        self.assertEqual(len(recipe["ingredients"]), 1)
        self.assertEqual(len(recipe["steps"]), 1)

    def test_types_include_recipe_mixed_newsarticle(self) -> None:
        node = {"@type": ["Recipe", "NewsArticle"], "name": "X"}
        self.assertTrue(_types_include_recipe(node))

    def test_recipe_instructions_howto_wrapper(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Recipe","name":"HowToWrap",
         "recipeIngredient":["a"],
         "recipeInstructions":{"@type":"HowTo","step":[
           {"@type":"HowToStep","text":"First"},
           {"@type":"HowToStep","text":"Second"}
         ]}}
        </script>
        """
        recipe, method, _w = _parse_html(html, "https://example.com/r")
        self.assertEqual(method, "json-ld")
        self.assertEqual(len(recipe["steps"]), 2)

    def test_webpage_mainentity_recipe(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"WebPage",
         "mainEntity":{"@type":"Recipe","name":"Nested","recipeIngredient":["salt"],
         "recipeInstructions":{"@type":"HowToStep","text":"Sprinkle."}}}
        </script>
        """
        recipe, method, _w = _parse_html(html, "https://example.com/r")
        self.assertEqual(method, "json-ld")
        self.assertEqual(recipe["title"], "Nested")
        self.assertTrue(recipe["ingredients"])
        self.assertTrue(recipe["steps"])

    def test_ld_json_wrapped_in_html_comment(self) -> None:
        html = """<script type="application/ld+json">
<!--
{"@context":"https://schema.org","@type":"Recipe","name":"Commented",
 "recipeIngredient":["x"],"recipeInstructions":[{"@type":"HowToStep","text":"y"}]}
-->
</script>"""
        recipe, method, _w = _parse_html(html, "https://example.com/r")
        self.assertEqual(method, "json-ld")
        self.assertEqual(recipe["title"], "Commented")


if __name__ == "__main__":
    unittest.main()
