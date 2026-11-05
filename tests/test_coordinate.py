"""坐标系与手性映射测试（唯一事实来源：mmd2max/coords.py）。

MMD 是左手系（+X 屏幕右、+Y 上、+Z 屏幕内，角色朝 -Z），3ds Max 是右手系
（Front 视图 +X 屏幕右、+Z 上、+Y 屏幕内）。要在 Max 里复现 MMD 的观感，
映射必须送 (屏幕右, 上, 屏幕内) → (屏幕右, 上, 屏幕内)，即 (x, y, z) -> (x, z, y)。
交换两根轴 ⇒ **det = -1**：含一次镜像，所以三角绕序要翻一次。

v2 用的是 det=+1 的 (-x, z, y)：角色正面是朝镜头了，但整张图是 MMD 的镜像
（左手动骨落到屏幕左），于是只能在 semantic 里再交换一次 L/R 骨名去盖住它。
本文件把「不镜像」写成可执行断言，改回旧映射会立刻红。

测试矩阵一律由 convert_vec 反推（不让它和实现各抄一份，否则改了实现测试还绿）。
"""
import math
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max import coords                                # noqa: E402
from mmd2max.coords import convert_vec                    # noqa: E402
from mmd2max.export_obj import auto_scale                 # noqa: E402
from tests.make_sample import create_sample               # noqa: E402


def matrix_from(fn):
    """把任一 3x3 线性映射按「列 = 基向量的像」还原成矩阵，用于求行列式。"""
    return [list(fn(axis)) for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]


