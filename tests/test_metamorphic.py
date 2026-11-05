"""M5 蜕变测试（README_V4 第 4 节：里程碑表的 M5 行，与同节「M5 ✅：蜕变测试」那条小结）：不钉「这个数是多少」，钉「输入这样变，输出必须这样变」。

为什么金标准之外还要这一层：金标准（baselines/香菱_*.tsv）钉的是**某一具模型在某一次定价
之后的具体读数**，换一具模型它就 skip；而这三条性质对**任何**输入都成立，所以它们抓的恰好
是本项目栽过最多次的那一类 —— 「每个数字看着都合理，但数字之间的关系错了」：

  · `rigids[idx]` 0 基/1 基（第 64 轮）：每根代理都建出来了，只有账本死，恒等式看不见；
  · 刚体尺寸没乘 scale（早期版本，代理比骨架大 1/scale 倍）：单看一张表完全自洽；
  · 手性/镜像（#5）：左右互换在「只数总数」的判据里是隐身的。

三条各管一件事：
  1) **单位协变**：把源模型每一处「PMX 长度」乘 2、导出 scale 除 2 —— 角色在世界里还是
     那么大 —— 产物必须**逐字节相同**（只有 meta 的 `scale` 那行按定义跟着变）。
     抓的是「该乘 scale 的没乘 / 不该乘的乘了 / 单位串位」这一整族：刚体尺寸漏乘（早期
     版本，代理比骨架大 1/scale 倍）、关节平移限位只做了轴置换没乘 scale（`joint_pos_*`
     乘了，本轮抓到的同一族第二个实例）、质量被当成长度乘了 scale —— 只要任何一个量对
     PMX 单位的次数不对，「翻倍再减半」就补不回来，而补不回来就是文本差异。
     为什么**不是**上一轮那条「scale 翻倍 ⇒ 一切长度翻倍」：那条性质在产品里是假的。
     导出层有四处刻意按**导出单位（厘米）**定价的绝对量 —— 颈轴归位（`_center_neck`
     的 1.5cm 阈值 / 3cm 位移上限）、乳头目标带（`_chest_band` 的 3cm 窗）、脚尖与脚盒
     截面的切片带、粗细下限与半径上限（`_WIDTH_MIN_CM` / `_RADIUS_CAP_CM`）。它们成立的
     前提是 `auto_scale` 把每个角色钉成 160cm 高，所以厘米才是跨模型稳定的量纲。拿
     scale 当自由输入去要求它们线性，等于要求「160cm 的绝对量随 scale 缩」：上一轮我就
     是被这条推着把 `_WIDTH_MIN_CM` 改成 `× scale`，实测把香菱的手掌算进了手的粗细
     （width 3.6953 → 58.3399，12 行掉到 10 行）。真模型上「scale 翻倍」的实测越界清单
     （当时的偏差探针 `test_runs/probe_m5_dev.py` 已删，理由见 README_HANDOVER 第七节
     末条 —— 逐字节协变成立后不需要偏差清单，`_first_diff` 直接给第一处不同的行）：
     颈骨 head_y 差 -3.817cm、脚尖 toe_w 6.574 → 0.018
     （切片带落空，量不出截面）、widths 行集 8 → 6 —— 全是那四处绝对量的正常反应。
     换成单位协变之后，那四处规则在**同一个厘米空间**里跑，天然逐字节相同；而写反的
     阈值（`_WIDTH_MIN_CM * scale`）在这条下当场红 —— 两轮 scale 不同，行集会分叉。
     变异探针实测：test_runs/probe_m5_mutate.py。
     盲区要说清楚：这条**看不见**「某个量该不该随模型尺寸缩」。它只保证 PMX 单位与导出
     scale 互相抵消；要不要再钉一条厘米绝对量是设计判断，归 M4 的回读 + 截图。
  2) **x 镜像**：把源模型每一处 x 取反再导出，输出的 x 位置列必须取反，y/z 与所有非位置列
     逐字不变，取景框的 `x_lo/x_hi` 必须**互相对调再取反**。抓的是「左右」这一维：
     分类按名字、坐标按坐标，两者都不许把镜像当成噪声。
     它的边界也要说清楚：蜕变只管**协方差**，不管 L/R 兜底那个选择选得对不对
     （把兜底整体关掉，输出照样自洽 —— 实测绿）。选择对不对由 M4 的回读 + 截图管。
  3) **两次导出逐字节相同**：同一输入跑两遍，产物一字不差。`export()` 走进程池
     （`_collect` 的 `pool.map`），分片按 start 排序拼回 —— 这条钉的就是「拼回顺序与调度无关」。
     它红了不用猜几何，先查排序。

列分类是**反向**要求的：只有明确列进每张表 `scale` / `mirror` 的列才允许变，**其余列一律按
「逐字不变」咬**。所以将来新增一列，如果它其实随 scale 变，这条会当场红 —— 而不是因为
没人写它而静默放过。

唯一一处**不是**「逐字不变」的文本列是 Biped 目标名（`biped_target` / `biped_node` /
limbs 的 `parent_target`+`child_target`）。这不是豁免，是产品的一条定价行为：
`semantic._lr_is_mirrored` 会拿几何（前向向量 × up 得到的「右」侧）核对「左/右标签」，
标签与几何相反时**整体**交换 L/R —— 作者把 左肩 摆在角色右肩上时，Biped 必须迁就几何，
否则整条手臂朝向反面。镜像源模型必然触发它，所以这里的性质不是「不许翻」，而是
**「要么全不翻，要么按 `_swap_lr` 全翻，不许只翻一半」**。只翻一半意味着某根骨按名字、
另一根按几何 —— 那正是这条兜底最坏的失效方式，`_lr` 决策集合必须恰好一个元素。

翻了这一半之后，按 Biped 目标名枚举的表（`limbs` / `widths`，`target_keyed`）的**行含义**
跟着换：镜像轮的 `Bip001 L Clavicle` 这行讲的是 PMX 左腕，而基准轮讲左腕的那行叫
`Bip001 R Clavicle`。所以对撞时基准行按 `_swap_lr(键)` 找，不是按同一个键找。
上一轮我把它写成「兜底触发 ⇒ 整表逐字不动」，被实测否掉了（`child_y 0.004976 → 0.004975`）：
真模型左右并不严格对称，能成立的是 `mirrored[T] == base[swap(T)]` 且 x 取反 ——
「数字跟着物理骨走，不跟着行位置走」。这条改完之后 `mmd_parent`/`mmd_child` 自动回到
「逐字不变」，不再需要专门的豁免列。

容差按**每张表写侧的小数位**推，不是一个全局常数：`fmt()` 是 `%.6f`，但 `widths` 的粗细
写的是 `%.4f`。见 `_tol`。
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from mmd2max import export_obj                       # noqa: E402
from mmd2max.pmx import load_pmx                      # noqa: E402
from tests import spec_table                          # noqa: E402
from tests.make_sample import create_sample           # noqa: E402

# 2 的整数次幂，不是随便挑的：翻倍与减半在 IEEE 里是**精确**运算（只动指数不动尾数），
# 所以 `fl((2a)·(s/2)) == fl(a·s)` 逐位成立，末位才敢要求「逐字节相同」。
# 挑 3 之类的乘数，性质本身就会被最后一位 ulp 打掉，红的是算术不是缺陷。
FACTOR = 2.0
DECIMALS_DEFAULT = 6  # fmt() 的小数位

# **基准 scale 用模型自己的 auto_scale，不写死。**上一轮这里写的是 0.08，注释还写着
# 「与香菱实跑用的 scale 一致」—— 实测香菱 auto scale = 7.6563，差了近 100 倍。
# 后果不是测试变松，而是**测试把产品带错了**：我为了让 0.08/0.16 两轮的 widths 行集
# 一致，把 `_width_rows` 里那条 0.02cm 下限改成「PMX 单位 × scale」，香菱的手掌因此
# 被算进了手的粗细（width 3.6953 → 58.3399，12 行掉到 10 行）。
# 教训写死在这里：蜕变测试挑的输入必须是生产会走的输入。`auto_scale` 把角色钉成
# 160cm 高，导出单位（厘米）因此是跨模型稳定的量纲，产品里那几条绝对阈值按厘米写
# 是对的。单位协变这条正好把这个前提变成判据：PMX 长度 ×2 配 scale ÷2，角色还是
# 160cm —— 谁在这套输入下改动了产物，谁就是对 PMX 单位的次数不对。

# ---------------------------------------------------------------- 每张表的分类
#
# key 形如 `physics#JOINT`：`#` 前是文件名，`#` 后是同一文件里的子记录类型。
# 字段：
#   kind   —— `@KIND` 子记录类型；None 表示主记录（主记录 = 第 1 列不是任何已声明
#             子记录的那些行，所以 RIGID 这种「写在表头里」的记录就是主记录，不是 kind）
#   key    —— 行身份列：两种模式下都必须逐字不变
#   length —— 这列是**导出单位里的长度**（厘米）：镜像后必须同值（`measured` 表按容差判），
#             单位协变下逐字节相同。名字只描述这列是什么，不描述哪个测试用它
#   mirror —— 镜像时取反（位置类的 x 分量）
#   lr     —— 镜像时允许（且只允许）按 `_swap_lr` 整体翻转的文本列
#   decimals —— 这表写数值时用的**小数位数**，容差由它推（见 `_tol`）
#   target_keyed —— 行按 Biped 目标名枚举：兜底翻转时基准行按 `_swap_lr(键)` 找
#   measured —— 数值是**从网格/骨轴量出来的**（要开方、点乘，跨分量），所以镜像后
#               非 x 的长度列只保证「同值到末位」，不保证逐字节相同
#   ragged —— 这表按设计就是变宽行（写侧在表头下的注释行里声明过），缺的列不许当空串
#   text_only —— 变长行到没法按列名的表（weights），整份文本比
TABLES = {
    "bones": {
        "kind": None,
        "key": ("idx",),
        "length": ("head_x", "head_y", "head_z", "tail_x", "tail_y", "tail_z"),
        "mirror": ("head_x", "tail_x"),
        "lr": ("biped_target",),
    },
    "limbs": {
        "kind": None,
        "key": ("parent_target", "child_target"),
        "length": ("desired_len", "child_x", "child_y", "child_z",
                  "foot_len", "toe_len", "tip_x", "tip_y", "tip_z",
                  "toe_w", "toe_h", "foot_w", "foot_h"),
        "mirror": ("child_x", "tip_x"),
        # 行按 **Biped 目标名**枚举（写侧是固定的 parent/child 配对表），不是按 PMX 骨序。
        # 所以 L/R 兜底一触发，`Bip001 L Clavicle → L UpperArm` 这一行**换的是骨**：
        # 它从「PMX 左腕」改指「PMX 右腕」。镜像轮的这一行讲左腕，基准轮讲左腕的那行
        # 名字是 `R Clavicle` —— 于是对撞时必须按 `_swap_lr(键)` 取基准行，取到的正是
        # **同一根物理骨**，它的 x 分量取反、其余分量与文本列逐字不变。
        # 「整表不动」是上一轮的模型，实测被否（child_y 0.004976 → 0.004975）：
        # 真模型左右不严格对称，能成立的只有 remap，不是 freeze。
        "target_keyed": True,
        "measured": True,
        "lr": (),
        "ragged": True,      # 第 10~18 列只有 mode=foot 的行才写
    },
    "physics": {
        "kind": None,
        "key": ("rigid_index",),
        "length": ("rigid_size_x", "rigid_size_y", "rigid_size_z",
                  "rigid_pos_x", "rigid_pos_y", "rigid_pos_z"),
        "mirror": ("rigid_pos_x",),
        "lr": (),
    },
    "physics#JOINT": {
        "kind": "JOINT",
        "key": ("joint_index",),
        # 限位与弹簧都是长度：漏一个就是「位置乘了、范围没乘」那一族缺陷的入口。
        # `joint_pos_min/max` 走 convert_vec（轴置换 + scale），与 `joint_pos_*` 同一处理。
        "length": ("joint_pos_x", "joint_pos_y", "joint_pos_z",
                  "joint_pos_min_x", "joint_pos_min_y", "joint_pos_min_z",
                  "joint_pos_max_x", "joint_pos_max_y", "joint_pos_max_z"),
        # 限位向量的 xyz 是**关节自身局部轴**，与欧拉角同理：镜像一个局部量不是「分量取反」，
        # 所以 `_mirror_x` 根本不碰它们，这里也不许动 —— 归到「逐字不变」。
        "mirror": ("joint_pos_x",),
        "lr": (),
    },
    "morphs": {
        "kind": None,
        "key": ("morph_index",),
        # 表情名/面板/类型/条数都不随几何变。条数尤其要咬住：它是「这个表情还在不在」
        # 的哨兵，掉了行数的话下面的 V 行也会一起少，而对撞只比 V 行看不出是谁没了。
        "length": (),
        "mirror": (),
        "lr": (),
    },
    "morphs#V": {
        "kind": "V",
        # V 行只有 `vertex_index + 三个偏移`，**它属于哪个表情写在上一条 MORPH 行里**：
        # 同一文件两种记录交错，读侧拿不到归属，于是 vertex_index 天然不唯一
        # （真模型：一个眉毛顶点在几十个表情里各出现一次）。上一轮 `assertNotIn` 就是
        # 撞在这里。身份退化成行序号 —— 「有哪些表情、每个几条」由上面的 morphs 主记录
        # 独立钉住，两者合起来才等于「第 i 条 V 行讲的是这个顶点的这个表情偏移」。
        "key": None,
        "length": ("offset_x", "offset_y", "offset_z"),
        "mirror": ("offset_x",),
        "lr": (),
    },
    "widths": {
        "kind": None,
        "key": ("biped_target",),
        "length": ("width",),
        "mirror": (),
        # 同 limbs：写侧按固定的 wanted 表（L Hand, L Finger0..4, R Hand…）枚举，
        # 兜底翻转时每行改指另一侧的骨 → 基准行按 `_swap_lr(键)` 找。
        # width 是无符号粗细，所以镜像轮的期望是「同值」而不是取反。
        "target_keyed": True,
        "measured": True,
        "lr": (),
        # 写侧是 `{r * 2.0:.4f}`（见 export_obj._width_rows 的调用点），不是 fmt 的 6 位。
        # 容差必须跟着这个位数走，否则 scale 轮会拿 2e-6 去判 1e-4 粒度的最后一位。
        "decimals": 4,
    },
    "biped_map": {
        "kind": None,
        "key": ("bone_index",),
        "length": (),
        "mirror": (),
        "lr": ("biped_node",),
    },
    # 权重是「骨序号:权重」对（无量纲、按骨号），任何几何变换都必须整份不动。
    # 走文本比较而不是 table()：它的第 2 列天生变长（一对顶点挂几根骨就几个字段）。
    "weights": {"kind": None, "key": None, "length": (), "mirror": (), "lr": (),
                "text_only": True},
}

# meta.tsv 是 key/value 两列。带这些前缀的值是「量出来的厘米数」：随 scale 线性；
# 镜像时只有 x 那几对取反，而且 lo/hi 必须**对调**（区间跟着翻，边界不许硬取反）。
META_LEN_PREFIX = ("chest_", "neck_")
META_X_SWAP = {("chest_box_x_lo", "chest_box_x_hi"),
               ("chest_bone_x_min", "chest_bone_x_max"),
               ("neck_box_x_lo", "neck_box_x_hi"),
               ("neck_bone_x_min", "neck_bone_x_max")}
META_EXCLUDED = ("scale",)      # 另有专门断言，不进线性/不变清单


def _swap_lr(name: str) -> str:
    """交换 Biped 节点名里的左右（" L " <-> " R "）。

    测试侧**自己实现**这一条，不复用 `semantic._swap_lr`：判据如果调用写入侧的函数，
    就等于让写入侧给自己盖章（规则 I 治的就是这个）。两边不一致时这条会红，而那不是
    测试坏了 —— 那是「产品换左右的手法」和「蜕变承认的手法」脱钩，正是该红的时候。
    """
    if " L " in name:
        return name.replace(" L ", " R ")
    if " R " in name:
        return name.replace(" R ", " L ")
    return name


def _tol(spec: dict) -> float:
    """容差 = 4 × 半个末位，末位由**这张表写侧的小数位**决定。

    为什么不是 0、也不是一个全局常数：数值要过一遍 `%.df` 文本。镜像轮比的是
    「写出来的 b」对「读出来的 a」（同一个数，只是两行文本），最大合法残差 =
    两侧的 2×0.5·10^-d；取 4 倍留浮余量。
    d=6 ⇒ 2e-6，d=4（widths 的 `%.4f`）⇒ 2e-4。
    上一轮 widths 的 `0.1157 → 0.2315`（期望 0.2314）就是拿 6 位的容差去判 4 位的表：
    红的是十进制最后一位的舍入，不是缺陷。
    单位协变那条**不用**容差 —— 它比的是整份字节，两个输入落在同一个厘米空间里，
    没有任何放大环节。
    """
    return 4 * 0.5 * 10 ** -spec.get("decimals", DECIMALS_DEFAULT)


def _export(model, scale: float, source: Path, outdir: Path) -> None:
    """两段式管线的测试入口：build_plan（纯计算）→ export（落盘）。

    每次喂一份 deepcopy：`build_plan` 的注释写明 heads/tails 会被**原地**修正，
    两个变体共用一个 model 就等于让第一个变体改掉第二个的输入。
    """
    plan = export_obj.build_plan(source, scale=scale, model=copy.deepcopy(model))
    export_obj.export(source, outdir, plan)


DELTA = _tol({})      # meta 的数值也走 fmt（6 位）


def _mirror_x(model):
    """把源模型每一处 x 取反（只动**模型空间里的位置类**数据）。

    不碰的三类都有理由：
      · 法线与骨的方向轴（fixed_axis/local_x/local_z）、刚体与关节的欧拉角 —— 镜像一个
        旋转不是「把分量取反」（`Φ·R·Φ⁻¹` 是轴取反 + 角取反），所以这些列归「逐字不变」；
      · 关节平移限位 `pos_min/pos_max` 与弹簧 —— 它们的 xyz 是**关节自身局部轴**上的分量，
        同上，不是模型坐标；
      · 刚体尺寸 —— 是半尺寸的大小，不是位置。
    """
    m = copy.deepcopy(model)
    for v in m.vertices:
        x, y, z = v.position
        v.position = (-x, y, z)
    for b in m.bones:
        x, y, z = b.position
        b.position = (-x, y, z)
        tx, ty, tz = b.tail_offset
        b.tail_offset = (-tx, ty, tz)
    for mo in m.morphs:
        if mo.morph_type == 1:                      # vertex morph
            mo.items = [(vid, (-off[0], off[1], off[2])) for vid, off in mo.items]
    for rb in m.rigid_bodies:
        x, y, z = rb.position
        rb.position = (-x, y, z)
    for jt in m.joints:
        x, y, z = jt.position
        jt.position = (-x, y, z)
    return m


def _scale_geom(model, k: float):
    """把源模型每一处「PMX 长度」乘 k —— 单位协变那条的输入变换。

    乘哪些、不乘哪些，判据只有一条：导出层把它当**长度**（过 `convert_vec` 或 `* scale`）
    的就乘，当**无量纲参数**写的就原样留着。清单：

      · 乘：顶点位置、骨 position/tail_offset、顶点 morph 偏移、刚体 position 与 size、
        关节 position 与 pos_min/pos_max（平移限位是厘米尺度的位移范围，PMX 里就是长度）；
      · 不乘：质量、阻尼、摩擦、弹跳（PMX 里就不是模型空间的量）、欧拉角与旋转限位
        （角度）、`tail_index` 指向的骨（它的位置已经在 bones.position 里乘过了，
        再乘一次就是双重缩放）、刚体 `rotation`（同理）。

    这条清单如果与导出层的口径不符，性质会红 —— 而那不是「测试太严」：
    「这个字段到底算长度还是算参数」正是两边必须一致的定价，红了就是脱钩了。
    k 必须是 2 的整数次幂（见 FACTOR），否则末位对不上，红的是算术。
    """
    m = copy.deepcopy(model)

    def mul(t):
        return tuple(v * k for v in t)

    for v in m.vertices:
        v.position = mul(v.position)
    for b in m.bones:
        b.position = mul(b.position)
        b.tail_offset = mul(b.tail_offset)
    for mo in m.morphs:
        if mo.morph_type == 1:                      # vertex morph
            mo.items = [(vid, mul(off)) for vid, off in mo.items]
    for rb in m.rigid_bodies:
        rb.position = mul(rb.position)
        rb.size = mul(rb.size)
    for jt in m.joints:
        jt.position = mul(jt.position)
        jt.pos_min = mul(jt.pos_min)
        jt.pos_max = mul(jt.pos_max)
    return m


class MetamorphicMixin:
    """三条性质共用的判据层：镜像按列分类逐行比，单位协变与 determinism 直接比字节。"""

    @staticmethod
    def _files(outdir: Path, stem: str) -> dict:
        names = ("bones", "limbs", "physics", "morphs", "widths", "meta",
                 "weights", "biped_map")
        return {n: (outdir / f"{stem}_{n}.tsv").read_text(encoding="utf-8") for n in names}

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._lr_state = "same"   # 本轮镜像有没有触发 L/R 兜底，见 _read_lr_state

    def _read_lr_state(self, a: dict, b: dict) -> str:
        """从 bones.tsv 的 `biped_target` 读出「这次镜像有没有触发 L/R 兜底」。

        刻意**不**调 `semantic._lr_is_mirrored`：那是写入侧的判据，读侧拿它当期望就是
        自证（规则 I 治的就是这个病）。bones 的行身份是 PMX 骨序号，镜像不动它，
        所以两行逐号一比就知道翻了没有。
        翻必须**整体**翻：一半按骨名、一半按几何是这条兜底最坏的失效方式。
        """
        ra = spec_table.table(a["bones"])
        rb = spec_table.table(b["bones"])
        self.assertEqual(len(ra), len(rb), f"bones 行数在两种输入下不同：{len(ra)} → {len(rb)}")
        decisions: set = set()
        for x, y in zip(ra, rb):
            va, vb = x["biped_target"], y["biped_target"]
            if not va:
                self.assertEqual(vb, "", f"镜像不该给非人形骨安上 Biped 目标：{x['idx']}")
                continue
            if vb == va:
                if va != _swap_lr(va):      # Pelvis/Head 本来就没左右，不计入决策
                    decisions.add("same")
                continue
            self.assertEqual(vb, _swap_lr(va),
                             f"biped_target 只能逐字不变或整体换左右：{x['idx']} {va} → {vb}")
            decisions.add("swap")
        self.assertEqual(len(decisions), 1,
                         f"L/R 兜底必须整体触发或整体不触发，实测决策 = {sorted(decisions)}")
        return decisions.pop()

    def _compare_mirror(self, a: dict, b: dict) -> None:
        self._lr_state = self._read_lr_state(a, b)
        for tag, spec in TABLES.items():
            fname = tag.partition("#")[0]     # `physics#JOINT` 读的是 physics.tsv
            if spec.get("text_only"):
                self.assertEqual(a[fname], b[fname], f"{fname}.tsv 在镜像下整份变了")
                continue
            numeric = set(spec["length"]) | set(spec["mirror"])
            mirror_cols = set(spec["mirror"])
            tol = _tol(spec)
            kind = spec["kind"]
            ragged = spec.get("ragged", False)
            a_rows = spec_table.table(a[fname], kind, variable_width=ragged)
            b_rows = spec_table.table(b[fname], kind, variable_width=ragged)
            self.assertEqual(len(a_rows), len(b_rows),
                             f"{tag}：行数变了（{len(a_rows)} → {len(b_rows)}）—— "
                             f"镜像只该动数值，不该动筛选结果")
            if not a_rows:
                continue
            keys = spec["key"]
            # `key=None`：身份用行在这张表里的**序号**（见 morphs#V 的注释）。
            # 序号不是随便挑的兜底：两种输入下枚举顺序都由源文件的 PMX 顺序决定，
            # 所以「第 i 行还是不是同一件事」本身就是要钉的东西。
            def ident_of(rows):
                if keys is None:
                    return [(str(i),) for i in range(len(rows))]
                return [tuple(r[c] for c in keys) for r in rows]

            ident, ident_b = ident_of(a_rows), ident_of(b_rows)
            base_rows: dict = {}
            for row, k in zip(a_rows, ident):
                if k in base_rows:
                    self.fail(f"{tag}：行身份 {k} 出现了两次，按键取基准行会取到任意一条 "
                              f"—— 这张表的 key 声明不够用（{list(row)}）")
                base_rows[k] = row
            # 镜像下行身份必须逐字不变：bones/biped_map 按 PMX 序号枚举，
            # limbs/widths 按固定的 Biped 目标表枚举 —— 镜像只会换「这一行指哪根骨」，
            # 不会换「有哪些行」。身份一旦变了就是行序或筛选被动过。
            self.assertEqual(ident, ident_b,
                             f"{tag}：行身份被镜像改动了（{ident[:3]} → {ident_b[:3]}）")
            for i, rb in enumerate(b_rows):
                key = (str(i),) if keys is None else tuple(rb[c] for c in keys)
                # 兜底整体翻转时，按目标名枚举的表里「讲同一根物理骨」的两行**名字不同**：
                # 镜像轮的 T 对应基准轮的 swap(T)。不 remap 就等于拿左腕的读数去对右腕。
                want_key = key
                if spec.get("target_keyed") and self._lr_state == "swap":
                    want_key = tuple(_swap_lr(c) for c in key)
                base = base_rows.get(want_key)
                if base is None:
                    self.fail(
                        f"{tag}{key}：按 swap 找基准行 {want_key} 没找到 —— 这表按 Biped 目标名"
                        f"枚举，兜底翻转后每一行都该有另一侧的同名骨。缺了就说明左右不成对，"
                        f"或者翻的是别的东西。")
                # 变宽行两行的**存在性**必须一致：脚段镜像后还是脚段。
                # 不查这一条的话，「镜像把 mode=foot 挤成了 link」会被读成「那一列没变」。
                self.assertEqual(sorted(base), sorted(rb),
                                 f"{tag}{key}（基准 {want_key}）：两行带的列不一样多 —— "
                                 f"镜像改变了行宽，脚段判定或列顺序被动到了")
                for col in sorted(base):
                    if keys is not None and col in keys:
                        continue
                    where = f"{tag}{key} .{col}（基准行 {want_key}）"
                    text_a, text_b = base[col], rb[col]
                    if col in spec["lr"]:
                        self._text(text_b, text_a, where)
                        continue
                    if col not in numeric:
                        self.assertEqual(text_b, text_a,
                                         f"{where}：镜像不该动它（{text_a} → {text_b}）")
                        continue
                    va, vb = float(text_a), float(text_b)
                    expect = -va if col in mirror_cols else va
                    if col in mirror_cols or spec.get("measured"):
                        self.assertAlmostEqual(vb, expect, delta=tol,
                                               msg=f"{where}：{va} → {vb}，期望 {expect}")
                    else:
                        self.assertEqual(text_b, text_a,
                                         f"{where}：镜像不该动它（{text_a} → {text_b}）")
        self._check_limbs_vs_map(a)
        self._check_limbs_vs_map(b)

    @staticmethod
    def _names(root: Path) -> list:
        """**相对路径**清单：贴图落在 textures/ 子目录里，只取 p.name 会把
        `a/textures/tex0.png` 找成 `a/tex0.png`（上一轮这条就是 FileNotFoundError）。"""
        return sorted(p.relative_to(root).as_posix()
                      for p in root.rglob("*") if p.is_file())

    def _compare_covariant(self, a: Path, b: Path, stem: str,
                           scale_a: float, scale_b: float) -> None:
        """单位协变的判据：整份产物逐字节相同，只有 meta 的 `scale` 按定义不同。

        刻意**不**按列比 —— 逐字节把 `_files`/TABLES 之外的东西（`.obj` 的顶点与法线、
        `.mtl`、贴图的字节）一起纳进来了，而那些正是「刚体尺寸没乘 scale」那一族缺陷
        真正落盘的地方。列分类（TABLES）留给镜像那条用。
        """
        meta = f"{stem}_meta.tsv"
        names_a, names_b = self._names(a), self._names(b)
        self.assertEqual(names_a, names_b, "两次导出连文件清单都不一样")
        self.assertTrue(names_a, "一份产物都没落盘")
        for name in names_a:
            text_a, text_b = (a / name).read_bytes(), (b / name).read_bytes()
            if name == meta:
                self._meta_covariant(text_a, text_b, scale_a, scale_b)
                continue
            if text_a == text_b:
                continue
            self.fail(f"{name}：PMX 长度 ×{FACTOR:g} 配 scale ÷{FACTOR:g} 之后变了 —— "
                      f"{self._first_diff(text_a, text_b)}")

    def _meta_covariant(self, raw_a: bytes, raw_b: bytes,
                        scale_a: float, scale_b: float) -> None:
        ka = spec_table.key_value(raw_a.decode("utf-8"))
        kb = spec_table.key_value(raw_b.decode("utf-8"))
        self.assertEqual(sorted(ka), sorted(kb), "meta 的键集合变了")
        for key in sorted(ka):
            if key in META_EXCLUDED:
                continue
            self.assertEqual(kb[key], ka[key],
                             f"meta.{key}：{ka[key]!r} → {kb[key]!r} —— 角色在世界里没变"
                             f"大小，这个读数凭什么变？")
        # scale 这两行是**输入的回显**：钉住它等于钉住「这一轮跑的确实是我给的比例」，
        # 否则上面那一串「逐字节相同」可能只是两轮都跑错了同一个比例。
        self.assertAlmostEqual(float(ka["scale"]), scale_a, delta=DELTA)
        self.assertAlmostEqual(float(kb["scale"]), scale_b, delta=DELTA)

    @staticmethod
    def _first_diff(raw_a: bytes, raw_b: bytes, width: int = 160) -> str:
        """第一处不同的**整行**文本（两边各一条），免得红了我再去 hexdump。"""
        n = min(len(raw_a), len(raw_b))
        i = next((k for k in range(n) if raw_a[k] != raw_b[k]), n)
        lo = raw_a.rfind(b"\n", 0, i) + 1
        return (f"第 {i} 字节起不同：基准 {raw_a[lo:lo + width]!r} vs "
                f"协变 {raw_b[lo:lo + width]!r}（长度 {len(raw_a)} → {len(raw_b)}）")

    def _check_limbs_vs_map(self, files: dict) -> None:
        """limbs 每行的 `mmd_parent`/`mmd_child` 必须就是 biped_map 里那个目标名绑的骨。

        这条和上面的对撞**互相独立**：对撞只管「同一根骨在两具模型间的读数关系」，
        它看不见「这一行的目标名和它声称量的那根 PMX 骨对不对得上」。两张表各写各的
        （`_limb_rows` 按目标配对枚举，`_biped_map_rows` 按 kept 顺序枚举），
        所以它们对得上才有信息量 —— 兜底改错边、或某一行漂到隔壁骨上都跑得掉不了。
        """
        rows = spec_table.table(files["limbs"], variable_width=True)
        if not rows:
            return
        bound: dict = {}
        for m in spec_table.table(files["biped_map"]):
            bound.setdefault(m["biped_node"], m["pmx_bone_name"])
        for row in rows:
            for side in ("parent", "child"):
                target = row[f"{side}_target"]
                self.assertIn(target, bound,
                              f"limbs 说 {target} 是一段，biped_map 里却没有这个目标")
                self.assertEqual(
                    row[f"mmd_{side}"], bound[target],
                    f"limbs 的 {side} 行说它量的是 {row[f'mmd_{side}']}，"
                    f"biped_map 说 {target} 绑的是 {bound[target]}")

    def _text(self, got: str, want: str, where: str) -> None:
        """镜像下**按骨序号**枚举的文本列：逐字不变，或整体按 `_swap_lr` 翻。

        整体性由 `_read_lr_state` 在 bones 上先钉死，这里只负责逐行核对，
        所以不再需要「决策集合」—— 一处不合规直接红，而不是攒到最后比集合大小。
        """
        if got == want:
            return
        self.assertEqual(got, _swap_lr(want),
                         f"{where}：只能逐字不变或整体换左右，实际是 {want!r} → {got!r}")

    def _compare_meta_mirror(self, a: dict, b: dict) -> None:
        ka, kb = spec_table.key_value(a["meta"]), spec_table.key_value(b["meta"])
        self.assertEqual(sorted(ka), sorted(kb), "meta 的键集合变了")
        for key in sorted(ka):
            if key in META_EXCLUDED:
                continue
            va, vb = ka[key], kb[key]
            if not va or not vb:
                # 没胸骨的模型（样例就没有）这些格是**空串**，不是 0：量不出来的东西
                # 写成 0 会被 Max 端当成一个真的取景框。空必须两边都空 ——
                # 「镜像把一个量从无变有」没有这种事，出现了就是某处在按镜像分支。
                self.assertEqual(
                    va, vb, f"meta.{key} 一边量得出来一边量不出来：{va!r} vs {vb!r}")
                continue
            if key.startswith(META_LEN_PREFIX):
                fa, fb = float(va), float(vb)
                # 区间随镜像**对调**：lo' = −hi、hi' = −lo（min/max 同理）。
                # 只对单个值取反是错的 —— 那会把区间的两个端点留在原来的次序上。
                pair = next((p for p in META_X_SWAP if key in p), None)
                if pair is None:
                    expect = fa
                else:
                    other = pair[1] if key == pair[0] else pair[0]
                    expect = -float(ka[other])
                self.assertAlmostEqual(fb, expect, delta=DELTA,
                                       msg=f"meta.{key}：{fa} → {fb}，期望 {expect}")
            else:
                self.assertEqual(va, vb, f"meta.{key} 在镜像下被改了")
        self.assertEqual(ka["scale"], kb["scale"],
                         "镜像这两轮的 scale 必须相同 —— 不同就是两个输入一起动了，"
                         "对撞出来的期望没法归给镜像")


class SampleMetamorphicTests(unittest.TestCase, MetamorphicMixin):
    """小样本：跑得快，覆盖三条性质的全部列分类，是常驻闸门。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.d = Path(self.temp.name)
        self.src = create_sample(self.d)
        self.model = load_pmx(self.src)
        self.base = export_obj.auto_scale(self.src, model=self.model)

    def tearDown(self):
        self.temp.cleanup()

    def _variant(self, tag: str, model, scale: float) -> Path:
        d = self.d / tag
        d.mkdir()
        _export(model, scale, self.src, d)
        return d

    def test_pmx_unit_size_is_invisible_in_the_export(self):
        """PMX 长度 ×2 配 scale ÷2 ⇒ 产物逐字节相同（角色在世界里还是那么大）。"""
        a = self._variant("base", self.model, self.base)
        b = self._variant("unit", _scale_geom(self.model, FACTOR), self.base / FACTOR)
        self._compare_covariant(a, b, "sample", self.base, self.base / FACTOR)

    def test_mirror_x_negates_positions_and_swaps_the_framing_boxes(self):
        a = self._variant("m1", self.model, self.base)
        b = self._variant("m2", _mirror_x(self.model), self.base)
        fa, fb = self._files(a, "sample"), self._files(b, "sample")
        self._compare_mirror(fa, fb)
        self._compare_meta_mirror(fa, fb)

    def test_two_exports_of_the_same_input_are_byte_identical(self):
        a = self._variant("d1", self.model, self.base)
        b = self._variant("d2", self.model, self.base)
        names_a, names_b = self._names(a), self._names(b)
        self.assertEqual(names_a, names_b, "两次导出连文件清单都不一样")
        self.assertTrue(names_a, "一份产物都没落盘")
        for name in names_a:
            self.assertEqual((a / name).read_bytes(), (b / name).read_bytes(),
                             f"{name} 两次导出不逐字节相同（进程池拼回顺序或 dict 序有问题）")


