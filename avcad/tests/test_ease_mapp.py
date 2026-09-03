"""② EASE/MAPP 对接导出（doc 35 Phase A）：speakers.csv / audience.csv / project.json。"""
import csv
import json
import os

from avcad.model.schema import DeviceInstance, Project
from avcad.deliverables.ease_mapp import (
    speakers_rows, audience_rows, build_project_json,
    export_ease_package, _stage_center, _ease_scale,
)


def _speaker(uid, x=0.0, y=0.0, z=0.0, aim_az=0.0, aim_el=0.0,
             rot_z=0.0, model="X8", power_w=600, sensitivity_db=95.0):
    return DeviceInstance(uid=uid, category="SPEAKER", name=uid, model=model,
                          active=True, x=x, y=y, z=z,
                          aim_az=aim_az, aim_el=aim_el, rot_z=rot_z,
                          electrical={"power_w": power_w},
                          params={"sensitivity_db": sensitivity_db})


def test_speakers_rows_full_fields_and_centered_coords():
    # 两台音箱，舞台中心取质心 (50,100)
    p = Project(instances=[
        _speaker("S1", x=0, y=0, z=1.5, aim_az=30, aim_el=-10),
        _speaker("S2", x=100, y=200, z=2.0, aim_az=-45, aim_el=5, rot_z=90),
    ])
    rows = speakers_rows(p)
    assert len(rows) == 2
    # 质心 = (50,100)；S1 相对质心 (-50,-100)，scale 默认 1 → -50.0 / -100.0
    r1 = next(r for r in rows if r[0] == "S1")
    assert r1[2] == "-50.0" and r1[3] == "-100.0"
    assert r1[4] == "1500.0"          # z 米→mm
    assert r1[5] == "30.0" and r1[6] == "-10.0" and r1[7] == "0.0"
    assert r1[8] == "SPEAKER"
    assert r1[9] == 600 and r1[10] == 95.0
    # S2 相对质心 (50,100) → 50.0 / 100.0
    r2 = next(r for r in rows if r[0] == "S2")
    assert r2[2] == "50.0" and r2[3] == "100.0"
    assert r2[5] == "-45.0" and r2[7] == "90.0"


def test_stage_center_override_via_meta():
    p = Project(instances=[_speaker("S1", x=1000, y=2000)])
    p.meta["stage_center"] = [1000, 2000]
    assert _stage_center(p) == (1000.0, 2000.0)
    assert _ease_scale(p) == 1.0


def test_non_speaker_excluded():
    p = Project(instances=[
        _speaker("S1", x=0, y=0),
        DeviceInstance(uid="m", category="MIXER", name="调音台", active=False),
    ])
    rows = speakers_rows(p)
    assert [r[0] for r in rows] == ["S1"]


def test_audience_from_meta():
    p = Project(instances=[_speaker("S1")])
    p.meta["audience_positions"] = [{"x": 5, "y": 3, "z": 1.2}]
    rows = audience_rows(p)
    assert len(rows) == 1
    assert rows[0][0] == "A1" and rows[0][3] == "1200.0"  # z 米→mm


def test_export_ease_package_writes_three_files(tmp_path):
    p = Project(instances=[_speaker("S1", x=50, y=50, z=1.2)])
    res = export_ease_package(p, str(tmp_path))
    assert set(res["files"].keys()) >= {"speakers.csv", "audience.csv", "project.json"}
    assert res["speaker_count"] == 1

    with open(res["files"]["speakers.csv"], encoding="utf-8") as f:
        r = list(csv.reader(f))
    assert r[0] == ["id", "model", "x", "y", "z", "aim_az", "aim_el",
                    "rot_z", "category", "power_w", "sensitivity_db"]
    assert r[1][0] == "S1"

    with open(res["files"]["project.json"], encoding="utf-8") as f:
        j = json.load(f)
    assert j["project"] == p.name
    assert j["speakers"][0]["id"] == "S1"
    assert "unmodeled_notes" in j
