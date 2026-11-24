"""`:viewer[...]` references: grammar parity with dreamlake-ai lib/preview/reference.ts."""
import pytest

from dreamlake.api import (
    format_viewer_ref,
    parse_viewer_ref,
    viewer_directive,
    viewer_href,
    viewer_layout_request,
)


@pytest.mark.parametrize(
    "text",
    [
        "fortyfive/teleop-berry@1#scene",
        "fortyfive/teleop-berry#scene",
        "library:fortyfive/flexiv#mjlab-flexiv-rizon4s-claw--v2",
        "scene:fortyfive/s1-lab@3",
        "scene:fortyfive/s1-lab",
    ],
)
def test_round_trip(text):
    ref = parse_viewer_ref(text)
    assert ref is not None
    assert format_viewer_ref(ref) == text == str(ref)


@pytest.mark.parametrize(
    "text",
    [
        "fortyfive/teleop-berry",
        "fortyfive/teleop-berry@latest#scene",
        "fortyfive/teleop-berry@0#scene",
        "library:fortyfive/flexiv",
        "library:fortyfive/flexiv#has space",
        "scene:fortyfive/s1-lab#entry",
        "",
    ],
)
def test_rejects(text):
    assert parse_viewer_ref(text) is None
    with pytest.raises(ValueError):
        viewer_directive(text)


def test_directive_href_and_layout_request():
    assert viewer_directive("ns/b@2#e") == ":viewer[ns/b@2#e]"
    assert viewer_href("ns/b@2#e") == "/ns/bundles/b/v/2/entries/e"
    assert viewer_href("library:ns/lib#a") == "/ns/libraries/lib?asset=a"
    assert viewer_href("scene:ns/s@3") == "/ns/scenes/s?version=3"
    assert viewer_layout_request("scene:ns/s@3", "r1", 7) == {
        "action": "open",
        "view": {"kind": "asset-preview", "resource": "scene:ns/s@3", "content": {"ref": "scene:ns/s@3"}},
        "destination": {"id": "r1"},
        "placement": "right",
        "ifRevision": 7,
    }
    with pytest.raises(ValueError):
        viewer_layout_request("ns/b@2#e", "r1", -1)
    with pytest.raises(ValueError):
        viewer_layout_request("ns/b@2#e", "r1", 1, placement="center")  # type: ignore[arg-type]
