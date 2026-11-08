"""PMX -> OBJ + 骨骼/权重数据，供 3ds Max 侧做轻量装配。

背景：3ds Max 2022 的 FBX 导入器处理 555 骨骼 + 12 万面时会卡死（CPU 满载、内存不涨，
疑似导入器内部死循环）。因此把重活全部放在 Python 侧完成，Max 只负责：
    导入 OBJ（原生、快速） -> 按数据建骨骼 -> 挂 Skin 权重 -> 另存 .max

输出文件：
    <name>.obj / <name>.mtl   网格、UV、法线、材质分组
    <name>_bones.tsv          骨骼：序号 名称 父序号 头坐标 尾坐标 [skin标志]
    <name>_weights.tsv        权重：顶点序号 <tab> 骨骼:权重 <tab> ...
    <name>_tex/               贴图副本（保留原始子目录，避免同名冲突）

坐标约定：全部在 `mmd2max/coords.py`（唯一事实来源）。要点：MMD 是左手系、Max 是
右手系，映射 (x,y,z)->(x,z,y) 含一次镜像（det=-1），因此 OBJ 的三角绕序翻一次；
写进 OBJ 的是 Y-up 的 (x, y, -z)，Max 导入器转完正好落在同一结果上，Max 端不再
对网格做任何补偿旋转。

分层（v3）：
    build_plan(source, scale, **参数) -> Plan   纯计算，不碰磁盘
    export(source, outdir, plan)                只负责把 Plan 落盘
    这样 GUI 可以在用户调参时反复 build_plan 做即时预览，而不会留下半个文件；
    预览看到的行/数字就是点「导出」后 TSV 里的内容（tests/test_plan.py 钉住这条）。

骨架模式（build_plan 的参数）：
    useful    默认（filter_useless=True）：剔除 control（IK/哑骨/调试）与 morph 骨
    full      保留全部骨骼
    humanoid  只保留标准人形骨骼，非人形骨权重归并到最近保留祖先
    （humanoid 模式下若某顶点无法映射，则按空间最近的标准骨兜底，绝不输出空权重）
    keep_guides=False（= CLI 的 --drop-guides）：在以上任一模式里再丢掉
    「既不是 Biped 目标、又没蒙皮、又没挂刚体、也不是 IK」的纯辅助骨
    （见 semantic.guide_bone_indices）。默认 True，保持历轮基线不变。

用法: python -m mmd2max.export_obj <input.pmx> <outdir> [scale] [min_weight]
      [humanoid|full] [accessory_parent] [--drop-guides]
"""
from __future__ import annotations

import math
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from mmd2max.pmx import PMXError, load_pmx
from mmd2max import semantic
from mmd2max.coords import (convert_vec, max_delta as coords_max_delta, obj_delta as coords_obj_delta,
                            obj_vec, rigid_rotation_max)


def fmt(value: float) -> str:
    # 舍入到零就统一写 "0.000000"：IEEE 的 -0.0 与 −1e-9 都会印成 "-0.000000"，
    # 同一个零两种文本 —— 镜像把 +0 翻成 −0 时，逐字节对撞会白红一次。
    text = f"{value:.6f}"
    return text[1:] if text == "-0.000000" else text


# 脚尖改绑（#35）。源 PMX 常把整只鞋（含脚尖）绑在足首上、給 つま先 留 0 个顶点
# （香菱实测：左/右つま先 权重合计=0.0、顶点数=0），于是 Max 里 Toe0 扳不动脚尖。
# 这里按「足首→つま先 的**水平**轴、过 つま先 pivot 的**竖直**平面」把足首权重的
# 一部分划给 Toe0：竖直平面才把脚尖整个厚度（鞋底到鞋面）一起划走，斜轴平面只会
# 改绑贴地那一层（#140）。回滚 = 把 TOE_SPLIT 改成 False，一行，改完与源文件权重
# 逐字节相同；只回滚平面口径 = TOE_SPLIT_VERTICAL_PLANE 改 False。
TOE_SPLIT = True
# 过渡带半宽 = 「足首→つま先」骨长的这个比例（分界 0.5/0.5 正好落在 つま先 pivot 平面上，
# 向前 band 全给 Toe0、向后 band 全留给足首）。0 会除零，所以关掉走上面的开关、不走这里。
# 为什么用比例而不是厘米：heads 是「已乘 scale 的导出坐标」，GUI 里改 scale 厘米数就废了
# （第一版写死 2.0cm，实测被当成 2.0 导出单位 = 200cm ⇒ 整只脚全落进过渡带）。
# 香菱足长 0.1201 导出单位 ⇒ band = 0.024 ≈ 2.4cm，正好是「鞋头那一截」的量级。
TOE_SPLIT_BAND = 0.2
# 可信门：足轴短于小腿（膝→踝）的这个比例就不认为它指得出脚尖方向，直接跳过。同样是比例。
TOE_SPLIT_MIN_RATIO = 0.05
# 分界平面必须是**竖直**的（#140）。旧代码拿「踝→趾」的三维斜轴当平面法线，林尼
# #139 把关节挪到鞋长 58.6% 后这根轴的单位向量变成 (-0.004, -0.579, -0.815) ——
# 竖直分量比水平分量还大。于是鞋面越高，(p - pivot)·法线 里的 -0.815×Δz 抵消得越狠：
# 实测脚掌平面前方 1964 个顶点里，z≥3cm 的 1006 个「脚尖上半个体积」一个都没划给
# Toe0，只有 z≤2.62cm 那 157 个贴地鞋底顶点归它。扳 Toe0 时只有薄薄一层鞋底跟着动，
# 用户原话「脚尖的体积结构并没有体现出来，就像只有脚底那一层鞋底有结构」。
# 改成「过趾关节、法线只取水平分量」的竖直平面，脚尖整个厚度一起走。
# 设 False 即逐字回滚到旧的斜轴平面。
TOE_SPLIT_VERTICAL_PLANE = True

# 脚掌前/后分界（#139）。PMX 的 `つま先` 骨位置**不是**跨模型常量：香菱放在脚长
# 约 40% 处（正好是 Biped 的 ball 关节），林尼却几乎贴着鞋尖（实测脚长 88.8% 处）。
# 旧代码把 つま先 head 直接当 Toe0 pivot，于是林尼的 Foot 一根骨吃掉整只鞋、
# Toe0 只剩鞋尖 2.23cm（用户红框：分界 83.3%，要的是蓝框 58.6%）。
# 现在改为从**网格**量鞋跟→鞋尖沿脚轴的跨度，把 Toe0 关节放在这个跨度的固定比例处；
# 这一个点同时是 limbs 的 child 坐标 / desired_len / toe_len 与权重分界平面的真值源。
# 比例来自用户 #139 蓝框实测（58.6%），与模型无关；设 None 即回滚到「照抄 PMX つま先」。
TOE_JOINT_FRAC = 0.586
# 鞋的轴向窗口：贴地带 + 横向窄带，避免把小腿/裙摆当成鞋跟。
TOE_JOINT_Z_LO = -3.0
TOE_JOINT_Z_HI = 4.0
TOE_JOINT_X_HALF = 6.0
TOE_JOINT_T_PAD = 6.0   # 相对「踝→つま先」水平距离再允许多少厘米给鞋跟外扩
# 鞋面窗口上沿（#140）。`_foot_profile` 旧代码写死 `z_cap = 脚掌z + 4.0`，那是照着
# 「趾关节贴在鞋尖、那一截只剩鞋底」调的。#139 把关节挪回鞋长 58.6% 之后，林尼
# 在这一截的鞋面高到 7.0cm，窗口却仍把 z>4.08 的顶点全丢掉 ⇒ toe_h 恒等于 4.095，
# Toe0 骨盒侧视 8.16×4.10 ≈ 2:1 的扁片，正是用户截图里那块「只有鞋底有结构」的框。
# 改成按踝高压顶：鞋面最高不会超过踝，取 脚掌z + 比例×(踝z - 脚掌z)；同时保留
# 旧的 +4cm 下限，赤足/薄鞋模型逐字不变。设 0 即回滚到写死 +4cm。
TOE_CAP_ANKLE_FRAC = 0.8


def _structural_toe_ball(positions, scale, ankle, ball):
    """从网格量鞋跟/鞋尖，返回「结构正确」的 (Toe0 关节位置, 新趾长)。

    沿 踝→つま先 的水平单位轴 (ux,uy) 投影所有鞋部顶点，取投影极值 lo(鞋跟)/hi(鞋尖)，
    关节 = lo + TOE_JOINT_FRAC*(hi-lo)；新趾长 = hi - 关节投影，即「前部」那一截的长度。
    z 保持 つま先 的贴地高度（脚掌关节不升高），所以只动水平位置，不破坏贴地/落地判据。
    量不出鞋（窗口空 / 跨度太短 / 轴退化）返回 None，调用方必须退回 PMX 原位 —— 宁可
    保持旧行为，也不要按猜出来的点摆关节。

    为什么必须同时返回趾长：`limbs.tsv` 的 child_x/y/z（Toe0 pivot）、desired_len
    （Foot→Toe0 段长）、toe_len（Toe0 骨盒长）三者和权重分界平面是**同一个点**的四种
    读法。只改其中一两个 = 骨盒戳出鞋尖或 Foot/Toe0 之间裂开缝（#37/#40 的老坑）。
    """
    if TOE_JOINT_FRAC is None or not positions:
        return None
    ax, ay, _az = ankle
    bx, by, bz = ball
    dx, dy = bx - ax, by - ay
    horiz = (dx * dx + dy * dy) ** 0.5
    if horiz < 1e-5:
        return None
    ux, uy = dx / horiz, dy / horiz
    lo = hi = None
    for p in positions:
        x, y, z = convert_vec(p, scale)
        if not (bz + TOE_JOINT_Z_LO <= z <= bz + TOE_JOINT_Z_HI):
            continue
        if abs(x - bx) > TOE_JOINT_X_HALF:
            continue
        t = (x - bx) * ux + (y - by) * uy
        if t < -(horiz + TOE_JOINT_T_PAD) or t > horiz + TOE_JOINT_T_PAD:
            continue
        if lo is None:
            lo = hi = t
        else:
            lo = min(lo, t)
            hi = max(hi, t)
    if lo is None or (hi - lo) < 1.0:
        return None
    tj = lo + TOE_JOINT_FRAC * (hi - lo)
    toe_len = hi - tj
    # 分界点必须真的落在 踝→つま先 之间且离鞋尖还有一截，否则这个模型不是「正常脚掌」。
    if toe_len < 0.5 or tj > 0.0:
        return None
    return ((bx + ux * tj, by + uy * tj, bz), toe_len)


def _relocate_toe_joints(heads, tails, biped_mapping, old_to_new,
                         positions, scale):
    """把 Toe0 骨从 PMX 的 `つま先` 位置挪到「鞋长 TOE_JOINT_FRAC」的结构分界点。

    为什么改 heads 而不是改 limbs.tsv 的 child 列：`bones.tsv`（Max 摆位）、
    `limbs.tsv` 的 child_x/y/z 与 desired_len、`_foot_profile` 的 ball 平面、
    `_split_toe_weights` 的权重分界平面、判据侧的全部期望值，都读**同一份 heads**。
    改一处，骨架与权重一起走，且判据不会和产物分叉；只在表里改 child 列则
    bones.tsv 的 head 仍停在鞋尖，摆位和权重分界立刻分成两套真值（#37/#40 的坑）。

    tail 按同一个 delta 平移，保持 PMX 的竖直 stub 方向不变 —— 结构向判据量的是
    tail-head，单独挪 head 会把这根骨读成斜的。
    """
    notes: list[str] = []
    if TOE_JOINT_FRAC is None or not positions or not biped_mapping:
        return notes
    target_to_new: dict = {}
    for old, target in biped_mapping.items():
        if target and old in old_to_new:
            target_to_new.setdefault(target, old_to_new[old])
    for side in ("L", "R"):
        toe = f"Bip001 {side} Toe0"
        foot = f"Bip001 {side} Foot"
        ti = target_to_new.get(toe)
        fi = target_to_new.get(foot)
        if ti is None or fi is None:
            continue
        if not (0 <= ti < len(heads) and 0 <= fi < len(heads)):
            continue
        ankle, ball = heads[fi], heads[ti]
        got = _structural_toe_ball(positions, scale, ankle, ball)
        if got is None:
            notes.append(f"  脚掌分界跳过 {toe}：网格量不出鞋跟/鞋尖，保持 PMX つま先 原位")
            continue
        new_ball, new_toe_len = got
        delta = tuple(new_ball[k] - ball[k] for k in range(3))
        heads[ti] = new_ball
        if 0 <= ti < len(tails):
            tails[ti] = tuple(tails[ti][k] + delta[k] for k in range(3))
        notes.append(
            f"  脚掌分界 {toe}：Toe0 沿脚轴向鞋跟侧挪 {-delta[1]:.2f}cm "
            f"到鞋长 {TOE_JOINT_FRAC:.1%} 处，前段趾长 {new_toe_len:.2f}cm"
            "（骨架与权重分界同一真值源）")
    return notes


def _split_toe_weights(deform, positions, scale, heads, parents,
                       biped_mapping, old_to_new, band: float = TOE_SPLIT_BAND):
    """把脚尖网格顶点的权重从足首（Foot）分给趾骨（Toe0），返回 (deform, 说明列表)。

    只认「Biped 目标以 Toe0 结尾、其父骨目标以 Foot 结尾，且两侧前缀逐字相同
    （`Bip001 L ` vs `Bip001 L `）」这一种配对，其余骨一根不碰。下面三个条件任一
    不满足（左右不配对 / 足轴短到量不出方向 / 源文件自己已给 Toe0 顶点）就整条跳过、
    并在 notes 里写明白为什么跳过。权重总量守恒：只从足首那一项里按比例挪，
    别的骨的一项都不动，所以每顶点合计仍等于 1.0，不会给 Skin 制造需要再归一的顶点。
    """
    if not TOE_SPLIT or band <= 0.0:
        return deform, []
    target_of: dict = {}
    for old, target in (biped_mapping or {}).items():
        if target and old in old_to_new:
            target_of[old_to_new[old]] = target
    notes = []
    for new_index, target in sorted(target_of.items()):
        if not target.endswith("Toe0"):
            continue
        foot_index = parents[new_index] if 0 <= new_index < len(parents) else -1
        foot_target = target_of.get(foot_index, "")
        if not foot_target.endswith("Foot"):
            continue
        if target[:-len("Toe0")] != foot_target[:-len("Foot")]:
            continue          # 左右不配对就宁可不改
        toe_head = heads[new_index]
        axis = [toe_head[i] - heads[foot_index][i] for i in range(3)]
        span = sum(v * v for v in axis) ** 0.5
        horiz = (axis[0] ** 2 + axis[1] ** 2) ** 0.5
        knee_index = parents[foot_index] if 0 <= foot_index < len(parents) else -1
        shin = 0.0
        if 0 <= knee_index < len(heads):
            shin = sum((heads[foot_index][i] - heads[knee_index][i]) ** 2
                       for i in range(3)) ** 0.5
        if span < TOE_SPLIT_MIN_RATIO * shin:
            ratio = span / shin if shin else 0.0
            notes.append(f"  脚尖改绑跳过 {target}：足轴只有小腿的 {ratio:.1%}，方向不可信")
            continue
        # 源文件自己就给了 Toe0 顶点 ⇒ 作者已经绑好，不插手（换模型验收要的是这条）。
        pre_toe = pre_foot = 0
        for weights in deform:
            for b, w in weights:
                if w <= 0.0:
                    continue
                if b == new_index:
                    pre_toe += 1
                elif b == foot_index:
                    pre_foot += 1
        if pre_toe * 20 >= pre_foot:
            notes.append(f"  脚尖改绑跳过 {target}：源文件已给它 {pre_toe} 个顶点"
                         f"（{foot_target} {pre_foot} 个），不改作者权重")
            continue
        # 竖直分界平面（#140）：法线丢掉 z 分量，鞋面上下的顶点在「往前多远」上
        # 一视同仁。斜轴平面会让高处的鞋面顶点被 -Δz 项抵消，只改绑贴地那层鞋底。
        # band 也跟着换成水平足长的比例 —— along 现在是水平距离，量纲要一致。
        if TOE_SPLIT_VERTICAL_PLANE:
            if horiz < 1e-5:
                notes.append(f"  脚尖改绑跳过 {target}：足轴水平投影退化，竖直分界平面立不起来")
                continue
            band_abs = band * horiz
            axis = [axis[0] / horiz, axis[1] / horiz, 0.0]
        else:
            band_abs = band * span
            axis = [v / span for v in axis]
        moved = 0
        partial = 0
        for vi, weights in enumerate(deform):
            foot_w = 0.0
            for b, w in weights:
                if b == foot_index:
                    foot_w = w
            if foot_w <= 0.0:
                continue
            point = convert_vec(positions[vi], scale)
            along = sum((point[i] - toe_head[i]) * axis[i] for i in range(3))
            share = 0.5 + along / (2.0 * band_abs)
            if share <= 0.0:
                continue
            if share > 1.0:
                share = 1.0
            kept_w = foot_w * (1.0 - share)
            toe_w = foot_w * share
            rebuilt = [(b, (kept_w if b == foot_index else w))
                       for b, w in weights if not (b == foot_index and kept_w <= 0.0)]
            for k, (b, _w) in enumerate(rebuilt):
                if b == new_index:
                    rebuilt[k] = (b, rebuilt[k][1] + toe_w)
                    break
            else:
                rebuilt.append((new_index, toe_w))
            deform[vi] = rebuilt
            moved += 1
            if 0.0 < share < 1.0:
                partial += 1
        if moved:
            notes.append(f"  脚尖改绑 {target}：从 {foot_target} 手里拿到 {moved} 个顶点"
                         f"（其中 {partial} 个在过渡带 = 足长 ±{band:.0%} 里混权重）")
    return deform, notes


# --------------------------------------------------------------------------
# 多进程 worker：必须是模块级函数，才能在 Windows 的 spawn 模式下被 pickle
# --------------------------------------------------------------------------

def _worker_vertices(payload):
    start, positions, scale, y_up = payload
    out = []
    for value in positions:
        # y_up=True：写 OBJ 惯用的 Y-up 值 (x, y, -z)，Max 导入器会转成 convert_vec 的结果；
        # y_up=False：直接写 Z-up，等于 convert_vec（给不走 Y-up 导入器的场合留的口子）。
        triple = obj_vec(value, scale) if y_up else convert_vec(value, scale)
        out.append(f"v {fmt(triple[0])} {fmt(triple[1])} {fmt(triple[2])}")
    return start, out


def _worker_normals(payload):
    start, normals, scale, y_up = payload
    out = []
    for value in normals:
        length = (value[0] ** 2 + value[1] ** 2 + value[2] ** 2) ** 0.5 or 1.0
        unit = (value[0] / length, value[1] / length, value[2] / length)
        # 法线是方向：与顶点用同一线性映射（镜像对手性同样生效）
        triple = coords_obj_delta(unit) if y_up else coords_max_delta(unit)
        out.append(f"vn {fmt(triple[0])} {fmt(triple[1])} {fmt(triple[2])}")
    return start, out


