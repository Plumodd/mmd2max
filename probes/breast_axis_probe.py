"""探针：胸部骨的「指向乳头」到底能不能从网格里量出来，以及哪些骨属于「辅助骨骼」。

跑法（本机）：
    cd C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03
    PYTHONIOENCODING=utf-8 ~/.local/bin/python3.12.exe probes/breast_axis_probe.py > probes/breast_axis_probe.txt

要回答的两个问题（都对应一次用户口头需求，不靠猜）：
1. 「胸部的骨骼应该表现胸部的指向方向，应该指向乳头」
   —— PMX 里 左胸/右胸 的 tail_offset 是一根纯竖直的短棒（本模型 +Z 7.66cm），
   在 Max 里画出来就是一根贴着胸腔往上长的牙签（用户截图里那个红色箭头）。
   要让它指向乳头，乳头必须有一个**可复算的几何定义**。这里同时量四种候选，
   看它们彼此差多少、和骨 head 的连线跟竖直方向差多少度。
2. 「加一个按钮开启或者关闭保留这些辅助骨骼（センター2 / WeaponR / 右親指先 …）」
   —— 先量清楚 PMX 的「非表示」位（flags & 0x0008）能不能把用户点名的这批骨
   和真正要留的骨分开，再决定开关按什么判据筛。
"""
from __future__ import annotations

import sys
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mmd2max import coords, pmx  # noqa: E402

PMX = Path("C:/Users/Administrator/Desktop/香菱/香菱.pmx")
META = ROOT / "test_runs/香菱/香菱_meta.tsv"

# 用户点名的「辅助骨骼」样本，逐个查它们在 PMX 里的属性
NAMED = ["センター", "センター2", "WeaponL", "WeaponR",
         "左親指先", "左人指先", "左中指先", "左薬指先", "左小指先",
         "右親指先", "右人指先", "右中指先", "右薬指先", "右小指先",
         "左胸", "右胸", "左つんく", "右つんく"]


def scale_from_meta() -> float:
    for line in META.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("\t")
        if key.strip() == "scale":
            return float(value.strip())
    raise SystemExit(f"{META} 里没有 scale")


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def norm(v):
    d = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / d, v[1] / d, v[2] / d) if d > 1e-12 else (0.0, 0.0, 0.0)


def dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def angle_from_up(v) -> float:
    """方向向量与 +Z（当前那根牙签的方向）的夹角，度。"""
    u = norm(v)
    return math.degrees(math.acos(max(-1.0, min(1.0, u[2]))))


