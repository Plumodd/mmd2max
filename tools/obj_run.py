"""生成 OBJ 装配脚本并驱动 3ds Max 执行，全程记录日志与弹窗处理情况。

用法: python tools/obj_run.py <obj目录> <模型名> [--no-skin]

装配方式只有一种：人形骨 -> Biped 节点，其余附属骨 -> Bone。v2 的纯 Bone
模式（第三个位置参数 "bone"）已删除，不再接受该参数。

--no-skin 只导入网格与建骨、不挂 Skin，用于快速冒烟验证脚本能否跑通
（首次实跑建议先加它，几十秒即可暴露 MaxScript 语法/运行时错误）。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from desktop import (  # noqa: E402
    capture_region,
    capture_screen,
    find_windows,
    list_windows,
    focus,
    paste_text,
    press_keys,
)
from uia_dialog import invoke_safe_button  # noqa: E402

MAX_EXE = r"C:\Program Files\Autodesk\3ds Max 2022\3dsmax.exe"
STARTUP_DIR = (Path.home() / "AppData/Local/Autodesk/3dsMax"
               / "2022 - 64bit/ENU/scripts/startup")
SHOTS = TOOLS.parent / "outputs" / "shots"
TEMPLATE = TOOLS.parent / "scripts" / "max_import_obj.ms"

# 启动弹窗白名单：标题关键字 -> 唯一允许点的那个按钮的确切名字。
# 这两个框都是「点它什么都不改」的确认键；右边那个 "Restore Factory Settings…"
# 会把用户的 Max 设置清零，永远不在候选里，也永远不点。
SAFE_STARTUP_BUTTONS = {
    "Startup Failure": "Continue Without Restore",
    "Heads Up": "Continue",
}


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _ascii_inputs(base: Path, stem: str, physics: bool) -> dict:
    """把可能有中文名的导出文件复制成固定 ASCII 名，规避 MaxScript openFile 的
    本地代码页(GBK)解析失败。返回 ascii 路径字典（物理/表情按开关置空）。"""
    mapping = {
        "obj": (base / (stem + ".obj"), base / "model.obj"),
        "bones": (base / (stem + "_bones.tsv"), base / "bones.tsv"),
        "weights": (base / (stem + "_weights.tsv"), base / "weights.tsv"),
        "biped_map": (base / (stem + "_biped_map.tsv"), base / "biped_map.tsv"),
        "limbs": (base / (stem + "_limbs.tsv"), base / "limbs.tsv"),
        "widths": (base / (stem + "_widths.tsv"), base / "widths.tsv"),
        "morphs": (base / (stem + "_morphs.tsv"), base / "morphs.tsv"),
        "physics": (base / (stem + "_physics.tsv"), base / "physics.tsv"),
    }
    out = {}
    for key, (src, dst) in mapping.items():
        if not src.is_file():
            out[key] = ""
            continue
        if key in ("physics",) and not physics:
            out[key] = ""
            continue
        try:
            if key == "obj":
                # mtllib 指向的 MTL 文件名含中文，Max 的 OBJ 导入器按 ANSI 读会乱码 →
                # 统一改成 ASCII 名 model.mtl，并把 MTL 一起复制过来（贴图路径在里面）。
                text = src.read_bytes().decode("utf-8", errors="replace")
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if line.startswith("mtllib "):
                        lines[i] = "mtllib model.mtl"
                        break
                dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
                mtl_src = base / (stem + ".mtl")
                if mtl_src.is_file():
                    (base / "model.mtl").write_bytes(mtl_src.read_bytes())
            else:
                dst.write_bytes(src.read_bytes())
            out[key] = _esc(dst)
        except OSError:
            # 复制失败则回退到原名（仍可能成功）
            out[key] = _esc(src)
    return out


def build_script(base: Path, stem: str, vertex_count: int,
                 skin: bool = True, suffix: str | None = None,
                 shot: bool = False, physics: bool = True) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    out_name = stem if suffix is None else f"{stem}_{suffix}"

    # 读取导出阶段写出的 _meta.tsv：若含 SDEF/QDEF，则让 Max 端开启双四元数混合
    dq = "false"
    spine_links = 3
    fingers = 5
    finger_links = 3
    # 脚链骨盒的俯仰：默认 true = 踝→球的斜轴（骨盒指回自己的子关节，#40）。
    # 导出侧若要压平量截面，会把口径写进 meta（foot_pitch=0），这里跟着压平 ——
    # 两端必须同一个值，否则「按平轴量出来的高度」又被写进斜着的盒子里。
    foot_pitch = "true"
    meta_path = base / f"{stem}_meta.tsv"
    if meta_path.is_file():
        for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "\t" not in line:
                continue
            key, _, val = line.partition("\t")
            if key == "has_sdef" and val.strip() == "1":
                dq = "true"
            elif key == "has_qdef" and val.strip() == "1":
                dq = "true"
            elif key == "foot_pitch":
                foot_pitch = "true" if val.strip() == "1" else "false"
            elif key == "fingers":
                try:
                    fingers = int(val.strip())
                except ValueError:
                    fingers = 5
            elif key == "finger_links":
                try:
                    finger_links = int(val.strip())
                except ValueError:
                    finger_links = 3
            elif key == "spine_links":
                try:
                    spine_links = int(val.strip())
                except ValueError:
                    spine_links = 3

    # 中文名导出文件 -> ASCII 临时副本，避免 MaxScript 读取时按 GBK 解析失败
    inputs = _ascii_inputs(base, stem, physics)
    header = (
        "/* mmd2max OBJ 装配脚本（自动生成，自包含） */\n"
        f'MMD2MAX_OBJ = "{inputs["obj"]}"\n'
        f'MMD2MAX_BONES = "{inputs["bones"]}"\n'
        f'MMD2MAX_WEIGHTS = "{inputs["weights"]}"\n'
        f'MMD2MAX_VERTS = "{vertex_count}"\n'
        f'MMD2MAX_OUTPUT = "{_esc(base / (out_name + ".max"))}"\n'
        f'MMD2MAX_MAP = "{inputs["biped_map"]}"\n'
        f'MMD2MAX_MARKER = "{_esc(base / (out_name + ".success.json"))}"\n'
        f'MMD2MAX_LOG = "{_esc(base / (out_name + "_objrun.log"))}"\n'
        f'MMD2MAX_DQ = {dq}\n'
        f'MMD2MAX_SPINE_LINKS = {spine_links}\n'
        f'MMD2MAX_FOOTPITCH = {foot_pitch}\n'
        f'MMD2MAX_FINGERS = {fingers}\n'
        f'MMD2MAX_FINGER_LINKS = {finger_links}\n'
        f'MMD2MAX_MORPHS = "{inputs["morphs"]}"\n'
        f'MMD2MAX_PHYSICS = "{inputs["physics"]}"\n'
        f'MMD2MAX_LIMBS = "{inputs["limbs"]}"\n'
        f'MMD2MAX_WIDTHS = "{inputs["widths"]}"\n'
        f'MMD2MAX_SKIN = {"true" if skin else "false"}\n'
        f'MMD2MAX_SHOT = {"true" if shot else "false"}\n'
        f'MMD2MAX_SHOTDIR = "{_esc(base / "shots")}"\n'
        "MMD2MAX_INTERACTIVE = false\n"
        "MMD2MAX_QUIET = true\n\n"
    )
    return header + template


def _esc(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def handle_startup_dialog() -> bool:
    """处理挡路的启动弹窗。只按 UIA 按钮名点白名单里那一个，其余一律不动。

    旧写法是「find_windows → 截图找最右侧色块 → 按坐标 click」，两处都会出事：
    那个 Qt 对话框实测 0 个子 HWND，坐标法够不着屏幕外的窗口；而它右边那个按钮
    是 "Restore Factory Settings…"，点下去把用户的 Max 设置真清零。
    现在判定权交给 UIA（能读 Qt 窗口的 Name，Win32 有时只看到空标题），
    白名单名字对不上就只记日志、什么都不点 —— 宁可卡住让人来看，也不猜。
    """
    handled = False
    for needle, button in SAFE_STARTUP_BUTTONS.items():
        ok, info = invoke_safe_button(needle, button)
        if not info["windows"]:
            continue                      # 没弹这个框是常态，不打日志刷屏
        _capture_best_effort(needle)
        if ok:
            log(f"已按名字点掉「{needle}」里的 {button}"
                f"（观测按钮：{[b['name'] for b in info['buttons']]}）")
            time.sleep(2)
            handled = True
        else:
            log(f"「{needle}」里没找到白名单按钮 {button}，什么都不点。"
                f"观测到：{[b['name'] for b in info['buttons']] or info}")
    return handled


def _capture_best_effort(needle: str) -> None:
    """能给 Win32 矩形就顺手截一张，纯留证据；拿不到也不算失败。"""
    try:
        hits = find_windows(needle)
        if not hits:
            return
        left, top, right, bottom = hits[0]["rect"]
        if right - left <= 0 or bottom - top <= 0:
            return
        SHOTS.mkdir(parents=True, exist_ok=True)
        capture_region(str(SHOTS / f"objrun_dialog_{needle.replace(' ', '_')}.bmp"),
                       left, top, right - left, bottom - top)
    except Exception as error:  # noqa: BLE001
        log(f"弹窗截图失败（不影响点按钮）：{error}")


GRACEFUL_EXIT_SECONDS = 180

# 每几轮轮询查一次启动弹窗：一次 UIA 查询要起一个 PowerShell 进程（约 1~2 秒），
# 每 5 秒都查会把轮询本身拖慢。弹窗是模态的，晚 10 秒点掉没有区别。
STARTUP_DIALOG_EVERY = 3


def wait_max_exit(proc, timeout: float = GRACEFUL_EXIT_SECONDS) -> bool:
    """等 3ds Max 自己退出（装配脚本末尾会 quitMax quiet:true）。True = 进程已结束。

    判据只看 proc.poll()，不看窗口：Max 退出时可能还留着子进程/窗口，
    主进程退了就算干净收尾。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(3)
    return proc.poll() is not None


