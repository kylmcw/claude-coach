import utils


def test_blank_is_default(monkeypatch):
    monkeypatch.setenv("GARMIN_COACH_PROFILE", "")
    assert utils.get_profile_suffix() == ""


def test_unset_is_default(monkeypatch):
    monkeypatch.delenv("GARMIN_COACH_PROFILE", raising=False)
    assert utils.get_profile_suffix() == ""


def test_unexpanded_placeholder_is_default(monkeypatch):
    # The bug this guards: an optional manifest field left blank can arrive literally.
    monkeypatch.setenv("GARMIN_COACH_PROFILE", "${user_config.profile_name}")
    assert utils.get_profile_suffix() == ""


def test_whitespace_is_default(monkeypatch):
    monkeypatch.setenv("GARMIN_COACH_PROFILE", "   ")
    assert utils.get_profile_suffix() == ""


def test_named_profile(monkeypatch):
    monkeypatch.setenv("GARMIN_COACH_PROFILE", "kayleigh")
    assert utils.get_profile_suffix() == "-kayleigh"
