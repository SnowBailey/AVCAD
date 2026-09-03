"""② /api/export-ease 端点包装层（dir 校验 + 调导出器 + 返回落盘路径）。"""
import os

from avcad.model.schema import DeviceInstance, Project
from avcad.ui import app as ui_app


def test_api_export_ease_writes_package(tmp_path, monkeypatch):
    # 用最小工程替掉 BOM 构建，专注测端点包装逻辑（dir 校验 + 调导出 + 返回）
    spk = DeviceInstance(uid="S1", category="SPEAKER", name="S1",
                         active=True, x=10, y=20, z=1.2)
    proj = Project(instances=[spk], name="T")

    def _fake_build(data):
        return proj, data.get("name", "T")

    monkeypatch.setattr(ui_app, "_build_project", _fake_build)

    res = ui_app._api_export_ease({"dir": str(tmp_path), "name": "T"})
    assert res["speaker_count"] == 1
    assert "speakers.csv" in res["files"]
    assert os.path.exists(res["files"]["speakers.csv"])
    assert res["name"] == "T"


def test_api_export_ease_rejects_missing_dir(tmp_path, monkeypatch):
    proj = Project(instances=[], name="T")
    monkeypatch.setattr(ui_app, "_build_project", lambda data: (proj, "T"))
    res = ui_app._api_export_ease({"dir": "", "name": "T"})
    assert "error" in res