def main_window(require_idle: bool = True) -> dict | None:
    """找到 Max 主窗口。require_idle 为真时排除标题带「未响应」的窗口。"""
    for window in list_windows():
        title = window["title"]
        if "3ds Max" not in title:
            continue
        if "Startup Failure" in title:
            continue
        if require_idle and "未响应" in title:
            continue
        if window["rect"][2] - window["rect"][0] > 900:
            return window
    return None


def run_via_listener(script: Path) -> bool:
    """聚焦 Max -> F11 打开 MAXScript 监听器 -> 粘贴 fileIn -> 回车。"""
    window = main_window()
    if window is None:
        log("未找到空闲的 Max 主窗口")
        return False
    log(f"聚焦主窗口 {window['title'].strip()!r}")
    focus(window["hwnd"])
    time.sleep(1.5)
    press_keys("F11")
    time.sleep(2.5)
    command = f'fileIn @"{script}"'
    log(f"粘贴命令：{command}")
    paste_text(command)
    time.sleep(0.8)
    press_keys("ENTER")
    return True


FLOATER_CLASS = "maxscriptdialog"     # 插件浮动面板（BsKeyTools 等）的窗口类，标题恒为空


def is_maxscript_error_dialog(window: dict, owner_pid: int | None = None) -> bool:
    r"""区分「MAXScript 报错弹窗」和「长得像它的插件浮动面板 / 别的 Max 窗口」。

    实跑踩过两次误判，代价都是「整轮装配被判失败 + Max 现场被保留」：
      1. BsKeyTools 这类浮动面板的窗口类正好叫 `MAXScriptDialog`、标题为空字符串；
      2. 上一轮探针留下的 MAXScript **文档窗口**（标题
         `C:\...\probe_syntax.ms - MAXScript`）—— 它是别的 Max 进程的窗口，
         本机新启动的 Max 还没来得及跑脚本，就被判成「出现报错弹窗」，
         轮询 1 秒即退出、装配脚本还被 finally 删掉，整轮白跑。
    真报错弹窗的标题只有错误种类（如「MAXScript Error」
    「MAXScript Auto-load Script Error Exception」），绝不含文件路径。
    owner_pid 给了就只认该进程自己的窗口（窗口没带 pid 信息时不误杀）。
    """
    title = window["title"].strip()
    if not title:
        return False
    if "maxscript" not in title.lower():
        return False
    if "listener" in title.lower():
        return False
    lowered = title.lower()
    if ".ms " in lowered or lowered.endswith(".ms") or ":\\" in title or ":/" in title:
        return False            # 文档/编辑器窗口：标题里带脚本路径
    if window.get("cls", "").lower() == FLOATER_CLASS:
        return False
    pid = window.get("pid")
    if owner_pid is not None and pid is not None and pid != owner_pid:
        return False
    return True


