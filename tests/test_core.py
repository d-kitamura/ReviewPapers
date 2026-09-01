from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import fitz

from packages.papers.discovery import discover_papers
from packages.review import texrender
from packages.review.record import (
    ReviewRecord,
    issued_status,
    load_record,
    mark_issued,
    new_comment_pin,
    save_record,
    sorted_pins,
)
from packages.review.reportpdf import (
    LINK_VIEW_PADDING,
    PIN_RADIUS,
    generate_comment_pdf,
)


class DiscoveryTests(unittest.TestCase):
    def test_each_version_uses_the_full_filename_stem_as_its_id(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("山田太郎_卒論_v1.pdf", "山田太郎_卒論_v2.pdf"):
                open(os.path.join(directory, name), "wb").close()
            papers = discover_papers(directory)
        self.assertEqual([paper.id for paper in papers], ["山田太郎_卒論_v1", "山田太郎_卒論_v2"])
        self.assertEqual([paper.name for paper in papers], ["山田太郎_卒論_v1", "山田太郎_卒論_v2"])


class RecordTests(unittest.TestCase):
    def test_round_trip_and_issue_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "results", "01.json")
            record = ReviewRecord("山田太郎_v1", "山田太郎_v1", "paper.pdf")
            record.comments.append(new_comment_pin(0, 100, 200, "ここを修正"))
            save_record(path, record)
            self.assertEqual(issued_status(path), "unissued")
            loaded = load_record(path)
            self.assertEqual(loaded.comments[0].text, "ここを修正")
            mark_issued(path, loaded)
            self.assertEqual(issued_status(path), "")

    def test_pin_reading_order(self):
        pins = [
            new_comment_pin(1, 20, 20, "third"),
            new_comment_pin(0, 200, 100, "second"),
            new_comment_pin(0, 20, 100, "first"),
        ]
        self.assertEqual([pin.text for pin in sorted_pins(pins)], ["first", "second", "third"])


class TeXRenderingTests(unittest.TestCase):
    def test_parser_preserves_inline_and_align_sources(self):
        comment = (
            "ここで，$\\bm{x}_{ij}=\\bm{A}_i\\bm{s}_{ij}$である。\n"
            "\\begin{align*}\n"
            "\\mathbb{E}[\\bm{x}] &= \\bm{0}.\n"
            "\\end{align*}"
        )
        segments = texrender.parse_comment(comment)
        self.assertEqual(
            [segment.kind for segment in segments],
            ["text", "inline_math", "text", "display_math"],
        )
        self.assertEqual(
            texrender.math_sources(comment),
            [
                "$\\bm{x}_{ij}=\\bm{A}_i\\bm{s}_{ij}$",
                "\\begin{align*}\n"
                "\\mathbb{E}[\\bm{x}] &= \\bm{0}.\n"
                "\\end{align*}",
            ],
        )

    def test_parser_rejects_incomplete_and_unsafe_tex(self):
        with self.assertRaises(texrender.TeXSyntaxError):
            texrender.parse_comment("数式 $x_i")
        with self.assertRaises(texrender.TeXSyntaxError):
            texrender.parse_comment(r"$\input{secret}$")

    def test_tectonic_renders_bm_mathbb_and_align(self):
        source = (
            "\\begin{align*}\n"
            "\\bm{x}_{ij} &= \\bm{A}_i\\bm{s}_{ij},\\\\\n"
            "\\mathbb{E}[\\bm{x}] &= \\bm{0}.\n"
            "\\end{align*}"
        )
        with tempfile.TemporaryDirectory() as cache:
            artifact = texrender.render_formula(source, cache)
            self.assertTrue(os.path.exists(artifact.pdf_path))
            self.assertGreater(artifact.width, 20)
            self.assertGreater(artifact.height, 20)
            image = texrender.render_formula_image(source, cache, scale=2)
            self.assertGreater(image.width, 40)
            self.assertGreater(image.height, 40)


class ReportPdfTests(unittest.TestCase):
    def test_generated_pdf_has_comment_page_source_page_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = os.path.join(directory, "source.pdf")
            output_path = os.path.join(directory, "output.pdf")
            source = fitz.open()
            source.new_page(width=595, height=842).insert_text((72, 72), "Dummy paper")
            source.save(source_path)
            source.close()

            record = ReviewRecord("Student_v1", "Student_v1", "source.pdf")
            tex_comment = "ここで，$\\bm{x}_{ij}=\\bm{A}_i\\bm{s}_{ij}$である。"
            record.comments.append(new_comment_pin(0, 120, 160, tex_comment))
            cache = os.path.join(directory, "tex-cache")
            with patch("packages.review.texrender.default_cache_dir", return_value=cache):
                generate_comment_pdf(record, source_path, output_path, "Review comments")

            with fitz.open(output_path) as result:
                self.assertEqual(len(result), 2)
                self.assertGreaterEqual(len(result[0].get_links()), 1)
                self.assertGreaterEqual(len(result[1].get_links()), 1)
                self.assertIn(r"$\bm{x}_{ij}=\bm{A}_i\bm{s}_{ij}$", result[0].get_text())
                self.assertGreaterEqual(len(result[0].get_xobjects()), 1)

                comment_link = result[0].get_links()[0]
                pin_link = result[1].get_links()[0]
                self.assertAlmostEqual(
                    comment_link["to"].y,
                    160 - PIN_RADIUS - LINK_VIEW_PADDING,
                )
                self.assertAlmostEqual(
                    pin_link["to"].y,
                    comment_link["from"].y0 - LINK_VIEW_PADDING,
                )

                source_boxes = [
                    drawing["rect"] for drawing in result[0].get_drawings()
                    if drawing.get("fill")
                    and all(abs(channel - 0.95) < 0.01 for channel in drawing["fill"])
                ]
                self.assertEqual(len(source_boxes), 1)
                for link in result[0].get_links():
                    self.assertFalse(link["from"].intersects(source_boxes[0]))

                pin_drawings = [
                    drawing for drawing in result[1].get_drawings()
                    if drawing.get("fill")
                    and tuple(round(channel, 1) for channel in drawing["fill"])
                    == (1.0, 0.9, 0.2)
                ]
                self.assertEqual(len(pin_drawings), 1)
                self.assertAlmostEqual(pin_drawings[0]["fill_opacity"], 0.5)


if __name__ == "__main__":
    unittest.main()
