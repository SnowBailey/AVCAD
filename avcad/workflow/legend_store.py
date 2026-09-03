"""图例库（**永久文档**）持久化层。

定位：这不是缓存，是一份可长期维护的**永久文档**。
每次用户在第③步确认 / 修改某个型号的端口定义，都会**立即原子写入磁盘**，
revision 递增并保留维护历史；下次遇到相同 (品牌, 型号, 类别) 时，
读到的就是**最后一次维护**的结果。

★ 优先级（重要）
    永久图例库  >  引擎推断值
即 build_project() 在实例建成（引擎推断端口）之后，立即用图例库里的定义
**整体覆盖** inst.ports —— 只要库里有这条记录，就以库为准，引擎推断仅作兜底。

★ 键
    brand::model::category
必须带 category：否则「无品牌型号的不同类别设备」（会议话筒 / 无线话筒发射端 /
天线 / 扬声器 …）会互相覆盖，造成图例丢失。
"""
from __future__ import annotations
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from avcad.model.schema import ConcretePort, Signal

# 永久图例库文件（旧名字 legend_cache.json 仅作为迁移来源，不再写入）
# 环境变量 AVCAD_LEGEND_LIBRARY 可覆盖路径：打包成 .app 后写入用户目录，避免只读 / 数据隔离问题
DEFAULT_CACHE = Path(
    os.environ.get("AVCAD_LEGEND_LIBRARY")
    or (Path(__file__).resolve().parents[1] / "data" / "legend_library.json")
)
LEGACY_CACHE = Path(
    os.environ.get("AVCAD_LEGEND_CACHE")
    or (Path(__file__).resolve().parents[1] / "data" / "legend_cache.json")
)

SCHEMA = "avcad.legend-library/1"
_HISTORY_KEEP = 5          # 每条图例保留最近几次维护记录


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dir_writable(p: Path) -> bool:
    """判断目录是否可写（建目录 + 试建临时文件双重校验）。"""
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".avcad_write_probe"
        probe.touch()
        probe.unlink()
        return True
    except (OSError, PermissionError):
        return False


