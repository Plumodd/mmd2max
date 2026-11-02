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
