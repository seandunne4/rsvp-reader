from rsvp.cli import main


def test_doctor_imports_dependencies(capsys):
    assert main(["doctor"]) == 0
    assert "pymupdf" in capsys.readouterr().out


def test_planned_commands_report_not_implemented(capsys):
    assert main(["eval"]) == 1
    assert "Not implemented" in capsys.readouterr().out
