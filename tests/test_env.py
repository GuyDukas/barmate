import os

from app.env import load_env


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_env(tmp_path / "nothing-here") is False


def test_values_reach_the_environment(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# a comment\n\nBARMATE_TEST_A=plain\n'
                    'BARMATE_TEST_B="quoted value"\n', encoding="utf-8")
    try:
        assert load_env(path) is True
        assert os.environ["BARMATE_TEST_A"] == "plain"
        assert os.environ["BARMATE_TEST_B"] == "quoted value"
    finally:
        for key in ("BARMATE_TEST_A", "BARMATE_TEST_B"):
            os.environ.pop(key, None)


def test_the_environment_wins_over_the_file(tmp_path):
    """Vercel injects the real values. A .env that shipped by accident must not
    be able to point the deployed function somewhere else."""
    path = tmp_path / ".env"
    path.write_text("BARMATE_TEST_C=from-the-file\n", encoding="utf-8")
    os.environ["BARMATE_TEST_C"] = "already-set"
    try:
        load_env(path)
        assert os.environ["BARMATE_TEST_C"] == "already-set"
    finally:
        os.environ.pop("BARMATE_TEST_C", None)
