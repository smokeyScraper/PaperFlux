"""Figures are cut from the PDF by their captions, without a model call."""
import tempfile
import unittest

import pymupdf

from paperflux.src.figures.extract import extract_figures


def _draw(page, rect, fill):
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=(0.1, 0.2, 0.4), fill=fill, width=2)
    shape.commit()


class ExtractFiguresTest(unittest.TestCase):
    def test_every_captioned_drawing_is_kept_including_the_appendix(self):
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        _draw(page, pymupdf.Rect(50, 80, 280, 260), (0.6, 0.7, 0.95))
        page.insert_textbox(
            pymupdf.Rect(50, 270, 280, 340),
            "Figure 1: Overview of the model architecture that reads tokens.",
            fontsize=10,
        )
        page.insert_text((50, 360), "As shown in Figure 1, accuracy rose.", fontsize=11)
        _draw(page, pymupdf.Rect(320, 400, 540, 600), (0.7, 0.9, 0.7))
        page.insert_textbox(
            pymupdf.Rect(320, 610, 545, 680),
            "Figure 2: Results compared with the baseline.",
            fontsize=10,
        )
        _draw(page, pymupdf.Rect(50, 460, 250, 600), (0.85, 0.85, 0.85))
        page.insert_textbox(
            pymupdf.Rect(50, 680, 270, 760),
            "Figure 8: Ablation in the appendix on hyperparameters.",
            fontsize=10,
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            doc.save(handle.name)
            figures = extract_figures(handle.name)

        self.assertEqual([fig.number for fig in figures], [1, 2, 8])
        self.assertEqual(figures[0].page, 1)
        self.assertIn("architecture", figures[0].caption)
        self.assertTrue(figures[0].png.startswith(b"\x89PNG"))
        self.assertNotIn("Figure 1:", figures[0].caption)

    def test_a_raster_image_under_a_fig_caption_is_kept(self):
        doc = pymupdf.open()
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 140, 90), 0)
        pix.set_rect(pix.irect, (20, 80, 180))
        page.insert_image(pymupdf.Rect(70, 80, 320, 250), pixmap=pix)
        page.insert_text((70, 280), "Fig. 3. The pipeline that routes each token.", fontsize=11)
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            doc.save(handle.name)
            figures = extract_figures(handle.name)
        self.assertEqual([fig.label for fig in figures], ["Figure 3"])
        self.assertIn("pipeline", figures[0].caption)

    def test_unreadable_pdf_is_a_retry_not_an_empty_result(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(b"%PDF")
            handle.flush()
            self.assertIsNone(extract_figures(handle.name))


if __name__ == "__main__":
    unittest.main()
