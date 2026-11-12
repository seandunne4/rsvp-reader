from rsvp.cli import main


def test_doctor_imports_dependencies(capsys):
    assert main(["doctor"]) == 0
    assert "pymupdf" in capsys.readouterr().out


