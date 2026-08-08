"""
Regression test for the mobile nav overflow bug (2026-07-26 audit finding):
on narrow viewports, the topbar's `.tb-line1` grid (three columns: period
toggle | nav | lang toggle) let the center `nav-btns` column claim more
than its fair share of width, squeezing `.tb-side-right` to zero and
pushing content past the viewport edge.

`min-width: 0` on `.nav-btns` (base.html, the same pattern already used on
`.tb-side`) lets the nav's own flex children wrap, but doesn't by itself
shrink the grid's unconstrained auto center track - the real fix is the
`grid-template-columns: 1fr` override in the `max-width: 640px` media
query, which stacks the three columns instead of asking any of them to
shrink.
"""

import pytest

PHONE_WIDTHS = [375, 390, 412]  # iPhone SE, iPhone 12/13, common Android


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_topbar_children_stay_within_viewport(live_server, browser, width):
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        page.goto(live_server + "/")
        page.wait_for_selector(".tb-line1")

        viewport_right = width
        for selector in (".tb-side-left", ".nav-btns", ".tb-side-right"):
            box = page.locator(selector).bounding_box()
            assert box is not None, f"{selector} not found in DOM"
            assert box["x"] + box["width"] <= viewport_right + 1, (
                f"{selector} overflows viewport at {width}px: "
                f"right edge {box['x'] + box['width']:.1f} > {viewport_right}"
            )
    finally:
        context.close()


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_lang_toggle_not_squeezed_to_zero(live_server, browser, width):
    """.tb-side-right (the es/en toggle) is the specific column that was
    getting compressed to zero width by the unconstrained nav-btns column."""
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        page.goto(live_server + "/")
        box = page.locator(".tb-side-right").bounding_box()
        assert box is not None
        assert box["width"] > 0, f".tb-side-right collapsed to zero width at {width}px"
    finally:
        context.close()
