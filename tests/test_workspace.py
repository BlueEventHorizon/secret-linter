#!/usr/bin/env python3
"""secret-linter の作業領域（workspace.py）のユニットテスト。

DES-087 のモジュール表 W-1（作る）・W-2（書く）・W-5（消す）と、テスト表 SC-14・SC-16 の
作業領域に関する検証に対応する。

- 置き場の権限（作成者だけが読める 0700）、検査ごとに別の置き場になること（並行する検査が干渉しない）
- 検査 ID の検証（パス区切り・``..`` 等の拒否）
- 連番ファイルの採番、既存のファイルを上書きしないこと
- 消すと検査の値がファイルとして残らないこと、置き場が無くても成功すること、シンボリックリンクの拒否

作業領域は OS の一時ディレクトリに作られるため、テストが作った置き場は tearDown で必ず削除する。
テストの入力値は、明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.test_workspace -v
"""

import importlib.util
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
    / "workspace.py"
)

_spec = importlib.util.spec_from_file_location("secret_linter_workspace", _SCRIPT_PATH)
workspace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(workspace)

WorkspaceError = workspace.WorkspaceError

#: 値の代わりに置く、明白なプレースホルダ
PLACEHOLDER = {"value": "EXAMPLE_PLACEHOLDER_VALUE"}


class WorkspaceTestBase(unittest.TestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        # 失敗したテストでも置き場を残さない。remove() 自体のテストが壊れていても消えるよう、
        # shutil を直接使う。
        import shutil

        for inspection_id in self._created:
            path = workspace.workspace_path(inspection_id)
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path, ignore_errors=True)

    def create(self):
        inspection_id = workspace.create()
        self._created.append(inspection_id)
        return inspection_id


class CreateTest(WorkspaceTestBase):
    def test_returns_32_hex_id(self):
        inspection_id = self.create()
        self.assertRegex(inspection_id, r"^[0-9a-f]{32}$")

    def test_place_is_directory_under_system_temp_with_prefix(self):
        inspection_id = self.create()
        path = workspace.workspace_path(inspection_id)
        self.assertTrue(path.is_dir())
        self.assertEqual(path.parent, Path(tempfile.gettempdir()))
        self.assertTrue(path.name.startswith(workspace.PLACE_PREFIX))
        self.assertTrue(path.name.endswith(inspection_id))

    @unittest.skipIf(os.name == "nt", "POSIX のモードを前提とする")
    def test_place_is_readable_only_by_creator(self):
        inspection_id = self.create()
        mode = stat.S_IMODE(os.lstat(workspace.workspace_path(inspection_id)).st_mode)
        self.assertEqual(mode, 0o700)

    @unittest.skipIf(os.name == "nt", "POSIX のモードを前提とする")
    def test_place_mode_is_not_widened_by_permissive_umask(self):
        old = os.umask(0)
        try:
            inspection_id = self.create()
        finally:
            os.umask(old)
        mode = stat.S_IMODE(os.lstat(workspace.workspace_path(inspection_id)).st_mode)
        self.assertEqual(mode & 0o077, 0)

    def test_each_inspection_gets_its_own_place(self):
        first = self.create()
        second = self.create()
        self.assertNotEqual(first, second)
        self.assertNotEqual(
            workspace.workspace_path(first), workspace.workspace_path(second)
        )

    def test_concurrent_creates_do_not_collide(self):
        ids = []
        lock = threading.Lock()

        def worker():
            inspection_id = workspace.create()
            with lock:
                ids.append(inspection_id)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self._created.extend(ids)
        self.assertEqual(len(set(ids)), 8)

    def test_retries_with_another_id_when_place_exists(self):
        taken = "a" * 32
        taken_path = workspace.workspace_path(taken)
        taken_path.mkdir(mode=0o700)
        self._created.append(taken)
        original = os.urandom
        calls = iter([bytes.fromhex(taken), bytes.fromhex("b" * 32)])
        self._created.append("b" * 32)
        workspace.os.urandom = lambda n: next(calls)
        try:
            inspection_id = workspace.create()
        finally:
            workspace.os.urandom = original
        self.assertEqual(inspection_id, "b" * 32)
        # 先にあった置き場には触れない
        self.assertTrue(taken_path.is_dir())
        self.assertEqual(list(taken_path.iterdir()), [])


