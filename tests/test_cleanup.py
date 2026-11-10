import os
import time

from app import tasks
from app.cleanup import remove_old_files


def test_only_files_older_than_an_hour_are_removed(tmp_path):
    old = tmp_path / "old.txt"
    fresh = tmp_path / "fresh.txt"
    old.write_text("x")
    fresh.write_text("x")
    two_hours_ago = time.time() - 2 * 3600
    os.utime(old, (two_hours_ago, two_hours_ago))

    assert remove_old_files(tmp_path, 3600) == 1
    assert not old.exists()
    assert fresh.exists()


def test_forgotten_work_folders_are_removed(tmp_path):
    work = tmp_path / "task.work"
    work.mkdir()
    (work / "llms.txt").write_text("x")
    two_hours_ago = time.time() - 2 * 3600
    os.utime(work, (two_hours_ago, two_hours_ago))

    assert remove_old_files(tmp_path, 3600) == 1
    assert not work.exists()


def test_missing_directory_is_fine(tmp_path):
    assert remove_old_files(tmp_path / "nope", 3600) == 0


def test_old_finished_tasks_are_forgotten():
    finished = tasks.create_task()
    running = tasks.create_task()
    tasks._finish(finished, tasks.DONE)

    assert tasks.forget_old_tasks(3600, now=time.time() + 7200) == 1
    assert tasks.get_task(finished) is None
    assert tasks.get_task(running) is not None