REAL_PMX = Path(os.environ.get("MMD2MAX_TEST_PMX",
                               r"C:\Users\Administrator\Desktop\test2\克雷薇.pmx"))


@unittest.skipUnless(REAL_PMX.exists(), f"真模型不在（{REAL_PMX}）")
class RealModelMetamorphicTests(unittest.TestCase, MetamorphicMixin):
    """真模型：小样本那 4 个顶点覆盖不到「权重归并 / 顶点紧凑化 / 按网格量出来的取景框」这些分支。

    三条性质在这里**共用一次基准导出**（base）：单位协变拿 base 对 unit，镜像拿 base 对
    mirror，各只多导一份。刻意**不**单独跑 determinism：它只与拼回顺序有关，小样本已经
    钉住，而真模型一次导出要几秒到几十秒 —— 同一件事付两遍时间不划算。
    """

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.temp.name)
        cls.src = REAL_PMX
        cls.stem = REAL_PMX.stem
        cls.model = load_pmx(cls.src)
        cls.base = export_obj.auto_scale(cls.src, model=cls.model)
        cls.dirs: dict = {}
        cls.files: dict = {}
        for tag, model, scale in (
            ("base", cls.model, cls.base),
            ("unit", _scale_geom(cls.model, FACTOR), cls.base / FACTOR),
            ("mirror", _mirror_x(cls.model), cls.base),
        ):
            # 每条性质各只动**一个**输入：unit 只动 PMX 单位与配对的 scale（这两个是
            # 同一个动作的两半），mirror 只动模型。两个一起动就对不出期望归给谁。
            d = cls.d / tag
            d.mkdir()
            _export(model, scale, cls.src, d)
            cls.dirs[tag] = d
            cls.files[tag] = cls._files(d, cls.stem)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_pmx_unit_size_is_invisible(self):
        self._compare_covariant(self.dirs["base"], self.dirs["unit"], self.stem,
                                self.base, self.base / FACTOR)

    def test_real_mirror_negates_x(self):
        a, b = self.files["base"], self.files["mirror"]
        self._compare_mirror(a, b)
        self._compare_meta_mirror(a, b)


if __name__ == "__main__":
    unittest.main()