class ValidateIdTest(WorkspaceTestBase):
    def test_rejects_invalid_ids(self):
        invalid = [
            "",
            "../etc",
            "a" * 31,
            "a" * 33,
            "A" * 32,  # 大文字は create が返さない形
            "g" * 32,
            "a" * 31 + "/",
            ("a" * 30) + "..",
            None,
            123,
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(WorkspaceError):
                    workspace.workspace_path(value)

    def test_invalid_id_rejected_by_every_operation(self):
        for operation in (
            lambda i: workspace.write(i, "x.json", {}),
            lambda i: workspace.write_next(i, "d", {}),
            lambda i: workspace.remove(i),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(WorkspaceError):
                    operation("../escape")

    def test_error_message_does_not_echo_the_id(self):
        bad = "SECRET-LIKE-ID-PLACEHOLDER"
        with self.assertRaises(WorkspaceError) as ctx:
            workspace.workspace_path(bad)
        self.assertNotIn(bad, str(ctx.exception))


class WriteTest(WorkspaceTestBase):
    def test_writes_payload_unchanged(self):
        inspection_id = self.create()
        target = workspace.write(inspection_id, "units.json", PLACEHOLDER)
        self.assertEqual(target, workspace.workspace_path(inspection_id) / "units.json")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), PLACEHOLDER)

    def test_keeps_non_ascii_text_as_is(self):
        inspection_id = self.create()
        payload = {"label": "検査単位"}
        target = workspace.write(inspection_id, "units.json", payload)
        self.assertIn("検査単位", target.read_text(encoding="utf-8"))

    def test_does_not_overwrite_existing_file(self):
        inspection_id = self.create()
        target = workspace.write(inspection_id, "ai_done.json", {"exit": "0"})
        before = target.read_text(encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            workspace.write(inspection_id, "ai_done.json", {"exit": "1"})
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_leaves_no_temporary_file_after_success_or_refusal(self):
        inspection_id = self.create()
        workspace.write(inspection_id, "units.json", PLACEHOLDER)
        with self.assertRaises(WorkspaceError):
            workspace.write(inspection_id, "units.json", PLACEHOLDER)
        names = sorted(
            p.name for p in workspace.workspace_path(inspection_id).iterdir()
        )
        self.assertEqual(names, ["units.json"])

    def test_creates_subdirectory_with_mode_0700(self):
        inspection_id = self.create()
        target = workspace.write(inspection_id, "sub/dir/file.json", PLACEHOLDER)
        self.assertTrue(target.is_file())
        if os.name != "nt":
            root = workspace.workspace_path(inspection_id)
            directory = target.parent
            while directory != root:
                mode = stat.S_IMODE(os.lstat(directory).st_mode)
                self.assertEqual(mode & 0o077, 0, directory)
                directory = directory.parent

    def test_creates_intermediate_directories_with_mode_0700_under_a_permissive_umask(
        self,
    ):
        inspection_id = self.create()
        old = os.umask(0o022)
        try:
            target = workspace.write(inspection_id, "a/b/c/file.json", PLACEHOLDER)
            written = workspace.write_next(inspection_id, "x/y", PLACEHOLDER)
        finally:
            os.umask(old)
        if os.name != "nt":
            root = workspace.workspace_path(inspection_id)
            for leaf in (target.parent, written.parent):
                directory = leaf
                while directory != root:
                    mode = stat.S_IMODE(os.lstat(directory).st_mode)
                    self.assertEqual(mode & 0o077, 0, directory)
                    directory = directory.parent

    def test_rejects_paths_outside_the_place(self):
        inspection_id = self.create()
        for rel in ("../outside.json", "/abs.json", "", ".", "a/../../b.json"):
            with self.subTest(rel=rel):
                with self.assertRaises(WorkspaceError):
                    workspace.write(inspection_id, rel, PLACEHOLDER)

    def test_fails_when_place_does_not_exist(self):
        with self.assertRaises(WorkspaceError):
            workspace.write("c" * 32, "units.json", PLACEHOLDER)

    def test_fails_when_place_is_symlink(self):
        inspection_id = "d" * 32
        link = workspace.workspace_path(inspection_id)
        self._created.append(inspection_id)
        with tempfile.TemporaryDirectory() as outside:
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(WorkspaceError):
                workspace.write(inspection_id, "units.json", PLACEHOLDER)
            self.assertEqual(os.listdir(outside), [])

    def test_different_inspections_do_not_interfere(self):
        first = self.create()
        second = self.create()
        workspace.write(first, "units.json", {"owner": "first"})
        # 同じ相対パスでも、別の検査 ID の置き場なので拒否されず、互いの内容を変えない
        workspace.write(second, "units.json", {"owner": "second"})
        read = lambda i: json.loads(  # noqa: E731
            (workspace.workspace_path(i) / "units.json").read_text(encoding="utf-8")
        )
        self.assertEqual(read(first), {"owner": "first"})
        self.assertEqual(read(second), {"owner": "second"})


class WriteNextTest(WorkspaceTestBase):
    def test_numbers_files_from_0001(self):
        inspection_id = self.create()
        first = workspace.write_next(inspection_id, "judgment", {"n": 1})
        second = workspace.write_next(inspection_id, "judgment", {"n": 2})
        third = workspace.write_next(inspection_id, "judgment", {"n": 3})
        self.assertEqual(
            [p.name for p in (first, second, third)],
            ["0001.json", "0002.json", "0003.json"],
        )
        self.assertEqual(json.loads(third.read_text(encoding="utf-8")), {"n": 3})

    def test_earlier_files_are_not_modified(self):
        inspection_id = self.create()
        first = workspace.write_next(inspection_id, "ai_events", {"n": 1})
        workspace.write_next(inspection_id, "ai_events", {"n": 2})
        self.assertEqual(json.loads(first.read_text(encoding="utf-8")), {"n": 1})

    def test_sequence_files_lists_numbered_files_in_ascending_order(self):
        # 連番の規則は workspace が 1 か所だけに持つ。読む側（ai_events 等）が使う公開関数
        inspection_id = self.create()
        first = workspace.write_next(inspection_id, "judgment", {"n": 1})
        second = workspace.write_next(inspection_id, "judgment", {"n": 2})
        directory = workspace.workspace_path(inspection_id) / "judgment"
        (directory / "notes.txt").write_text("not numbered", encoding="utf-8")
        (directory / "12.json").write_text(
            "{}", encoding="utf-8"
        )  # 4 桁に満たない名前は連番ではない
        self.assertEqual(workspace.sequence_files(directory), [(1, first), (2, second)])

    def test_directories_are_numbered_independently(self):
        inspection_id = self.create()
        workspace.write_next(inspection_id, "judgment", {})
        workspace.write_next(inspection_id, "judgment", {})
        other = workspace.write_next(inspection_id, "ai_events", {})
        self.assertEqual(other.name, "0001.json")

    def test_next_number_follows_the_largest_existing_number(self):
        inspection_id = self.create()
        directory = workspace.workspace_path(inspection_id) / "judgment"
        directory.mkdir(mode=0o700)
        (directory / "0005.json").write_text("{}", encoding="utf-8")
        target = workspace.write_next(inspection_id, "judgment", {})
        self.assertEqual(target.name, "0006.json")

    def test_ignores_files_that_are_not_sequence_files(self):
        inspection_id = self.create()
        directory = workspace.workspace_path(inspection_id) / "judgment"
        directory.mkdir(mode=0o700)
        (directory / "notes.json").write_text("{}", encoding="utf-8")
        (directory / "12.json").write_text(
            "{}", encoding="utf-8"
        )  # 4 桁未満は連番ではない
        target = workspace.write_next(inspection_id, "judgment", {})
        self.assertEqual(target.name, "0001.json")

    def test_does_not_overwrite_when_number_was_taken_first(self):
        # 採番の後に同じ番号が先に公開された場合を作る: _last_number が古い値を返し続ける。
        inspection_id = self.create()
        directory = workspace.workspace_path(inspection_id) / "ai_events"
        directory.mkdir(mode=0o700)
        existing = directory / "0001.json"
        existing.write_text(json.dumps({"keep": True}), encoding="utf-8")

        original = workspace._last_number
        answers = iter([0, 1])
        workspace._last_number = lambda d: next(answers)
        try:
            target = workspace.write_next(inspection_id, "ai_events", {"keep": False})
        finally:
            workspace._last_number = original
        self.assertEqual(target.name, "0002.json")
        self.assertEqual(
            json.loads(existing.read_text(encoding="utf-8")), {"keep": True}
        )

    def test_concurrent_writers_never_overwrite_each_other(self):
        inspection_id = self.create()
        count = 12
        errors = []

        def worker(n):
            try:
                workspace.write_next(inspection_id, "ai_events", {"n": n})
            except Exception as exc:  # noqa: BLE001 - 結果を集計して検証する
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        directory = workspace.workspace_path(inspection_id) / "ai_events"
        files = sorted(p for p in directory.iterdir() if p.suffix == ".json")
        self.assertEqual(len(files), count)
        values = sorted(json.loads(p.read_text(encoding="utf-8"))["n"] for p in files)
        self.assertEqual(values, list(range(count)))

    def test_rejects_directory_outside_the_place(self):
        inspection_id = self.create()
        with self.assertRaises(WorkspaceError):
            workspace.write_next(inspection_id, "../outside", {})

    def test_fails_when_place_does_not_exist(self):
        with self.assertRaises(WorkspaceError):
            workspace.write_next("e" * 32, "judgment", {})


class ReadJsonTest(WorkspaceTestBase):
    """作業領域の JSON を読む処理は 1 か所にある。失敗は ``WorkspaceError`` で、理由に値を含めない。"""

    def test_reads_a_json_file(self):
        inspection_id = self.create()
        path = workspace.write(
            inspection_id, "a.json", {"value": "EXAMPLE_PLACEHOLDER_VALUE"}
        )
        self.assertEqual(
            workspace.read_json(path, "対象"), {"value": "EXAMPLE_PLACEHOLDER_VALUE"}
        )

    def test_missing_file_raises_with_the_name(self):
        inspection_id = self.create()
        path = workspace.workspace_path(inspection_id) / "none.json"
        with self.assertRaises(WorkspaceError) as ctx:
            workspace.read_json(path, "対象")
        self.assertIn("対象", str(ctx.exception))
        self.assertIn("ありません", str(ctx.exception))

    def test_broken_json_raises_without_the_content(self):
        inspection_id = self.create()
        path = workspace.workspace_path(inspection_id) / "broken.json"
        path.write_text("{EXAMPLE_PLACEHOLDER_VALUE", encoding="utf-8")
        with self.assertRaises(WorkspaceError) as ctx:
            workspace.read_json(path, "対象")
        self.assertIn("読めません", str(ctx.exception))
        self.assertNotIn("EXAMPLE_PLACEHOLDER_VALUE", str(ctx.exception))

    def test_unreadable_path_raises(self):
        inspection_id = self.create()
        with self.assertRaises(WorkspaceError):
            workspace.read_json(
                workspace.workspace_path(inspection_id), "対象"
            )  # ディレクトリ


class SequenceFilenameTest(WorkspaceTestBase):
    def test_four_digits_with_json_suffix(self):
        self.assertEqual(workspace.sequence_filename(1), "0001.json")
        self.assertEqual(workspace.sequence_filename(12345), "12345.json")

    def test_write_next_uses_the_same_name(self):
        inspection_id = self.create()
        path = workspace.write_next(inspection_id, "judgment", {})
        self.assertEqual(path.name, workspace.sequence_filename(1))


class RemoveTest(WorkspaceTestBase):
    def test_removes_place_and_all_contents(self):
        inspection_id = self.create()
        workspace.write(inspection_id, "units.json", PLACEHOLDER)
        workspace.write_next(inspection_id, "judgment", PLACEHOLDER)
        workspace.write_next(inspection_id, "ai_events", PLACEHOLDER)
        workspace.remove(inspection_id)
        self.assertFalse(os.path.lexists(workspace.workspace_path(inspection_id)))

    def test_no_file_holding_the_value_remains_after_remove(self):
        # SC-14: 結果（値）がファイルとして残らない
        inspection_id = self.create()
        place = workspace.workspace_path(inspection_id)
        workspace.write(inspection_id, "pattern.json", PLACEHOLDER)
        workspace.write_next(inspection_id, "judgment", PLACEHOLDER)
        workspace.remove(inspection_id)
        self.assertFalse(place.exists())
        leftovers = [
            p
            for p in Path(tempfile.gettempdir()).glob(
                f"{workspace.PLACE_PREFIX}{inspection_id}*"
            )
        ]
        self.assertEqual(leftovers, [])

    def test_succeeds_when_place_does_not_exist(self):
        self.assertIsNone(workspace.remove("f" * 32))

    def test_succeeds_when_called_twice(self):
        inspection_id = self.create()
        workspace.remove(inspection_id)
        self.assertIsNone(workspace.remove(inspection_id))

    def test_does_not_touch_other_inspections(self):
        mine = self.create()
        other = self.create()
        workspace.write(other, "units.json", PLACEHOLDER)
        workspace.remove(mine)
        self.assertTrue((workspace.workspace_path(other) / "units.json").is_file())

    def test_refuses_symlink_place_and_keeps_target(self):
        inspection_id = "1" * 32
        link = workspace.workspace_path(inspection_id)
        self._created.append(inspection_id)
        with tempfile.TemporaryDirectory() as outside:
            victim = Path(outside) / "victim.txt"
            victim.write_text("keep", encoding="utf-8")
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(WorkspaceError):
                workspace.remove(inspection_id)
            self.assertTrue(victim.is_file())
            self.assertTrue(link.is_symlink())

    def test_refuses_place_that_is_a_regular_file(self):
        inspection_id = "2" * 32
        path = workspace.workspace_path(inspection_id)
        self._created.append(inspection_id)
        path.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            workspace.remove(inspection_id)
        self.assertTrue(path.is_file())

    def test_removes_symlink_inside_place_without_following_it(self):
        inspection_id = self.create()
        place = workspace.workspace_path(inspection_id)
        with tempfile.TemporaryDirectory() as outside:
            victim = Path(outside) / "victim.txt"
            victim.write_text("keep", encoding="utf-8")
            (place / "link").symlink_to(outside, target_is_directory=True)
            workspace.remove(inspection_id)
            self.assertFalse(place.exists())
            self.assertTrue(victim.is_file())

    @unittest.skipIf(
        os.name == "nt" or not hasattr(os, "getuid"), "所有者の概念が POSIX 前提"
    )
    def test_refuses_place_owned_by_someone_else(self):
        inspection_id = self.create()
        real_getuid = os.getuid
        workspace.os.getuid = lambda: real_getuid() + 1
        try:
            with self.assertRaises(WorkspaceError):
                workspace.remove(inspection_id)
        finally:
            workspace.os.getuid = real_getuid
        self.assertTrue(workspace.workspace_path(inspection_id).is_dir())

    def test_error_message_does_not_contain_stored_values(self):
        inspection_id = "3" * 32
        link = workspace.workspace_path(inspection_id)
        self._created.append(inspection_id)
        with tempfile.TemporaryDirectory() as outside:
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(WorkspaceError) as ctx:
                workspace.remove(inspection_id)
        self.assertNotIn(PLACEHOLDER["value"], str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