def _legend_user_path() -> Path:
    """用户可写目录下的图例库路径（打包只读时回落点）。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AVCAD"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "AVCAD"
    else:
        base = Path.home() / ".avcad"
    return base / "legend_library.json"


def _resolve_legend_path(path: Optional[str] = None) -> Path:
    """解析图例库路径（显式 > 模块默认(含 AVCAD_LEGEND_LIBRARY) > 只读则用户目录重定向）。

    ★ S7 修复：打包成 .app / Program Files 后 avcad/data 为只读，原逻辑直接写该目录
    会抛 PermissionError 使 /api/legend 保存 500。这里在默认库所在目录只读时自动重定向
    到用户可写目录，保证「永久文档」仍能落盘且不崩。开发模式下 data 目录可写，本函数
    等价于原 DEFAULT_CACHE，行为不变。
    """
    if path:
        return Path(path).expanduser()
    default = Path(DEFAULT_CACHE)    # 尊重测试对 DEFAULT_CACHE 的 monkeypatch（可能是 str）
    if _dir_writable(default.parent):
        return default
    return _legend_user_path()


@dataclass
class LegendPort:
    signal: str           # Signal 枚举名，如 "XLR"
    role: str = "io"      # in / out / io
    side: str = "right"   # left / right / top / bottom
    count: int = 1        # 该类端口的数量（一类 N 口 → 图上展开为 LABEL1…LABELn）
    label: str = ""       # 主标签；为空时用 signal 名
    air: bool = False     # 空中/非线缆接口（如天线 RF）

    def to_dict(self) -> dict:
        return {
            "signal": self.signal, "role": self.role, "side": self.side,
            "count": self.count, "label": self.label, "air": self.air,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LegendPort":
        return cls(
            signal=d["signal"], role=d.get("role", "io"), side=d.get("side", "right"),
            count=int(d.get("count", 1)), label=d.get("label", ""),
            air=bool(d.get("air", False)),
        )


@dataclass
class Legend:
    brand: str
    model: str
    category: str = ""
    ports: List[LegendPort] = field(default_factory=list)
    slots: List[dict] = field(default_factory=list)   # 卡槽条可视化，如 [{type,count,label}]
    note: str = ""
    # 电气量化参数（可选，用于 GB 55024-2022 强条 ERROR 级判定）：
    #   ground_wire_mm2 接地/等电位联结导体截面积(mm²，强条≥4)；
    #   emg_spl_db 应急广播设计声压级(dB)；bg_spl_db 背景噪声(dB)。
    # 缺则退回 WARN 提醒；与 DeviceInstance.electrical 同构，图例库=真相覆盖之。
    electrical: dict = field(default_factory=dict)
    # ---- 永久文档的维护元数据 ----
    source: str = "user"        # user=用户确认 / engine=引擎推断回填 / migrated=迁移
    revision: int = 1           # 第几次维护
    created_at: str = ""
    updated_at: str = ""
    history: List[dict] = field(default_factory=list)  # 最近若干次维护快照

    def to_dict(self) -> dict:
        return {
            "brand": self.brand, "model": self.model, "category": self.category,
            "key": LegendStore.key(self.brand, self.model, self.category),
            "ports": [p.to_dict() for p in self.ports],
            "slots": list(self.slots), "note": self.note,
            "electrical": dict(self.electrical or {}),
            "source": self.source, "revision": self.revision,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Legend":
        return cls(
            brand=d.get("brand", ""), model=d.get("model", ""),
            category=d.get("category", ""),
            ports=[LegendPort.from_dict(x) for x in d.get("ports", [])],
            slots=list(d.get("slots", [])), note=d.get("note", ""),
            electrical=dict(d.get("electrical") or {}),
            source=d.get("source", "user"),
            revision=int(d.get("revision", 1) or 1),
            created_at=d.get("created_at", ""), updated_at=d.get("updated_at", ""),
            history=list(d.get("history", []) or []),
        )


class LegendStore:
    def __init__(self, path: Optional[str] = None):
        self.path = _resolve_legend_path(path)
        # 仅当「未显式指定 path」且「解析出的路径已偏离默认库」时才算打包只读重定向：
        # 此时首次运行用户目录文件不存在，需从包内随附默认库播种。其余情况（显式 path、
        # 测试 monkeypatch DEFAULT_CACHE、开发模式）一律不自动播种，保持空库语义。
        self._redirected = (path is None and self.path != Path(DEFAULT_CACHE))
        self._mem: dict = {}
        self._load_file()

    # ---- 键：brand::model::category（category 必带，避免不同类别互相覆盖） ----
    @staticmethod
    def key(brand: str, model: str, category: str = "") -> str:
        b = (brand or "").strip() or "_generic"
        m = (model or "").strip() or "_"
        c = (category or "").strip()
        return f"{b}::{m}::{c}" if c else f"{b}::{m}"

    # ---- 读写内存 ----
    def get(self, brand: str, model: str, category: str = "") -> Optional[Legend]:
        """按 brand::model::category 取图例。

        ★ 类别回退规则（2026-09-01 收紧）：
          - 精确键命中 -> 直接返回
          - 未传 category -> 取该型号下键名最小的一条（原行为，兼容旧调用）
          - **同型号在图例库里只有一条** -> 允许跨类别命中
            （主库类别漂移时图例不至于整体失效，实例类别随之被图例纠正）
          - **同型号有多条（同型号跨类别设备）** -> 必须类别精确匹配
            此前无条件回退到「任意类别的第一条」，会把会议主机的图例套到
            同型号处理器上，且 ``apply()`` 还会把实例 category 一起改错。
        """
        k = self.key(brand, model, category)
        if k in self._mem:
            return self._mem[k]
        prefix = self.key(brand, model)
        cands = sorted((kk, v) for kk, v in self._mem.items()
                       if kk == prefix or kk.startswith(prefix + "::"))
        if not cands:
            return None
        if not (category or "").strip():
            return cands[0][1]
        if len(cands) == 1:
            return cands[0][1]
        return None

    def has(self, brand: str, model: str, category: str = "") -> bool:
        return self.get(brand, model, category) is not None

    def put(self, legend: Legend, source: str = "user") -> Legend:
        """写入一条图例（内存）。键含 category，并维护 revision / 历史。"""
        k = self.key(legend.brand, legend.model, legend.category)
        old = self._mem.get(k)
        now = _now()
        if old is not None:
            legend.created_at = old.created_at or old.updated_at or now
            legend.revision = int(old.revision or 0) + 1
            hist = list(old.history or [])
            hist.append({
                "revision": int(old.revision or 0),
                "updated_at": old.updated_at or now,
                "ports": [p.to_dict() for p in old.ports],
                "slots": list(old.slots or []),
                "note": old.note or "",
                "source": old.source or "user",
            })
            legend.history = hist[-_HISTORY_KEEP:]
        else:
            legend.created_at = now
            legend.revision = 1
            legend.history = list(legend.history or [])[-_HISTORY_KEEP:]
        legend.updated_at = now
        legend.source = source
        self._mem[k] = legend
        return legend

    def all(self) -> List[Legend]:
        return list(self._mem.values())

    def info(self) -> dict:
        """图例库概览（给 UI 展示「这是一份永久文档，不是缓存」）。"""
        return {
            "path": str(self.path),
            "schema": SCHEMA,
            "count": len(self._mem),
            "updated_at": max([lg.updated_at for lg in self._mem.values()] or [""]),
        }

    # ---- 文件持久化（永久文档） ----
    def _load_file(self) -> None:
        if self.path.exists():
            src = self.path
        elif self._redirected:
            # 打包只读重定向：用户目录文件首次尚不存在，但包内随附默认图例库存在，
            # 先读入内存（后续保存写入用户目录）；非重定向场景不自动播种，保持空库。
            bundled = Path(__file__).resolve().parents[1] / "data" / "legend_library.json"
            if bundled.exists():
                src = bundled
            else:
                return
        else:
            return
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            return
        dirty = False
        for rec in data.get("legends", []):
            try:
                lg = Legend.from_dict(rec)
                if not lg.updated_at:
                    lg.updated_at = _now()
                    lg.created_at = lg.created_at or lg.updated_at
                    dirty = True
                k = self.key(lg.brand, lg.model, lg.category)
                prev = self._mem.get(k)
                # 同键重复（旧版本 bug 导致）→ 保留 revision 更大 / 更新时间更晚的那条
                if prev is None or int(lg.revision or 0) >= int(prev.revision or 0):
                    self._mem[k] = lg
            except Exception:
                continue
        if dirty and self._mem:
            try:
                self.save()
            except Exception:
                pass

    def save(self) -> None:
        """原子写：先写临时文件再 os.replace，避免半截文件。

        ★ S7 修复：打包后 avcad/data 在只读 .app 包内，直接写会抛 PermissionError 使
        /api/legend 保存 500。self.path 已由 _resolve_legend_path 重定向到用户可写目录；
        此处再套 try/except：万一目标仍不可写（极端只读环境），安全跳过本次落盘
        （内存记录仍在、会话内可用），绝不抛异常中断保存请求。
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            return
        # 去重：同一个 key 只保留一条（防止历史数据里的重复记录）
        dedup: dict = {}
        for lg in self._mem.values():
            k = self.key(lg.brand, lg.model, lg.category)
            prev = dedup.get(k)
            if prev is None or int(lg.revision or 0) >= int(prev.revision or 0):
                dedup[k] = lg
        self._mem = dedup
        data = {
            "schema": SCHEMA,
            "kind": "永久图例库（非缓存；图例库优先级高于引擎推断）",
            "updated_at": _now(),
            "count": len(dedup),
            "legends": [lg.to_dict() for lg in dedup.values()],
        }
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except (OSError, PermissionError):
            # 只读环境兜底：本次不落盘，不影响内存中的图例记录
            return

    # ---- 回填到实例（图例库 > 引擎推断） ----
    def apply(self, inst, legend: Optional[Legend] = None,
              source: str = "user") -> object:
        """把图例展开为 ConcretePort **覆盖** inst.ports。

        调用时机：build_project() 在引擎推断出端口之后调用本方法，
        因此只要图例库里有这条记录，就以库为准（永久文档优先）。
        """
        lg = legend or self.get(inst.brand, inst.model, getattr(inst, "category", ""))
        if lg is None:
            return inst
        if lg.category:
            inst.category = lg.category
        if lg.slots:
            inst.slots = list(lg.slots)
        # 电气量化参数：图例库=真相，覆盖引擎/BOM 推断值（用于 GB 55024 强条 ERROR 判定）
        if lg.electrical:
            inst.electrical = {**getattr(inst, "electrical", {}), **dict(lg.electrical)}
        ports = []
        for t in lg.ports:
            sig = Signal(t.signal)
            base = t.label or sig.value
            n = max(1, int(t.count or 1))
            for i in range(n):
                plabel = base + (str(i + 1) if n > 1 else "")
                ports.append(ConcretePort(
                    id=f"{inst.uid}:{base}_{i+1}",
                    uid=inst.uid, side=t.side, signal=sig, label=plabel,
                    index=i, role=t.role, air=t.air,
                ))
        inst.ports = ports
        try:
            setattr(inst, "legend_source", lg.source or source)
            setattr(inst, "legend_revision", lg.revision)
            setattr(inst, "legend_updated_at", lg.updated_at)
        except Exception:
            pass
        return inst