def det(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


class CoordinateMappingTests(unittest.TestCase):
    def test_up_axis_maps_to_max_z(self):
        # MMD 的 +Y（上）必须映射到 Max 的 +Z（上）
        self.assertEqual(coords.max_delta((0, 1, 0)), (0, 0, 1))

    def test_character_facing_maps_to_minus_y(self):
        # 角色朝 MMD -Z；Front 视图从 -Y 往 +Y 看，所以 -Z 必须落到 -Y（否则看到背面）
        self.assertEqual(coords.max_delta((0, 0, -1)), (0, -1, 0))

    def test_left_is_not_mirrored(self):
        """PMX 左腕的 x>0（三台模型实测），映射后必须仍落在 Max +X。

        Front 视图里 +X 是屏幕右；一个人面对你时，他的左手在你的右手边 ——
        所以 左/右 要对，就必须是 +X，而不是 v2 的 -X。
        """
        self.assertEqual(coords.max_delta((1, 0, 0)), (1, 0, 0))

    def test_mapping_mirrors_so_winding_must_flip(self):
        m = matrix_from(lambda v: convert_vec(v, 1.0))
        self.assertAlmostEqual(det(m), -1.0, places=9)
        self.assertAlmostEqual(coords.determinant(), det(m), places=9)

    def test_scale_is_uniform(self):
        import math
        s = 8.763
        v = (2.0, -3.0, 5.0)
        out = convert_vec(v, s)
        # 只允许轴交换 + 等比缩放：不得有分量取反之外的符号变化
        self.assertEqual(sorted(map(abs, out)), sorted([abs(c * s) for c in v]))
        norm_out = math.sqrt(sum(c * c for c in out))
        norm_in = math.sqrt(sum(c * c for c in v))
        self.assertAlmostEqual(norm_out / norm_in, s, places=9)

    def test_obj_values_land_on_convert_vec(self):
        """OBJ 写的 (x, y, -z) 经 Max 导入器 (a,b,c)->(a,-c,b) 后 == convert_vec。

        这条断言把「导入器约定」钉在测试里：一旦实跑发现 Max 不是这么转的，
        这里就该改，而不该在 MAXScript 里再加一次补偿旋转。
        """
        for v in ((1.0, 2.0, 3.0), (-0.5, 0.25, 7.0), (0.0, 0.0, -1.0), (4.0, 4.0, 4.0)):
            self.assertEqual(coords.max_from_obj(coords.obj_vec(v, 1.0)),
                             convert_vec(v, 1.0))
            self.assertEqual(coords.max_from_obj(coords.obj_delta(v)),
                             coords.max_delta(v))

    def test_auto_scale_derives_sane_value(self):
        """auto_scale 必须自己推出量纲，而不是留在 1.0（= 没推）。

        样例走 create_sample 现场生成：早先这条钉的是 fixtures/ 里一份**没随
        tests/ 一起分发**的 .pmx，于是它每轮都静默 skip —— 一条永不执行的断言比
        没有断言更糟，它会让人以为这个数被看着。
        """
        with tempfile.TemporaryDirectory() as tmp:
            pmx = create_sample(Path(tmp))
            scale = auto_scale(pmx, target_cm=160.0)
        self.assertGreater(scale, 1.0)
        self.assertLess(scale, 100.0)


def _mat_vec(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


class RigidRotationTests(unittest.TestCase):
    """PMX 刚体欧拉（弧度）→ 写进 `node.rotation = (eulerAngles a b c)` 的**度数**。

    两条口径都是本机 Max 2022 **量出来**的，不是推的
    （探针 87 / 87b，test_runs/probe87/probe_api*.txt）：
      1) `eulerAngles` 吃度数；`(eulerAngles a b c) as matrix3` 的线性算子
         = Rz(c)·Ry(b)·Rx(a)（探针 87 的 J 行逐元素相同，与其余 5 种顺序差 0.25~0.67）。
      2) 节点那边是反的：`.rotation = (eulerAngles 0 0 90)` 之后 transform 第一行
         （= 该节点局部 X 在世界里的指向）= [0,-1,0]（探针 87b 的 4c 行）。
         即「节点算子（列 = 基向量的像）」= 那条口径下纯数学矩阵的**转置**。
    下面每条断言都从这两条走，不从实现反推 —— 否则「实现抄一份、测试抄一份」，
    改错了还绿。
    """

    D = math.pi / 180.0

    @staticmethod
    def node_operator(triple_deg):
        """实测口径 2：写入的三重角 -> 「Max 局部矢量 → Max 世界矢量」的算子。"""
        a, b, c = (t * RigidRotationTests.D for t in triple_deg)
        return coords._mat3_transpose(coords._euler_std(a, b, c))

    def test_measured_node_convention_is_transpose_not_identity(self):
        """口径 2 直接落成断言：写 (eulerAngles 0 0 90) 后局部 X 指到世界 -Y。

        这条是探针 87b 的 4c 行。少了那一次 transpose，下面每条都会红在这里，
        而不是「双方都转置、互相抵消」那种查不出来的绿。
        """
        got = _mat_vec(self.node_operator((0.0, 0.0, 90.0)), (1.0, 0.0, 0.0))
        for want, val in zip((0.0, -1.0, 0.0), got):
            self.assertAlmostEqual(want, val, places=9)

    def test_euler_is_in_degrees_not_radians(self):
        """PMX 给弧度、Max 吃度数：MMD 绕上轴 +90° 必须写成 90.0 而不是 1.5708。

        这条看着像废话，但它是第 64~66 轮之外单独的第二个缺陷：即使 `transpose`
        那些抛错都修好，按弧度写下去每根代理也只转 0.09°，肉眼和普通「没转」分不开。
        """
        out = coords.rigid_rotation_max((0.0, math.pi / 2, 0.0))
        self.assertAlmostEqual(max(abs(v) for v in out), 90.0, places=6)
        # MMD 的 +Y（上）在 Max 是 +Z（上）：角度落在第三个槽位
        self.assertAlmostEqual(out[0], 0.0, places=6)
        self.assertAlmostEqual(out[1], 0.0, places=6)

    def test_identity_stays_identity(self):
        self.assertEqual(tuple(round(v, 9) for v in coords.rigid_rotation_max((0.0, 0.0, 0.0))),
                         (0.0, 0.0, 0.0))

    def _assert_axes_land_where_physics_says(self, pmx_rad, label):
        """代理的局部轴（= MMD 局部轴经 Φ）转完之后的世界指向，必须等于
        「先在 MMD 里转，再经 Φ」。Φ = coords.MATRIX，与位置用的是同一张映射。"""
        r = coords.rotation_matrix_pmx(pmx_rad)
        n = self.node_operator(coords.rigid_rotation_max(pmx_rad))
        for u in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            want = coords.max_delta(_mat_vec(r, u))
            got = _mat_vec(n, coords.max_delta(u))
            for i in range(3):
                self.assertAlmostEqual(want[i], got[i], places=9,
                                       msg=f"{label} 的第 {i} 轴对不上：want={want} got={got}")

    def test_axes_match_for_typical_and_worst_case_eulers(self):
        """前三条是单轴（任何欧拉顺序都一样）；后四条含三轴同动与万向锁。

        「管线万向锁」两条是特意挑的：`_euler_from_std` 的判据在**拆节点那一侧**，
        而节点矩阵是 Φ·R·Φ 的转置，所以 PMX 的 b=±90 到节点侧不是万向锁、
        PMX 的 c=±90 才是（实测 m[2][0]=∓1）。两类都要有。
        """
        cases = {
            "单轴 X 90": (math.pi / 2, 0.0, 0.0),
            "单轴 Y 90": (0.0, math.pi / 2, 0.0),
            "单轴 Z -90": (0.0, 0.0, -math.pi / 2),
            "三轴 30/40/50": (0.5236, 0.6981, 0.8727),
            "小角度 1/2/3 度": (0.01745, 0.03491, 0.05236),
            "PMX b=-90（节点侧非锁）": (45 * RigidRotationTests.D,
                                        -90 * RigidRotationTests.D,
                                        30 * RigidRotationTests.D),
            "管线万向锁 90/90/0": (90 * RigidRotationTests.D, 90 * RigidRotationTests.D, 0.0),
            "管线万向锁 0/90/90": (0.0, 90 * RigidRotationTests.D, 90 * RigidRotationTests.D),
            "三轴同 90": (90 * RigidRotationTests.D, 90 * RigidRotationTests.D,
                          90 * RigidRotationTests.D),
        }
        for label, e in cases.items():
            self._assert_axes_land_where_physics_says(e, label)

    def test_gimbal_branch_rebuilds_the_same_operator(self):
        """cos(b)=0 时只有 c-a 有定义：拆回去必须重建出同一个算子，不许差 2.0。

        第一版这里写错（数值上把 -m[2][0] 当成 sin(b) 之后又按普通分支解 c），
        b=±90 的重建误差是 2.00 —— 那不是精度问题，是每一根裙摆刚体都转飞。
        """
        for b in (90.0, -90.0):
            for a in (0.0, 37.0):
                for c in (0.0, -64.0):
                    m = coords._euler_std(a * self.D, b * self.D, c * self.D)
                    back = coords._euler_from_std(m)
                    rebuilt = coords._euler_std(*back)
                    for i in range(3):
                        for j in range(3):
                            self.assertAlmostEqual(m[i][j], rebuilt[i][j], places=9,
                                                   msg=f"b={b} a={a} c={c}")

    def test_pmx_euler_order_is_the_documented_choice(self):
        """R_pmx 的欧拉顺序是**选定**的（Rz·Ry·Rx，与 Max 默认口径同一套），不是推出来的。

        自洽类判据抓不到顺序：任何旋转矩阵都能拆回一组等价的 Rz·Ry·Rx 三重角，
        所以「R_pmx 用 Rx·Ry·Rz」和「用 Rz·Ry·Rx」在往返测试里长得一模一样
        （变异脚本 test_runs/mutate88_py.py 的 V4 第一次就是这么绿的）。
        这里改用解析式手算一个三轴同动的矩阵当金标准。PMX 规范没写刚体欧拉顺序，
        改这条的唯一正当理由是「实跑截图证明 MMD 用的是别的顺序」。
        """
        a, b, c = (30 * self.D, 40 * self.D, 50 * self.D)
        ca, sa = math.cos(a), math.sin(a)
        cb, sb = math.cos(b), math.sin(b)
        cc, sc = math.cos(c), math.sin(c)
        want = ((cb * cc, sa * sb * cc - ca * sc, ca * sb * cc + sa * sc),
                (cb * sc, sa * sb * sc + ca * cc, ca * sb * sc - sa * cc),
                (-sb, sa * cb, ca * cb))
        got = coords.rotation_matrix_pmx((a, b, c))
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(want[i][j], got[i][j], places=12,
                                       msg=f"R_pmx[{i}][{j}] 与解析式不符：{want} vs {got}")

    def test_pmx_rotation_matrix_is_a_true_rotation(self):
        """R_pmx 必须正交且 det=+1：镜像只允许来自 Φ，不许混进旋转本身。

        Φ 的 det=-1 是这套映射的核心事实（见 CoordinateMappingTests），共轭
        Φ·R·Φ⁻¹ 仍给真旋转；一旦哪步把转置写成共轭的一半，这里就会出 det=-1。
        """
        for e in ((0.3, 0.4, 0.5), (math.pi / 2, 0.0, 0.0), (0.0, 0.0, 0.0)):
            m = coords.rotation_matrix_pmx(e)
            self.assertAlmostEqual(coords.determinant(), -1.0, places=9)  # 镜像只许来自 Φ
            self.assertAlmostEqual(det(m), 1.0, places=9)
            for i in range(3):
                for j in range(3):
                    dot = sum(m[i][k] * m[j][k] for k in range(3))
                    self.assertAlmostEqual(dot, 1.0 if i == j else 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
