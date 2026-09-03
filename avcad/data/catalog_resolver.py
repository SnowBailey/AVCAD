"""易科国际产品资料清单 -> AVCAD 型号库解析器。

由 scripts/build_catalog.py 生成的 eko_catalog.json 加载，
提供按 (品牌, 型号) 或 物料编码 的匹配（精确 / 紧凑 / 子串回退），
把公司模板库映射为 AVCAD 可消费的 {category, features, params, name, code, ...}。

仅音频类别(V1)参与出图；电源/灯光/视频/中控/通讯 标记为后置。

匹配策略（按优先级）：
  1) 精确：(brand.upper, model.upper)
  2) 紧凑：去除空格/连字符/斜杠/下划线后比较（应对 BOM 里 "Ottocanali4K4" / "OTTOCANALI-4K4"）
  3) 子串：同品牌下，查询串包含或被包含于型号（应对 "ULXD4D-Q" 这类带后缀写法）
"""
from __future__ import annotations
import glob
import json
import os
import re
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(_HERE, "eko_catalog.json")

# 类别 -> 设备模板 yaml（与 model/specs.py 加载路径一致）
CAT_SPEC = {
    "SOURCE": "source", "WIRELESS_MIC": "wireless_mic", "ANTENNA": "antenna",
    "ANT_DIST": "ant_dist", "ANT_COMBINE": "ant_combine", "WIRELESS_RX": "wireless_rx", "MIXER": "mixer",
    "PROCESSOR": "processor", "SPEAKER_MGR": "speaker_mgr", "AMP": "amp",
    "SPEAKER": "speaker", "SWITCH": "switch", "IO": "io",
    "MIC_HOST": "mic_host",
}

# 不画出图的品牌（阳哥指定）：Green-GO=无线内通基本不用；Community/Apart=不画。
# 这些品牌的产品仍可被识别类别（用于统计/检索），但出图流程跳过。
DRAW_EXCLUDE_BRANDS = {"GREEN-GO", "COMMUNITY", "APART"}


def _norm(s):
    return (s or "").strip().upper()


def _tight(s):
    """紧凑归一：去掉空白/连字符/斜杠/反斜杠/下划线，保留字母数字与 '.' ':'。"""
    return re.sub(r"[\s\-_/\\]", "", str(s or "")).upper()


def _latest_valid_backup(path):
    """主库损坏时的兜底：在 ``path`` 同目录找最近一份**合法**的 ``.bak.*`` 备份。

    - 只读介质（打包版随包内置基线 / 无写权限）无法回写，直接返回 ``None``。
    - 备份本身也须是合法 JSON，跳过损坏的备份，避免「用坏备份覆盖坏主库」。
    返回可回退的备份路径；找不到则返回 ``None``。
    """
    if not os.access(path, os.W_OK):
        return None
    cands = sorted(glob.glob(str(path) + ".bak.*"),
                   key=os.path.getmtime, reverse=True)
    for bak in cands:
        try:
            with open(bak, encoding="utf-8") as f:
                json.load(f)
        except (ValueError, OSError):
            # 备份本身损坏或读不了 → 试更早的
            continue
        return bak
    return None