def _worker_uvs(payload):
    """写 OBJ 的 vt。**V 必须翻转**：PMX 的 UV 原点在左上（DirectX 习惯，v 向下），
    OBJ 的 vt 原点是左下（v 向上）。不翻的话贴图会上下颠倒、
    在角色身上就是「贴错了」（用户反馈）。

    实测依据（本模型）：面片额头处 v=0.107、下巴处 v=0.779 ——
    越往下 v 越大，确认是左上原点，所以要写 1-v。
    """
    start, uvs = payload
    return start, [f"vt {fmt(u)} {fmt(1.0 - v)}" for u, v in uvs]


def _worker_faces(payload):
    start, faces = payload
    out = []
    for a, b, c in faces:
        # 绕序**必须**翻：MMD→Max 的映射含一次镜像（coords.determinant() == -1），
        # 不翻就正面变背面。v2 用的是 det=+1 的 (-x, z, y)，所以那时不用翻 ——
        # 代价是整张图是镜像的（左右手反）。见 mmd2max/coords.py 的推导。
        out.append(f"f {c + 1}/{c + 1}/{c + 1} {b + 1}/{b + 1}/{b + 1} {a + 1}/{a + 1}/{a + 1}")
    return start, out


def _worker_weights(payload):
    start, deform = payload
    out = []
    for weights in deform:
        parts = []
        for bone, weight in weights:
            text = f"{weight:.6f}"
            # 「印出来是 0.000000」的权重不写成链路：Max 的 Skin 收到权重 0.0 会把这条
            # 链路整条丢掉（海灯节实测：332 条 PMX 权重 <5e-7 的链路写进去、回读不到骨号），
            # 于是 `skin_rb/links_missing` 为这些**本来就没有形变影响**的数变红。
            # 现在权重表 = 「Max 能持有的链路集合」，两边同基，缺一条就是真缺一条。
            if weight > 0.0 and text != "0.000000":
                parts.append(f"{bone}:{text}")
        out.append("\t".join(parts))
    return start, out


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield i, items[i:i + size]


def _drop_unreferenced(positions, normals, uvs, faces, deform):
    """删掉「不被任何面引用」的顶点并重编号，返回 (顶点四件套, faces, deform, remap, 丢弃数)。

    3ds Max 的 OBJ 导入器只按面建顶点，`v` 行里没被任何 `f` 引用的会被直接丢掉，
    于是 Max 网格的顶点序号 = 保留顶点的紧凑序号，而权重表若仍按 PMX 原序号写，
    整条权重就错位挂到别的顶点上（实测香菱：19212 行权重 vs 18892 顶点，差 320）。
    所以在导出侧先紧凑化，让两边的序号天然一致 —— 而不是到 Max 端再猜。
    """
    total = len(positions)
    used = sorted({i for face in faces for i in face if 0 <= i < total})
    if len(used) == total:
        # 一个都没丢：不复制数组，但 remap 仍是「恒等」，不是空表 ——
        # 下游（morph 行）拿 remap 查旧序号，空表会把每个顶点都当成已丢弃。
        return positions, normals, uvs, faces, deform, list(range(total)), 0
    remap = [-1] * total
    for new_index, old_index in enumerate(used):
        remap[old_index] = new_index
    keep = list(used)

    def compact(items):
        return [items[i] for i in keep]

    return (compact(positions), compact(normals), compact(uvs),
            [tuple(remap[i] for i in face) for face in faces],
            compact(deform), remap, total - len(used))


def _collect(pool, worker, payloads, total):
    results = [None] * total
    for start, lines in pool.map(worker, payloads):
        results[start:start + len(lines)] = lines
    return results


def build_bones(model, scale: float):
    heads, tails, parents = [], [], []
    count = len(model.bones)
    for index, bone in enumerate(model.bones):
        head = convert_vec(bone.position, scale)
        if 0 <= bone.tail_index < count:
            tail = convert_vec(model.bones[bone.tail_index].position, scale)
        else:
            offset = convert_vec(bone.tail_offset, scale)
            tail = (head[0] + offset[0], head[1] + offset[1], head[2] + offset[2])

        def far_enough(a, b):
            return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) \
                >= _DEGENERATE_EPS_CM ** 2

        if not far_enough(head, tail):
            for child_index in range(count):
                if model.bones[child_index].parent_index == index:
                    tail = convert_vec(model.bones[child_index].position, scale)
                    break
        if not far_enough(head, tail):
            tail = (head[0], head[1], head[2] + _STUB_LEN_CM)

        heads.append(head)
        tails.append(tail)
        parents.append(bone.parent_index)
    return heads, tails, parents


def _nearest_kept(model, old_to_new, kept_set, index):
    """沿父链向上找最近的保留骨骼，返回其新序号（找不到返回 -1）。"""
    seen = set()
    current = index
    while current is not None and current >= 0 and current not in seen:
        if current in kept_set:
            return old_to_new[current]
        seen.add(current)
        current = model.bones[current].parent_index
    return -1


def _build_skeleton_from_kept(model, scale: float, kept: list,
                              biped_mapping: dict | None = None):
    """从「保留的旧序号列表」构建导出骨架，返回
    (保留旧序号列表, 旧->新映射, 新父级列表, 头列表, 尾列表)。

    尾方向优先用 **Biped 链的下一节**（biped_mapping 提供），这一点非常关键：
    PMX 的骨骼链里常插入扭转骨（右腕 -> 右腕捩 -> 右ひじ），若按「第一个保留后代」
    取 tail，UpperArm 的尾会落到「腕捩」（12.37）而不是肘（20.61），
    Max 端按 tail 当段长就会把上臂缩到只有真实长度的 60%，肘跑到模型外。
    没有 Biped 映射的骨仍按「最近的保留后代」取尾。
    """
    if not kept:
        raise ValueError("没有可保留的骨骼，无法构建骨架")
    old_to_new = {old: new for new, old in enumerate(kept)}
    kept_set = set(kept)
    heads_full, tails_full, _ = build_bones(model, scale)

    # Biped 链：目标节点名 -> 该节点的子节点名；以及 Biped 目标名 -> 原始骨序号
    chain_child = semantic.biped_chain_child_map()
    target_to_old: dict = {}
    if biped_mapping:
        for old, target in biped_mapping.items():
            if target:
                target_to_old.setdefault(target, old)

    heads, tails, parents = [], [], []
    for old in kept:
        head = heads_full[old]
        child = None
        # 1) 优先：Biped 链的下一节（跳过扭转骨，得到真实肢段）
        if biped_mapping:
            target = biped_mapping.get(old)
            if target:
                next_target = chain_child.get(target)
                if next_target:
                    child = target_to_old.get(next_target)
        # 2) 兜底：最近的保留后代（保持骨骼链方向正确）
        if child is None:
            for cand in kept:
                if cand == old:
                    continue
                node = cand
                seen = set()
                while node is not None and node >= 0 and node not in seen:
                    parent = model.bones[node].parent_index
                    if parent == old:
                        child = cand
                        break
                    seen.add(node)
                    node = parent
                if child is not None:
                    break
        if child is not None:
            tail = heads_full[child]
        else:
            tail = tails_full[old]
        # 仅在首尾重合（零长度）时做最小修正，避免把所有中线骨强制拉成竖直
        if (tail[0] - head[0]) ** 2 + (tail[1] - head[1]) ** 2 + (tail[2] - head[2]) ** 2 \
                < _DEGENERATE_EPS_CM ** 2:
            tail = (head[0], head[1], head[2] + _STUB_LEN_CM / 2.0)
        heads.append(head)
        tails.append(tail)
        ancestor = _nearest_kept(model, old_to_new, kept_set,
                                 model.bones[old].parent_index)
        parents.append(ancestor if ancestor != -1 else -1)

    return kept, old_to_new, parents, heads, tails


def _humanoid_skeleton(model, scale: float, biped_mapping: dict | None = None,
                       dropped: set | None = None):
    """只保留标准人形骨骼，返回 (保留的旧序号列表, 旧->新映射, 新父级列表, 头列表, 尾列表)。

    使用 semantic.humanoid_kept_indices 做匹配（名称 / 英文名 / 别名 / 左右变体），
    required 骨骼缺失或同一语义 ID 被多根骨骼命中时直接报错。control/morph 骨已被
    humanoid_kept_indices 排除（见 semantic）。
    """
    kept, old_to_new, missing, conflicts = semantic.humanoid_kept_indices(model)
    if not kept:
        raise ValueError("未找到任何标准人形骨骼，无法使用 humanoid_only")
    if missing:
        raise PMXError("humanoid_only 缺失必需人形骨骼：" + "、".join(missing))
    if conflicts:
        raise PMXError("humanoid_only 存在语义冲突（多个骨骼映射到同一语义 ID）："
                      + "、".join(conflicts))
    if dropped:
        kept = [i for i in kept if i not in dropped]
    return _build_skeleton_from_kept(model, scale, kept, biped_mapping)


def _useful_skeleton(model, scale: float, roles: dict,
                     biped_mapping: dict | None = None,
                     dropped: set | None = None):
    """保留 role=='skeleton' 的骨骼（人形骨 + D 骨 + 扭转骨 + 头发/裙摆附属骨），
    丢弃 control（IK / 物理哑骨 / 调试辅助）与 morph（表情骨）骨骼。

    被丢弃骨的权重在调用方经由 _remap_humanoid 归并到最近的保留祖先。
    返回 (保留旧序号列表, 旧->新映射, 新父级列表, 头列表, 尾列表)。
    """
    kept = [i for i, _b in enumerate(model.bones)
            if roles.get(i) == "skeleton" and (not dropped or i not in dropped)]
    return _build_skeleton_from_kept(model, scale, kept, biped_mapping)


def _redirect_bone_pairs(deform, redirect: dict):
    """按 `{旧骨序号: 新骨序号}` 改写权重里的骨序号，并把落到同一根骨的分量相加。

    用在「并行变形骨（D 骨）改绑」上：改的是**原始 PMX 骨序号空间**，所以必须排在
    `_remap_humanoid` 之前；否则 D 链一删，权重会沿父链归并到躯干骨上，再也追不回
    它本来该落在哪根腿骨。
    """
    if not redirect:
        return deform
    out = []
    for weights in deform:
        merged: dict = {}
        for bone_index, weight in weights:
            target = redirect.get(bone_index, bone_index)
            merged[target] = merged.get(target, 0.0) + weight
        out.append(list(merged.items()))
    return out


def _remap_humanoid(deform, positions, model, kept_set, old_to_new, new_heads,
                    scale: float):
    """把非人形骨的权重归并到最近保留祖先；无法映射时按空间最近标准骨兜底。

    绝不产生空权重顶点（审查项 P0#2）。

    注意 scale：new_heads 是「已乘 scale 的导出坐标」，兜底时顶点位置也必须乘同一个
    scale 才能比较。早期版本传 1.0，导致比较的两边差 12.5 倍（scale=0.08 时），
    空间最近骨会选错——默认走不到这里，属于潜伏 bug。
    """
    remapped = []
    for vi, weights in enumerate(deform):
        merged: dict = {}
        for b, w in weights:
            # 注意：_nearest_kept 返回的已经是「新序号」，不能再经 old_to_new 二次映射
            # （早期版本在此双重映射，导致非人形骨权重触发 KeyError）。
            if b in old_to_new:
                new_index = old_to_new[b]
            else:
                new_index = _nearest_kept(model, old_to_new, kept_set, b)
            if new_index is None or new_index < 0:
                continue
            merged[new_index] = merged.get(new_index, 0.0) + w
        if not merged:
            # 空间兜底：用顶点位置（转换到与 heads 一致的坐标系）找最近标准骨
            vx, vy, vz = convert_vec(positions[vi], scale)
            best = None
            best_d = float("inf")
            for ni in range(len(new_heads)):
                hx, hy, hz = new_heads[ni]
                d = (hx - vx) ** 2 + (hy - vy) ** 2 + (hz - vz) ** 2
                if d < best_d:
                    best_d = d
                    best = ni
            if best is None:
                raise PMXError(f"顶点 {vi} 无法映射到任何人形骨骼")
            merged[best] = 1.0
        total = sum(merged.values())
        remapped.append([(k, w / total) for k, w in merged.items()])
    return remapped


def _prune_weights(deform, skin_flag, new_parent):
    """min_weight 剪枝：丢弃 skin_flag==0 的骨骼权重，并把被丢弃骨骼的权重

    归并到最近的「参与蒙皮」祖先；若仍为空则兜底到任意参与蒙皮的骨骼，
    保证不出现空权重顶点（审查项 P0#1 / P0#2）。

    重要：此时 deform / skin_flag / new_parent 全部是「导出后的新序号」空间。
    早期版本在这里用 model.bones[cur].parent_index（原始 PMX 序号空间）查祖先，
    两套序号混用会走到完全无关的骨上——min_weight>0 时才会触发，属潜伏 bug。
    """
    remapped = []
    fallback = next((i for i, f in enumerate(skin_flag) if f == 1), None)
    for weights in deform:
        merged: dict = {}
        for b, w in weights:
            if 0 <= b < len(skin_flag) and skin_flag[b] == 1:
                merged[b] = merged.get(b, 0.0) + w
        if not merged:
            for b, w in weights:
                cur = b
                seen = set()
                tgt = None
                while cur is not None and cur >= 0 and cur not in seen:
                    if 0 <= cur < len(skin_flag) and skin_flag[cur] == 1:
                        tgt = cur
                        break
                    seen.add(cur)
                    if cur >= len(new_parent):
                        break
                    cur = new_parent[cur]
                if tgt is not None:
                    merged[tgt] = merged.get(tgt, 0.0) + w
        if not merged:
            if fallback is None:
                raise PMXError("min_weight 剪枝后某顶点无任何有效骨骼")
            merged[fallback] = 1.0
        total = sum(merged.values())
        remapped.append([(k, w / total) for k, w in merged.items()])
    return remapped


def _to_new_skeleton(model, old_to_new, kept_set, bone_index: int):
    """把「原始 PMX 骨序号」（如刚体引用的骨）换算成导出后的新序号。

    若该骨被保留则直查 old_to_new；否则沿父链向上找最近的保留骨（权重归并同款逻辑）。
    找不到返回 -1（调用方决定挂到场景根或跳过）。
    """
    if bone_index is None or bone_index < 0:
        return -1
    if bone_index in old_to_new:
        return old_to_new[bone_index]
    return _nearest_kept(model, old_to_new, kept_set, bone_index)


MAX_MORPH_CHANNELS = 100      # 3ds Max 的 Morpher 修改器只有 0..99 共 100 个通道


def _morph_rows(model, scale: float, vert_remap=None):
    """生成顶点变形（MMD 表情）的 Morpher 通道数据行。

    仅 type==1（顶点变形）可生成几何目标（Morpher 通道需要同拓扑网格）；
    bone/uv/material 等其他类型记录为 SKIP，不进 Morpher。
    偏移按线性坐标变换（convert_vec 是线性的，故 delta 同法变换）。
    超过 100 个顶点变形的模型只保留前 100 个（Max Morpher 的硬上限），
    其余标 CHANNEL_FULL，让 Max 端与用户都知道被截断了。
    vert_remap：_drop_unreferenced 的旧->新序号（-1=该顶点不进网格，偏移一并丢弃，
    反正它不被任何面引用，画不出来）。
    返回 (含表头的正文行, 实际通道数)；写文件由 export() 负责。

    一个文件两种记录：MORPH 行是本行的列名（表头行），V 行的列名写在紧随其后的
    `@V` 行里（见 _kind_header 的说明）—— 两者列号含义不同，合成一行会互相骗。
    """
    cols = ("row_kind", "morph_index", "morph_name", "morph_name_en",
            "morph_panel", "morph_type", "item_count_or_flag")
    v_cols = ("vertex_index", "offset_x", "offset_y", "offset_z")
    lines: list = [_header_line(cols), _kind_header("V", v_cols)]
    count = 0
    for mi, m in enumerate(model.morphs):
        if m.morph_type != 1:
            lines.append(f"MORPH\t{mi}\t{m.name}\t{m.name_en}\t{m.panel}\t"
                         f"{m.morph_type}\tSKIP")
            continue
        if count >= MAX_MORPH_CHANNELS:
            lines.append(f"MORPH\t{mi}\t{m.name}\t{m.name_en}\t{m.panel}\t"
                         f"{m.morph_type}\tCHANNEL_FULL")
            continue
        count += 1
        # 先按 remap 过一遍，好让表头的条数 = 下面真实写出的 V 行数
        # （Max 端只把第 7 列当哨兵、按行读取，但数字对不上会让人以为丢了表情）。
        items = m.items
        if vert_remap is not None:
            items = [(vert_remap[v], o) for v, o in items
                     if 0 <= v < len(vert_remap) and vert_remap[v] >= 0]
        lines.append(f"MORPH\t{mi}\t{m.name}\t{m.name_en}\t{m.panel}\t"
                     f"{m.morph_type}\t{len(items)}")
        for vidx, offset in items:
            dx, dy, dz = convert_vec(offset, scale)
            lines.append(f"V\t{vidx}\t{fmt(dx)}\t{fmt(dy)}\t{fmt(dz)}")
    return lines, count


# 躯干中轴在 Biped 里的顺序（从下往上）。只列实际存在的即可相邻配对。
CENTER_NODE_ORDER = ("Bip001 Pelvis", "Bip001 Spine", "Bip001 Spine1",
                     "Bip001 Spine2", "Bip001 Neck", "Bip001 Head")


def _limb_segments_allowed() -> set:
    """允许「按段缩放」的 (父节点, 子节点) 白名单。

    必须覆盖**整条 FK 链的每一节**，否则子关节到不了目标：
    Biped 的 FK 里「子关节到父关节的距离」由父骨的 link 长度锁定，只能转向不能改距。
    上一轮漏了 Clavicle→UpperArm 与 Pelvis→Thigh，于是肩差 2.85、髋差 1.45，
    肘/膝被连带带偏 5.75 / 0.95（实测日志）。

    刻意排除的段：
      - Pelvis→Spine / Spine→Spine1 / …：缩放 Pelvis 会连带改变整体身高；
      - Spine2→Clavicle：缩放 Spine2 会扭曲胸腔。
    躯干段长不一致只是视觉问题，不影响四肢贴合，收益远小于风险。
    """
    allowed = set()
    for side in ("L", "R"):
        for parent, child in (
            ("Clavicle", "UpperArm"),
            ("UpperArm", "Forearm"),
            ("Forearm", "Hand"),
            ("Thigh", "Calf"),
            ("Calf", "Foot"),
            ("Foot", "Toe0"),
        ):
            allowed.add((f"Bip001 {side} {parent}", f"Bip001 {side} {child}"))
        for index in ("0", "1", "2", "3", "4"):
            for link in range(3):
                p = f"Bip001 {side} Finger{index}" + ("" if link == 0 else str(link))
                allowed.add((p, f"Bip001 {side} Finger{index}{link + 1}"))
    # 髋：Biped 的两条大腿挂在 Pelvis 两侧，横向距离由骨盆缩放决定
    allowed.add(("Bip001 Pelvis", "Bip001 L Thigh"))
    allowed.add(("Bip001 Pelvis", "Bip001 R Thigh"))
    return allowed