def migrate_legacy_library() -> bool:
    """一次性迁移：旧 legend_cache.json → 永久图例库 legend_library.json。

    只在默认库文件尚不存在时执行；旧文件自此不再被写入。
    """
    try:
        if DEFAULT_CACHE.exists() or not LEGACY_CACHE.exists():
            return False
        data = json.loads(LEGACY_CACHE.read_text(encoding="utf-8"))
        st = LegendStore.__new__(LegendStore)      # 绕过 __init__，避免递归
        st.path = _resolve_legend_path()           # 落到可写位置（打包只读时重定向）
        st._mem = {}
        for rec in data.get("legends", []):
            try:
                lg = Legend.from_dict(rec)
                lg.source = lg.source or "migrated"
                if not lg.updated_at:
                    lg.updated_at = _now()
                lg.created_at = lg.created_at or lg.updated_at
                k = st.key(lg.brand, lg.model, lg.category)
                prev = st._mem.get(k)
                if prev is None or int(lg.revision or 0) >= int(prev.revision or 0):
                    st._mem[k] = lg
            except Exception:
                continue
        if st._mem:
            st.save()
        return True
    except Exception:
        return False


# 导入时执行一次迁移（此后图例库就是唯一的永久文档）
migrate_legacy_library()


def apply_legend(inst, store: LegendStore) -> object:
    """便捷函数：用图例库自动回填单个实例（命中才改，未命中保留引擎推断值）。"""
    return store.apply(inst)