def safe_load_json(path):
    """加载主库 JSON，带「损坏自动回退 `.bak`」保险。

    ★ 2026-09-03 加：此前 ``eko_catalog.json`` 一旦被写坏（字符串内裸换行 /
      缺闭合引号等），``json.load`` 直接抛 ``JSONDecodeError``，UI 启动即 500、
      跑测试全红，且坏文件没有 .bak 时无处回退。现在：

        1) 先正常 ``json.load``；
        2) 抛 ``JSONDecodeError`` / ``UnicodeDecodeError`` → 透明回退到最近
           合法 ``.bak`` 并覆盖回主文件，再成功加载；
        3) 连备份都坏 / 无备份 → 才把异常抛上去（真正不可恢复）。

    回退会打印一行告警，方便在日志里一眼看到「主库曾被自动修复」。
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        restored = _latest_valid_backup(path)
        if restored:
            print(f"[catalog] 主库损坏，已自动从备份恢复: {os.path.basename(restored)}")
            # 用备份字节直接覆盖回主文件（比 shutil.copy2 更直接，
            # 也更稳：不依赖 copy2 的元数据操作）
            with open(restored, "rb") as f:
                data = f.read()
            with open(path, "wb") as f:
                f.write(data)
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        raise


class Catalog:
    def __init__(self, path: str = DEFAULT_JSON):
        self.data = safe_load_json(path)
        self.products = self.data.get("products", [])
        self.by_bm = {}
        self.by_code = {}
        self.by_bm_tight = {}
        self.by_code_tight = {}
        self.by_brand_tight = {}  # 品牌紧凑 -> [产品...]（子串回退用）
        for p in self.products:
            bm = (_norm(p.get("brand")), _norm(p.get("model")))
            if bm not in self.by_bm:
                self.by_bm[bm] = p
            tbm = (_tight(p.get("brand")), _tight(p.get("model")))
            if tbm not in self.by_bm_tight:
                self.by_bm_tight[tbm] = p
            code = _norm(p.get("code"))
            if code and code not in self.by_code:
                self.by_code[code] = p
            tcode = _tight(p.get("code"))
            if tcode and tcode not in self.by_code_tight:
                self.by_code_tight[tcode] = p
            tb = _tight(p.get("brand"))
            self.by_brand_tight.setdefault(tb, []).append(p)

    def get(self, brand=None, model=None, code=None):
        if code:
            p = self.by_code.get(_norm(code)) or self.by_code_tight.get(_tight(code))
            if p:
                return p
        bm = (_norm(brand), _norm(model))
        p = self.by_bm.get(bm)
        if p:
            return p
        # 紧凑归一匹配（BOM 写法差异）
        tbm = (_tight(brand), _tight(model))
        p = self.by_bm_tight.get(tbm)
        if p:
            return p
        # 同品牌子串回退（应对带后缀型号）
        if brand and model:
            tb = _tight(brand)
            tm = _tight(model)
            cand = self.by_brand_tight.get(tb, [])
            best = None
            for cp in cand:
                cm = _tight(cp.get("model"))
                if tm and len(tm) >= 3 and (tm in cm or cm in tm):
                    if best is None or len(cm) < len(_tight(best.get("model"))):
                        best = cp
            if best:
                return best
        return None

    def resolve(self, brand=None, model=None, code=None):
        """返回 AVCAD 可消费的解析结果，或 None（完全未命中）。"""
        p = self.get(brand, model, code)
        if not p:
            return None
        cat = p.get("category")
        spec = CAT_SPEC.get(cat) if cat else None
        bn = (p.get("brand") or "").upper()
        drawable = bool(spec) and bn not in DRAW_EXCLUDE_BRANDS
        out = {
            "matched": True,
            "code": p.get("code"),
            "name": p.get("name"),
            "category": cat,
            "features": p.get("features", []),
            "params": p.get("params", {}),
            "active": p.get("active"),
            "defer_reason": p.get("defer_reason"),
            "drawable": drawable,            # 是否有专属设备模板且品牌允许出图
            # 人工校正标记：True 时即使名称能兜底出类别也不出图
            # （停产型号 / 视频网传 / 线缆等，如「天线延长线」名称含「天线」）
            "no_draw": bool(p.get("no_draw")),
            "draw_excluded_brand": bn in DRAW_EXCLUDE_BRANDS,
            "template": spec,                # 模板名（无则 None）
            "country": p.get("country"),
            "brand": p.get("brand"),
            "model": p.get("model"),
            "price": p.get("price"),
        }
        return out


# 模块级单例
_default = None


def load(path: str = DEFAULT_JSON) -> Catalog:
    global _default
    _default = Catalog(path)
    return _default


def resolve(brand=None, model=None, code=None):
    global _default
    if _default is None:
        _default = Catalog()
    return _default.resolve(brand, model, code)


if __name__ == "__main__":
    c = Catalog()
    print("产品总数:", len(c.products))
    for q in [("Shure", "ULXD4D"), ("Powersoft", "Ottocanali 4K4"),
              ("Powersoft", "OTTOCANALI-4K4"), ("YAMAHA", "TF5"), ("Community", "R.25 94Z")]:
        r = c.resolve(*q)
        print(q, "->", (r or {}).get("category"), (r or {}).get("drawable"), (r or {}).get("features"))