def _clamp_tip_bone_lengths(bone_names, parents, heads, tails, limit: float = 2.0):
    """把「眼部 tip 骨」（目先 / 瞳）的 tail 拉近到 limit 长度。

    为什么要做：MMD 里 目先 的 tail 指向眼睛正前方很远（本模型长 7.08），
    3ds Max 的 Bone 会按 head→tail 画成一根长条，看起来像箭头；
    而它实际只代表瞳孔位置，长度没有意义。压到 2cm 后和其他骨一个量级。
    """
    if not bone_names:
        return
    has_child = [False] * len(bone_names)
    for i, pi in enumerate(parents):
        if 0 <= pi < len(has_child):
            has_child[pi] = True
    for i, name in enumerate(bone_names):
        if has_child[i]:
            continue
        if "目先" not in name and "瞳" not in name:
            continue
        h = heads[i]
        t = tails[i]
        d = [t[k] - h[k] for k in range(3)]
        length = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
        if length <= limit or length < 1e-6:
            continue
        tails[i] = (h[0] + d[0] / length * limit,
                    h[1] + d[1] / length * limit,
                    h[2] + d[2] / length * limit)


# 胸部骨的识别：只认「左右胸」这一类，不认躯干段。
# 为什么不能只看「名字里有胸」：上半身/下半身/胸腔/胸郭都带「胸」或挨着胸，
# 它们是躯干中轴、tail 由 Biped 链决定，改了指向就把整条脊柱搞坏。
BREAST_WORDS = ("胸", "バスト", "breast", "bust")
BREAST_SKIP = ("上半身", "下半身", "胸腔", "胸郭", "胸骨")


def _is_breast_bone(name: str, name_en: str = "") -> bool:
    text = f"{name} {name_en}"
    if any(word in name for word in BREAST_SKIP):
        return False
    return any(word in text for word in BREAST_WORDS)