def handle_maxscript_dialog(capture_dir: Path | None = None,
                            owner_pid: int | None = None) -> str | None:
    """捕获 MAXScript 报错对话框的截图（标题含 'MAXScript' 且非监听器/浮动面板）。

    关键约束：**只截图、绝不点 OK、绝不关闭弹窗**——把弹窗留给用户自己看/截，
    同时把截图存盘便于我们用 ViewImage 自行读取错误文本。
    owner_pid 传本轮启动的 Max 进程号，别的 Max 实例留下的窗口一律不算。
    返回截图路径（成功时）或 None。
    """
    for window in find_windows("MAXScript"):
        if not is_maxscript_error_dialog(window, owner_pid):
            continue
        title = window["title"]
        left, top, right, bottom = window["rect"]
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            continue
        shot = (capture_dir or SHOTS) / "maxscript_dialog.bmp"
        try:
            (capture_dir or SHOTS).mkdir(parents=True, exist_ok=True)
            capture_region(str(shot), left, top, width, height)
            log(f"已保存报错弹窗截图：{shot}（标题：{title.strip()!r}）【未点 OK，弹窗保持打开】")
            return str(shot)
        except Exception as error:  # noqa: BLE001
            log(f"MAXScript 对话框截图异常：{error}")
        return None
    return None