def main() -> int:
    model = pmx.load_pmx(PMX)
    scale = scale_from_meta()
    bones = model.bones
    print(f"模型 {PMX.name}  scale={scale}  骨 {len(bones)}  顶点 {len(model.vertices)}")

    print("\n== 1) 用户点名的骨：PMX 原始属性（Max 帧坐标，Z-up，-Y 是正面）==")
    print("name\tparent\tflags\thidden0x8\tdisp0x0060?\ttail_kind\ttail_len_cm\thead\ttail")
    heads = {}
    tails = {}
    for i, b in enumerate(bones):
        head = coords.convert_vec(b.position, scale)
        heads[i] = head
        if 0 <= b.tail_index < len(bones):
            tail = coords.convert_vec(bones[b.tail_index].position, scale)
            kind = "bone"
        else:
            off = coords.convert_vec(b.tail_offset, scale)
            tail = (head[0] + off[0], head[1] + off[1], head[2] + off[2])
            kind = "offset"
        tails[i] = tail
        if b.name in NAMED:
            print(f"{b.name}\t{b.parent_index}\t0x{b.flags:04x}\t"
                  f"{'Y' if b.flags & 0x0008 else 'n'}\t{b.flags & 0x0060:04x}\t{kind}\t"
                  f"{dist(head, tail):.3f}\t"
                  f"({head[0]:.2f},{head[1]:.2f},{head[2]:.2f})\t"
                  f"({tail[0]:.2f},{tail[1]:.2f},{tail[2]:.2f})")

    print("\n== 2) 非表示位（0x0008）在全模型上的分布 ==")
    hidden = [b.name for b in bones if b.flags & 0x0008]
    shown = [b.name for b in bones if not b.flags & 0x0008]
    print(f"非表示 {len(hidden)} 根 / 表示 {len(shown)} 根")
    print("表示骨（前 60）：" + "、".join(shown[:60]))

    print("\n== 3) 蒙皮归属：每根骨被多少顶点当最大权重 ==")
    dominant: dict[int, int] = {}
    for v in model.vertices:
        if not v.weights:
            continue
        idx, _w = max(v.weights, key=lambda t: t[1])
        dominant[idx] = dominant.get(idx, 0) + 1
    for i, b in enumerate(bones):
        if b.name in NAMED:
            print(f"{b.name}\t顶点 {dominant.get(i, 0)}")

    print("\n== 4) 乳头的四种几何候选（只看 左胸/右胸）==")
    for i, b in enumerate(bones):
        if b.name not in ("左胸", "右胸"):
            continue
        head = heads[i]
        pts = []
        for v in model.vertices:
            if not v.weights:
                continue
            idx, w = max(v.weights, key=lambda t: t[1])
            if idx == i and w >= 0.5:
                pts.append(coords.convert_vec(v.position, scale))
        if not pts:
            print(f"{b.name}：没有 >=0.5 主导权重的顶点，量不了")
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        centroid = (cx, cy, cz)
        parent = b.parent_index
        phead = heads.get(parent, head)
        sgn = 1.0 if cx >= 0 else -1.0
        # A: 最靠前（-Y 最小）的顶点
        a = min(pts, key=lambda p: p[1])
        # B: 离父骨 head 最远的顶点（父骨在脊柱上 ⇒ 越鼓越远）
        bb = max(pts, key=lambda p: dist(p, phead))
        # C: 沿「质心 − 父骨 head」方向投影最远的顶点
        outward = norm(sub(centroid, phead))
        c = max(pts, key=lambda p: (p[0] - phead[0]) * outward[0]
                + (p[1] - phead[1]) * outward[1] + (p[2] - phead[2]) * outward[2])
        # D: 离身体中线平面（x=0）最远 + 最靠前的折中：max(|x| - 前伸) 不对，
        #    改成「在质心所在水平带内取最靠前」，看它和 A 差多少
        band = [p for p in pts if abs(p[2] - cz) <= 2.0]
        d = min(band or pts, key=lambda p: p[1])
        print(f"\n{b.name}  head=({head[0]:.2f},{head[1]:.2f},{head[2]:.2f})"
              f"  父骨[{parent}]={bones[parent].name if parent >= 0 else '-'}"
              f"  主导顶点 {len(pts)}")
        print(f"  质心=({centroid[0]:.2f},{centroid[1]:.2f},{centroid[2]:.2f})")
        lo = tuple(min(p[k] for p in pts) for k in range(3))
        hi = tuple(max(p[k] for p in pts) for k in range(3))
        print(f"  区域包围盒 x[{lo[0]:.2f},{hi[0]:.2f}] "
              f"y[{lo[1]:.2f},{hi[1]:.2f}] z[{lo[2]:.2f},{hi[2]:.2f}] "
              f"=> 跨 {hi[0]-lo[0]:.2f} x {hi[1]-lo[1]:.2f} x {hi[2]-lo[2]:.2f} cm")
        # E/F：单点最靠前容易被一个野顶点带跑，所以再看「最靠前的一小撮」的平均。
        fwd_sorted = sorted(pts, key=lambda p: p[1])
        for tag, group in (("E 最靠前 5 点均值", fwd_sorted[:5]),
                           ("F 最靠前 25% 均值",
                            fwd_sorted[:max(1, len(fwd_sorted) // 4)])):
            ex = sum(p[0] for p in group) / len(group)
            ey = sum(p[1] for p in group) / len(group)
            ez = sum(p[2] for p in group) / len(group)
            p = (ex, ey, ez)
            vec = sub(p, head)
            print(f"  {tag}\t({p[0]:.2f},{p[1]:.2f},{p[2]:.2f})\t"
                  f"距 head {dist(head, p):.2f}cm\t与竖直方向夹 {angle_from_up(vec):.1f}°")
        vec = sub(centroid, head)
        print(f"  G 区域质心\t({centroid[0]:.2f},{centroid[1]:.2f},{centroid[2]:.2f})\t"
              f"距 head {dist(head, centroid):.2f}cm\t"
              f"与竖直方向夹 {angle_from_up(vec):.1f}°")
        # H：只用「这根骨自己的皮肤区域」推顶点，不假设角色朝哪个轴 ——
        # 先拿 head→质心 当粗方向，沿它取最外伸的 25% 求均值，再迭代一次。
        direction = norm(vec)
        apex = centroid
        for _ in range(2):
            ranked = sorted(pts, key=lambda p: sum(
                (p[k] - head[k]) * direction[k] for k in range(3)), reverse=True)
            group = ranked[:max(1, len(ranked) // 4)]
            apex = tuple(sum(p[k] for p in group) / len(group) for k in range(3))
            direction = norm(sub(apex, head))
        vec = sub(apex, head)
        print(f"  H 自身区域迭代顶点\t({apex[0]:.2f},{apex[1]:.2f},{apex[2]:.2f})\t"
              f"距 head {dist(head, apex):.2f}cm\t"
              f"与竖直方向夹 {angle_from_up(vec):.1f}°\t"
              f"方向 ({vec[0]/dist(head,apex):.3f},{vec[1]/dist(head,apex):.3f},{vec[2]/dist(head,apex):.3f})")
        for tag, p in (("A 最靠前", a), ("B 离父骨最远", bb),
                       ("C 沿外法向最远", c), ("D 同高度带最靠前", d)):
            vec = sub(p, head)
            print(f"  {tag}\t({p[0]:.2f},{p[1]:.2f},{p[2]:.2f})\t"
                  f"距 head {dist(head, p):.2f}cm\t与竖直方向夹 {angle_from_up(vec):.1f}°")
        # 现在的牙签方向
        print(f"  现 PMX tail\t({tails[i][0]:.2f},{tails[i][1]:.2f},{tails[i][2]:.2f})\t"
              f"与竖直方向夹 {angle_from_up(sub(tails[i], head)):.1f}°")

    print("\n== 5) 「辅助骨骼」判据候选：哪些骨既没被蒙皮用、也没挂刚体、也不是 Biped 目标 ==")
    any_weight: dict[int, float] = {}
    for v in model.vertices:
        for idx, w in v.weights:
            if w > 0:
                any_weight[idx] = any_weight.get(idx, 0.0) + w
    rigid_bones = {rb.bone_index for rb in model.rigid_bodies}
    joint_bones = set()
    for j in model.joints:
        for rb_index in (j.rigid_a, j.rigid_b):
            if 0 <= rb_index < len(model.rigid_bodies):
                joint_bones.add(model.rigid_bodies[rb_index].bone_index)
    biped_targets = {}
    for line in (ROOT / "test_runs/香菱/香菱_bones.tsv").read_text(
            encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        # 列序：idx bone_name parent role biped_target head_* ... —— 第 5 列才是 Biped 目标
        if len(cols) >= 5:
            biped_targets[cols[1]] = cols[4]
    drop, keep = [], []
    for i, b in enumerate(bones):
        why = []
        if biped_targets.get(b.name):
            why.append("Biped 目标")
        if any_weight.get(i, 0.0) > 0:
            why.append(f"蒙皮 {any_weight[i]:.1f}")
        if i in rigid_bones or i in joint_bones:
            why.append("刚体/关节")
        if b.flags & 0x0020:
            why.append("IK")
        (keep if why else drop).append((b.name, "、".join(why)))
    print(f"判据「三者皆无 ⇒ 纯辅助」：可丢 {len(drop)} 根 / 保留 {len(keep)} 根")
    print("\n用户点名的骨落在哪一边：")
    named_set = set(NAMED)
    for name, why in drop:
        if name in named_set:
            print(f"  [丢] {name}")
    for name, why in keep:
        if name in named_set:
            print(f"  [留] {name}\t因为 {why}")
    print(f"\n会被丢掉的骨名（前 80）：{'、'.join(n for n, _ in drop[:80])}")
    print(f"\n保留下来的骨名（前 80）：{'、'.join(n for n, _ in keep[:80])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
