"""Reproduce Windows status-reader locks without sending model requests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_agent.progress import write_status_snapshot


class ProgressSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "runs")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "status.json"
        write_status_snapshot(self.path, {"completed": 1})

    def test_temporary_sharing_violation_recovers_without_truncating_previous_json(self):
        replace = os.replace
        attempts = []
        def occasionally_locked(source, target):
            attempts.append(source)
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["completed"], 1)
            if len(attempts) < 3:
                raise PermissionError("status reader denies atomic replacement")
            replace(source, target)
        with patch("review_agent.progress.os.replace", side_effect=occasionally_locked), \
                patch("review_agent.progress.time.sleep") as delay:
            write_status_snapshot(self.path, {"completed": 2})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(delay.call_count, 2)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["completed"], 2)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_persistent_lock_preserves_previous_snapshot_and_cleans_own_temp_file(self):
        with patch("review_agent.progress.os.replace", side_effect=PermissionError("locked")) as replace, \
                patch("review_agent.progress.time.sleep"):
            with self.assertRaises(PermissionError):
                write_status_snapshot(self.path, {"completed": 2})
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["completed"], 1)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
    def test_actual_windows_reader_without_delete_sharing_blocks_then_recovers(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.path), 0x80000000, 3, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            with self.assertRaises(PermissionError):
                write_status_snapshot(self.path, {"completed": 2}, backoff_seconds=0)
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["completed"], 1)
        finally:
            kernel.CloseHandle(handle)
        write_status_snapshot(self.path, {"completed": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["completed"], 2)


if __name__ == "__main__":
    unittest.main()
