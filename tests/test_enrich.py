import json
from types import SimpleNamespace

from social2shorts.enrich import (
    DEFAULT_STYLE,
    FIXED_TAGS,
    Segment,
    build_ass,
    generate_metadata,
    render_ass,
    sanitize_meta,
    transcribe,
)
from social2shorts.models import CatalogEntry, Platform


def _entry(caption="@bob ride at night #fyp TikTok"):
    return CatalogEntry(platform=Platform.tiktok, source_id="1", url="u", caption=caption,
                        duration=20, view_count=1, like_count=1, comment_count=0,
                        upload_date="20260101", track="", artist="")


def test_build_ass_contains_dialogue():
    p = build_ass([Segment(0, 1, "salut"), Segment(1, 2, "moto")], style=DEFAULT_STYLE)
    assert "Dialogue:" in open(p, encoding="utf-8").read()


def test_render_ass_short_phrases_and_bold():
    out = render_ass([Segment(0, 4, "un deux trois quatre cinq six")])
    assert out.count("Dialogue:") == 2
    assert ",-1,0,0,0," in out  # bold


def test_sanitize_meta_caps_title_and_tags():
    m = sanitize_meta({"title": "x" * 200, "description": "d", "tags": [f"t{i}" for i in range(50)]})
    assert len(m.title) <= 100 and len(m.tags) <= 15
    assert all(t in m.tags for t in FIXED_TAGS)


def test_sanitize_strips_noise():
    m = sanitize_meta({"title": "@bob Night ride #fyp on TikTok", "description": "d",
                       "tags": ["#FYP", "Night Ride"]})
    assert "@" not in m.title and "tiktok" not in m.title.lower() and "fyp" not in m.title.lower()
    assert "fyp" not in m.tags and "nightride" in m.tags


def test_generate_metadata_with_fake_llm():
    seen = {}

    def fake(prompt):
        seen["p"] = prompt
        return "```json\n" + json.dumps({"title": "Midnight ride through Paris",
                                         "description": "d", "tags": ["paris"]}) + "\n```"

    e = _entry()
    m = generate_metadata(e, "bonjour", llm=fake)
    assert m.title != e.caption and m.title == "Midnight ride through Paris"
    assert "bonjour" in seen["p"]
    assert all(t in m.tags for t in FIXED_TAGS)


def test_transcribe_with_fake_model():
    class Fake:
        def __init__(self, segs):
            self.segs = segs

        def transcribe(self, path, **kw):
            return iter(self.segs), None

    segs = [SimpleNamespace(start=0, end=1.5, text=" salut "), SimpleNamespace(start=2, end=3, text="  ")]
    assert transcribe("x.mp4", model=Fake(segs)) == [Segment(0.0, 1.5, "salut")]
    assert transcribe("x.mp4", model=Fake([])) == []
