"""MMD(PMX) → 3ds Max 的坐标与手性映射：整条管线唯一的事实来源。

为什么必须有这个模块：手性（镜像）判断一旦散落在 Python、OBJ 数值、MAXScript 三处，
就会出现「网格与骨骼一正一反」「左右手对调」这类只能靠肉眼截图发现、改一处崩一处的问题。
所有映射都在这里算一次，其余模块只引用。

    MMD：左手系，+X = 屏幕右，+Y = 上，+Z = 屏幕内；角色朝 -Z（面对镜头）。
         实测三台模型 左腕 的 x > 0 ⇒ +X 是「角色自身的左侧」在屏幕上的位置（右侧）。
    Max：右手系，Front 视图从 -Y 往 +Y 看；+X = 屏幕右，+Z = 上，+Y = 屏幕内。

要在右手系的 Max 里复现 MMD 的观感（正视图看到正面、且左右不镜像），必须满足
    屏幕右 → 屏幕右，上 → 上，屏幕内 → 屏幕内
即 (x, y, z)_MMD -> (x, z, y)_Max。交换两根轴 ⇒ **行列式 = -1，含一次镜像**。
这是左手系转右手系的必然代价，由此推出两条硬约束：

    1) 三角**绕序必须翻一次**（export_obj 的 flip_faces），否则正面变背面；
    2) PMX 骨名的 左/右 **不需要交换**（semantic 的 L/R 自检在正确手性下应保持沉默），
       v2 之所以要交换，是因为它用的 (-x, z, y) 行列式为 +1 —— 那是一张镜像图。

OBJ 侧（`obj_vec`）：Max 2022 的 OBJ 导入器把 Y-up 的 (a,b,c) 转成 Z-up 的 (a,-c,b)
（v2 实跑证实：只有这样角色才是站立的，绕 Z 转 180° 后正对 Front 视图）。
所以要让它最终落在 `convert_vec` 上，写进 OBJ 的值必须是 `(x, y, -z)`，
Max 端就**不需要任何补偿旋转**了。顺带好处：这个 OBJ 在 Blender 等右手系工具里
打开也是正面、不镜像的。
"""
from __future__ import annotations

import math

# convert_vec 的 3x3 线性部分（按行存：结果的第 i 个分量 = 该行与 (x,y,z) 的点积）
MATRIX = ((1, 0, 0), (0, 0, 1), (0, 1, 0))


def convert_vec(value, scale: float = 1.0) -> tuple[float, float, float]:
    """MMD 坐标 → 3ds Max 世界坐标（Z-up，含一次镜像，det = -1）。"""
    return (value[0] * scale, value[2] * scale, value[1] * scale)


def obj_vec(value, scale: float = 1.0) -> tuple[float, float, float]:
    """MMD 坐标 → 写进 OBJ 的 Y-up 数值。

    经过 Max 导入器 `(a,b,c) -> (a,-c,b)` 之后正好等于 `convert_vec`。
    """
    return (value[0] * scale, value[1] * scale, -value[2] * scale)


def obj_delta(value) -> tuple[float, float, float]:
    """方向/法线（不缩放）→ OBJ 的 Y-up 值，与 `obj_vec` 同一线性部分。"""
    return (value[0], value[1], -value[2])


def max_delta(value) -> tuple[float, float, float]:
    """方向/法线（不缩放）→ Max 世界坐标，与 `convert_vec` 同一线性部分。"""
    return (value[0], value[2], value[1])


def horizontal(value) -> tuple[float, float]:
    """Max 水平面分量（Front 视图里 x = 屏幕右，y = 屏幕内），供朝向判定。"""
    return (value[0], value[2])


