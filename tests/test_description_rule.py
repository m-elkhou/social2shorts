"""Strict description rule (owner, 2026-11-10): detailed text, signature, hashtags LAST."""

from types import SimpleNamespace

import pytest

from social2shorts.models import ShortMeta
from social2shorts.publish import youtube as yt
from social2shorts.publish.youtube import DescriptionRuleError, description_problems

SIG = "Suis-moi sur Instagram : @exemple"
BODY = (
    "Une balade de nuit sur une route de montagne, filmée au guidon : les virages "
    "s'enchaînent sous les phares.\n\nUn Short tourné sans artifice, pour partager le plaisir "
    "de rouler et l'ambiance d'une sortie "
    "entre passionnés. Abonne-toi pour la suite !"
)
GOOD = f"{BODY}\n\n{SIG}\n\n#moto #nuit #balade #Shorts"


def test_compliant_description_passes():
    assert description_problems(GOOD, SIG) == []


def test_no_signature_configured_is_refused():
    assert any("not configured" in p for p in description_problems(GOOD, ""))


def test_signature_must_hold_the_instagram():
    assert any("Instagram" in p for p in description_problems(GOOD.replace(SIG, "Merci"), "Merci"))


def test_hashtags_must_close_the_description():
    assert any("hashtags only" in p for p in description_problems(GOOD + "\n\nMerci", SIG))
    assert description_problems(f"{BODY}\n\n#moto #nuit #balade #Shorts\n\n{SIG}", SIG)


def test_too_few_hashtags_or_too_short_text_is_refused():
    assert any("fewer" in p for p in description_problems(f"{BODY}\n\n{SIG}\n\n#moto #Shorts", SIG))
    assert any("too short" in p for p in description_problems(f"Joli\n\n{SIG}\n\n#a #b #c #d", SIG))


def test_with_hashtags_always_ends_with_the_hashtag_line():
    out = yt.with_hashtags(f"{BODY}\n\n#moto #Shorts", ["nuit", "balade"], SIG)
    assert out.splitlines()[-1].startswith("#") and SIG in out
    assert out.rfind(SIG) < out.rfind(out.splitlines()[-1])
    assert description_problems(out, SIG) == []


def test_upload_is_refused_before_anything_is_sent(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    calls = []
    service = SimpleNamespace(videos=lambda: calls.append("videos"))
    client = yt.YouTubeClient("cs.json", "tok.json", signature=SIG, service=service)
    with pytest.raises(DescriptionRuleError):
        bare = ShortMeta(title="t", description="Night ride", tags=["moto"])
        client.upload(video, bare, privacy="unlisted")
    assert calls == []  # the YouTube service was never touched