def handle_dialogs() -> None:
    handle_startup_dialog()
    handle_maxscript_dialog()


def result_code(base: Path, name_part: str, dialog_seen: bool = False,
                dialog_path: str | None = None) -> int:
    """把「跑完之后磁盘上有什么」翻译成退出码。只有 0 才允许 GUI 报「完成」。

    四个产物路径全部由 (目录, 输出名) 推出，所以这里不接 main() 的局部变量，
    单测得以在不启动 Max 的前提下逐种组合核对。
    """
    marker = base / f"{name_part}.success.json"
    error_file = Path(str(marker) + ".error.txt")
    max_file = base / f"{name_part}.max"
    logfile = base / f"{name_part}_objrun.log"
    if marker.is_file() and max_file.is_file():
        log(f"完成：{max_file}")
        return 0
    log("失败：本轮没有可交付的装配结果（GUI 不许报「完成」）")
    if marker.is_file():
        log(f"  成功标记在，但场景文件缺失：{max_file}（saveMaxFile 没落盘）")
        return 6
    if error_file.is_file():
        log(f"  脚本报错：{error_file.read_text(encoding='utf-8', errors='replace').strip()}")
        return 4
    if dialog_seen:
        log(f"  MAXScript 报错弹窗（截图：{dialog_path}），Max 现场已保留")
        return 3
    log(f"  无成功标记、无 error.txt：超时或 Max 自行退出。日志：{logfile}")
    return 5


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        log('用法: python tools/obj_run.py <obj目录> <模型名> [--no-skin] [--physics] '
            '[--shot] [--manual] [--gen-only]')
        return 2
    if len(args) > 2:
        # v2 的第三个位置参数是装配模式（bone / biped）；v3 只剩 Biped + 附属骨。
        # 宁可报错也不要悄悄忽略旧命令行，否则用户以为跑了 biped 其实参数没生效。
        log(f"多余的位置参数 {args[2:]}：v3 不再支持模式选择，固定为 Biped + 附属骨。")
        return 2
    base = Path(args[0]).resolve()
    stem = args[1]
    # --no-skin：只导入网格与建骨，不挂 Skin。用于快速冒烟，验证脚本能跑通。
    skin = "--no-skin" not in sys.argv
    suffix = None if skin else "noskin"
    # 物理（刚体/关节代理）改为**默认关闭**：用户明确要求「裙子上挂 Bone 就够，
    # 不要刚体」。需要时显式传 --physics 才生成。
    physics = "--physics" in sys.argv
    # 默认（无物理）输出就用干净名字：克雷薇.max；只有显式开物理才加 _phys。
    if physics:
        suffix = f"{suffix}_phys" if suffix else "phys"
    # --shot：装配完成后额外抓正视图/顶视图线框截图，便于核对骨骼贴合。
    shot = "--shot" in sys.argv
    # --manual：仅生成脚本并以「分离」方式启动 Max，不轮询/不点弹窗/不杀进程，
    # 全部交人工操作（截图、调试 MAXScript 报错等）。
    manual = "--manual" in sys.argv
    # --gen-only：只生成装配脚本并写出，绝不启动/轮询/杀 Max。用于「用户自己手动
    # 在 Max 里 File▸Run Script」的工作流——助手只交付脚本路径，不做任何进程操作。
    gen_only = "--gen-only" in sys.argv
    log(f"skin={skin} physics={physics} shot={shot} manual={manual} gen_only={gen_only}")

    weights = base / f"{stem}_weights.tsv"
    vertex_count = sum(1 for _ in weights.open(encoding="utf-8"))
    log(f"权重文件 {weights} 顶点数 {vertex_count}")

    script_text = build_script(base, stem, vertex_count,
                               skin=skin, suffix=suffix, shot=shot, physics=physics)
    name_part = stem if suffix is None else f"{stem}_{suffix}"
    # 用固定 ASCII 文件名：中文 .ms 路径经 -U MAXScript 启动时容易被按
    # 本地代码页(GBK)误解析，导致 "auto-load script error / line 1" 失败。
    # 生成前先做 MAXScript 静态体检：保留字（off/ok/mapped…）、括号平衡、
    # if...do+else、localTime()、行首续行。这类问题会让整个脚本在「编译阶段」
    # 就失败，用户只能看到一句 Syntax error，而且第一个错误会把后面的全挡住
    # （改一个冒一个）。在 Python 侧先拦下来。
    from check_ms import check_source
    problems = check_source(script_text)
    if problems:
        log("生成的 MAXScript 有语法问题，已中止（未写文件）：")
        for item in problems[:20]:
            log("   " + item)
        return 2

    out_script = base / "mmd2max_run.ms"
    # 注意：必须用无 BOM 的 utf-8。带 BOM(utf-8-sig) 会让 MAXScript 在
    # 自动加载时把文件头 BOM 字节当成语法错误，报 "line 1" 解析失败。
    out_script.write_text(script_text, encoding="utf-8")
    log(f"已生成脚本 {out_script}")

    if gen_only:
        # 只交付脚本，完全不碰 Max 进程（用户自行在 Max 里 Run Script）。
        log(f"已生成脚本（gen-only，不启动 Max）：{out_script}")
        log("请在 3ds Max 菜单 File ▸ Run Script 载入该脚本，或在 MAXScript 监听器执行：")
        log(f'  fileIn @"{out_script}"')
        return 0

    if manual:
        # 用 cmd /c start 把 Max 完全分离启动：即便本 Python 进程退出，Max
        # 也不会被 Bash 后台任务拆掉，全部交给人工。脚本保留不删除。
        launch_cmd = f'cmd /c start "" "{MAX_EXE}" -U MAXScript "{out_script}"'
        try:
            subprocess.Popen(launch_cmd, shell=True)
            log(f"已分离启动 Max（手动模式）：{launch_cmd}")
        except Exception as error:  # noqa: BLE001
            log(f"启动 Max 失败：{error}")
        log(f"Max 现已独立运行，脚本保留于：{out_script}")
        log("请手动在 Max 中查看 / 截图 / 调试；如需重跑脚本，可在 MAXScript 监听器执行：")
        log(f'  fileIn @"{out_script}"')
        return 0

    marker = base / f"{name_part}.success.json"
    error_file = Path(str(marker) + ".error.txt")
    logfile = base / f"{name_part}_objrun.log"
    for stale in (marker, error_file, logfile):
        stale.unlink(missing_ok=True)

    # 清理可能残留的旧启动脚本，避免下次 Max 启动误执行
    try:
        (STARTUP_DIR / "mmd2max_objrun.ms").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass

    # 通过 -U MAXScript 让 Max 启动后自动执行装配脚本
    # （UI/视口已就绪，适合截图；不依赖启动目录的时序猜测）
    proc = subprocess.Popen([MAX_EXE, "-U", "MAXScript", str(out_script)])
    log(f"3dsmax pid={proc.pid}（-U MAXScript 执行 {out_script}）")

    # 报错弹窗截图统一存到 <base>/shots，便于 ViewImage 自行读取错误文本
    capture_dir = base / "shots"
    capture_dir.mkdir(parents=True, exist_ok=True)

    deadline = time.time() + 3600
    last_size = -1
    dialog_seen = False
    dialog_path = None
    marker_seen_at = None
    ticks = 0
    try:
        while time.time() < deadline:
            # 只截图、不点 OK、不关弹窗；一旦捕获到报错弹窗就停轮询，保留 Max 现场
            dlg = handle_maxscript_dialog(capture_dir, proc.pid)
            if dlg:
                dialog_seen = True
                dialog_path = dlg
                log("已捕获报错弹窗截图，停止轮询并保留 Max 现场（你可随时手动截图）。")
                # 同时尝试读取模板写入的 error.txt（若有更详细的文本）
                if error_file.is_file():
                    log(f"error.txt: {error_file.read_text(encoding='utf-8', errors='replace').strip()}")
                break
            # UIA 每次要起一个 PowerShell（约 1~2s），所以 3 轮才查一次；
            # 脚本已经在跑（标记都出来了）就不可能再冒启动对话框，直接不查。
            ticks += 1
            if marker_seen_at is None and ticks % STARTUP_DIALOG_EVERY == 1:
                handle_startup_dialog()
            size = logfile.stat().st_size if logfile.is_file() else -1
            if size != last_size:
                last_size = size
                for line in logfile.read_text(encoding="utf-8",
                                              errors="replace").splitlines()[-6:]:
                    log(f"  | {line}")
            if marker.is_file():
                if marker_seen_at is None:
                    marker_seen_at = time.time()
                    log("成功标记已出现（.max 已落盘），继续等视口截图抓完、Max 自行退出")
                if proc.poll() is not None:
                    log("3ds Max 已正常退出（崩溃计数未被污染）")
                    break
                if time.time() - marker_seen_at > GRACEFUL_EXIT_SECONDS:
                    log(f"标记出现后 {GRACEFUL_EXIT_SECONDS}s 仍未退出，进入收尾")
                    break
            else:
                if error_file.is_file():
                    log(f"失败：{error_file.read_text(encoding='utf-8', errors='replace').strip()}")
                    break
            time.sleep(5)
        if dialog_seen and not marker.is_file():
            log(f"警告：装配过程中出现 MAXScript 报错对话框，截图：{dialog_path}")
    finally:
        try:
            out_script.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        if dialog_seen:
            # 保留 Max 与报错弹窗现场：不杀进程，方便人工查看/截图
            log(f"Max 现场保留未杀：报错弹窗截图见 {dialog_path}（如需结束请手动关闭 3ds Max）。")
        else:
            # 装配脚本末尾会自己 quitMax() 正常退出，这里只等它。
            # 以前是无条件 `taskkill /F`，强杀被 Max 记成「上次会话异常终止」，
            # 计数一路累积（实测 HKCU\Software\Autodesk\3dsMax\24.0\MiniDump\
            # 24.3.0.3404：SessionStartCount=167 / SessionCleanCloseCount=50），
            # 之后每次启动都弹 "Startup Failure Detection" 那个 Qt 模态框 ——
            # 它挡住 -U MAXScript 的自动执行，用户不在电脑前就是死锁。
            # taskkill 只留作超时兜底（宁可多等，也别再污染崩溃计数）。
            if not wait_max_exit(proc, GRACEFUL_EXIT_SECONDS):
                log(f"Max 在 {GRACEFUL_EXIT_SECONDS}s 内没自行退出，兜底 taskkill /F"
                    "（这会让 Max 记一次异常终止，下次启动可能弹 Startup Failure Detection）")
                subprocess.run(["taskkill", "/F", "/IM", "3dsmax.exe"],
                               capture_output=True, text=True, errors="replace")

    # 退出码必须反映真实产物。实跑踩到：MAXScript 报错、既没有 .max 也没有成功
    # 标记，但本函数仍 return 0，GUI 于是把这一轮写成「完成」，用户以为装配成功。
    return result_code(base, name_part, dialog_seen, dialog_path)


if __name__ == "__main__":
    raise SystemExit(main())