def determinant() -> float:
    """映射的行列式：-1 = 含镜像（因此必须翻绕序）。"""
    m = MATRIX
    return float(m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                 - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                 + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def max_from_obj(obj_value) -> tuple[float, float, float]:
    """模拟 Max 2022 OBJ 导入器：Y-up (a,b,c) → Z-up (a,-c,b)。

    只为测试存在：把「导入器约定」写在一处，`obj_vec` 与 `convert_vec` 的一致性
    才有可执行的断言，而不用等一次几十分钟的 3ds Max 实跑。
    """
    return (obj_value[0], -obj_value[2], obj_value[1])


# ---------------------------------------------------------------------------
# 旋转（PMX 刚体朝向）
#
# 为什么这段必须在 Python，而不是在 MAXScript 里做矩阵共轭：
# MAXScript 那一侧连着量出四条「我以为有、实际没有」的写法（探针 87 / 87b，
# test_runs/probe87/probe_api*.txt）——
#     transpose <matrix3>      → No "transpose" function for (matrix3 ...)
#     <Angle4>.order = #xyz    → Unknown property: "order"
#     translate <point3>       → No "Translate" function for [10,20,30]
#     eulerAngles a b c order:#xyz → Argument count error: EulerAngles wanted 3, got 6
# 加上 `eulerAngles` 的单位是**度**而 PMX 给弧度（探针 87 的 F/G 行），
# 在 Max 里算这张矩阵要踩的坑比它算的数学还多。所以 Python 把最终要写给
# `node.rotation` 的三个**度数**算出来，MAXScript 只做那条 Max 自带脚本里出现
# 66 次的写法：`proxy.rotation = (eulerAngles a b c)`。
#
# 两条口径都是**实测**的，不是推的：
#   1) `(eulerAngles 30 40 50) as matrix3` 的线性算子 = Rz(50)·Ry(40)·Rx(30)
#     （探针 87 的 J 行矩阵与这个乘积逐元素相同，与其余 5 种顺序差 0.25~0.67）；
#      `eulerToQuat ... order:1` 与 `as quat` 四分量相同 ⇒ order:1 就是默认口径。
#   2) 节点那边是**反的**：`.rotation = (eulerAngles 0 0 90)` 之后 transform 的
#      第一行（= 该节点局部 X 轴在世界里的指向）= [0,-1,0]（探针 87b 的 4c 行），
#      而同一个三重值的纯数学矩阵把 X 映到 [0,1,0]（探针 87 的 H 行口径）。
#      即 node 的旋转算子 N = std(a,b,c)^T。
#      顺带两条同源读数：4a 写 (quat 0 0 -0.7071 0.7071) 得到与 4c **完全相同**的
#      transform，且读回 `.rotation` 与写入值相等 —— 节点用的 quat 是数学 quat 的共轭。
# 合起来：要把 MMD 的朝向 R_pmx 搬到 Max 节点，需要
#      N = Φ · R_pmx · Φ⁻¹（Φ 含镜像，det=-1；共轭仍给出真旋转，det=+1）
#      且 N = std(a,b,c)^T  ⇒  std(a,b,c) = N^T = Φ · R_pmx^T · Φ（Φ 对称且对合）
# 镜像把角度翻一次、节点口径又翻一次，两次抵消 —— 所以「MMD 里绕上轴 +90 度」
# 在 Max 里仍是绕上轴 +90 度，观感一致。这条不是推出来的：tests/test_coords.py
# 拿「MMD 局部 X 轴经 R_pmx 再经 Φ」与「rigid_rotation_max 复原的节点第一行」对撞。
# ---------------------------------------------------------------------------

_EULER_TOL = 1e-9


def _mat3_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                 for i in range(3))


def _mat3_transpose(a):
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


def _rot_x(t):
    c, s = math.cos(t), math.sin(t)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def _rot_y(t):
    c, s = math.cos(t), math.sin(t)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def _rot_z(t):
    c, s = math.cos(t), math.sin(t)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _euler_std(a, b, c):
    """Max 的 `eulerAngles a b c`（弧度版）对应的线性算子 = Rz·Ry·Rx。"""
    return _mat3_mul(_mat3_mul(_rot_z(c), _rot_y(b)), _rot_x(a))


def _euler_from_std(m):
    """把真旋转矩阵拆回 `eulerAngles` 三重角（弧度）。

    cos(b)=0 是万向锁：那时只有 c-a 有定义，取 a=0 再解 c。分支公式是代入
    b=±90° 后 Rz·Ry·Rx 的解析结果（不是数值搜出来的），两种符号各一条。
    """
    sy = max(-1.0, min(1.0, -m[2][0]))
    b = math.asin(sy)
    if abs(math.cos(b)) < _EULER_TOL:
        if sy > 0:
            return 0.0, b, math.atan2(-m[0][1], m[0][2])
        return 0.0, b, math.atan2(-m[0][1], -m[0][2])
    return math.atan2(m[2][1], m[2][2]), b, math.atan2(m[1][0], m[0][0])


def rotation_matrix_pmx(value):
    """PMX 刚体/关节的欧拉（弧度）→ 线性算子 Rz·Ry·Rx。

    PMX 规范没写欧拉顺序（MMD 各家实现也不一致），所以这里**指定**与 Max 默认
    口径同一套（探针 87 实测的 Rz·Ry·Rx），并把这条选择记成代码里唯一的一处。
    顺序只在三根轴都不为零时才看得出来，而裙摆/头发刚体绝大多数只有一个非零角
    ——单轴情况下任何顺序都给出同一个旋转。
    """
    return _euler_std(float(value[0]), float(value[1]), float(value[2]))


def rigid_rotation_max(value) -> tuple[float, float, float]:
    """PMX 欧拉（弧度）→ 直接写进 `node.rotation = (eulerAngles a b c)` 的度数。"""
    node_want = _mat3_transpose(_mat3_mul(_mat3_mul(MATRIX, rotation_matrix_pmx(value)),
                                          MATRIX))
    a, b, c = _euler_from_std(node_want)
    return (math.degrees(a), math.degrees(b), math.degrees(c))

