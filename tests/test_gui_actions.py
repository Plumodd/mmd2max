"""GUI 动作区（导出 / 生成脚本 / 跑 Max）的测试。

动作区是 GUI 唯一会写盘、唯一会碰 3ds Max 的地方，所以这里把三件事钉住：
  1. ① 只写产物文件，不启动任何进程；
  2. ② 走 obj_run --gen-only，同样不碰 Max；
  3. ③ 没产物就拒绝、弹确认才可能启动 —— 确认框在测试里一律答「取消」，
     绝不让测试把 3ds Max 拉起来（它会 resetMaxFile 并 taskkill）。
外加两条排版契约：「动作」横条不许被窗口缩小裁到屏幕外，窗口下限按控件申请值算。
需要能建 Tk 窗口；无显示环境自动 skip。
"""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from tests.make_sample import create_sample            # noqa: E402

import gui                                             # noqa: E402

SCALE = "8.763"


def _make_app():
    app = gui.App()
    app.withdraw()                 # 不真弹窗：手动 update() 泵事件即可
    return app


class ActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 一个进程里只建一个 Tk 根窗口：反复建/销 Tk 会把 after 回调留在旧解释器状态里
        try:
            cls.app = _make_app()
            cls.app.update()
        except Exception as exc:                        # noqa: BLE001
            raise unittest.SkipTest(f"本机建不起 Tk 窗口：{exc}")
        cls.addClassCleanup(cls.app.destroy)
        cls.real_ask = gui.messagebox.askokcancel

    @classmethod
    def tearDownClass(cls):
        gui.messagebox.askokcancel = cls.real_ask

    def setUp(self):
        self.app = self.__class__.app
        self.temp = Path(tempfile.mkdtemp(prefix="mmd2max_gui_"))
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.pmx = create_sample(self.temp / "src")
        self.out = self.temp / "out"
        self.app.physics_var.set(False)
        self.app.shot_var.set(True)
        self.app.noskin_var.set(False)
        self.app.path_var.set(str(self.pmx))
        self.app.outdir_var.set(str(self.out))
        self.app.auto_scale_var.set(False)
        self.app.scale_var.set(SCALE)
        self.app.base_rows = None
        self.app.last_plan = None
        self.app.preview = None
        self.app.log_text.configure(state="normal")
        self.app.log_text.delete("1.0", "end")
        self.app.log_text.configure(state="disabled")
        self.asked = []
        gui.messagebox.askokcancel = self._fake_ask
        self.wait(self.app._kick)

    def _fake_ask(self, title, text, **kwargs):
        self.asked.append((title, text, kwargs))
        return False

    def pump(self, seconds=120.0):
        """替 Tk 泵事件：窗口是 withdraw 的，只能手动喂。"""
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            if not self.app.busy and not self.app.job_title \
                    and self.app.last_plan is not None:
                return
            time.sleep(0.02)
        self.fail("等待 GUI 事件循环超时")

    def wait(self, action, seconds=120.0):
        action()
        self.pump(seconds)

    def log_text(self):
        return self.app.log_text.get("1.0", "end")

    # ------------------------------------------------------------------ 用例
    def test_export_writes_products_only(self):
        self.wait(self.app._export)
        stem = self.pmx.stem
        for name in (f"{stem}.obj", f"{stem}_bones.tsv", f"{stem}_weights.tsv",
                     f"{stem}_biped_map.tsv", f"{stem}_limbs.tsv"):
            self.assertTrue((self.out / name).is_file(), name)
        # 产物里不含 Max 相关残留：① 不该生成脚本、不该有 .max
        self.assertFalse((self.out / "mmd2max_run.ms").exists())
        self.assertFalse(list(self.out.glob("*.max")))
        self.assertIn("已导出", self.app.status_var.get())
        self.assertIn("下一步", self.log_text())

    def test_export_failure_is_visible_not_swallowed(self):
        # 拿一个「已存在的文件」当导出目录：mkdir 必然失败，错误要出现在状态行/日志里
        self.app.outdir_var.set(str(self.pmx))
        self.wait(self.app._export)
        self.assertIn("失败", self.app.status_var.get())
        self.assertTrue(self.log_text().strip())

    def test_gen_ms_runs_obj_run_without_touching_max(self):
        self.wait(self.app._export)
        self.wait(self.app._gen_ms)
        script = self.out / "mmd2max_run.ms"
        self.assertTrue(script.is_file(), self.log_text()[-800:])
        self.assertIn("MMD2MAX_OUTPUT", script.read_text(encoding="utf-8"))
        # --gen-only 明确不启动 Max：没有 .max、没有 success 标记、没有 objrun 日志
        self.assertFalse(list(self.out.glob("*.max")))
        self.assertFalse(list(self.out.glob("*.success.json")))
        self.assertFalse(list(self.out.glob("*_objrun.log")))
        self.assertIn("脚本已生成", self.app.status_var.get())

    def test_run_max_refuses_without_products(self):
        self.app._run_max()
        self.pump(10.0)
        self.assertIn("还没有导出产物", self.app.status_var.get())
        self.assertEqual(self.asked, [])           # 连确认框都不该弹

    def test_run_max_asks_before_launch_and_honours_cancel(self):
        self.wait(self.app._export)
        self.wait(self.app._run_max)
        self.assertEqual(len(self.asked), 1, self.asked)
        title, body, options = self.asked[0]
        # 确认框必须把两件不可逆的事说清楚（用户明确要求的约定）
        self.assertIn("resetMaxFile", body)
        self.assertIn("taskkill", body)
        self.assertIn("3ds Max", title)
        # 回车键不许把 Max 拉起来：默认焦点必须落在「取消」
        self.assertEqual(options.get("default"), gui.messagebox.CANCEL)
        self.assertEqual(options.get("icon"), "warning")
        # 答「取消」后不许有任务在跑，也不许留下 Max 的产物
        self.assertEqual(self.app.job_title, "")
        self.assertFalse(list(self.out.glob("*.success.json")))
        self.assertIn("已取消", self.app.status_var.get())

    def test_suffix_follows_the_checkboxes(self):
        self.assertEqual(self.app._max_suffix(), "")
        self.app.noskin_var.set(True)
        self.assertEqual(self.app._max_suffix(), "_noskin")
        self.app.physics_var.set(True)
        self.assertEqual(self.app._max_suffix(), "_noskin_phys")
        self.app.noskin_var.set(False)
        self.assertEqual(self.app._max_suffix(), "_phys")

    def test_max_argv_mirrors_the_checkboxes(self):
        tail = [str(a) for a in self.app._max_argv(self.out, self.pmx)[1:]]
        self.assertEqual(Path(tail[0]).name, "obj_run.py")
        self.assertEqual(tail[1:], [str(self.out), self.pmx.stem, "--shot"])
        self.app.physics_var.set(True)
        self.app.shot_var.set(False)
        tail = [str(a) for a in self.app._max_argv(self.out, self.pmx)[1:]]
        self.assertEqual(tail[1:], [str(self.out), self.pmx.stem, "--physics"])

    def test_max_report_tells_where_the_shots_are(self):
        (self.out / "shots").mkdir(parents=True)
        (self.out / "shots" / "front.png").write_bytes(b"x")
        missing = self.out / "sample.max"
        report = self.app._max_report(self.out, self.pmx, missing, 0)
        self.assertIn("没生成", report)
        self.assertIn("截图 1 张", self.log_text())
        missing.write_bytes(b"0" * 2048)
        report = self.app._max_report(self.out, self.pmx, missing, 0)
        self.assertIn("完成", report)
        self.assertIn("sample.max（2 KB）", self.log_text())
        self.assertIn("成功标记 不在", self.log_text())

    def test_action_row_cannot_be_squeezed_off_screen(self):
        """pack 按申请顺序切分空间：底部横条必须先 pack。否则窗口一缩小，
        第一个被裁到屏幕外的就是「③ 跑 3ds Max」—— 按钮按不到，界面等于废了。"""
        order = [str(w) for w in
                 self.app.tk.splitlist(self.app.tk.call("pack", "slaves", self.app._w))]
        expanders = [c for c in self.app.winfo_children() if c.pack_info()["expand"]]
        self.assertEqual(len(expanders), 1, "只该有一个会伸缩的中间区")
        body = str(expanders[0])
        bottom = [str(c) for c in self.app.winfo_children()
                  if c.pack_info()["side"] == "bottom"]
        self.assertIn(str(self.app.btn_export.master), bottom)   # 「动作」横条
        self.assertTrue(all(order.index(w) < order.index(body) for w in bottom),
                        f"底部横条必须在伸缩区之前 pack：{order}")

    def test_min_size_is_derived_not_guessed(self):
        """下限按控件申请值算（写死的数字换台机器/字体就会裁内容）：
        高度要容得下所有横条，宽度要容得下最宽的那一条（「动作」行 = 三颗按钮）。
        建窗之后不再跟着内容爬，免得用户每算一次窗口下限就变一点。"""
        panel = self.app.btn_export.master
        width, height = self.app.minsize()
        self.assertEqual(height, self.app.winfo_reqheight())
        self.assertGreaterEqual(width, panel.winfo_reqwidth() + 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
