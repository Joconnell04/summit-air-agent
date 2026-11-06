from scripts.changed_tests import select_tests


def test_docs_and_snapshots_run_no_tests():
    assert select_tests(["docs/brief.md", "retell/PROMPT-LOG.md", "evals/rounds/x.md", "README.md"]) == []


def test_tool_server_changes_run_its_tests_and_the_retell_bridge():
    assert select_tests(["app/tools.py"]) == ["tests/tool_server", "tests/voice_retell"]


def test_a_changed_test_file_runs_itself():
    assert select_tests(["tests/tool_server/test_ts_booking.py"]) == ["tests/tool_server/test_ts_booking.py"]


def test_an_unknown_path_runs_everything():
    assert select_tests(["Makefile"]) is None
    assert select_tests(["new_folder/thing.py"]) is None
