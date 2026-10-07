"""RECOVERYLENS_PUBLIC_DEMO=true must disable every route that acts on the SERVER's own local
filesystem (Real Recovery Mode, Recovery Shield, free-form storage-image upload, restore-to-folder)
while leaving every Demo Mode / read-only analysis route working normally. This is what makes it
safe to point a public hosting link at a shared server: a remote visitor can never scan drive
letters, register arbitrary local folders, or write files to the server outside the demo cases.

Run from backend/:  .venv\\Scripts\\python -m pytest -q tests/test_public_demo.py
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PublicDemoModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["RECOVERYLENS_PUBLIC_DEMO"] = "true"
        from app import main as main_module
        cls.main_module = importlib.reload(main_module)  # re-reads the env var at module load time
        from fastapi.testclient import TestClient
        cls.ctx = TestClient(cls.main_module.app)
        cls.c = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        del os.environ["RECOVERYLENS_PUBLIC_DEMO"]
        importlib.reload(cls.main_module)  # restore normal mode for any test file imported after this one

    def test_config_reports_public_demo(self):
        self.assertEqual(self.c.get("/api/config").json(), {"public_demo": True})

    def test_storage_image_upload_disabled(self):
        r = self.c.post("/api/cases/upload", files={"file": ("x.pdf", b"%PDF-1.4 x")}, data={"authorized": "true"})
        self.assertEqual(r.status_code, 403)

    def test_real_recovery_routes_do_not_exist(self):
        # GET on a nonexistent /api/... path hits the SPA fallback's explicit "api/ -> 404" check;
        # POST matches the fallback's route pattern (any path) but not its GET-only method, so
        # Starlette reports 405 before the api/ check ever runs -- both mean "this route is gone".
        self.assertEqual(self.c.get("/api/real/drives").status_code, 404)
        self.assertEqual(self.c.post("/api/real/recyclebin/scan", json={"drive": "C:"}).status_code, 405)

    def test_shield_routes_do_not_exist(self):
        self.assertEqual(self.c.get("/api/shield/sources").status_code, 404)
        self.assertEqual(self.c.post("/api/shield/sources", json={"path": "C:\\anything"}).status_code, 405)

    def test_restore_to_folder_disabled(self):
        r = self.c.post("/api/cases/some-case/files/R001/restore", json={"authorized": True})
        self.assertEqual(r.status_code, 403)

    def test_demo_mode_still_fully_works(self):
        cid = self.c.post("/api/cases", json={"dataset": "B"}).json()["id"]
        self.assertTrue(cid)
        self.assertEqual(self.c.get(f"/api/cases/{cid}").status_code, 200)
        self.assertEqual(self.c.get("/api/datasets").status_code, 200)


if __name__ == "__main__":
    unittest.main()