def _aim_breast_bones(bone_names, bone_names_en, parents, deform, positions,
                      scale, heads, tails, min_region: int = 6) -> list[str]:
    """把胸部骨（左胸/右胸/バスト…）的 tail 改成**从网格量出来的乳头点**。

    为什么必须改：PMX 里这对骨的 tail_offset 是一根纯竖直的短棒（香菱实测
    head→tail = +Z 7.66cm，正好等于 scale 的默认尾长），作者只是留了个默认值，
    没有任何方向含义。Max 按 head→tail 画 BoneGeometry，于是胸腔上长出两根向上的
    牙签 —— 用户 2026-11-03 的截图判据：「胸部的骨骼应该表现胸部的指向方向，
    应该指向乳头」。

    乳头怎么定义才不靠猜（probes/breast_axis_probe.py 实测）：
      · 取「以这根骨为最大权重、且权重 ≥0.5」的顶点当它自己的皮肤区域。香菱实测
        左右各 18 个点，包围盒 2.82 × 3.09 × 4.19cm —— MMD 的胸部骨本来就只绑乳头
        那一小块，所以区域本身就是答案的一半，不必再猜整个乳房的外形。
      · 顶点 = 沿 head→区域质心 方向投影最外的 25% 求均值，迭代两轮收敛。
        为什么不直接取「最靠前那个点」：单点会被一个野顶点带跑；为什么不用质心：
        质心在鼓面内部（实测比顶点靠后 1.2cm，方向差 13°）。
      · 这条估计**不假设角色朝哪个轴**（只用骨自己的 head 和它绑着的点），所以
        换模型、换朝向都成立。探针里它和「最靠前 25% 均值」读到同一个点（4.55cm /
        与竖直夹 96.4°，即水平略向下），两条独立算法对撞一致才敢当判据。

    只在「这根骨没有保留的后代」时动手：有后代的话 tail 已经指向子关节（真链），
    覆盖它就是把骨骼链画错。区域太小（<min_region 点）也原样留着 —— 宁可不改，
    也不拿三五个点编出一个方向。返回改动了哪几根（进 plan.notes，日志里留痕）。
    """
    candidates = [i for i, name in enumerate(bone_names)
                  if _is_breast_bone(name, bone_names_en[i] if i < len(bone_names_en) else "")]
    if not candidates:
        return []
    has_child = [False] * len(bone_names)
    for i, parent_index in enumerate(parents):
        if 0 <= parent_index < len(has_child):
            has_child[parent_index] = True
    region: dict[int, list] = {i: [] for i in candidates}
    for index, weights in enumerate(deform):
        if not weights:
            continue
        owner, weight = max(weights, key=lambda pair: pair[1])
        if owner in region and weight >= 0.5:
            # positions 是 **PMX 原始坐标**（未转换未缩放），heads/tails 是 Max 坐标，
            # 两边必须经同一个 convert_vec 才可比 —— _neck_axis_y 走的是同一条路。
            region[owner].append(convert_vec(positions[index], scale))
    changed = []
    for i in candidates:
        if has_child[i]:
            continue
        pts = region.get(i) or []
        head = heads[i]
        if len(pts) < min_region:
            continue
        centroid = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
        aim = centroid
        for _ in range(2):
            direction = _unit(_sub(aim, head))
            ranked = sorted(pts, key=lambda p: _dot(_sub(p, head), direction),
                            reverse=True)
            group = ranked[:max(1, len(ranked) // 4)]
            aim = tuple(sum(p[k] for p in group) / len(group) for k in range(3))
        if _dist(head, aim) < 1e-3:
            continue          # 顶点几乎落在骨头上：没有方向可言，别把 tail 变成零长
        tails[i] = aim
        changed.append(f"{bone_names[i]}→({aim[0]:.2f},{aim[1]:.2f},{aim[2]:.2f})"
                       f" {len(pts)}点/{_dist(head, aim):.2f}cm")
    return changed


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


# 特写取景框（#70 胸部、#71 颈部）的余量。一对旋钮、两处共用：
# 余量是**模型实测高度**的比例，不是厘米 —— heads/tails 与网格坐标都是「已乘 scale 的
# 导出单位」，写死厘米数换一具模型（或 GUI 里改身高）就废，同一类事故见上面
# TOE_SPLIT_BAND 的注释。
# 为什么这只框必须由导出侧量：上一版把它写成「场景包围盒高度 × 0.58~0.72」，那两个比例
# 是对着 PMX 标称身高 160 定的，而 .ms 里的 h 是场景实测高度 140.513 —— 分母一换，整只带
# 低了 4cm：第 35 轮的 chest_front / bone_chest_front 里一根胸骨都没有（胸骨实测
# z 105.23~105.73，带的上沿只到 101.17），而 shot_rb 的 fill_pct 报 63%、全绿 ——
# 「框里到底有没有那个东西」这件事，填充率在结构上就看不见。现在框跟着量出来的骨走：
# 任何一具模型，只要认得出那几根骨，框就一定框得住它。
# 0.06 的账（香菱实测网格高 H=144.33、胸骨 z 跨度 0.51）：带高 17.9cm，经 #69 按视口宽高比
# 收过之后横向 30.9cm；4.66cm 的胸骨在 820x474 的视口里横向约 124px ——
# 「指着乳头」在像素上读得出来，框里又还留得下肩与颈根，骨不至于孤零零地飘着。
# 颈部同一对数（#71）：颈链实测 z 100.76（上半身2 的 head）..128.29（頭 的 tail）= 27.5cm，
# 上下各外扩 8.7cm ⇒ 带高 44.8cm，比旧的比例带（z 77.28..137.70 = 60.4cm）紧 1.35 倍，
# 而**上沿几乎没动**（137.0 vs 137.7）—— 旧带一直就把头顶裁在 137，收紧只发生在下面，
# 腰腹那 25cm 本来不是颈部特写要看的东西。
BAND_PAD_Z = 0.06
# 宽与深的余量单独给（第 36 轮的账）：那轮 z 已经是量出来的，x/y 却还拿「场景包围盒 ±10cm」
# 凑，于是框的前后中心被身后的裙摆与长发拽到 y=+9.8，#69 按宽高比收水平跨度时是**朝框中心**
# 收的 —— 收到的 y -0.41..20.01 把站在 -11.75..-7.24 的两根胸骨整个裁到框外，
# bone_chest_side 里只剩半张脸。0.10 的账（H=144.33 → 14.4cm）：胸骨前后跨度 4.5cm，
# 框深 33.3cm，装得下整个胸腔的前后（胸前缘 -12.8 / 背后缘 +3.1）还留 15cm 余量 ——
# 特写里看得见胸的轮廓，而不是两根骨悬在空白里。横向同理给 14.4cm（收框后 30.9 > 27.2，
# 不再被 x 绑架，#69 那一段也就不用改）。
# 颈部的 x/y 极值只有 3.8cm（颈与头都贴着中线），14.4cm 的余量把框撑到 ~33cm；这只框的
# 纵向更高（44.8），所以 zoomToBounds 由纵向贴、横向实际可见约 51cm ⇒ 肩线仍在画面里。
BOX_PAD_XY = 0.10

FRAMING_AXES = ("x", "y", "z")

# 颈部特写要看的东西 = 颈链三节：#17 的颈部落位 + #27 的躯干后仰与颈前倾，三个判据的骨
# 全部进极值，框才「按构造」罩得住它们 —— 只框 首/頭 会把 上半身2 甩出画面，那正是
# .ms 旧注释里点名要避免的事（「用户说『Spine2 没后仰』时画面里没有它」）。
# 按 **Biped 目标**认而不按骨名认：`首` 与 `左手首/右手首/左足首/右足首` 都含「首」，
# 名字规则迟早被一具奇形怪状的模型骗；而 biped_map 是这条链为什么存在的唯一真值源。
NECK_TARGETS = ("Bip001 Spine2", "Bip001 Neck", "Bip001 Head")


class FramingBand(NamedTuple):
    """一张特写的取景依据，两组端点都是导出单位的 **(x, y, z)** 三元组。

    `b_min` / `b_max` 是量出来的 subject 端点极值（要看的东西），`lo` / `hi` 是框。
    分成两组数是判据的需要：Max 只许用框，而 Python 拿 subject 去问「Max 这一轮实际用的
    框罩住骨了没有」—— 用框去验框（把 lo/hi 原样比回来）只能证明 CFG 没丢，证明不了框里
    有东西，而后者才是第 35 轮真正坏掉的那件事。
    三个轴都量（只量 z 是第 36 轮的半成品：z 对了，y 仍然把骨裁在框外）。
    """
    lo: tuple
    hi: tuple
    b_min: tuple
    b_max: tuple


def _mesh_height(positions, scale) -> float:
    """网格实测高度（导出单位），没有顶点返回 0 —— 调用处拿 0 当「量不出来」。"""
    if not positions:
        return 0.0
    heights = [convert_vec(p, scale)[2] for p in positions]
    return max(heights) - min(heights)


def _framing_band(pts, mesh_h) -> FramingBand | None:
    """subject 点集 + 网格高 → 取景框与极值；点集空或量不出高度返回 None。"""
    if not pts or mesh_h <= 0.0:
        return None
    b_min = tuple(min(p[a] for p in pts) for a in range(3))
    b_max = tuple(max(p[a] for p in pts) for a in range(3))
    pads = (BOX_PAD_XY * mesh_h, BOX_PAD_XY * mesh_h, BAND_PAD_Z * mesh_h)
    lo = tuple(b_min[a] - pads[a] for a in range(3))
    hi = tuple(b_max[a] + pads[a] for a in range(3))
    return FramingBand(lo=lo, hi=hi, b_min=b_min, b_max=b_max)


def _chest_band(bone_names, bone_names_en, heads, tails, positions, scale):
    """胸部特写该框的那块空间（导出单位的绝对坐标），量不出来返回 None。

    认骨用 `_is_breast_bone`，和 `_aim_breast_bones` 同一条命名规则 —— 所以「被改成指向
    乳头的那两根骨」与「取景框框住的那两根骨」不可能分叉。
    head 与 tail 都进极值：tail 是量出来的乳头点（没改动的则是 PMX 默认尾），两者都在胸上。
    返回 None（模型里没有叫「胸 / bust」的骨）时 meta 那 12 条写空串，Max 退回旧的场景
    包围盒带：认不出胸骨的模型至少还有特写可看，而不是整段截图被跳过。
    """
    pts = []
    for i, name in enumerate(bone_names):
        name_en = bone_names_en[i] if i < len(bone_names_en) else ""
        if _is_breast_bone(name, name_en):
            pts.append(heads[i])
            pts.append(tails[i])
    return _framing_band(pts, _mesh_height(positions, scale))


def _neck_band(heads, tails, biped_mapping, old_to_new, positions, scale):
    """颈部特写该框的那块空间：颈链三节（`NECK_TARGETS`）的 head/tail 全进极值。

    与 `_center_neck` 用同一张 `biped_mapping → old_to_new` 的换算表（那里是「按 Biped
    节点名找导出序号再挪骨」，这里是「按同一张表找导出序号再量框」），所以「颈骨被摆到
    哪」与「颈部特写框在哪」不可能分叉 —— 这是 #70 那条「认骨只认一次」的规则在颈侧的落法。
    三节里只要有一节在就量（有些模型没有 上半身2）；三节都没有 ⇒ None ⇒ meta 那 12 条写
    空串，Max 退回旧的比例带。
    """
    target_to_new: dict = {}
    for old, target in (biped_mapping or {}).items():
        if target and old in old_to_new:
            target_to_new.setdefault(target, old_to_new[old])
    pts = []
    for target in NECK_TARGETS:
        ni = target_to_new.get(target)
        if ni is not None and ni < len(heads):
            pts.append(heads[ni])
            pts.append(tails[ni])
    return _framing_band(pts, _mesh_height(positions, scale))


def band_meta_pairs(prefix: str, band) -> list[str]:
    """取景框 + subject 极值 → meta 的 12 行 `键<TAB>值`；band=None 时 12 行的值都是空串。

    框的 6 行进 CFG（Max 只读数、不许自己算），极值的 6 行只给判据表当尺 ——
    两边的键名分别由 tools/obj_run.META_TO_CFG 与 META_FOR_CHECK 钉住。
    每行写成「同一轴的两端挨着」，obj_run._ruler 的切片靠的就是这个顺序。
    """
    lines = []
    for i, axis in enumerate(FRAMING_AXES):
        for key, value in ((f"{prefix}_box_{axis}_lo", None if band is None else band.lo[i]),
                           (f"{prefix}_box_{axis}_hi", None if band is None else band.hi[i])):
            lines.append(f"{key}\t{'' if value is None else fmt(value)}")
    for i, axis in enumerate(FRAMING_AXES):
        for key, value in ((f"{prefix}_bone_{axis}_min", None if band is None else band.b_min[i]),
                           (f"{prefix}_bone_{axis}_max", None if band is None else band.b_max[i])):
            lines.append(f"{key}\t{'' if value is None else fmt(value)}")
    return lines


def _band_note(label: str, band) -> str:
    """取景框的人读摘要（进 plan.notes，日志里留痕用）。"""
    return (f"  {label}特写取景框："
            + " ".join(f"{a} {band.lo[i]:.2f}..{band.hi[i]:.2f}"
                       for i, a in enumerate(FRAMING_AXES))
            + f"（{'/'.join(f'{v:.2f}' for v in band.b_min)}"
            + f"..{'/'.join(f'{v:.2f}' for v in band.b_max)} 是量出来的骨端点，"
            + f"高度外扩 {BAND_PAD_Z:.0%}、宽深外扩 {BOX_PAD_XY:.0%} 的网格高）")


def _dist(a, b):
    return _dot(_sub(a, b), _sub(a, b)) ** 0.5


def _unit(v):
    length = _dot(v, v) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def _neck_axis_y(positions, scale: float, neck, band: float = 3.0):
    """量颈部网格的前后中线（Max y，前 = -y）。采不到可靠切片返回 None。

    每层只算 |x| 不超过「该层中位半宽」的顶点，把袖子/长发排除在外；再对 band
    内的各层取中位数，避开下巴/发梢那一两层的跳变。香菱实测（z=113~119）：
    各层中线 -1.49 / -0.25 / -0.53 / +0.13 / +0.27 / +0.11 / +0.08 → 中位数 +0.08，
    而 MMD 的 首 骨在 y=-0.04 —— 差 0.12cm，说明 MMD 本来就把 首 放在颈轴上。
    """
    if not positions:
        return None
    nx, _ny, nz = neck
    layers: dict = {}
    for p in positions:
        x, y, z = convert_vec(p, scale)
        if -band <= z - nz <= band:
            layers.setdefault(int(round(z)), []).append((x, y))
    centers = []
    for pts in layers.values():
        if len(pts) < 6:
            continue
        half = sorted(abs(px - nx) for px, _py in pts)[len(pts) // 2]
        core = [py for px, py in pts if abs(px - nx) <= max(half, 1.0)]
        if len(core) < 6:
            continue
        centers.append((min(core) + max(core)) / 2.0)
    if len(centers) < 3:
        return None
    centers.sort()
    return centers[len(centers) // 2]


def _center_neck(heads, biped_mapping, old_to_new, positions=None,
                 scale: float = 1.0, tol: float = 1.5) -> float:
    """把 首(Bip001 Neck) 挪到「颈部网格中线」上，只动前后（y），高度不变。

    为什么不能用「上下两骨的中点」这类几何猜法（这是上一版的规则，已废弃）：
    MMD 的躯干骨放在**体腔中心**，颈部骨放在**颈轴**上，两者本来就不一条线。
    香菱实测 上半身2 在 y=-4.92（胸腔中心，前缘 -12.8 / 后缘 +3.1），首 在 y=-0.04
    （颈轴）。取「上半身 与 頭 的中点」会把脖子往前拽 3.03cm 到喉咙前面，
    侧视就是「脖子从胸口里长出来」—— 用户报的 #17「脖子位置没对上」正是这个。
    反过来，让 首 回到实测颈轴，胸腔中心 (-4.92) 就成了上链最靠前的点，
    也就是用户说的「Spine2 通常带一点挺胸」：挺胸是颈轴归位的自然结果，
    不需要额外加转角。

    只有明显偏离（> tol）才动，且一次最多挪 3cm；Head 永不动（头骨位置不能动）。
    返回实际位移（cm），0 表示没动。
    """
    target_to_new: dict = {}
    for old, target in (biped_mapping or {}).items():
        if target and old in old_to_new:
            target_to_new.setdefault(target, old_to_new[old])
    ni = target_to_new.get("Bip001 Neck")
    if ni is None:
        return 0.0
    hx, hy, hz = heads[ni]
    want = _neck_axis_y(positions, scale, heads[ni])
    if want is None:
        return 0.0
    dy = want - hy
    if abs(dy) < tol:
        return 0.0
    dy = max(-3.0, min(3.0, dy))
    heads[ni] = (hx, hy + dy, hz)
    return abs(dy)


# 下面两条阈值是**导出单位（厘米）**里的绝对量，不跟 scale 缩。
# 上一轮我（被蜕变测试带偏）把它们改成「PMX 单位 × scale」，实测把香菱打崩了：
# 香菱的 auto scale = 7.6563（1 个 PMX 单位 = 7.66cm），于是 2.5cm 的上限被放大成
# 239cm、0.02cm 的下限放大成 1.91cm —— widths.tsv 从 12 行掉到 10 行，
# `Bip001 R Hand` 的粗细从 3.6953 涨到 58.3399（手掌+邻指全进了样本）。
# 之所以该钉在厘米而不是 PMX 单位：`auto_scale` 把角色**固定成 160cm 高**，
# 所以导出单位是跨模型稳定的物理量；PMX 单位反而每个模型不一样（20 单位的模型和
# 1875 单位的模型差近 100 倍）。代价要说清楚：这两条让「scale 翻倍 ⇒ 一切线性」在
# scale 这个**人造输入**上不成立。M5 因此不再要求「scale 翻倍 ⇒ 一切线性」（那条在产品里
# 是假的，上一轮就是被它推着改了这两条常量、把香菱改崩），改钉**单位协变**：PMX 长度翻倍 +
# scale 减半 ⇒ 产物逐字节相同。厘米绝对量在那条下天然成立（见 tests/test_metamorphic.py）。
_RADIUS_CAP_CM = 2.5      # 顶点到骨轴的最大计入垂距（导出厘米）
_WIDTH_MIN_CM = 0.02      # 细到低于这条的段不算粗细（导出厘米）

# 零长度骨的判据与占位尾长度同样钉在导出厘米上（理由与上面两条一致）。
# 之前它们写成 `scale * 0.001` / `scale * 0.1` / `scale * 0.05`，也就是「若干个 PMX
# 单位」—— 那是作者画法，不是物理量：香菱（1 单位 = 7.66cm）占位尾 0.76563cm，
# 克雷薇（1 单位 = 8.76cm）占位尾 0.8763cm，同一根无名骨在两具模型上长得不一样，
# 而单位协变要求它俩**逐字节相同**。取值依据见 test_runs/probe_stub_spectrum.py 量的谱：
# 两具模型里「判为零长」的根数在 0.002cm ~ 0.3cm 整个区间内都不变（真骨最短 0.99cm，
# 重合的那些要么精确为 0，要么是 0.000008cm 的浮点尾巴），所以 0.05cm 落在安全窗正中。
# 占位尾取 0.8cm / 0.4cm = 今天两具模型实测值的中间，且远低于 `_RADIUS_CAP_CM`，
# 所以 `length * 0.9` 仍然顶不到粗细上限。
_DEGENERATE_EPS_CM = 0.05   # 首尾距离小于这条 = 零长度骨（导出厘米）
_STUB_LEN_CM = 0.8          # 占位尾：往 +z 撑多长（导出厘米）


def _bone_radius(positions, scale: float, ph, ch):
    """估计某段骨骼周围网格的「半径」（顶点到轴线垂距的中位数）。

    用途：Biped 默认手脚很粗，直接套在 MMD 网格上会明显大一圈
    （用户截图：手指骨比手指还粗）。用网格实测半径算目标粗细，
    Max 端据此收缩非长度轴。

    `cap` 与下面 `_width_rows` 的下限都是**导出厘米**里的绝对量：`auto_scale` 保证角色
    恒为 160cm 高，所以厘米是跨模型稳定的，而 PMX 单位不是（不同模型差近 100 倍）。
    上一轮把这两条改成「PMX 单位 × scale」，香菱（auto scale 7.6563）实测把手掌算进
    了手的粗细（3.6953 → 58.3399）—— 已回滚，理由见常量定义处的注释。
    """
    ax = [ch[i] - ph[i] for i in range(3)]
    length = (ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2) ** 0.5
    if length < 1e-6:
        return None
    ax = [v / length for v in ax]
    # 采样窗口卡在 10%~90% 段长：太靠两端会把相邻关节的球体算进来。
    # 半径上限取 max(2.5, 0.9×段长)：手指这种细骨必须收紧，否则邻指/手掌的顶点
    # 会把「中位数」抬高好几倍（早期版本就是这样把 1.5cm 的手指算成 6.2cm）。
    cap = max(_RADIUS_CAP_CM, length * 0.9)
    samples = []
    for p in positions:
        v = convert_vec(p, scale)
        d = [v[0] - ph[0], v[1] - ph[1], v[2] - ph[2]]
        t = d[0] * ax[0] + d[1] * ax[1] + d[2] * ax[2]
        if t < 0.1 * length or t > 0.9 * length:
            continue
        perp2 = max(0.0, d[0] ** 2 + d[1] ** 2 + d[2] ** 2 - t * t)
        perp = perp2 ** 0.5
        if perp < cap:
            samples.append(perp)
    if len(samples) < 6:
        return None
    samples.sort()
    # 取 30 分位而不是中位数：骨骼通常略偏表面一侧，中位数会偏粗
    return samples[int(len(samples) * 0.3)]


def _palm_span(positions, scale: float, deform, hand_index: int,
               heads: list, target_to_new: dict, side: str):
    """沿「腕 → 五指根重心」轴量手掌网格的真实跨度（导出厘米）。

    Biped 的 Hand 链接默认是按身高给的桨形盒：林尼实测最终场景里长轴盒跨度
    10.24cm，而手掌网格沿掌轴只占 6.5cm —— 只缩横截面（旧行为）会把「手掌骨盒
    比手掌大一圈」原样留下。这里拿**手掌骨自己占主导权重的顶点**在掌轴上的投影
    取 5%~95% 分位，量的是网格而不是骨头，所以能直接和用户截图里的蓝圈对照。

    量不到（手掌没有顶点 / 缺指根骨）时退回「腕 → 五指根重心」的骨几何距离：
    那仍是有来历的数，不是猜的常量；但样本不足 30 个顶点时绝不拿分位数凑。
    """
    if hand_index is None or not positions:
        return None
    wrist = heads[hand_index]
    roots = [target_to_new.get(f"Bip001 {side} Finger{i}") for i in range(5)]
    if any(r is None for r in roots):
        return None
    centroid = tuple(sum(heads[r][k] for r in roots) / len(roots) for k in range(3))
    axis = [centroid[k] - wrist[k] for k in range(3)]
    span = math.sqrt(sum(v * v for v in axis))
    if span < 1e-6:
        return None
    axis = [v / span for v in axis]
    projections = []
    if deform is not None:
        for vi, weights in enumerate(deform):
            if not any(b == hand_index and w >= 0.5 for b, w in weights):
                continue
            v = convert_vec(positions[vi], scale)
            projections.append(sum((v[k] - wrist[k]) * axis[k] for k in range(3)))
    if len(projections) < 30:
        return span
    projections.sort()
    lo = projections[int(len(projections) * 0.05)]
    hi = projections[int(len(projections) * 0.95)]
    return max(0.5, hi - lo)


def _width_rows(scale: float, old_to_new: dict, heads: list, tails: list,
                biped_mapping: dict | None, positions=None, deform=None):
    """生成「手部粗细表」行：目标世界宽度，供 Max 端收缩 Biped 手掌/手指的粗细。

    只处理 Hand / Finger0~4：这几节 Biped 默认最粗，和网格差得最明显。
    列：biped_target \t width \t palm_len
    第 3 列只有 Hand 两行有值（沿掌轴量出的真实掌长，见 `_palm_span`）；
    其余行留空，老产物没有这一列时 Max 与判据侧都沿用旧行为。
    返回 (含表头的正文行, 数据行数)。
    """
    lines = [_header_line(("biped_target", "width", "palm_len"))]
    if not biped_mapping or not positions:
        return lines, 0

    wanted = []
    for side in ("L", "R"):
        wanted.append(f"Bip001 {side} Hand")
        for i in range(5):
            wanted.append(f"Bip001 {side} Finger{i}")

    target_to_old: dict = {}
    for old, target in biped_mapping.items():
        if target:
            target_to_old.setdefault(target, old)
    target_to_new = {target: old_to_new[old]
                     for target, old in target_to_old.items() if old in old_to_new}
    palms = {side: _palm_span(positions, scale, deform,
                              target_to_new.get(f"Bip001 {side} Hand"),
                              heads, target_to_new, side)
             for side in ("L", "R")}

    count = 0
    first_width: dict[str, float] = {}
    for target in wanted:
        old = target_to_old.get(target)
        if old is None or old not in old_to_new:
            continue
        ni = old_to_new[old]
        r = _bone_radius(positions, scale, heads[ni], tails[ni])
        # 下限 0.02 是**导出厘米**（= 0.2mm）：比这还细的段量不出粗细，丢掉。
        # 它是绝对的，所以 widths.tsv 的行集会随 scale 变 —— 这是设计，不是缺陷：
        # 生产里 scale 由 `auto_scale` 钉成「角色 160cm 高」，厘米是稳定量纲。
        # 上一轮反过来把它改成「PMX 单位 × scale」，香菱实测掉了两根 Finger3、
        # 手掌算进手的粗细（3.6953 → 58.3399），已回滚。
        if r is None or r <= _WIDTH_MIN_CM:
            continue
        side = target.split()[1]
        palm = palms.get(side) if target.endswith(" Hand") else None
        palm_text = f"{palm:.4f}" if palm else ""
        lines.append(f"{target}\t{r * 2.0:.4f}\t{palm_text}")
        first_width[target] = r * 2.0
        count += 1

    # ---- 第三节（末节）指骨：#139「第三节手指都比原来的结构长了一节」----
    # 末节是**叶子骨**：`limbs.tsv` 只给「父→子」段定价（没有子关节就没有段），
    # widths.tsv 原先也只覆盖 Hand + Finger0~4（第一节），于是末节三轴全部保留
    # Biped 按身高给的默认值。林尼实测末节盒长 3.33/3.88/3.33/2.77cm，
    # 而 PMX 末节骨 head→tail 只有 2.02/2.22/2.29/1.70cm ⇒ 长出 46~75%。
    # 拇指 2.93cm 恰好接近默认的 2.77cm，所以用户说「除了大拇指」—— 数据自洽。
    #
    # 修法复用 #136 已有的第 3 列长度闭环（Max 侧 mmd2maxApplyWidths 的 palm 分支，
    # 判据侧 box_rb/hand_length_cm）：把末节骨加进 widths.tsv，第 3 列填 PMX
    # 末节骨 head→tail 长度。这一列的语义因此从「掌长」扩为「该骨长度轴的目标盒跨度」，
    # Python 判据与 Max 写入仍读同一张表，不新增通道、不改 GRADED_SURFACE 的 check 集。
    # 粗细沿用同一根手指第一节的实测值（指尖顶点稀，单独量不稳；同一指粗细相近）。
    for side in ("L", "R"):
        for i in range(5):
            tip = f"Bip001 {side} Finger{i}2"
            first = f"Bip001 {side} Finger{i}"
            if first not in first_width:
                continue
            ni = target_to_new.get(tip)
            if ni is None:
                continue
            h, t = heads[ni], tails[ni]
            tip_len = ((h[0] - t[0]) ** 2 + (h[1] - t[1]) ** 2
                       + (h[2] - t[2]) ** 2) ** 0.5
            if tip_len <= 0.5:
                continue
            lines.append(f"{tip}\t{first_width[first]:.4f}\t{tip_len:.4f}")
            count += 1
    return lines, count


_TOE_PLANES = 13   # 沿趾方向切几刀量鞋面轮廓
_TOE_FRONT = 0.9   # 最前面 10% 是圆头收口，不参与「最低鞋面」判定
_FOOT_BANDS = 12   # 踝→脚掌分成几段量鞋的内外/上下余量
_FOOT_BAND_MIN = 8  # 一段里至少 8 个「窗内」顶点才有投票权（3 个会被一条装饰边带跑）

# 第十四轮实跑回读到的 Biped 脚盒**真实摆位**（本地坐标，长度轴 = 2/Y）：
#   min=[-1.44334,-2.37011,-3.40802] max=[8.05423,7.93483,3.46225]
#   竖直方向 15.2% 在轴上方、84.8% 在轴下方；长度方向从轴起点往后 21.1% 段长、
#   往前 70.7% 段长；横向对称。#scale 逐字保持这些比例（缩后
#   [-0.232596,+1.29795] 与 [-1.44334,+8.05423] 同比例）。
# 这三条把 #19/#37 的判据基础推翻了：它一直按「盒子绕长度轴中心对称」算，
# 于是「高 = 2×min(上下净空)」把一只本来就挂在轴下方的盒子当成上下各一半，
# 量出来的 1.53055 只有真正可用的 84.8% 里那一小条 —— 用户看到的就是一根
# 斜搭在脚背上的牙签板（「像一个框一样框住脚的脚跟」那张对比图）。
FOOT_BOX_DOWN_FRAC = 0.848   # 骨盒高度里落在**轴下方**的比例
FOOT_BOX_UP_FRAC = 0.152     # 落在轴上方的比例（两者相加 = 1）
FOOT_BOX_BACK_FRAC = -0.2111  # 骨盒沿长度轴往后探出这么多**段长**（脚后跟就是靠它框住的）
FOOT_BOX_FWD_FRAC = 0.7067    # 往前伸到这么多段长（Biped 自己钉的，改不了）

# #39 的判据（量带 + 不对称取高），false ⇒ 逐字回到第十四轮（绕轴对称取高 = 1.53055）：
#   1) 量带改成骨盒**真正存在**的那一段 [-21.1%, +70.7%] 段长；
#   2) 高改成「轴下方还剩多少净空 ÷ 0.848」= 让盒底尽量贴到鞋底上。
#      不再拿上方净空当判据：上方只占 15.2%，而骨盒往上伸进靴筒/小腿不算穿模
#      （香菱的鞋是靴子，靴口在踝上方 0.8cm，靴筒一直往上到 17cm）。
#   3) 宽只许**盒子里面**的网格投票：量带变成一个更宽的横条，香菱的裙摆顶点
#      （离轴 +136cm）会混进来把宽从 3.71 撑到 8.44（比整只鞋 6.41 还宽、左右差 2.2）。
FOOT_HEELBOX = True

# 脚的轴俯仰开关：**只管轴的俯仰**，true ⇒ 第十五轮那只水平轴 + 高 8.66 的盒子，
# false ⇒ 轴按 PMX「踝->球」的三维方向斜插（第十六轮 = 一根斜牙签）。
# 第十五→十六轮为什么压平被回滚：Biped 的子关节（Toe0 的 pivot）被 段校验 钉在 PMX 的
# 球部，而骨盒画在**自己那根轴**上。轴一压平，盒子就不再过球部 —— 踝 z=7.53、球 z=1.94，
# 水平轴和「踝->自己的子关节」差 29.93 度，趾骨盒整个掉在盒子下方 6.3cm 处
# （用户：「Bip001 Toe0 的起始轴就不能接上 Bip001 Foot 的末端」）。
# 当时写下的结论是「#39 和 #40 只能选一个」，理由是一条看起来改不动的几何事实：
#   盒底面垂直于轴 ⇒ 轴斜 29.93 度，盒底面就斜 29.93 度；要在后跟下方够到地面
#   （那里离轴 7.3cm）H 得 8.7，同一根斜盒的前下角就钻到地下 2.94cm、
#   后下角退到踝后 5.7cm（整只鞋才到踝后 1.5cm）。
#   实测：斜轴/逐带贴底 = 4.71×2.32（链通），平轴 = 3.71×8.66（框住脚跟、链断）。
# **这条「只能选一个」被探针推翻了**（biped_gap2.ms，stock Biped，脚本一次没碰过）：
#   1) 骨盒远端面 == 子节点沿轴坐标，差 = 0.0 —— Biped 本来就把骨头画到子关节，
#      第十六轮那 3.29cm 空气不是 Biped 的画法，是我们自己把长度拟成了三维 pivot 间距
#      （肢段拟合跑在摆位之前，之后没人回头补父骨长度）。
#   2) 长度轴 #scale 乘 k，盒远端与子节点**同步**乘 k（×1.3 那一轮：子节点位移
#      3.42361 == 盒远端位移 3.42361），link 14.4522→17.2838，退出 figure 模式仍在。
#   ⇒ 盒子的沿轴长度是**可写的**（Max 2022 没有任何盒体积 API，setBox/setWidth/setTaper
#     全都不存在，只有 #scale），所以平轴那只 3.71×8.66 的厚盒子可以被 足盒接趾 沿水平轴
#     拉到「前端面正好过球部」：#39 的落地 + 框跟 + #40/#41 的接趾同时成立。
# 于是默认翻成 true。代价是 链向（骨轴 vs 自己的子关节）按构造读 29.93 度 —— 那是
# 水平轴的定义，不是断链；真正的判据换成新加的 盒隙（父盒前端面到子关节沿轴的距离 +
# 子关节须落在端面矩形内）。README 第 22/23 条记的是同一笔账。
# 轴一翻，Max 端的「写方向」也必须跟着翻，否则「量截面」和「画盒子」又不同源 ——
# 这一个词是唯一开关：值原样写进 meta 的 foot_box_flat，再由 obj_run 搬进 MMD2MAX.CFG，
# Max 侧读到的 bool 与这里写的 bool 同真同假（M1b 起中间不再取反）。
FOOT_BOX_FLAT = True


def _toe_sections(pts, faces, ball, ux, uy, t_hi, x_half, z_cap, planes):
    """沿「脚掌→鞋尖」方向切 planes 个横截面，返回 (t, x_lo, x_hi, z_lo, z_hi) 列表。

    为什么切面而不是「取某一片顶点」：鞋面是一条**向前下坡**的曲面，而 Biped 的骨盒
    以 pivot 为中心对称，盒子能不能整块待在鞋里，取决于它前进方向上最低的那段鞋面。
    香菱整只鞋落进窗口的顶点只有 266 个，按 0.5cm 分带每带只剩 2~14 个点，
    取 max/min 会被单个装饰顶点带跑；切三角形才能量到顶点之间的真实轮廓。
    """
    ts = [(x - ball[0]) * ux + (y - ball[1]) * uy for (x, y, _z) in pts]
    cand = []
    for f in faces:                      # 预筛：三个顶点里至少有一个落在 t 窗口内
        for i in range(3):
            if -0.5 <= ts[f[i]] <= t_hi + 0.5:
                cand.append(f)
                break
    out = []
    for k in range(planes):
        c = t_hi * k / (planes - 1)
        hits = []
        for f in cand:
            seg = []
            for i in range(3):
                j = (i + 1) % 3
                a, b = ts[f[i]], ts[f[j]]
                if a == b or (a - c) * (b - c) > 0.0:
                    continue
                r = (c - a) / (b - a)
                pa, pb = pts[f[i]], pts[f[j]]
                x = pa[0] + (pb[0] - pa[0]) * r
                z = pa[2] + (pb[2] - pa[2]) * r
                if abs(x - ball[0]) <= x_half and z <= z_cap:
                    seg.append((x, z))
            if len(seg) >= 2:            # 只剩一个点 = 截面被窗口切断，整段丢掉
                hits += seg
        if hits:
            xs = [p[0] for p in hits]
            zs = [p[1] for p in hits]
            out.append((c, min(xs), max(xs), min(zs), max(zs)))
    return out


def _foot_link_box(pts, ankle, ball, heelbox=None, flatten=None):
    """沿「踝→脚掌」这根轴量鞋，返回骨盒截面 (宽, 高)。

    flatten（#40 引入、#41 翻默认）只管**轴的俯仰**：
      False = 用 PMX 踝→球的三维方向（第十六轮）。骨盒的长轴正好穿过 Toe0 的 pivot
              （子关节被 段校验 钉在球部），链天生是通的，代价是盒子被逐带取高压成
              4.71×2.32 那块薄板 —— 用户两张对照图左边那根「牙签」。
      True  = 压成水平（第十五轮，现默认）。盒底变成水平面，能一直下到鞋底最低点，
              香菱量到 3.71×8.66 那只「框住脚跟」的盒子；骨轴和自己的子关节因此
              差 29.93 度，这一段空气由 Max 端的 足盒接趾（mmd2maxFitBoneReach）
              沿水平轴把前端面拉到球部补掉，探针证明盒子长度可写，所以两个要求能同时要。

    heelbox（#39 的判据）管**量带和取高**，与俯仰无关：
      - 量带 = 骨盒真正存在的那一段 t ∈ [FOOT_BOX_BACK_FRAC, FOOT_BOX_FWD_FRAC] × 段长；
      - 高 = 「盒底面离鞋底最近那一带的下方净空 ÷ FOOT_BOX_DOWN_FRAC」—— 盒底贴着
        鞋底走，但任何一带都不许钻到鞋底下面（斜轴时盒底跟着斜，逐带取紧是唯一的
        安全写法：拿全窗最低点香菱会戳到地下 2.94cm）；
      - 不再拿上方净空当判据：盒顶只占 15.2%，伸进靴筒/小腿不算穿模；
      - 宽仍按「两侧都摸得到面的段里取更紧那一侧」，但**只许盒子里面的网格投票**
        （Biped 横向本来是对称的，那一维没算错；算错的是量带混进了盒顶以上的小腿/裙摆）。
    heelbox=False：逐字回到第十四轮（绕轴对称取高 = 3.92599×1.53055）。

    老判据（保留当回滚位）原本假设「Biped 的肢骨盒子以长度轴为中心对称」，所以
    「骨头在鞋里」= 轴上每一段、四个方向的净空都容得下盒子：
        宽 = 2 × min(各段里更近的那一侧边)
        高 = 2 × min(各段里更近的那一侧上下边)
    两个方向**各自独立**取紧，且一段只有「轴两侧都摸得到面」才有投票权 —— 鞋口
    朝上是开口的，拿开口当判据会把骨头压成牙签，完全不理它又留着穿模。
    上一版只量了 Toe0（limbs.tsv 第 15/16 列），Foot 的截面从没有人管 ⇒ 骨盒
    8.00×6.61cm，而沿这根轴量到的鞋内切盒子只有约 3.9×1.6cm（用户：
    「脚的骨骼并没有包在脚的里面，实际上脚穿模出来了」）。

    为什么按「分段」而不是切横截面（_toe_sections 那套）：线性函数在三角形上的
    极值必在顶点上取到，一个斜带（两块平行平面之间）里只要顶点够密，顶点的最值
    就是轮廓的最值；切单截面才需要补三角形交点。带太稀（< _FOOT_BAND_MIN 个点）
    直接跳过，不让它当判据 —— 被一片装饰顶点收紧、或撑到牙签，都是拿噪声当证据。

    窗口全部相对**轴自己**给（不是绝对坐标，换模型不用改数）：
      - t：heelbox 量骨盒那一段，否则量骨头伸到的那一段（0..段长，两端各放 0.5 容差）
      - side：离轴 7cm 以外丢掉 —— 对面那只脚在 ±11.5cm，裙摆飘带也一并出去
      - up：不设窗口。上下各方向的净空由「这一段两侧都有面」单独把关（见下），
        所以小腿在脚踝上方继续延伸的那些面不会被误当成鞋面来收紧判据。
    """
    if heelbox is None:
        heelbox = FOOT_HEELBOX
    if flatten is None:
        flatten = FOOT_BOX_FLAT
    ax, ay, az = ankle
    bx, by, bz = ball
    dx, dy, dz = bx - ax, by - ay, bz - az
    seg = (dx * dx + dy * dy + dz * dz) ** 0.5
    if seg < 0.5:
        return None
    if flatten:
        horiz = (dx * dx + dy * dy) ** 0.5
        if horiz < 1e-4:
            return None      # 踝和球在同一根铅垂线上：压平就没方向了，宁可不量
        ux, uy, uz = dx / horiz, dy / horiz, 0.0
    else:
        ux, uy, uz = dx / seg, dy / seg, dz / seg
    if heelbox:
        t_lo, t_hi = FOOT_BOX_BACK_FRAC * seg, FOOT_BOX_FWD_FRAC * seg
    else:
        t_lo, t_hi = 0.0, seg
    # side = 水平且垂直于轴（脚骨是平躺的，「宽」就是它）；up = side × 轴（「高」）
    sx, sy = uy, -ux
    sl = (sx * sx + sy * sy) ** 0.5
    if sl < 1e-4:
        return None          # 轴接近竖直：宽高判据退化，宁可不量
    sx, sy = sx / sl, sy / sl
    upx, upy, upz = sy * uz, -sx * uz, sx * uy - sy * ux
    rows = []
    for (x, y, z) in pts:
        vx, vy, vz = x - ax, y - ay, z - az
        t = vx * ux + vy * uy + vz * uz
        if t < t_lo - 0.5 or t > t_hi + 0.5:
            continue
        s = vx * sx + vy * sy
        if abs(s) > 7.0:
            continue
        o = vx * upx + vy * upy + vz * upz
        rows.append((t, s, o))
    bands = []
    for k in range(_FOOT_BANDS):
        lo = t_lo + (t_hi - t_lo) * k / _FOOT_BANDS
        hi = t_lo + (t_hi - t_lo) * (k + 1) / _FOOT_BANDS
        if k == _FOOT_BANDS - 1:
            bands.append([r for r in rows if lo - 1e-6 <= r[0] <= hi + 1e-6])
        else:
            bands.append([r for r in rows if lo <= r[0] < hi])
    foot_h = None
    o_lo = o_hi = None
    if heelbox:
        if flatten:
            # 平轴：盒底是一张水平面，第十五轮按「整窗最低点」把盒底一直下到底 ——
            # 用户画的那个框（3.71×8.66）就是这么来的，留着当 #40 的回滚位，逐字不变。
            if len(rows) < _FOOT_BAND_MIN:
                return None
            down = -min(r[2] for r in rows)
        else:
            # 斜轴：盒底面跟着轴一起斜，同一张面在各带里离鞋底并不等距，所以判据
            # 必须**逐带取紧**：哪一带最挤就按哪一带收。拿全窗最低点（平轴那种写法）
            # 香菱会量出 8.87 的盒，盒前角直接钻到地下 2.94cm、盒后角退到踝后 5.7cm
            # （整只鞋只到踝后 1.5cm）—— 斜轴 + 深盒在几何上不能同时要（见上面开关注释）。
            downs = [-min(r[2] for r in b) for b in bands if len(b) >= _FOOT_BAND_MIN]
            if not downs:
                return None
            down = min(downs)
        if down <= 0.05:
            return None
        # 封顶取段长，免得换到高跟/悬空模型时量出一个比骨头还长的盒子。
        foot_h = min(down / FOOT_BOX_DOWN_FRAC, seg)
        # 宽只许由**盒子里面**的网格投票：骨盒在 (t, o) 平面里只占 o ∈ [-0.848H, +0.152H]
        # 这一条，小腿、裙摆、袖子里那些高出盒顶的面根本碰不到它，既压不窄它，
        # 也不许把「这一侧还有多少空地」撑大 —— 香菱实测裙摆顶点混进来把宽撑到
        # 8.44（比整只鞋 6.41 还宽）、左右还差 2.2cm（#26 修的就是左右不一致）。
        o_lo = -FOOT_BOX_DOWN_FRAC * foot_h - 1e-6
        o_hi = FOOT_BOX_UP_FRAC * foot_h + 1e-6
    edge = vert = None
    for band in bands:
        if o_lo is not None:
            band = [r for r in band if o_lo <= r[2] <= o_hi]
        if len(band) < _FOOT_BAND_MIN:
            continue
        s_min = min(r[1] for r in band)
        s_max = max(r[1] for r in band)
        o_min = min(r[2] for r in band)
        o_max = max(r[2] for r in band)
        # 「这一侧没有网格」= 鞋在这一段是**开口**的（脚口/袜口朝上），
        # 开口不能当判据：拿它收紧会把骨头压成牙签；拿它放行又会留着穿模。
        # 所以两个方向各自独立判定：只有「轴两侧都摸得到面」的那一段才有投票权。
        if s_min < -0.05 and s_max > 0.05:
            tight = min(abs(s_min), abs(s_max))
            if edge is None or tight < edge:
                edge = tight
        if o_min < -0.05 and o_max > 0.05:
            room = min(abs(o_min), abs(o_max))
            if vert is None or room < vert:
                vert = room
    if edge is None:
        return None
    if heelbox:
        return (2.0 * edge, foot_h)
    if vert is None:
        return None
    return (2.0 * edge, 2.0 * vert)


def _foot_profile(positions, scale: float, ankle, ball, faces=None,
                  label: str = "脚段"):
    """从网格量这只鞋：鞋尖坐标、趾长、Toe0 该有多宽多高、Foot 该有多宽多高、踝→鞋尖全长。

    量不出来分两种，下场不同，话也要分开说：
      - **抛 PMXError**（骨的几何坏了）：踝与脚掌水平重合 ⇒ 脚趾方向根本推不出来，而
        窗口、鞋尖、截面全都依赖这根轴，硬写出来的数会当真把 Toe0 撑成一根大骨头
        （#40 那一类），宁可不导出。
      - **返回 None**（这只脚前面没有网格）：窗口全空，或窗口里的点没有一个越过脚掌
        平面。样例夹具 `make_sample.py` 只有 4 个躯干顶点、赤足模型的网格在 つま先 收口，
        都走这一支；调用方退回普通段，并在表里写一行 `#` 说明。这个「退回」不是静默：
        `verdicts.foot_pairs_with_mesh` 会用权重表（导出的**输入**，与 mode 列无关）再问
        一遍这双脚有没有蒙皮顶点，蒙着顶点却退化了 = 判据侧当场抛。
        旧写法把两种都悄悄退成 `mode=link`，于是脚盒 / 接趾 / 复摆整族验收凭空消失，
        导出成功、日志全绿、脚骨戳穿鞋面（用户：「脚的骨骼并没有包在脚的里面，实际上
        脚穿模出来了」）。

    方向必须由骨头自己推出来。手性修正之后这个坐标系里脚掌指向 **-y**，
    而上一版按「+y 最大」取鞋尖，量到的其实是脚后跟那一片：
    于是趾长变成 12.28cm（比整只脚 11.23cm 还长），Max 端照着它把
    Bip001 L/R Toe0 撑成一根大骨头（用户反馈：Toe0 太大了）。

    窗口全部由踝/脚掌的相对位置导出，不写死某个模型的绝对坐标：
      - 贴地上沿：z ≤ max(脚掌 z + 4, 脚掌 z + 0.8×(踝 z − 脚掌 z))（#140：
        旧写死 +4 是照「趾关节贴在鞋尖」调的，关节挪到鞋长中段后鞋面高到 7cm，
        窗口却仍砍在 4cm ⇒ toe_h 恒等于 4.095、骨盒只剩薄薄一层鞋底）
      - 横向：离脚掌 x ≤ 6（两脚相距约 12，不会串到对面）
      - 纵向：从踝往后 2cm 起，往后（裙摆、飘带）一律丢掉

    宽高口径（用户第二轮反馈：「我要整块骨头呆在鞋子里」）：
    旧口径照抄「Foot 末端那一片的截面」，香菱量到 5.86×4.19 —— 比 3.57 的趾长还高，
    侧视里盒子上沿戳出鞋面。骨盒是绕 pivot 中心对称的，所以「在鞋里」等价于
    四个方向都留得下：高 = 2 × min(最低鞋面 − pivot_z, pivot_z − 最高鞋底)，
    宽 = 2 × min(pivot_x − 最内侧, 最外侧 − pivot_x)。
    宽度按「整段跨度上的最外侧」算（正视轮廓包含即可）：鞋头向前收窄且向外偏，
    一根笔直的骨头不可能在每个截面上都不出侧沿，逐截面取紧会让趾骨退化成牙签。
    没有面可切时退回旧口径，只可能偏大、不会写 0。

    foot_w / foot_h（#37）走的是**同一句话的 Foot 段版本**：判据交给 _foot_link_box
    沿踝→脚掌轴逐段量，因为骨盒是绕「长度轴」而非绕 pivot 对称的整根柱子。
    上一版这两列根本不存在 —— widths.tsv 只有手，脚趾例程只管 Toe0，
    Foot 的截面从没人量过，于是 8.00×6.61 的盒子撑在 3.93×1.53 的鞋里（用户：
    「你的脚的骨骼也并没有包在脚的里面，实际上脚穿模出来了」）。
    """
    ax, ay, az = ankle
    bx, by, bz = ball
    dx, dy = bx - ax, by - ay
    horiz = (dx * dx + dy * dy) ** 0.5
    if horiz < 1e-5:
        raise PMXError(
            f"{label} 量不出鞋：踝与脚掌在水平面上重合（踝 {ankle} / 脚掌 {ball}）。"
            "脚趾朝向推不出来，而 Max 端必须照这根轴摆骨盒 —— 宁可不导出，"
            "也不要按 Biped 默认朝向猜一只脚出来")
    ux, uy = dx / horiz, dy / horiz
    x_half = 6.0
    z_cap = max(bz + 4.0, bz + TOE_CAP_ANKLE_FRAC * max(0.0, az - bz))
    pts = [convert_vec(p, scale) for p in positions]
    band = []
    for (x, y, z) in pts:
        if z > z_cap or abs(x - bx) > x_half:
            continue
        # t：沿脚趾方向离脚掌平面的有符号距离（>0 朝鞋尖）
        t = (x - bx) * ux + (y - by) * uy
        if t < -(horiz + 2.0):
            continue
        band.append((x, y, z, t))
    if not band:
        return None
    tmax = max(item[3] for item in band)
    if tmax <= 1e-5:
        # 窗口里有网格，但最靠前的一点也不在脚掌平面之前：这只脚前面没有网格
        # （赤足模型、网格正好在 つま先 收口，都是这种）。趾长只能是 0，而
        # 「趾长 = 0 的骨盒目标」进了判据表就是要求骨头缩成 0 —— 交给调用方退化，
        # 由 `verdicts.foot_pairs_with_mesh` 拿权重表判断这算不算事故。
        return None
    front = [item for item in band if item[3] > tmax - 1.0]
    n = len(front)
    tip = (sum(i[0] for i in front) / n,
           sum(i[1] for i in front) / n,
           sum(i[2] for i in front) / n)
    # 旧口径：脚掌平面（= Foot link 末端）那一片的截面，同时当作量不到时的兜底
    near = [item for item in band if abs(item[3]) <= 1.0] or front
    xs = [i[0] for i in near]
    zs = [i[2] for i in near]
    band_w = max(xs) - min(xs)
    band_h = max(zs) - min(zs)
    toe_w, toe_h = band_w, band_h
    if faces and tmax > 1e-5:
        secs = _toe_sections(pts, faces, ball, ux, uy, tmax * _TOE_FRONT,
                             x_half, z_cap, _TOE_PLANES)
        if secs:
            head_up = min(s[4] for s in secs) - bz
            head_down = bz - max(s[3] for s in secs)
            head_left = bx - min(s[1] for s in secs)
            head_right = max(s[2] for s in secs) - bx
            h = 2.0 * min(head_up, head_down)
            w = 2.0 * min(head_left, head_right)
            if h > 0.05:
                toe_h = min(h, band_h)
            if w > 0.05:
                toe_w = min(w, band_w)
    link = _foot_link_box(pts, ankle, ball)
    if link is None:
        # 量不到（缺面/窗口太稀）：退回「脚掌那一片的截面」并按段长封顶 60%。
        # 兜底只可能偏细，不会像上一版那样留着一根 8cm 粗的骨盒戳穿鞋面。
        cap = (horiz * horiz + (bz - az) ** 2) ** 0.5 * 0.6
        foot_w, foot_h = min(band_w, cap), min(band_h, cap)
    else:
        foot_w, foot_h = link
    return {
        "tip": tip,
        "toe_len": tmax,
        "toe_w": toe_w,
        "toe_h": toe_h,
        "foot_w": foot_w,
        "foot_h": foot_h,
        "foot_len": ((ax - tip[0]) ** 2 + (ay - tip[1]) ** 2
                     + (az - tip[2]) ** 2) ** 0.5,
    }


def _limb_rows(model, scale: float, old_to_new: dict, heads: list,
               biped_mapping: dict | None, positions=None, faces=None):
    """生成「Biped 肢段长度表」行，供 Max 端做闭环缩放。

    为什么不在 MaxScript 里现算：PMX 的 tail 会指向扭转骨（右腕 -> 右腕捩 ->
    右ひじ），按 tail 当段长会把上臂缩到 60%。真实段长必须取「本关节 head ->
    Biped 链下一节的 head」。这张表把这个事实固化在 Python 侧（可单测、可核对），
    Max 端只做「读长度 + 闭环缩放」，不再自己解析骨名/索引。

    列：parent_target  child_target  desired_len  mmd_parent  mmd_child
    返回 (含表头的正文行, 数据行数)。
    """
    # 注意（用户第五次反馈后修正）：脚**不再特殊处理**。
    # MMD 的 つま先 本来就在「脚掌/ball」上（本模型实测：踝 y=0.23 z=6.26，
    # つま先 y=6.39 z=-1.46，约为脚长的 40%），正好对应 Biped 的 Toe0 关节。
    # 之前为了「脚趾落在鞋尖」强行把 Toe0 摆到鞋尖（y=13.5），结果整个脚掌被拉成
    # 一根长骨、Toe0 变成鞋尖外面一丁点的小骨（用户截图：脚掌中间没有关节）。
    # 现在按普通段处理：Foot 长度 = 踝→つま先，Toe0 精确落在脚掌关节上，
    # 之后由 Biped 自己的趾骨从该点向前延伸 —— 这才是正常的脚掌两段结构。
    cols = ("parent_target", "child_target", "desired_len",
            "mmd_parent", "mmd_child", "child_x", "child_y", "child_z", "mode",
            "foot_len", "toe_len", "tip_x", "tip_y", "tip_z",
            "toe_w", "toe_h", "foot_w", "foot_h")
    lines = [_header_line(cols),
             "# 第 10~18 列只有 mode=foot 的行才有：踝→鞋尖 / 脚掌→鞋尖(沿趾) / "
             "鞋尖x / 鞋尖y / 鞋尖z / 脚尖处宽 / 脚尖处高 / 踝处足背宽 / 踝处足背高"]
    if not biped_mapping:
        return lines, 0

    allowed = _limb_segments_allowed()
    target_to_old: dict = {}
    for old, target in biped_mapping.items():
        if target:
            target_to_old.setdefault(target, old)

    # ---- 先算「躯干中轴链」中实际存在的节点，相邻两级配对 ----
    # 为什么要拟合躯干链：Biped 每一节的可见长度 = 父骨 link 长度。
    # 若 link 长度和「两节点实际距离」不一致，画面上就会出现空档或重叠
    # （用户截图：Pelvis 与 Spine 之间裂开一条缝，胸腔看着错位）。
    center_present = [t for t in CENTER_NODE_ORDER if t in target_to_old]
    center_pairs = list(zip(center_present, center_present[1:]))

    overrides = semantic.limb_mode_overrides()
    foot_pairs = {p for p, m in overrides.items() if m == "foot"}
    # 大腿段（#36）：髋与踝都钉在 PMX 目标上之后，膝盖唯一的自由度就是这条腿绕
    # 「髋→踝」轴的扭转角，而 Max 端没有任何一处设过它 —— 于是两膝各自被 Biped 的
    # 默认膝轨道拽向中线（用户：「两个小腿的朝向有问题内八了，实际上并没有」）。
    # 标成 mode=leg 交给 Max 端做闭环；这里只负责**指出是哪两段**，不写角度。

    def emit(parent_target, child_target, po, co):
        """写一行肢段：长度 = 两个关节 head 的真实距离，第 6~8 列写子关节（child head）
        在 Max 坐标系里的位置，第 9 列是 mode（普通段 = link）。

        每行都必须带子关节位置，普通段也不例外：Max 端要把「骨盘三节子段」的目标长度
        按 COM 锚点（左右腿根中点）重算，锚点正是从这两行的 child 位置算出来的。
        （曾经只有脚段补齐这 4 列，其余行 5 列 —— 于是 mmd2maxReadLimbs 里的
        childPos 恒为 undefined，骨盘改写逻辑静默失效，髋宽照旧偏 2.23cm。）
        位置也不能留空列：MAXScript 的 filterString 会丢掉空字段，索引会整体错位。

        脚段（Foot→Toe0）额外多给 9 列：mode=foot / 踝→鞋尖 / 趾长 / 鞋尖坐标 /
        脚尖处宽 / 脚尖处高 / 踝处足背宽 / 踝处足背高。为什么要单独给：MMD 只提供
        足首(踝) 和 つま先(脚掌 ball)，「鞋有多长、脚趾该多粗」都得从网格量。
        用户的两条要求分别是「pivot 接在 Foot 的 link 末端」= ballPos、
        「长度只占贴着脚尖的一小段」= toe_len、
        「整块骨头呆在鞋子里」= 第 15/16 列（趾段）与第 17/18 列（Foot 段，
        同一句判据的两半：前者量脚尖那一小段，后者沿踝→脚掌轴逐段量）。
        全部在这里量好写进表里，Max 端只照着做闭环缩放，不再自己猜。

        脚段量不到鞋，分两种下场（细节写在 `_foot_profile` 的文档里）：
          - 踝与脚掌水平重合（脚趾方向推不出来）= **抛 PMXError 中止导出**。
          - 这只脚前面没有网格 = 退回普通段，但在表里写一行 `#` 说明，且这个「退回」由
            判据侧独立复核（`verdicts.foot_pairs_with_mesh` 拿 weights/bones 问这双脚
            有没有蒙皮顶点；有 ⇒ 抛）。样例夹具 `make_sample.py` 只有 4 个躯干顶点，
            走的正是这一支，所以不能一刀切都抛。
            旧写法把两种都退成 `mode=link` 且不吭声：`verdicts.box_targets` 与脚盒 /
            接趾 / 复摆那些行只从 `mode=foot` 的行取尺，退化之后**没有一行判据会红**，
            日志照样全绿，画面上是一根按 Biped 默认粗细撑进鞋里的脚骨
            （用户：「你的脚的骨骼也并没有包在脚的里面，实际上脚穿模出来了」）。

        大腿段（Thigh→Calf）标 mode=leg：这一行的 child 位置就是 PMX 的膝，
        Max 端拿它和「髋→踝」轴比出膝盖的扭转残差（见 mmd2maxLegRoll）。
        这里只给标记不给角度 —— 角度是 Max 里**摆完之后**才存在的量。
        """
        ph = heads[old_to_new[po]]
        ch = heads[old_to_new[co]]
        desired = ((ph[0] - ch[0]) ** 2 + (ph[1] - ch[1]) ** 2
                   + (ph[2] - ch[2]) ** 2) ** 0.5
        if desired <= 1e-5:
            return 0
        mode = overrides.get((parent_target, child_target), "link")
        if (parent_target, child_target) in foot_pairs and positions:
            shoe = _foot_profile(positions, scale, ph, ch, faces,
                                 label=f"{parent_target} -> {child_target}")
            if shoe is not None:
                tip = shoe["tip"]
                lines.append(
                    f"{parent_target}\t{child_target}\t{desired:.6f}\t"
                    f"{model.bones[po].name}\t{model.bones[co].name}\t"
                    f"{ch[0]:.6f}\t{ch[1]:.6f}\t{ch[2]:.6f}\t{mode}\t"
                    f"{shoe['foot_len']:.6f}\t{shoe['toe_len']:.6f}\t"
                    f"{tip[0]:.6f}\t{tip[1]:.6f}\t{tip[2]:.6f}\t"
                    f"{shoe['toe_w']:.6f}\t{shoe['toe_h']:.6f}\t"
                    f"{shoe['foot_w']:.6f}\t{shoe['foot_h']:.6f}")
                return 1
            # 这只脚上没有网格顶点：9~18 列无处可量，退回普通段，但**在表里留一行说明**。
            # 静悄悄写 mode=link 才是上一版的错 —— 脚链的骨盒 / 接趾 / 复摆判据整族没了尺，
            # 而没有任何一处能看出表少了东西。留了这行，`verdicts` 再拿权重表复核一次：
            # 「Foot/Toe0 都蒙着顶点却走到这里」= 抛，见 `foot_pairs_with_mesh`。
            lines.append(f"# 脚段退化：{parent_target} -> {child_target} 的脚掌窗口里没有"
                         "越向前方的网格顶点，第 9 列写 link、第 10~18 列不写"
                         "（脚链骨盒判据没有尺）")
            mode = "link"
        elif (parent_target, child_target) in foot_pairs:
            # 一份网格顶点也没有（`positions` 为空）：同上，问不出「有没有鞋」。
            mode = "link"
        lines.append(f"{parent_target}\t{child_target}\t{desired:.6f}\t"
                     f"{model.bones[po].name}\t{model.bones[co].name}\t"
                     f"{ch[0]:.6f}\t{ch[1]:.6f}\t{ch[2]:.6f}\t{mode}")
        return 1

    count = 0
    for parent_target, child_target in semantic.biped_chain_pairs():
        if (parent_target, child_target) not in allowed:
            continue
        po = target_to_old.get(parent_target)
        co = target_to_old.get(child_target)
        if po is None or co is None:
            continue
        if po not in old_to_new or co not in old_to_new:
            continue
        count += emit(parent_target, child_target, po, co)

    # ---- 躯干中轴链（Pelvis->Spine->Spine1->Spine2->Neck->Head）----
    for parent_target, child_target in center_pairs:
        po = target_to_old.get(parent_target)
        co = target_to_old.get(child_target)
        if po is None or co is None:
            continue
        count += emit(parent_target, child_target, po, co)

    return lines, count


def _accessory_parent_column(kept, old_to_new, parents, heads, biped_mapping,
                             policy: str):
    """计算「附属骨链根」在 Max 里应挂到哪个 Biped 节点，返回逐骨的新父序号列表
    （-1 表示沿用 bones.tsv 第 3 列的默认父级）。

    背景（用户实测反馈）：裙摆等附属骨在 MMD 里 parent = 下半身，而
    下半身 被映射成 Bip001 Spine，于是整条裙摆挂在「腰以下一节」。
    用户认为结构上应贴在几何最近的 Biped 骨上。

    两种策略各有代价，所以做成可选：
      - "nearest"（默认）：挂到「离该骨 head 最近的 Biped 节点」。用户要求：
        裙摆根部在腰线上，就贴腰线那根骨（本模型实测 = Bip001 Spine1）。
      - "hierarchy"：完全跟随 MMD 层级（裙摆跟『下半身』走）。
        动画语义上更贴近 MMD 原作者意图，但视觉贴合度差。
    """
    count = len(kept)
    column = [-1] * count
    if policy != "nearest" or not biped_mapping:
        return column

    candidates = []
    for old, target in biped_mapping.items():
        if target and old in old_to_new:
            ni = old_to_new[old]
            candidates.append((ni, heads[ni], target))
    if not candidates:
        return column

    center_candidates = [c for c in candidates if c[2] in CENTER_NODE_ORDER]

    for ni in range(count):
        old = kept[ni]
        if biped_mapping.get(old):
            # 自身就是 Biped 骨，不参与
            continue
        parent_new = parents[ni]
        if parent_new < 0 or parent_new >= count:
            continue
        parent_target = biped_mapping.get(kept[parent_new])
        if not parent_target:
            # 父级不是 Biped 骨（还在附属骨链内部），不动
            continue
        # 原本挂在躯干上的附属骨（裙摆/头发）只在躯干链里选最近节点：
        # 否则侧面的裙根会因为「离手臂更近」而被挂到 UpperArm 上，
        # 结果裙角跟着手臂甩 —— 比错一节还糟糕。
        pool = center_candidates if (parent_target in CENTER_NODE_ORDER
                                     and center_candidates) else candidates
        hx, hy, hz = heads[ni]
        best, best_d = -1, float("inf")
        for cni, ch, _t in pool:
            if cni == ni:
                continue
            d = (hx - ch[0]) ** 2 + (hy - ch[1]) ** 2 + (hz - ch[2]) ** 2
            if d < best_d:
                best_d, best = d, cni
        # 只在「确实换了父级」时才写，避免把无操作也计入改挂数量
        if best >= 0 and best != parent_new:
            column[ni] = best
    return column


def _bone_node_names(bone_names, kept, biped_mapping):
    """每根**保留骨**在 Max 场景里的节点名，按新骨序号索引（口径见 `semantic.node_name`）。

    重名的那些返回空串：Max 给重名节点自动加后缀（`名前001`），而「哪个副本拿到裸名」
    取决于建序 —— 我们没量过 Max 的这套规则，拿它当期望值就是一把坏仪器。
    所以这两行**不喂给名字尺**：`verdicts.PhysicsExpectation.named_rows` 少数这几根，
    `rigid_parented` 仍然全量卡住「挂没挂上」，只是「挂的是不是这一根」在重名模型上
    退成不量 —— 退得看得见（汇总行 `名字不计=N 根`），不假装量到了。
    """
    names = []
    for index in range(len(bone_names)):
        target = biped_mapping.get(kept[index], "") if biped_mapping else ""
        names.append(semantic.node_name(target, bone_names[index]))
    seen: dict = {}
    for n in names:
        seen[n] = seen.get(n, 0) + 1
    return [n if seen[n] == 1 else "" for n in names]


def _physics_rows(model, scale: float, old_to_new: dict, kept_set: set,
                  bone_node_names=(), bone_redirect: dict | None = None):
    """生成刚体 & 关节数据行，供 Max 端构建物理代理（MassFX / 约束）。

    刚体引用的 PMX 骨序号会被换算成导出后的新序号（找不到则 -1，挂场景根）。
    位置按坐标变换；旋转**同时**给出原始 MMD 弧度欧拉（`rigid_rot_*`，只作诊断）
    与 Max 节点可直接写的度数（`rigid_rot_max_*`，见 coords.rigid_rotation_max）。

    尺寸必须和位置用同一个 scale：PMX 的长度单位与坐标单位相同，早期版本只给位置
    乘了 scale、尺寸没乘，导致刚体代理相对骨架大 1/scale 倍（scale=0.08 时大 12.5 倍）。
    shape 语义（PMX 规范）：
        0 = 球（size = 半径）
        1 = 方块（size = 三个方向的**半边长**）
        2 = 胶囊（size = 半径、高度）
    返回正文行。

    一个文件两种记录：RIGID 行的列名是表头行，JOINT 行的列名写在紧随其后的 `@JOINT`
    行里（见 _kind_header）—— 同一列号在两种记录里含义不同（第 4 列 RIGID 是 name_en、
    JOINT 是 joint_type），硬合成一行就会有一边读错列。

    `rigid_bone_node`（#126）是这一格 `rigid_bone` 那个号**该对应到的场景节点名**，
    由 `_bone_node_names` 按 `semantic.node_name` 算出：Max 端回读 `proxy.parent.name`
    与它比，才有「挂上骨了，但挂的是不是这一根」这条尺。名字在这里算而不是让 Max 去
    bones.tsv 里查，是因为「号 → 名字」这一步正是我们要独立核的那一段。
    """
    cols = ("row_kind", "rigid_index", "rigid_name", "rigid_name_en",
            "rigid_bone", "rigid_group", "rigid_nocollide_mask", "rigid_shape",
            "rigid_size_x", "rigid_size_y", "rigid_size_z",
            "rigid_pos_x", "rigid_pos_y", "rigid_pos_z",
            "rigid_rot_x", "rigid_rot_y", "rigid_rot_z",
            "rigid_rot_max_x", "rigid_rot_max_y", "rigid_rot_max_z",
            "rigid_mass", "rigid_move_damping", "rigid_rotation_damping",
            "rigid_repulsion", "rigid_friction", "rigid_physics_mode",
            "rigid_bone_node")
    joint_cols = ("joint_index", "joint_name", "joint_type",
                  "joint_rigid_a", "joint_rigid_b",
                  "joint_pos_x", "joint_pos_y", "joint_pos_z",
                  "joint_rot_x", "joint_rot_y", "joint_rot_z",
                  "joint_pos_min_x", "joint_pos_min_y", "joint_pos_min_z",
                  "joint_pos_max_x", "joint_pos_max_y", "joint_pos_max_z",
                  "joint_rot_min_x", "joint_rot_min_y", "joint_rot_min_z",
                  "joint_rot_max_x", "joint_rot_max_y", "joint_rot_max_z",
                  "joint_pos_spring_x", "joint_pos_spring_y", "joint_pos_spring_z",
                  "joint_rot_spring_x", "joint_rot_spring_y", "joint_rot_spring_z")
    lines: list = [_header_line(cols), _kind_header("JOINT", joint_cols)]
    for ri, rb in enumerate(model.rigid_bodies):
        # 并行变形骨（D 骨）链已删：刚体也要跟着权重改落到同位的人形骨，
        # 否则 `_to_new_skeleton` 会沿父链找到「下半身」= Bip001 Spine，
        # 于是腿上的 16 个刚体代理全挂在腰上（林尼实测）。
        raw_bone = (bone_redirect or {}).get(rb.bone_index, rb.bone_index)
        bone_new = _to_new_skeleton(model, old_to_new, kept_set, raw_bone)
        px, py, pz = convert_vec(rb.position, scale)
        # 尺寸与位置必须用同一 scale（早期版本漏乘，代理会比骨架大 1/scale 倍）
        sx, sy, sz = (rb.size[0] * scale, rb.size[1] * scale, rb.size[2] * scale)
        rx, ry, rz = rb.rotation
        # Max 端只负责把三个**度数**写进 `node.rotation`；手性共轭与节点口径
        # 反转全在 coords.rigid_rotation_max 里算（那里的注释记了两条实测依据）。
        mx, my, mz = rigid_rotation_max(rb.rotation)
        # 期望场景节点名（#126）：Max 那边回读 `proxy.parent.name` 与这一格比。
        # 表里只有 `rigid_bone` 这个**号**，而「号 → 场景里那根骨」中间隔着 Max 的
        # boneNodes 数组序；名字是这两端唯一能各算各的公共量。
        node_name = ""
        if 0 <= bone_new < len(bone_node_names):
            node_name = bone_node_names[bone_new]
        lines.append(
            "RIGID\t" + "\t".join([
                str(ri), rb.name, rb.name_en, str(bone_new),
                str(rb.group), str(rb.nocollide_mask), str(rb.shape),
                fmt(sx), fmt(sy), fmt(sz),
                fmt(px), fmt(py), fmt(pz),
                fmt(rx), fmt(ry), fmt(rz),
                fmt(mx), fmt(my), fmt(mz),
                fmt(rb.mass), fmt(rb.move_damping), fmt(rb.rotation_damping),
                fmt(rb.repulsion), fmt(rb.friction), str(rb.physics_mode),
                node_name,
            ]))
    for ji, jt in enumerate(model.joints):
        pos = convert_vec(jt.position, scale)
        rot = jt.rotation
        # 平移限位（pos_min/pos_max）是**同单位的长度**（PMX 厘米），曾经漏乘 scale ——
        # M5 蜕变测试拿 scale=0.08 与 0.16 两次导出对撞抓到的：位置乘了、限位没乘，
        # 于是「允许移动的范围」相对骨架缩小 1/scale 倍。与刚体尺寸那条同族：
        # 凡是长度，必须在同一张表里乘同一个 scale，否则单看一份产物完全自洽。
        # 轴向也要走 convert_vec：Max 端的注释里这两个三元组按 Max 的 x/y/z 读。
        pmin = convert_vec(jt.pos_min, scale)
        pmax = convert_vec(jt.pos_max, scale)
        lines.append(
            "JOINT\t" + "\t".join([
                str(ji), jt.name, str(jt.joint_type),
                str(jt.rigid_a), str(jt.rigid_b),
                fmt(pos[0]), fmt(pos[1]), fmt(pos[2]),
                fmt(rot[0]), fmt(rot[1]), fmt(rot[2]),
                fmt(pmin[0]), fmt(pmin[1]), fmt(pmin[2]),
                fmt(pmax[0]), fmt(pmax[1]), fmt(pmax[2]),
                fmt(jt.rot_min[0]), fmt(jt.rot_min[1]), fmt(jt.rot_min[2]),
                fmt(jt.rot_max[0]), fmt(jt.rot_max[1]), fmt(jt.rot_max[2]),
                fmt(jt.pos_spring[0]), fmt(jt.pos_spring[1]), fmt(jt.pos_spring[2]),
                fmt(jt.rot_spring[0]), fmt(jt.rot_spring[1]), fmt(jt.rot_spring[2]),
            ]))
    return lines


def _copy_textures(model, source, outdir, warnings):
    """复制贴图到 <outdir>/textures/，**改用 ASCII 文件名**，返回相对路径。

    为什么必须改名：PMX 的贴图路径几乎都是日文（本模型 `tex\服.png`），
    3ds Max 的 OBJ 导入器按本地代码页(ANSI/GBK)读 MTL，日文路径会乱码 → 贴图丢失，
    模型就是一片灰（用户反馈「你还没贴图呢」）。同时输出相对路径（相对 MTL 所在目录），
    这样整个导出目录可以整体搬走，不依赖绝对路径。
    """
    texdir = outdir / "textures"
    texdir.mkdir(parents=True, exist_ok=True)
    base = source.parent.resolve()
    texture_paths = []
    for index, texture in enumerate(model.textures):
        if not texture:
            texture_paths.append("")
            continue
        rel = texture.replace("\\", "/")
        candidate = (base / rel).resolve()
        # 越界防护：只接受位于模型目录（含子目录）内的贴图
        try:
            candidate.relative_to(base)
        except ValueError:
            warnings.append(f"贴图路径越界，已跳过（不复制模型目录外的文件）：{texture}")
            texture_paths.append("")
            continue
        if not candidate.is_file():
            warnings.append(f"贴图不存在，已跳过：{texture}")
            texture_paths.append("")
            continue
        suffix = candidate.suffix.lower() or ".png"
        ascii_name = f"tex_{index:04d}{suffix}"
        target = texdir / ascii_name
        if not target.is_file():
            shutil.copy2(candidate, target)
        # MTL 里写相对路径（Max 的 OBJ 导入器按 MTL 所在目录解析）
        texture_paths.append(f"textures/{ascii_name}")
    return texture_paths


def _write_lines(path: Path, lines: list) -> None:
    """正文行 -> 文件（每行一个 \\n，末尾也带一个）。空正文写出单个换行，与旧版一致。"""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _header_line(cols) -> str:
    """表头行 = tab 分隔的列名，必须是文件的第 1 行、且不以 # 开头（Max 端按它取列）。

    为什么表头写在正文旁边而不是 export() 里另写一份：列名一旦和取值分处两地，
    加列/换序就是两边各改各的 —— 旧协议里 Max 端只能写 `p[12]`、`item[4]` 这种魔数
    下标，顺序一变就**静默读错列**（physics 早期版本整体错一位，把 name_en 当骨骼、
    把 mask 当形状，就是这么来的）。表头落地后 Max 端按列名取数、缺列直接抛错。
    """
    return "\t".join(cols)


def _kind_header(kind: str, cols) -> str:
    """另一种记录的列名：`@<记录类型>\t列名...`，紧跟在表头行后面。

    为什么需要第二种表头：physics.tsv（RIGID/JOINT）和 morphs.tsv（MORPH/V）一个文件
    里装了两种记录，同一列号在两种记录里含义不同（physics 第 4 列在 RIGID 是 name_en、
    在 JOINT 是 joint_type），一行表头装不下；而把行改成对齐的宽表会动到数值本身。
    `@` 行给出该记录自己的列名，Max 端 mmd2maxReadTable 把它们并进同一张列名表
    （名字各带 rigid_/joint_ 前缀，不会撞车），并且不当成数据行。
    """
    return "@" + kind + "\t" + "\t".join(cols)


def _bone_rows(bone_names, kept, parents, heads, tails, skin_flag, roles,
               biped_mapping, max_parent_col):
    """生成 bones.tsv（表头 + 正文）。

    列：idx 名称 父idx role biped_target hx hy hz tx ty tz skin_flag max_parent
    role / biped_target / max_parent 均为追加列，不破坏旧版 MaxScript 的前 12 列解析。
    max_parent：附属骨链根在 Max 里改挂到哪个 Biped 节点（-1 = 沿用第 3 列的父级）。
    """
    cols = ("idx", "bone_name", "parent", "role", "biped_target",
            "head_x", "head_y", "head_z", "tail_x", "tail_y", "tail_z",
            "skin_flag", "max_parent")
    lines = [_header_line(cols)]
    for index, bone_name in enumerate(bone_names):
        old_index = kept[index]
        hx, hy, hz = heads[index]
        tx, ty, tz = tails[index]
        flag = skin_flag[index] if index < len(skin_flag) else 1
        role = roles.get(old_index, "skeleton") if roles is not None else "skeleton"
        biped_target = biped_mapping.get(old_index, "")
        lines.append(f"{index}\t{bone_name}\t{parents[index]}\t{role}\t"
                     f"{biped_target}\t"
                     f"{fmt(hx)}\t{fmt(hy)}\t{fmt(hz)}\t"
                     f"{fmt(tx)}\t{fmt(ty)}\t{fmt(tz)}\t"
                     f"{flag}\t{max_parent_col[index]}")
    return lines


def _biped_map_rows(kept, bone_names, biped_mapping):
    # 以「骨序号」为键而不是骨名：PMX 骨名是日文，MaxScript 侧若按本地代码页
    # (GBK) 解析会乱码，导致 getNodeByName 找不到节点；用序号可完全规避。
    cols = ("bone_index", "biped_node", "pmx_bone_name")
    lines = [_header_line(cols)]
    for new_index, old_index in enumerate(kept):
        target = biped_mapping.get(old_index)
        if target:
            lines.append(f"{new_index}\t{target}\t{bone_names[new_index]}")
    return lines


# --------------------------------------------------------------------------
# 计算层：参数 -> 全部导出数据（纯函数，不碰磁盘）。
# GUI 的「参数即时预览对照」和 export() 消费同一个 Plan，看到的数字必然一致。
# --------------------------------------------------------------------------

@dataclass
class Plan:
    """一次「参数组合」算出来的全部结果，字段即各 TSV 的正文。"""

    source: Path
    model: object
    scale: float
    min_weight: float
    humanoid_only: bool
    filter_useless: bool
    accessory_parent: str
    keep_guides: bool = True

    # ---- 网格 ----
    positions: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    uvs: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    # 归并 + 剪枝后的最终权重，索引已是「新骨序号」空间
    deform: list = field(default_factory=list)
    # 顶点紧凑化：PMX 原序号 -> 导出序号（-1=未被任何面引用，已丢弃）
    vert_remap: list = field(default_factory=list)
    dropped_verts: int = 0

    # ---- 骨骼 ----
    bone_names: list = field(default_factory=list)
    kept: list = field(default_factory=list)          # 保留的原始 PMX 骨序号
    kept_set: set = field(default_factory=set)
    old_to_new: dict = field(default_factory=dict)
    parents: list = field(default_factory=list)       # 新序号空间的父级
    heads: list = field(default_factory=list)         # 已含 tail 修正 / 颈部 / 躯干摊平
    tails: list = field(default_factory=list)
    roles: dict | None = None
    totals: list = field(default_factory=list)        # 每骨总权重
    skin_flag: list = field(default_factory=list)
    skin_count: int = 0
    max_parent_col: list = field(default_factory=list)
    reparented: int = 0
    neck_shift: float = 0.0

    # ---- Biped 映射 ----
    biped_mapping: dict = field(default_factory=dict)
    biped_missing: list = field(default_factory=list)
    biped_conflicts: list = field(default_factory=list)

    # ---- 各 TSV 正文 ----
    bones_lines: list = field(default_factory=list)
    biped_map_lines: list = field(default_factory=list)
    morph_lines: list = field(default_factory=list)
    physics_lines: list = field(default_factory=list)
    limb_lines: list = field(default_factory=list)
    width_lines: list = field(default_factory=list)
    meta_lines: list = field(default_factory=list)

    # ---- 计数 ----
    morph_count: int = 0              # 实际写入的 Morpher 通道数
    vertex_morphs: int = 0            # 模型里的顶点变形总数（可能 > 100）
    limb_count: int = 0
    width_count: int = 0
    n_skeleton: int = 0
    n_control: int = 0
    n_morph: int = 0
    spine_links: int = 0
    fingers: int = 0
    finger_links: int = 0

    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # 导出时会逐行打印的过程说明


def build_plan(source: Path, scale: float = 0.08, min_weight: float = 0.0,
               humanoid_only: bool = False, filter_useless: bool = True,
               max_skin_bones: int | None = None,
               accessory_parent: str = "nearest",
               keep_guides: bool = True, model=None) -> Plan:
    """把参数换算成 Plan。除读 PMX 外不产生任何副作用。

    model：已解析的 PMXModel，GUI 反复调参时传进来，省掉每次重新解析（大模型 ~2MB）。
    keep_guides：False 时丢掉「什么都不干的辅助骨」（见 semantic.guide_bone_indices）。
    计算顺序与旧版 export() 逐段一致：heads/tails 会被原地修正，顺序换了就对不上。
    """
    if model is None:
        model = load_pmx(source)

    plan = Plan(source=source, model=model, scale=scale, min_weight=min_weight,
                humanoid_only=humanoid_only, filter_useless=filter_useless,
                accessory_parent=accessory_parent, keep_guides=keep_guides)
    warnings = plan.warnings
    warnings.extend(model.warnings)
    if "SDEF" in model.deform_types or "QDEF" in model.deform_types:
        warnings.append("已按近似 LBS 处理 SDEF/QDEF：旋转中心与体积保真会丢失，"
                        "仅适合近似静态姿态。")

    positions = [v.position for v in model.vertices]
    normals = [v.normal for v in model.vertices]
    uvs = [v.uv for v in model.vertices]
    faces = [tuple(face) for face in model.faces]
    deform = [v.weights for v in model.vertices]
    # 顶点紧凑化必须排在所有按顶点序号取数的步骤之前（权重归并、肢段、morph 都吃它），
    # 这样下游只有一套序号，不存在「哪种表用原序号、哪种用新序号」的坑。
    (positions, normals, uvs, faces, deform, vert_remap,
     dropped_verts) = _drop_unreferenced(positions, normals, uvs, faces, deform)
    if dropped_verts:
        plan.notes.append(f"  未被任何面引用的顶点：{dropped_verts} 个，已从 OBJ/权重/表情里丢弃"
                          "（Max 的 OBJ 导入器本来就会丢它们，不重编号会让权重错位）")

    # ---- Biped 映射先算：骨架 tail 修正与肢段表都依赖它 ----
    biped_mapping, biped_missing, biped_conflicts = semantic.biped_targets(model)
    # 同坐标的重复躯干骨（如本模型的「下半身」与「腰」完全同位）要彻底剔除，
    # 否则它仍会作为 Bone 留在场景里，裙摆等附属骨就还挂在它下面。
    dropped_torso = semantic.dropped_torso_indices(model)
    # 脚趾 EX 辅助骨（足先EX/つま先EX 及其子链）与撑满鞋头的 Biped Toe0 重叠，整段删除
    toe_ex = semantic.dropped_toe_ex_indices(model)
    if toe_ex:
        dropped_torso |= toe_ex
        plan.notes.append(f"  脚趾 EX 辅助骨：{len(toe_ex)} 根（含子链）已删除——"
                          "它们与 Biped Toe0 重叠在鞋头里；权重/刚体归并到 つま先、足首")

    # ---- 并行变形骨（D 骨）改绑：必须在权重归并之前，改的是原始 PMX 骨序号 ----
    # 有些模型（林尼）把整条腿的蒙皮放在「左足D/左ひざD/左足首D/左足先EX」这套
    # 与标准腿骨同位的副本上，标准骨一根顶点都没有。旧做法把这些 D 骨当附属骨留着，
    # 于是两条腿的 Skin 挂在 BoneGeometry 上、链根还被改挂到 Pelvis，Biped 腿骨一转
    # 网格纹丝不动。这里把权重与刚体引用改落到同位的人形骨，再把整条副本链删掉。
    twin_redirect, twin_drop = semantic.deform_twin_plan(model, biped_mapping)
    if twin_redirect:
        deform = _redirect_bone_pairs(deform, twin_redirect)
        dropped_torso |= twin_drop
        sample = "、".join(
            f"{model.bones[i].name}→{model.bones[j].name}"
            for i, j in sorted(twin_redirect.items())[:4])
        plan.notes.append(f"  并行变形骨改绑：{len(twin_redirect)} 根（{sample} …）——"
                          "权重/刚体引用落到同位的 Biped 骨，重复链不再进附属骨")

    # ---- 选择保留的骨架骨骼（并剔除无用骨 / 归并权重）----
    # 「辅助骨骼」开关：默认 True = 行为与历轮基线逐字相同。关掉它只丢
    # semantic.guide_bone_indices 判出来的那批骨（既无 Biped 目标、又无蒙皮权重、
    # 又无刚体引用、又不是 IK），权重照样经 _remap_humanoid 归到保留祖先。
    guides = set()
    if not keep_guides:
        guides = semantic.guide_bone_indices(model, biped_mapping)
        if guides:
            dropped_torso |= guides
            names = "、".join(model.bones[i].name for i in sorted(guides)[:12])
            plan.notes.append(f"  辅助骨骼：{len(guides)} 根已丢弃（开关关闭），"
                              f"如 {names}；权重归并到保留祖先")
    roles = None
    if humanoid_only:
        skeleton = _humanoid_skeleton(model, scale, biped_mapping, dropped_torso)
        kept, old_to_new, parents, heads, tails = skeleton
        kept_set = set(kept)
        deform = _remap_humanoid(deform, positions, model, kept_set, old_to_new,
                                 heads, scale)
        bone_names = [model.bones[i].name for i in kept]
        plan.notes.append(f"  人形骨骼：{len(kept)} / 原 {len(model.bones)} 根")
    elif filter_useless:
        roles = semantic.classify_bone_roles(model)
        skeleton = _useful_skeleton(model, scale, roles, biped_mapping, dropped_torso)
        kept, old_to_new, parents, heads, tails = skeleton
        kept_set = set(kept)
        deform = _remap_humanoid(deform, positions, model, kept_set, old_to_new,
                                 heads, scale)
        bone_names = [model.bones[i].name for i in kept]
        n_control = sum(1 for r in roles.values() if r == "control")
        n_morph = sum(1 for r in roles.values() if r == "morph")
        plan.notes.append(f"  有效骨架：{len(kept)} / 原 {len(model.bones)} 根"
                          f"（剔除 control={n_control} morph={n_morph}）")
    else:
        # 「保留全部骨骼」模式：连 control/morph/同坐标躯干骨都不丢，所以这里只减
        # 辅助骨，绝不把 dropped_torso 整套搬进来（那会把这个模式已经定价的行为改掉，
        # 基线里 humanoid/full 的骨数就差好几根）。
        # 但一旦真丢了骨，权重里就会出现已经不存在的骨序号 —— 必须和上面两条走同一条
        # 归并路径，不能假设 old_to_new 是恒等映射。
        kept = [i for i in range(len(model.bones)) if i not in guides]
        kept, old_to_new, parents, heads, tails = _build_skeleton_from_kept(
            model, scale, kept, biped_mapping)
        kept_set = set(kept)
        bone_names = [model.bones[i].name for i in kept]
        if guides:
            deform = _remap_humanoid(deform, positions, model, kept_set, old_to_new,
                                     heads, scale)

    # 脚掌前/后分界（#139）：PMX 的 つま先 骨位不是跨模型常量（香菱在脚长 40%，
    # 林尼却贴着鞋尖 89%），必须从网格量鞋长重新定价。这一步必须排在
    # _split_toe_weights / _bone_rows / _limb_rows / _width_rows **之前** —— 它们
    # 全读同一份 heads，改一处则骨架、权重分界、判据期望一起走，不会分叉。
    plan.notes.extend(_relocate_toe_joints(
        heads, tails, biped_mapping, old_to_new, positions, scale))
    # 眼部 tip 骨（目先/瞳）的 tail 在 MMD 里指得很远，画出来像长箭头；压短它
    _clamp_tip_bone_lengths(bone_names, parents, heads, tails)
    # 胸部骨：PMX 给的是一根竖直牙签，改成指向网格里量出来的乳头
    kept_names_en = [model.bones[old].name_en for old in kept]
    breast_notes = _aim_breast_bones(
        bone_names, kept_names_en, parents, deform, positions, scale, heads, tails)
    for note in breast_notes:
        plan.notes.append(f"  胸部骨指向乳头：{note}")
    # 胸部特写的取景框：跟着量出来的胸骨走，不再按「身高 × 猜的比例」（#70）
    chest_band = _chest_band(bone_names, kept_names_en, heads, tails, positions, scale)
    if chest_band:
        plan.notes.append(_band_note("胸部", chest_band))
    # 颈部：把 首 挪到「颈部网格实测中线」（MMD 的躯干骨在体腔中心、颈骨在颈轴，
    # 两者不同线；按上下骨取中点会把脖子拽到喉咙前面，见 _center_neck 文档）
    neck_shift = _center_neck(heads, biped_mapping, old_to_new, positions, scale)
    if neck_shift > 0.01:
        plan.notes.append(f"  颈部：Neck 移到颈部网格实测中线，前后位移 {neck_shift:.2f}cm")
    # 颈部特写的取景框：排在 _center_neck **之后** —— 量的是「颈骨最后落在哪」，
    # 挪过骨再框才罩得住；先框后挪就等于框里那节骨被自己推出去了（#70 那一类分叉）。
    neck_band = _neck_band(heads, tails, biped_mapping, old_to_new, positions, scale)
    if neck_band:
        plan.notes.append(_band_note("颈部", neck_band))

    # ---- 脚尖改绑：必须排在 skin_flag 统计之前 ----
    # 排后面的话 Toe0 会以「权重合计 0」被 min_weight 剪出蒙皮列表，改了也白改。
    deform, toe_notes = _split_toe_weights(deform, positions, scale, heads, parents,
                                           biped_mapping, old_to_new)
    plan.notes.extend(toe_notes)

    # ---- 先确定要加入 Skin 的骨骼（skin_flag），再据此剪枝 deform ----
    bone_total = len(heads)
    totals = [0.0] * bone_total
    for weights in deform:
        for bone_index, weight in weights:
            if 0 <= bone_index < bone_total:
                totals[bone_index] += weight
    if min_weight > 0.0:
        skin_flag = [1 if totals[i] >= min_weight else 0 for i in range(bone_total)]
    else:
        skin_flag = [1] * bone_total

    if min_weight > 0.0:
        # parents 已是「导出后新序号」空间，直接当父链用（不能用 model.bones[...]）
        deform = _prune_weights(deform, skin_flag, parents)

    skin_count = sum(skin_flag)
    if max_skin_bones is not None and skin_count > max_skin_bones:
        warnings.append(
            f"加入蒙皮的骨骼数 {skin_count} 超过安全上限 {max_skin_bones}，"
            f"单网格 Skin 可能卡死；可提高 min_weight 阈值（或开 humanoid_only）"
            "来减少参与蒙皮的骨骼数。")

    # ---- 附属骨链根改挂（策略由 accessory_parent 决定）----
    max_parent_col = _accessory_parent_column(
        kept, old_to_new, parents, heads, biped_mapping, accessory_parent)
    reparented = sum(1 for v in max_parent_col if v >= 0)
    if reparented:
        plan.notes.append(f"  附属骨改挂（策略={accessory_parent}）：{reparented} 条链根")

    if biped_missing:
        warnings.append("以下必需人形骨骼缺失，若使用 biped 模式会失败："
                        + "、".join(biped_missing))
    if biped_conflicts:
        warnings.append("以下人形骨骼存在语义冲突（多根骨命中同一语义 ID）："
                        + "、".join(biped_conflicts))

    # ---- 各 TSV 正文 ----
    bones_lines = _bone_rows(bone_names, kept, parents, heads, tails, skin_flag,
                             roles, biped_mapping, max_parent_col)
    biped_map_lines = _biped_map_rows(kept, bone_names, biped_mapping)

    morph_lines, morph_count = _morph_rows(model, scale, vert_remap)
    vertex_morphs = sum(1 for m in model.morphs if m.morph_type == 1)
    if vertex_morphs > MAX_MORPH_CHANNELS:
        warnings.append(
            f"顶点变形 {vertex_morphs} 个，超过 3ds Max Morpher 的 100 通道上限，"
            f"只导出前 {MAX_MORPH_CHANNELS} 个（其余在 morphs.tsv 里标 CHANNEL_FULL）。"
            "需要全部表情就得分组：复制一份 .max 再换一批通道。")

    physics_lines = _physics_rows(model, scale, old_to_new, kept_set,
                                  _bone_node_names(bone_names, kept, biped_mapping),
                                  twin_redirect)
    limb_lines, limb_count = _limb_rows(model, scale, old_to_new, heads,
                                        biped_mapping, positions, faces)
    width_lines, width_count = _width_rows(scale, old_to_new, heads, tails,
                                           biped_mapping, positions, deform)

    if roles:
        n_control = sum(1 for r in roles.values() if r == "control")
        n_morph = sum(1 for r in roles.values() if r == "morph")
        n_skeleton = sum(1 for r in roles.values() if r == "skeleton")
    else:
        n_control = n_morph = 0
        n_skeleton = len(model.bones)
    spine_links = semantic.spine_link_count(biped_mapping)
    fingers, finger_links = semantic.finger_layout(biped_mapping)
    meta_lines = [
        _header_line(("key", "value")),
        f"scale\t{scale!r}",
        # #106：判据的强度必须由**产物的事实**决定，不认命令行、也不认目录名。
        # 「这一轮是按哪一组参数导的」只有跟着产物落盘才作数 —— 第 76 轮为了让交付件
        # 不覆盖金标准夹具而把 PMX 改了个名，验收层就整片退成 report，512 全绿其实是
        # 「判据没参与」。scale / min_weight / keep_guides 与 kept_bones、vertex_count
        # 一起构成定价的两把钥匙（哪一具模型 + 哪一组配置）。#119 起下面那两行
        # has_sdef / has_qdef 也进了配置串 —— 它们从 M1b 起一直没有读者，而「没人读」
        # 不等于「不影响读数」：含 SDEF 的一轮从此借不到线性混合那两组的锁。
        # 值一律用 repr：`repr(0.0)` 是 `0.0`，与定价条目里手写的文本同一字形。
        f"min_weight\t{min_weight!r}",
        f"keep_guides\t{'true' if keep_guides else 'false'}",
        f"has_sdef\t{1 if 'SDEF' in model.deform_types else 0}",
        f"has_qdef\t{1 if 'QDEF' in model.deform_types else 0}",
        f"filter_useless\t{1 if filter_useless else 0}",
        f"spine_links\t{spine_links}",
        f"fingers\t{fingers}",
        f"finger_links\t{finger_links}",
        f"kept_bones\t{len(kept)}",
        f"role_skeleton\t{n_skeleton}",
        f"role_control\t{n_control}",
        f"role_morph\t{n_morph}",
        f"morph_count\t{morph_count}",
        f"rigid_count\t{len(model.rigid_bodies)}",
        f"joint_count\t{len(model.joints)}",
        # 顶点数与「被丢弃的未引用顶点数」：Max 端报「顶点数不符」时，
        # 有这两个数才能一眼判断是导入器吞了顶点，还是权重表写错了。
        f"vertex_count\t{len(positions)}",
        f"dropped_verts\t{dropped_verts}",
        # #40/#41：脚链骨盒的轴俯仰口径。导出侧按哪根轴量截面，Max 端就必须按同一根
        # 轴写骨盒，否则「按水平轴量出来的高度」被塞进斜插的盒子里。
        # 这里直接写**字面值** true/false（不写 0/1、不反转）：obj_run 原样搬进
        # MMD2MAX.CFG，MAXScript 按 bool 读，一条链上没有任何取反（M1b 之前
        # FOOT_BOX_FLAT→foot_pitch→FOOTPITCH→`if not` 连反三次，翻开关的人数不清自己翻到哪儿了）。
        f"foot_box_flat\t{'true' if FOOT_BOX_FLAT else 'false'}",
        # #70/#71：两张特写的取景框 + subject 极值，各 12 条（导出单位的绝对坐标）。
        # 空串 = 这具模型认不出那几根骨，Max 那侧退回旧的场景包围盒带。写成 12 个键而不是
        # 三个「x y z」串：CFG 的每个值都是单个数，Max 用 mmd2maxCfgNumOpt 直接验数，
        # 不在脚本里拆字符串。
        *band_meta_pairs("chest", chest_band),
        *band_meta_pairs("neck", neck_band),
    ]

    plan.positions = positions
    plan.normals = normals
    plan.uvs = uvs
    plan.faces = faces
    plan.deform = deform
    plan.vert_remap = vert_remap
    plan.dropped_verts = dropped_verts
    plan.bone_names = bone_names
    plan.kept = kept
    plan.kept_set = kept_set
    plan.old_to_new = old_to_new
    plan.parents = parents
    plan.heads = heads
    plan.tails = tails
    plan.roles = roles
    plan.totals = totals
    plan.skin_flag = skin_flag
    plan.skin_count = skin_count
    plan.max_parent_col = max_parent_col
    plan.reparented = reparented
    plan.neck_shift = neck_shift
    plan.biped_mapping = biped_mapping
    plan.biped_missing = biped_missing
    plan.biped_conflicts = biped_conflicts
    plan.bones_lines = bones_lines
    plan.biped_map_lines = biped_map_lines
    plan.morph_lines = morph_lines
    plan.morph_count = morph_count
    plan.vertex_morphs = vertex_morphs
    plan.physics_lines = physics_lines
    plan.limb_lines = limb_lines
    plan.limb_count = limb_count
    plan.width_lines = width_lines
    plan.width_count = width_count
    plan.meta_lines = meta_lines
    plan.n_skeleton = n_skeleton
    plan.n_control = n_control
    plan.n_morph = n_morph
    plan.spine_links = spine_links
    plan.fingers = fingers
    plan.finger_links = finger_links
    return plan


def export(source: Path, outdir: Path, plan: Plan, obj_y_up: bool = True) -> dict:
    """把 Plan 落盘：OBJ / MTL / 贴图 + 全部 TSV。

    参数（scale / 骨架模式 / min_weight / 附属骨策略）全部在 `build_plan()` 里，
    这里只管写文件。`obj_y_up`：True 时 OBJ 写 Y-up 数值（`coords.obj_vec`），交给
    Max 的 OBJ 导入器转成 Z-up；骨骼数据始终是 Z-up（直接在 Max 里创建）。
    两条路径的终点相同。

    产出：
        <name>.obj/.mtl           网格与材质
        <name>_bones.tsv          骨骼（role / biped_target / max_parent 为追加列）
        <name>_biped_map.tsv      新骨序号 -> Biped 节点名
        <name>_weights.tsv        逐顶点蒙皮权重
        <name>_morphs.tsv         顶点变形 → Morpher 通道
        <name>_physics.tsv        刚体 & 关节
        <name>_limbs.tsv          Biped 肢段长度表
        <name>_widths.tsv         Biped 手部粗细表
        <name>_meta.tsv           给 Max 端的数字汇总
    """
    scale = plan.scale
    model = plan.model
    warnings = plan.warnings
    outdir.mkdir(parents=True, exist_ok=True)

    for note in plan.notes:
        print(note)

    chunk = 20000
    workers = 4

    # ---- 网格 / 权重文本（deform 已由 build_plan 定稿）----
    with ProcessPoolExecutor(max_workers=workers) as pool:
        v_lines = _collect(pool, _worker_vertices,
                           [(s, c, scale, obj_y_up)
                            for s, c in _chunked(plan.positions, chunk)],
                           len(plan.positions))
        n_lines = _collect(pool, _worker_normals,
                           [(s, c, scale, obj_y_up)
                            for s, c in _chunked(plan.normals, chunk)],
                           len(plan.normals))
        t_lines = _collect(pool, _worker_uvs,
                           [(s, c) for s, c in _chunked(plan.uvs, chunk)], len(plan.uvs))
        f_lines = _collect(pool, _worker_faces,
                           [(s, c) for s, c in _chunked(plan.faces, chunk)],
                           len(plan.faces))
        w_lines = _collect(pool, _worker_weights,
                           [(s, c) for s, c in _chunked(plan.deform, chunk)],
                           len(plan.deform))

    # 材质 -> 面区间
    face_ranges = []
    cursor = 0
    for material in model.materials:
        face_count = material.index_count // 3
        face_ranges.append((cursor, cursor + face_count))
        cursor += face_count

    # ---- 贴图 ----
    texture_paths = _copy_textures(model, source, outdir, warnings)

    # ---- OBJ ----
    obj_path = outdir / f"{source.stem}.obj"
    mtl_name = f"{source.stem}.mtl"
    with obj_path.open("w", encoding="utf-8") as stream:
        stream.write(f"mtllib {mtl_name}\n")
        stream.write("\n".join(v_lines) + "\n")
        stream.write("\n".join(t_lines) + "\n")
        stream.write("\n".join(n_lines) + "\n")
        for index, material in enumerate(model.materials):
            start, end = face_ranges[index]
            stream.write(f"usemtl mmdmat{index}\n")
            stream.write("\n".join(f_lines[start:end]) + "\n")

    # ---- MTL（含透明度 / 高光，便于材质预览）----
    with (outdir / mtl_name).open("w", encoding="utf-8") as stream:
        for index, material in enumerate(model.materials):
            stream.write(f"newmtl mmdmat{index}\n")
            d = material.diffuse
            stream.write(f"Kd {d[0]:.4f} {d[1]:.4f} {d[2]:.4f}\n")
            if d[3] < 1.0:
                stream.write(f"d {d[3]:.4f}\n")
            s = material.specular
            stream.write(f"Ks {s[0]:.4f} {s[1]:.4f} {s[2]:.4f}\n")
            stream.write(f"Ns {material.specular_power:.4f}\n")
            texture = texture_paths[material.texture_index] \
                if 0 <= material.texture_index < len(texture_paths) else ""
            if texture:
                safe = texture.replace("\\", "/")
                stream.write(f"map_Kd {safe}\n")
            stream.write("\n")

    # ---- 骨骼 / Biped 映射表：正文由 build_plan 生成，这里只负责落盘 ----
    # biped_map 用「骨序号」而不是骨名做键，Max 端按序号查节点，规避 GBK 乱码。
    bones_path = outdir / f"{source.stem}_bones.tsv"
    _write_lines(bones_path, plan.bones_lines)

    biped_map_path = outdir / f"{source.stem}_biped_map.tsv"
    _write_lines(biped_map_path, plan.biped_map_lines)

    # ---- 顶点变形（表情）→ Morpher 通道数据 ----
    morphs_path = outdir / f"{source.stem}_morphs.tsv"
    _write_lines(morphs_path, plan.morph_lines)
    if plan.morph_count:
        print(f"  顶点 morph（Morpher 通道）：{plan.morph_count} 个")
    if plan.vertex_morphs > MAX_MORPH_CHANNELS:
        print(f"  警告：表情 {plan.vertex_morphs} 个 > Morpher 上限 100，已截断")

    # ---- 物理刚体 & 关节 ----
    physics_path = outdir / f"{source.stem}_physics.tsv"
    _write_lines(physics_path, plan.physics_lines)
    if model.rigid_bodies:
        print(f"  刚体 {len(model.rigid_bodies)} / 关节 {len(model.joints)} 已导出")

    # ---- Biped 肢段长度表（Max 端闭环缩放的唯一事实来源）----
    limbs_path = outdir / f"{source.stem}_limbs.tsv"
    _write_lines(limbs_path, plan.limb_lines)
    if plan.limb_count:
        print(f"  Biped 肢段表：{plan.limb_count} 段")

    # ---- 手部粗细表（Biped 默认手指很粗，套在网格手上明显偏大）----
    widths_path = outdir / f"{source.stem}_widths.tsv"
    _write_lines(widths_path, plan.width_lines)
    if plan.width_count:
        print(f"  Biped 手部粗细：{plan.width_count} 根")

    # ---- 权重 ----
    weights_path = outdir / f"{source.stem}_weights.tsv"
    with weights_path.open("w", encoding="utf-8") as stream:
        # 表头贴着正文写：第 2 列往后的每一列是一个 `骨序号:权重` 对，列数随顶点变化，
        # 所以只有前两列有名字（Max 端从第 2 列开始成对取，见 mmd2maxApplySkin）。
        stream.write(_header_line(("vertex_index", "bone_weight_pairs")) + "\n")
        empty = 0
        for index, line in enumerate(w_lines):
            if not line:
                empty += 1
            stream.write(f"{index}\t{line}\n")
        if empty:
            warnings.append(f"{empty} 个顶点导出为空权重行（兜底后仍无法映射）。")
            raise PMXError(f"导出发现 {empty} 个空权重顶点，已中止以避免静默丢失权重。")

    # ---- 元数据（装配口径见 MMD2MAX.CFG：obj_run.py 按这份 meta 生成）----
    meta_path = outdir / f"{source.stem}_meta.tsv"
    _write_lines(meta_path, plan.meta_lines)
    if "SDEF" in model.deform_types or "QDEF" in model.deform_types:
        # 诚实提示：Max 端那段「按 has_sdef 开双四元数混合」在 M1b 删掉了 —— 跑过的
        # 五个模型全是 has_sdef=0，那段从没执行过，留着只是看着安心。真遇到 SDEF 模型
        # 时这句是唯一会说「形变口径和源文件不一样」的地方，别把它删了。
        # #119 之后这两行 meta 不再是死数据：它们进定价指纹（`verdicts.export_config`
        # 的 `dq=` 那一维），所以含 SDEF 的模型**不会**继承线性混合那两组的锁 ——
        # 要么整片退成 report（没被定价过的模型），要么当场中止（同网格但配置没定价）。
        kinds = "/".join(k for k in ("SDEF", "QDEF") if k in model.deform_types)
        print(f"  [提示] 源模型含 {kinds} 骨，但 Max 端按普通线性混合装配（双四元数分支已在 M1b 删除）；"
              "这一轮的形变口径由 meta 的 has_sdef / has_qdef 进定价指纹（verdicts.export_config 的 dq= 那一维），"
              "而「源含 DQ + 线性装配」这一组从没实跑过定价 —— 要么整片退成 report，要么当场中止，"
              "不会借用线性模型那两组的锁。要判 DQ 模型的形变质量，先在这台 Max 上把双四元数装回来并实跑定价。")

    # ---- 材质：只交 .mtl，不再另出清单 ----
    # 以前这里还写一份 <name>_materials.tsv（alpha / spec / sphere / draw_flags），
    # 注释写的是「供将来 Max 端构建更完整的材质」。将来一直没来：Max 侧只自检
    # `.mtl` 的 mtllib / map_Kd（scripts/max_import_obj.ms 的材质自检），全仓库没有任何
    # 读者，只有 stdout 打印一行路径。留着的代价不是磁盘，是「看起来像契约」——
    # 会让人以为改材质口径要改这张表，而真正的口径在 .mtl 里。
    # MMD 特有的 sphere_mode / draw_flags 确实没进 .mtl；真要接toon时从 pmx 现读
    # （mmd2max/pmx.py 的 Material 已经解析了这些字段），不要预先落一张没人读的表。

    for w in warnings:
        print(f"  [警告] {w}")

    return {
        "obj": obj_path,
        "mtl": outdir / mtl_name,
        "bones": bones_path,
        "biped_map": biped_map_path,
        "weights": weights_path,
        "meta": meta_path,
        "morphs": morphs_path,
        "physics": physics_path,
        "limbs": limbs_path,
        "widths": widths_path,
        "texdir": outdir / "textures",
        "vertex_count": len(plan.positions),
        "face_count": len(plan.faces),
        "bone_count": len(model.bones),
        "kept_bone_count": len(plan.kept),
        "skin_bone_count": plan.skin_count,
        "morph_count": plan.morph_count,
        "rigid_count": len(model.rigid_bodies),
        "joint_count": len(model.joints),
        "limb_count": plan.limb_count,
        "accessory_reparented": plan.reparented,
        "spine_links": plan.spine_links,
        "fingers": plan.fingers,
        "finger_links": plan.finger_links,
        "warnings": warnings,
    }


def auto_scale(source: Path, target_cm: float = 160.0, model=None) -> float:
    """按模型高度自动推算 MMD 单位→厘米的缩放。

    MMD 模型身高通常约 20 单位对应 ~160cm，即 1 单位≈8cm。
    仍以「目标身高 cm / 模型包围盒高度(MMD 单位)」为准，避免对特定模型写死常量。
    model：已解析的模型，GUI 复用缓存省一次解析。
    """
    if model is None:
        model = load_pmx(source)
    if not model.vertices:
        return 8.0  # 兜底：1 MMD 单位≈8cm 的常用经验值
    ys = [v.position[1] for v in model.vertices]
    h = max(ys) - min(ys)
    if h <= 0:
        return 8.0
    return round(target_cm / h, 4)


def main(argv: list[str]) -> int:
    # --drop-guides 是开关而不是位置参数：插在任意位置都生效，免得为它重排 5~7 号槽
    keep_guides = "--drop-guides" not in argv
    argv = [a for a in argv if a != "--drop-guides"]
    if len(argv) < 3:
        print("用法: python -m mmd2max.export_obj <input.pmx> <outdir> "
              "[scale|auto] [min_weight] [humanoid|full] [--drop-guides]")
        return 1
    source = Path(argv[1])
    outdir = Path(argv[2])
    scale_arg = argv[3] if len(argv) > 3 else "auto"
    if scale_arg.lower() == "auto":
        scale = auto_scale(source)
        print(f"  自动推算 scale = {scale}（按身高≈160cm）")
    else:
        scale = float(scale_arg)
    min_weight = float(argv[4]) if len(argv) > 4 else 0.0
    # 默认 useful：剔除 IK / 物理哑骨 / 调试辅助骨等「无用骨」（用户明确要求）。
    # full=保留全部骨骼（v1 行为）；humanoid=只留标准人形骨（更严格）。
    mode = argv[5].lower() if len(argv) > 5 else "useful"
    humanoid_only = (mode == "humanoid")
    filter_useless = (mode != "full")
    # 第 7 参数：附属骨（裙摆/头发等）挂到哪个 Biped 节点
    #   hierarchy = 跟随 MMD 层级（默认，动画语义正确）
    #   nearest   = 挂到离链根最近的 Biped 节点（视觉更贴合，但改变动画归属）
    accessory = argv[6].lower() if len(argv) > 6 else "nearest"
    if accessory not in ("hierarchy", "nearest"):
        print(f"  未知 accessory_parent={accessory!r}，回退 nearest")
        accessory = "nearest"
    info = export(source, outdir, build_plan(
        source, scale, min_weight, humanoid_only=humanoid_only,
        filter_useless=filter_useless, accessory_parent=accessory,
        keep_guides=keep_guides))
    for key in ("obj", "bones", "weights", "morphs", "physics"):
        print(f"  {key}: {info[key]}")
    print(f"  顶点 {info['vertex_count']}  三角面 {info['face_count']}  "
          f"原骨骼 {info['bone_count']} -> 保留 {info['kept_bone_count']}  "
          f"加入蒙皮 {info['skin_bone_count']}")
    print(f"  morph 通道 {info['morph_count']}  刚体 {info['rigid_count']}  "
          f"关节 {info['joint_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
