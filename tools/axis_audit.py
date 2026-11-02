"""v4 #57 审计：PMX 的「轴固定化 / 局部轴」到底有没有数据，能不能当 roll 的第二只眼睛。

为什么先审计再实现：biped_taper.ms 那一轮烧在「以为 `dumpInterface` 存在」上，
skinOps.forceUpdate 那一轮烧在「以为调用有效」上。这次不猜 —— PMX 规范说骨可以带
作者手填的轴向，但**这个 flag 在真实模型里出现的频率没人验过**。如果香菱这类模型
一个都没声明，#57 就是死路，趁早改道；如果普遍声明，那它就是 v4 规则 I 要的
伪-真值表：轴向由作者填，与我们从 pivot→tail 反推的朝向**没有任何共同计算**，
两者不一致就能变红（#26/#40 那种自证清白判据做不到的事）。

用法：
    python tools/axis_audit.py [a.pmx b.pmx ...]      # 不给参数则用本地已知的模型
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mmd2max.coords import max_delta          # noqa: E402
from mmd2max.pmx import load_pmx              # noqa: E402
from mmd2max.semantic import pmx_to_biped     # noqa: E402

DEFAULT_MODELS = [
    Path("C:/Users/Administrator/Desktop/香菱/香菱.pmx"),
]

# 轴固定化 / ローカル軸信息 的 flag 位（felixjones PMX 2.0 规范）
FLAG_FIXED_AXIS = 0x0400
FLAG_LOCAL_AXIS = 0x0800


def _norm(value):
    length = math.sqrt(sum(component * component for component in value))
    if length <= 1e-12:
        return None
    return tuple(component / length for component in value)


def _angle_deg(a, b) -> float:
    """两单位向量的夹角（度）。用 asin(|cross|)+acos(dot) 而不是单 acos，
    避免小角度时被浮点精度压成 0 —— 判据要的恰恰是「差 3 度也算差」。"""
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    cross = _norm((a[1] * b[2] - a[2] * b[1],
                   a[2] * b[0] - a[0] * b[2],
                   a[0] * b[1] - a[1] * b[0]))
    if cross is None:
        return 0.0 if dot > 0 else 180.0
    return math.degrees(math.atan2(math.sqrt(max(0.0, 1.0 - dot * dot)), dot))


def _canonical_axis(value) -> str | None:
    """PMX 的轴固定化向量按规范落在骨自己的**局部坐标**里。作者若填 (1,0,0)/(0,1,0)/(0,0,1)
    这类标准基，就能直接读出「这根骨只绕自身的哪根轴转」；任意向量则返回 None。
    返回值是局部系的标记，不能拿 max_delta 当世界方向去比 —— 那正是会骗人的地方。"""
    if value is None:
        return None
    unit = _norm(value)
    if unit is None:
        return "zero"
    for i, axis in enumerate("XYZ"):
        for sign in (1.0, -1.0):
            target = [0.0, 0.0, 0.0]
            target[i] = sign
            if all(abs(c - t) < 1e-3 for c, t in zip(unit, target)):
                return f"{'-' if sign < 0 else ''}{axis}"
    return None


def derived_length_axis(model, index) -> tuple | None:
    """我们从几何反推的「骨的长度轴」：pivot → tail（tail 是骨就用位置，是顶点偏移就用偏移）。
    返回 Max 世界系单位向量；这**就是**现在 Biped 骨盒长度方向的唯一来源（单源风险所在）。"""
    bone = model.bones[index]
    if bone.tail_index >= 0:
        raw = tuple(b - p for b, p in
                    zip(model.bones[bone.tail_index].position, bone.position))
    elif bone.flags & 0x0001:
        return None
    else:
        raw = bone.tail_offset
    return _norm(max_delta(raw))


def audit_model(path: Path) -> dict:
    model = load_pmx(path)
    biped_map = pmx_to_biped()
    rows = []
    for index, bone in enumerate(model.bones):
        declared_x = _norm(max_delta(bone.local_x)) if bone.local_x else None
        declared_z = _norm(max_delta(bone.local_z)) if bone.local_z else None
        declared_y = _norm(max_delta(bone.local_y())) if bone.has_local_axis else None
        geometry = derived_length_axis(model, index)
        raw_fixed = bone.fixed_axis
        rows.append({
            "index": index,
            "name": bone.name,
            "biped": biped_map.get(bone.name),
            "fixed": raw_fixed is not None,
            "fixed_raw": raw_fixed,
            "canonical": _canonical_axis(raw_fixed),
            "geom": geometry,
            "local": declared_x is not None,
            "x_vs_geom": (_angle_deg(declared_x, geometry)
                          if declared_x and geometry else None),
            "x_vs_y": (_angle_deg(declared_x, declared_y)
                       if declared_x and declared_y else None),
            "flip": (declared_x is not None and declared_z is not None and
                     sum(x * z for x, z in zip(declared_x, declared_z)) > 1e-4),
        })
    deviations = [row["x_vs_geom"] for row in rows if row["x_vs_geom"] is not None]
    # 轴固定化向量的**独立**对照：不猜它属于哪根骨，直接和全场所有骨自己反推的长度轴比，
    # 取最小夹角。若「作者声明的转轴」≈「我们从 pivot 算出的某根骨的方向」，两个来源
    # 没有任何共同计算，对上就是真对上（v4 规则 I 要的伪-真值表）。
    for row in rows:
        row["best"] = None
        if not row["fixed"]:
            continue
        # 这些模型的轴固定化向量在绑骨姿态下数值上就是世界方向（局部系在静止姿态与
        # 世界系重合），所以按方向变换后再比。
        axis = _norm(max_delta(row["fixed_raw"]))
        if axis is None:
            continue
        best = None
        for other in rows:
            if other["geom"] is None:
                continue
            # 转轴是「线」不是「射线」：作者填的方向与我们的骨向反号时仍同一根轴，
            # 所以取 min(夹角, 180-夹角)，否则会量出假的 180 度冲突。
            angle = _angle_deg(axis, other["geom"])
            angle = min(angle, 180.0 - angle)
            if best is None or angle < best[1]:
                best = (other["name"], angle)
        row["best"] = best
    return {
        "path": path,
        "bone_count": len(model.bones),
        "fixed_count": sum(1 for row in rows if row["fixed"]),
        "local_count": sum(1 for row in rows if row["local"]),
        "local_biped_count": sum(1 for row in rows if row["local"] and row["biped"]),
        "non_orthogonal": sum(1 for row in rows if row["x_vs_y"] is not None
                              and abs(row["x_vs_y"] - 90.0) > 1.0),
        "max_x_vs_geom": max(deviations) if deviations else None,
        "rows": rows,
    }


def report(result: dict) -> None:
    print(f"\n===== {result['path'].name} =====")
    total = result["bone_count"]
    print(f"骨数 {total} / 轴固定化 {result['fixed_count']} / "
          f"局部轴 {result['local_count']}（其中 Biped 映射骨 {result['local_biped_count']}）")
    print(f"X-Z 不垂直的骨 {result['non_orthogonal']} 条")
    for row in result["rows"]:
        if not row["fixed"]:
            continue
        raw = ", ".join(f"{component:.3f}" for component in row["fixed_raw"])
        best = row["best"]
        match = (f"≈骨「{best[0]}」方向，差 {best[1]:.2f} 度" if best else "无对照")
        print(f"   轴固定 {row['name']:<14} {(row['biped'] or '-'):<16} "
              f"({raw}) 标准基={row['canonical'] or '非轴对齐'}  {match}")
    if not result["local_count"]:
        print("⇒ 无局部轴数据：#57 在这台模型上没有真值可用（不实现，只记账）。")
        return
    print(f"局部 X 轴 vs 几何长度轴 最大偏差 = {_fmt(result['max_x_vs_geom'])} 度")
    print("  骨名 / Biped 目标 / X-几何偏差 / X-Y 垂直度（度）")
    shown = 0
    for row in result["rows"]:
        if not row["local"]:
            continue
        print(f"   {row['name']:<18} {(row['biped'] or '-'):<18} "
              f"{_fmt(row['x_vs_geom']):>8} {_fmt(row['x_vs_y']):>8}")
        shown += 1
        if shown >= 40:
            print(f"   …（余下 {result['local_count'] - shown} 条略）")
            break


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv[1:]] or [
        path for path in DEFAULT_MODELS if path.is_file()]
    if not paths:
        print("找不到默认 PMX，请把模型路径作为参数传进来。")
        return 1
    for path in paths:
        if not path.is_file():
            print(f"[跳过] 文件不存在：{path}")
            continue
        report(audit_model(path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
