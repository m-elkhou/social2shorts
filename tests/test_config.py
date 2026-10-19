from insta2youtube.config import load_config


def test_load_config_defaults(tmp_path):
    p = tmp_path/"c.toml"; p.write_text('tiktok_profile_url="https://www.tiktok.com/@motoride.mh"\nyoutube_handle="@motoride-mh"\noriginals_dir="o"\nwork_dir="w"\ndb_path="db.sqlite"\n')
    s = load_config(str(p))
    assert s.min_duration_s == 12.0 and s.default_privacy == "unlisted" and s.phash_frames == 6
