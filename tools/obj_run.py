"""生成 OBJ 装配脚本并驱动 3ds Max 执行，全程记录日志与弹窗处理情况。

用法: python tools/obj_run.py <obj目录> <模型名> [--no-skin]

装配方式只有一种：人形骨 -> Biped 节点，其余附属骨 -> Bone。v2 的纯 Bone
模式（第三个位置参数 "bone"）已删除，不再接受该参数。

--no-skin 只导入网格与建骨、不挂 Skin，用于快速冒烟验证脚本能否跑通
（首次实跑建议先加它，几十秒即可暴露 MaxScript 语法/运行时错误）。
"""
from __future__ import annotations

import os
import re
import string
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PROJECT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(PROJECT))

from desktop import (  # noqa: E402
    capture_region,
    capture_screen,
    find_windows,
    list_windows,
    focus,
    paste_text,
    press_keys,
)
from mmd2max import passes  # noqa: E402
from mmd2max.targets import TARGETS, cfg_values as target_cfg_values  # noqa: E402
from mmd2max.verdicts import (  # noqa: E402
    REPORT_NAME,
    SPEC_NAME,
    build_spec,
    parse_table,
    parse_weights,
    skin_expectation,
    spec_text,
)
from uia_dialog import invoke_safe_button  # noqa: E402

# 装配目标值在 CFG 里的键名（顺序 = mmd2max.targets.TARGETS 的声明顺序）。
# 只在这里取一次 keys()：Max 侧 mmd2maxLoadCfg 逐键必读，名单和 targets 分叉就是缺键中止。
TARGET_CFG_KEYS = tuple(TARGETS)

# 3ds Max 装在哪不写死。这里原来是一个写死的装目录字面量（C 盘 Program Files 下那台
# 2022）：换一台把 Max 装在别的盘、别的根（比如 D:\Programs）的机器就整条链路起不来，
# 而报的错只是「找不到文件」，使用者分不清是自己机器没装好还是脚本坏了。
# 发现顺序 = 环境变量 -> 注册表 -> 常见目录 glob，三条都只为拿到 <装目录>\3dsmax.exe。
# 版本仍优先 2022：全部探针与判据（骨盒、#rotation、.fig 载体）只在这一个版本上量过，
# 拿 2024 跑不是「更新更好」，是「没实测」——所以选中非 2022 时要在日志里说一声。
MAX_EXE_ENV = "MMD2MAX_MAX_EXE"
VERIFIED_MAX_YEAR = 2022
# 没注册表（绿色版、卸载残留、权限读不到）时才走这条：只认这些根下的 Autodesk\3ds Max *。
MAX_EXE_DIR_ROOTS = ("Program Files", "Program Files (x86)", "Programs", "Software")
MAX_EXE_HINT = (
    f"没找到 3ds Max 的 3dsmax.exe（试过环境变量 {MAX_EXE_ENV}、注册表 "
    r"HKLM\SOFTWARE\Autodesk\3dsMax 与各盘的 Autodesk\3ds Max *）。"
    f"装在别处就用 {MAX_EXE_ENV}=完整路径 指过来，例如 "
    f'set {MAX_EXE_ENV}=D:\\Programs\\Autodesk\\3ds Max 2022\\3dsmax.exe')


def _reg_max_exes() -> list[tuple[int, Path]]:
    """注册表里的 (版本年, 3dsmax.exe)。子键名是内部版本号：24.0 = 2022。

    只收 exe 真在盘上的那条 —— 这台机的 23.0（2021）还躺在注册表里，目录早没了，
    照着它启动就是拿一条陈旧记录去撞一个不存在的文件。
    """
    try:
        import winreg
    except ImportError:  # 非 Windows：没有注册表这条路
        return []
    out: list[tuple[int, Path]] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"SOFTWARE\Autodesk\3dsMax",
                     r"SOFTWARE\WOW6432Node\Autodesk\3dsMax"):
            try:
                root = winreg.OpenKey(hive, base)
            except OSError:  # 允许失败：这台机没写过这个键（未装、或装在别的厂商目录）
                continue
            with root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    name = winreg.EnumKey(root, i)
                    head = name.split(".")[0]
                    if not head.isdigit():  # "Current Version" 这类非版本子键
                        continue
                    try:
                        with winreg.OpenKey(root, name) as key:
                            loc = winreg.QueryValueEx(key, "Installdir")[0]
                    except OSError:  # 允许失败：这条版本键没带装目录
                        continue
                    exe = Path(loc) / "3dsmax.exe"
                    if exe.is_file():
                        out.append((int(head) + 1998, exe))
    return out


def _glob_max_exes() -> list[tuple[int, Path]]:
    """逐盘扫 <盘>\\<程序目录>\\Autodesk\\3ds Max <年>\\3dsmax.exe。"""
    out: list[tuple[int, Path]] = []
    for letter in string.ascii_uppercase:
        drive = Path(letter + ":\\")
        if not drive.is_dir():
            continue
        for root_name in MAX_EXE_DIR_ROOTS:
            vendor = drive / root_name / "Autodesk"
            if not vendor.is_dir():
                continue
            for entry in sorted(vendor.glob("3ds Max *")):
                matched = re.search(r"3ds Max (\d{4})", entry.name)
                exe = entry / "3dsmax.exe"
                if matched and exe.is_file():
                    out.append((int(matched.group(1)), exe))
    return out


def find_max_exe() -> Path | None:
    """要启动的 3dsmax.exe；一条候选都没有返回 None（在导入期抛错会让测试全灭）。"""
    override = os.environ.get(MAX_EXE_ENV, "").strip()
    if override:
        return Path(override).expanduser() if Path(override).is_file() else None
    seen: dict[str, tuple[int, Path]] = {}
    for year, exe in _reg_max_exes() + _glob_max_exes():
        seen.setdefault(str(exe).lower(), (year, exe))
    if not seen:
        return None
    # 先要 2022（判据在它上面量的），剩下的按新到旧；同一版本多条路径时取字典序第一个，
    # 至少每次跑都是同一条，不会因为扫描顺序变了就换一台 Max。
    ranked = sorted(seen.values(), key=lambda yx: (yx[0] != VERIFIED_MAX_YEAR, -yx[0]))
    return ranked[0][1]


def max_year_of_exe(exe: Path) -> int | None:
    """从目录名读版本年（3ds Max 2022 -> 2022）；读不出返回 None（不瞎猜）。"""
    matched = re.search(r"3ds Max (\d{4})", str(exe))
    return int(matched.group(1)) if matched else None


def max_startup_dirs(exe: Path) -> list[Path]:
    """上一代「把脚本丢进启动目录让它自动执行」留下的目录，清理用。

    目录名带版本和语言（`2022 - 64bit\\ENU`），语言这台机是 ENU、中文装可能是 CHS，
    所以按年份取、把下面实际存在的语言目录都扫出来，不猜。
    """
    local = Path(os.environ.get("LOCALAPPDATA")
                 or (Path.home() / "AppData" / "Local"))
    year = max_year_of_exe(exe) or VERIFIED_MAX_YEAR
    root = local / "Autodesk" / "3dsMax" / f"{year} - 64bit"
    if not root.is_dir():
        return []
    return [p for p in (d / "scripts" / "startup" for d in sorted(root.glob("*")))
            if p.is_dir()]

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
    本地代码页(GBK)解析失败。返回 ascii 路径字典（物理/表情按开关置空）。

    值这里是**原始 Path 或空串**，不做 MAXScript 转义：路径要落进 MMD2MAX.CFG
    当数据，转义属于「怎么把数据写进脚本」那一层，由 _cfg_text 处理。
    """
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
            out[key] = dst
        except OSError:
            # 复制失败则回退到原名（仍可能成功）
            out[key] = src
    return out


CFG_NAME = "MMD2MAX.CFG"

# CFG 自己没读成时（缺文件/缺键/值非法），MaxScript 侧连 MMD2MAX_MARKER 都还没拿到，
# 于是把错误写到 CFG 同目录的这个定名文件（见 max_import_obj.ms 的 mmd2maxErrorFilePath）。
# 名字两边各写一份、靠这条注释互相指过去 —— 改了这边不改那边，配置类失败就又变成
# 「只有监听器里有证据、上层报超时」。
CFG_ERROR_NAME = "mmd2max_cfg.error.txt"

# 胸部取景的两组 meta 键 + 对应的 CFG 键（#70）：框的六条进 CFG，极值的六条只给判据表当尺。
# 顺序固定成「同一轴的两端挨着」，_chest_ruler 与 mmd2max.verdicts 的切片都靠这个顺序。
CHEST_AXES = ("x", "y", "z")
CHEST_BOX_KEYS = tuple(f"chest_box_{axis}_{edge}"
                       for axis in CHEST_AXES for edge in ("lo", "hi"))
CHEST_CFG_KEYS = tuple(f"MMD2MAX_CHEST_{axis}_{edge}"
                       for axis in ("X", "Y", "Z") for edge in ("LO", "HI"))

# 装配侧要求的全部键，顺序即 CFG 落盘顺序。MAXScript 的 mmd2maxLoadCfg 逐键取数、
# 缺键即抛；build_cfg 结尾拿这份名单自查，两边键集分叉时在 Python 侧就报出来。
CFG_KEYS = (
    "MMD2MAX_OBJ", "MMD2MAX_BONES", "MMD2MAX_WEIGHTS", "MMD2MAX_VERTS",
    "MMD2MAX_OUTPUT", "MMD2MAX_MAP", "MMD2MAX_MARKER", "MMD2MAX_LOG",
    # 判据通道（M2b）：VERDICTS 是 Python 写的「该量什么、允许差多少」，Max 只往里读
    # 路径、往 REPORT 追加读数。两个都不在 CFG_EMPTY_OK 里 —— 没有报告文件这一轮
    # 就永远没法验收，那是「绿日志 + 没证据」，正是本项目反复踩的那类事故。
    "MMD2MAX_VERDICTS", "MMD2MAX_REPORT",
    "MMD2MAX_MORPHS", "MMD2MAX_PHYSICS", "MMD2MAX_LIMBS", "MMD2MAX_WIDTHS",
    "MMD2MAX_SPINE_LINKS", "MMD2MAX_FINGERS", "MMD2MAX_FINGER_LINKS",
    "MMD2MAX_FOOTBOXFLAT",
    # 胸部特写的取景框：以前这里是 .ms 里的「场景包围盒 ±10cm 与 高度 × 猜的比例」——
    # 第 35 轮那条 z 带比胸骨低 4cm（特写里一根骨都没有而填充率 63% 全绿），第 36 轮 z 修好
    # 了、x/y 仍按场景包围盒凑，框的前后中心被身后的裙摆拽到 y=+9.8，按宽高比收完只剩
    # y -0.4..20.0，站在 -11.75..-7.24 的两根骨照样被裁出框外。
    # 三个轴都由导出侧量，账见 mmd2max/export_obj._chest_band。
    *CHEST_CFG_KEYS,
    # 收敛循环的遍数上限（M3）：数值住在 mmd2max/passes.py 的注册表，Max 侧只读。
    # 以前它是 .ms 里的字面量 `for passNo = 1 to 3`，Python 只能靠契约测试去钉一个
    # 自己看不见的数；现在 audit_gate 拿同一个数核对 Max 报回来的 iters。
    "MMD2MAX_CONVERGE_MAX",
    # 装配目标值（容差/开关），全部来自 mmd2max.targets —— M2 把它们从 .ms 里的
    # `global MMD2MAX_DIRTOL = 3.0` 搬到这里：写的一方必须是有测试的一方。
    *TARGET_CFG_KEYS,
    "MMD2MAX_SKIN", "MMD2MAX_SHOT", "MMD2MAX_SHOTDIR",
    "MMD2MAX_INTERACTIVE",
)

# 允许是空串（= 该阶段整段跳过）的键；其余键为空一律在本函数里就拦掉。
# LIMBS 不在名单里：没有肢段表就没有闭环缩放，Biped 会退回默认人体比例。
# 这份名单必须和 MAXScript 侧 mmd2maxLoadCfg 的空路径校验一致。
# CHEST_* 六条是「允许空的数值键」：模型里没有叫「胸 / bust」的骨时导出量不出框，
# Max 那侧退回旧的场景包围盒带（mmd2maxCfgNumOpt 空串 → undefined）。
CFG_EMPTY_OK = ("MMD2MAX_MORPHS", "MMD2MAX_PHYSICS", "MMD2MAX_WIDTHS",
                *CHEST_CFG_KEYS)

# meta 里装配侧必须拿到的键 -> CFG 里的键（键名 = MAXScript 全局名，两边同一套词）。
# 缺任何一个键 = 中止。以前缺键退回这里硬编码的默认值，于是「一份没有 spine_links 的
# 旧 meta」会静默按 3 节脊柱建 Biped：日志全绿、几何却是另一套，正是 v4 要根除的
# 「配置有默认值」这一类。
# 值是空串也算「键在」：取景框那六条就是（见上面的 CFG_EMPTY_OK）。
META_TO_CFG = {
    "spine_links": "MMD2MAX_SPINE_LINKS",
    "fingers": "MMD2MAX_FINGERS",
    "finger_links": "MMD2MAX_FINGER_LINKS",
    "foot_box_flat": "MMD2MAX_FOOTBOXFLAT",
    **dict(zip(CHEST_BOX_KEYS, CHEST_CFG_KEYS)),
}

# 只给判据表当尺、不进 CFG 的 meta 键（#70）：胸骨自己的 (x, y, z) 端点极值。
# Max 拿它们是没用的（它不许按骨名找骨），Python 拿它们才判得出「特写里到底有没有那两根骨」：
# 框必须罩住这六极，而这六极来自导出的骨坐标，与框是怎么算出来的无关 —— 所以这一条
# 不是恒等式。第 35 轮缺的正是这把尺：当时只有填充率，框里没骨也照样全绿。
# 三个轴都要（只量 z 是第 36 轮的半成品）：#69 的按视口宽高比收框收的就是水平轴，
# 那一收把骨裁到框外，只有 y 的极值看得见这件事。
META_FOR_CHECK = tuple(f"chest_bone_{axis}_{edge}"
                       for axis in CHEST_AXES for edge in ("min", "max"))


def read_meta(base: Path, stem: str) -> dict:
    """读 `<名>_meta.tsv`（线格式 v2：第 1 行 `key<TAB>value`，`#` 行是人读注释）。"""
    path = base / f"{stem}_meta.tsv"
    if not path.is_file():
        raise SystemExit(f"缺少导出元信息文件：{path}\n"
                         "装配的每一项配置都来自导出结果，请先跑 tools/run_model.py。")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        key, _, value = line.partition("\t")
        key, value = key.strip(), value.strip()
        if key == "key" and value == "value":
            continue                      # 表头行本身，不是数据
        values[key] = value
    missing = [k for k in META_TO_CFG if k not in values]
    if missing:
        raise SystemExit(f"{path} 缺少必需的键：{'、'.join(missing)}"
                         "（装配侧不接受默认值，重跑导出即可）")
    return values


def build_cfg(base: Path, stem: str, vertex_count: int, skin: bool = True,
              suffix: str | None = None, shot: bool = False,
              physics: bool = False, interactive: bool = False) -> tuple[dict, Path]:
    """装配用的**全部**输入与开关，集中成一个 key→value 表，落到 <目录>/MMD2MAX.CFG。

    v3 的做法是在生成的 .ms 头部逐行注入 22 个 global。那带来一个查不出来的分裂：
    自动跑读的是注入头，人手 `fileIn scripts/max_import_obj.ms` 时得自己记得这 22 个
    值 —— 漏一个 flag（比如 MMD2MAX_FOOTPITCH）就摆出不同的几何，而两边日志都是绿的。
    现在两条路径都只靠一个 MMD2MAX_CFG 起点，其余全从同一份文件读，不可能分叉。
    值一律写**字面量**：bool 就写 true/false（不再 0/1 再取反），路径写原始反斜杠
    （转义是 .ms 那侧的事，不污染数据文件）。
    """
    meta = read_meta(base, stem)
    inputs = _ascii_inputs(base, stem, physics)
    out_name = stem if suffix is None else f"{stem}_{suffix}"
    cfg = {
        "MMD2MAX_OBJ": str(inputs["obj"]),
        "MMD2MAX_BONES": str(inputs["bones"]),
        "MMD2MAX_WEIGHTS": str(inputs["weights"]),
        "MMD2MAX_VERTS": str(int(vertex_count)),
        "MMD2MAX_OUTPUT": str(base / f"{out_name}.max"),
        "MMD2MAX_MAP": str(inputs["biped_map"]),
        "MMD2MAX_MARKER": str(base / f"{out_name}.success.json"),
        "MMD2MAX_LOG": str(base / f"{out_name}_objrun.log"),
        "MMD2MAX_VERDICTS": str(base / SPEC_NAME),
        "MMD2MAX_REPORT": str(base / REPORT_NAME),
        "MMD2MAX_MORPHS": str(inputs["morphs"]),
        "MMD2MAX_PHYSICS": str(inputs["physics"]),
        "MMD2MAX_LIMBS": str(inputs["limbs"]),
        "MMD2MAX_WIDTHS": str(inputs["widths"]),
        "MMD2MAX_SPINE_LINKS": meta["spine_links"],
        "MMD2MAX_FINGERS": meta["fingers"],
        "MMD2MAX_FINGER_LINKS": meta["finger_links"],
        "MMD2MAX_FOOTBOXFLAT": meta["foot_box_flat"],
        # 胸部特写的取景框（#70）：六个数，空串 = 导出侧量不出胸骨，Max 退回场景包围盒带。
        "MMD2MAX_CHEST_X_LO": meta["chest_box_x_lo"],
        "MMD2MAX_CHEST_X_HI": meta["chest_box_x_hi"],
        "MMD2MAX_CHEST_Y_LO": meta["chest_box_y_lo"],
        "MMD2MAX_CHEST_Y_HI": meta["chest_box_y_hi"],
        "MMD2MAX_CHEST_Z_LO": meta["chest_box_z_lo"],
        "MMD2MAX_CHEST_Z_HI": meta["chest_box_z_hi"],
        "MMD2MAX_CONVERGE_MAX": str(passes.max_iters("converge")),
        "MMD2MAX_SKIN": "true" if skin else "false",
        "MMD2MAX_SHOT": "true" if shot else "false",
        "MMD2MAX_SHOTDIR": str(base / "shots"),
        # 交互位 = 「这一轮有人盯着屏幕」。只有 --manual / --gen-only 会打开它：自动跑
        # 点不掉弹窗，所以那边必须是 false（跑完自己 quitMax、重置场景不问人）。以前这个键
        # 恒为 false，于是 .ms 里「保留现场给人看 / 重置前问一句」的分支一次也没跑到过，
        # 而 --manual 的提示语写着「请手动在 Max 中查看」——脚本其实一跑完就把 Max 关了，
        # 话是假的。（另一半短路 MMD2MAX_QUIET 恒为 true，已在本轮一并删掉。）
        "MMD2MAX_INTERACTIVE": "true" if interactive else "false",
    }
    # 目标值（容差/开关）只有一个来源：mmd2max.targets。以前它们是 .ms 里的 `global X = 3.0`，
    # Python 侧看不见这些判据，也就没法校验（M2 的 verdicts 表要用的正是这几个数）。
    cfg.update(target_cfg_values())
    bad = [k for k, v in cfg.items()
           if v in ("True", "False", "None", "") and k not in CFG_EMPTY_OK]
    if bad:
        # Path 忘了 str()、bool 写成 Python 的 True、必需路径为空 —— 都会在 Max 侧
        # 变成「打不开文件」这种离根因很远的报错，在这里就拦掉。
        raise SystemExit(f"CFG 的值不合法（必需项为空或是 Python 字面量）：{'、'.join(bad)}")
    if set(cfg) != set(CFG_KEYS):
        # MAXScript 侧 mmd2maxLoadCfg 逐键取数、缺键即抛。两边键集一旦分叉，
        # 报错会落在「CFG 里没有键 X」这种离根因很远的地方，这里先自家对齐。
        raise SystemExit(f"CFG 键集与 CFG_KEYS 不一致：多 {sorted(set(cfg) - set(CFG_KEYS))} / "
                         f"缺 {sorted(set(CFG_KEYS) - set(cfg))}")
    return cfg, base / CFG_NAME


def cfg_text(cfg: dict) -> str:
    """CFG 文件的正文：线格式 v2（第 1 行是列名 `key<TAB>value`），无 BOM。

    行序按 CFG_KEYS 排，不按 dict 的插入序：目标值那一批是 `cfg.update()` 加在最后的，
    照插入序写出来就和 CFG_KEYS 不一致，而「两轮 CFG 逐行可比」正靠这个顺序。
    """
    missing = [k for k in CFG_KEYS if k not in cfg]
    if missing:
        raise SystemExit(f"CFG 缺键，写不出与 CFG_KEYS 同序的文件：{'、'.join(missing)}")
    lines = ["key\tvalue"] + [f"{key}\t{cfg[key]}" for key in CFG_KEYS]
    return "\n".join(lines) + "\n"


# 规格表需要的、只能从 meta 拿的两个计数。缺键一律中止，不给默认值：
# 「没有 kept_bones 就退化成不检查 Skin 骨数」等于把这条判据删掉，而删掉的判据
# 不会报错，只会让下一轮全绿。
META_FOR_SPEC = ("kept_bones", "morph_count")


def _chest_ruler(meta: dict, base: Path) -> tuple[tuple, tuple] | None:
    """胸部特写的尺：导出侧量出来的胸骨端点极值 `((x,y,z)min, (x,y,z)max)`，没有就 None。

    为什么不拿 CFG 里的那只框当期望值（`expected = 框`）：那等于「拿 Max 用的框去要求
    Max 用的框」，框里到底有没有那两根骨还是没人管 —— 第 35 轮就是这一类。极值来自 meta
    的 `chest_bone_*`（导出时按骨坐标量的），与框是怎么算的无关。
    框和极值是同一段代码写出来的两组 meta：**只有一组在 = 两份导出产物分叉了**，中止。
    两个方向都查（有极值没框 / 有框没极值）：后者会让这些行悄悄退成 report，而「悄悄
    少一条判据」正是本项目最难被发现的那类事故。
    """
    box = [meta.get(k, "") for k in CHEST_BOX_KEYS]
    ends = [meta.get(k, "") for k in META_FOR_CHECK]
    if any(ends) != all(box):
        raise SystemExit(f"{base}：meta 的胸骨极值（{'、'.join(META_FOR_CHECK)}）与取景框"
                         f"（{'、'.join(CHEST_BOX_KEYS)}）不是同时存在的一组，两份导出产物分叉，"
                         "重跑 tools/run_model.py")
    if not any(ends):
        return None                   # 认不出胸骨的模型：这些行退成 report
    try:
        nums = [float(v) for v in ends]
    except ValueError as error:
        raise SystemExit(f"{base}：meta 的胸骨极值读不出数（{error}），"
                         "重跑 tools/run_model.py")
    b_min = tuple(nums[0::2])         # META_FOR_CHECK 是 (x min, x max, y min, ...) 成对排的
    b_max = tuple(nums[1::2])
    bad = [a for a, lo, hi in zip(CHEST_AXES, b_min, b_max) if lo > hi]
    if bad:
        raise SystemExit(
            f"{base}：meta 的胸骨极值在 {'/'.join(bad)} 轴上 min 大于 max，"
            "导出侧的量已经不自洽，重跑 tools/run_model.py")
    return b_min, b_max


def verdicts_text(base: Path, stem: str, cfg: dict) -> str:
    """按 CFG 装配判据规格表（MMD2MAX.VERDICTS 正文）。

    取数只走两条路：CFG（路径、开关、目标值）与导出文件（limbs.tsv 的行、meta 的计数）。
    没有第三条 —— 尤其是**不许**从上一轮的日志或报告里取，那正是「拿 Max 自己报的数
    要求 Max 报这个数」的恒等式。`model=stem` 决定哪些行带「已定价读数」：
    只有被定价过的那具模型拿数字当锁，换模型时那些行退化成 report（必须出现、不卡数）。
    """
    meta = read_meta(base, stem)          # 纯读文件，与 build_cfg 里那次读的是同一份
    missing = [k for k in META_FOR_SPEC if not meta.get(k)]
    if missing:
        raise SystemExit(
            f"{base / f'{stem}_meta.tsv'} 缺少键 {'、'.join(missing)}：装配的期望值必须有来源，"
            "重跑 tools/run_model.py 生成新的 meta")
    _, limbs = parse_table(Path(cfg["MMD2MAX_LIMBS"]).read_text(encoding="utf-8"))
    # 蒙皮回读的期望值（M4b）：骨骼表与权重表各读一遍。路径取自 CFG，所以 Max 打开的是
    # **同一份文件**，而解析发生在另一个进程 —— 这正是这批判据不是恒等式的原因：
    # 一边算的是「输入文件说了什么」，另一边量的是「场景里的 Skin 挂着什么」。
    _, bones = parse_table(Path(cfg["MMD2MAX_BONES"]).read_text(encoding="utf-8"))
    weights_rows = parse_weights(Path(cfg["MMD2MAX_WEIGHTS"]).read_text(encoding="utf-8"))
    vertex_count = int(cfg["MMD2MAX_VERTS"])
    # meta 的 kept_bones 从这里开始只干一件事：核对「骨骼表的行数 == 导出摘要说的骨数」。
    # 它以前顶在 `skin/bones` 的期望值上，而那一行报的是 skinOps.GetNumberBones ——
    # 两个数只在「每一根骨都参与蒙皮」时相等（香菱正好如此，所以这条尺歪了 23 轮没人看见）。
    # 现在期望值来自 bones.tsv 的 skin_flag（`skin_expect.bones_skinned`）。
    if len(bones) != int(meta["kept_bones"]):
        raise SystemExit(f"骨骼表 {len(bones)} 行，meta 的 kept_bones 是 {meta['kept_bones']}："
                         "两份导出产物已经分叉，重跑 tools/run_model.py；"
                         "拿分叉的表装出来的判据全是垃圾，不如不跑这一轮")
    if len(weights_rows) != vertex_count:
        raise SystemExit(f"权重表 {len(weights_rows)} 行，CFG 的 MMD2MAX_VERTS 是 {vertex_count}："
                         "权重按顶点序号写入，行数与顶点数不符就是整表错位（#13 那一类）")
    skin_expect = skin_expectation(bones, weights_rows)
    # 骨盒回读（M4c）的第二把尺：手部粗细表。路径同样取自 CFG，所以 Max 打开的那份
    # 和这边解析的是同一个文件。空串/文件不在 = 这一轮没有粗细表，Max 那边按同一条
    # 规则跳过（mmd2maxReadWidths），两边的行数一起归零 —— 不许这边有行那边没行。
    widths_path = Path(cfg["MMD2MAX_WIDTHS"])
    widths = []
    if widths_path.is_file():
        _, widths = parse_table(widths_path.read_text(encoding="utf-8"))
    # 附属骨方向回读（#66）的第二份输入：biped_map 说明哪些行被 Biped 节点接管。
    # 路径同样取自 CFG，所以 Max 建骨时用的那张映射表就是这边剔行用的那张。
    _, biped_map = parse_table(Path(cfg["MMD2MAX_MAP"]).read_text(encoding="utf-8"))
    chest_bone = _chest_ruler(meta, base)
    try:
        rows = build_spec(
            model=stem,
            vertex_count=vertex_count,
            limbs=limbs,
            skin_expect=skin_expect,
            widths=widths,
            bones=bones,
            biped_map=biped_map,
            morph_count=int(meta["morph_count"]),
            skin=cfg["MMD2MAX_SKIN"] == "true",
            foot_reach=cfg["MMD2MAX_FOOTREACH"] == "true",
            rollback=cfg["MMD2MAX_ROLLBACK"] == "true",
            chest_bone=chest_bone,
        )
    except ValueError as error:
        # 导出表少列、值不是数字、截面目标非正 —— 这些都在「尺」这一侧。换成 SystemExit
        # 是为了让重跑导出的人看到「哪个目录 + 缺哪一列」，而不是 traceback 里孤零零一句
        # ValueError；中止本身两边一致：宁可这一轮不跑，也不装一套没有来历的判据。
        raise SystemExit(f"装配判据规格表失败（{base}）：{error}")
    return spec_text(rows)


def build_script(base: Path, stem: str, vertex_count: int,
                 skin: bool = True, suffix: str | None = None,
                 shot: bool = False, physics: bool = True,
                 interactive: bool = False) -> str:
    """生成 `mmd2max_run.ms`：只注入 CFG 路径，模板原样跟在后面。"""
    template = TEMPLATE.read_text(encoding="utf-8")
    cfg, cfg_path = build_cfg(base, stem, vertex_count, skin=skin, suffix=suffix,
                              shot=shot, physics=physics, interactive=interactive)
    cfg_path.write_text(cfg_text(cfg), encoding="utf-8")
    # 规格表紧跟 CFG 落盘，路径取自 CFG 自己：两边指向不同文件的话，Max 读的那张表
    # 就不是 Python 用来判的那张，拼表时全靠运气。
    spec_path = Path(cfg["MMD2MAX_VERDICTS"])
    spec_path.write_text(verdicts_text(base, stem, cfg), encoding="utf-8")
    header = (
        "/* mmd2max OBJ 装配脚本（自动生成）——配置单源（M1b）。\n"
        "   这份文件只注入 CFG 路径；全部输入/开关都在 MMD2MAX.CFG 里，\n"
        "   所以手跑 scripts/max_import_obj.ms 与自动跑这一份读到同一份配置。 */\n"
        f'global MMD2MAX_CFG = "{_esc(cfg_path)}"\n\n'
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


def failure_note(error_file: Path, cfg_error: Path) -> str | None:
    """本轮已落盘的失败原因（error.txt 正文），没有则 None。

    两个文件都要查：`<输出名>.success.json.error.txt` 跟着成功标记的名字走，
    而 CFG 自己没读成时 MaxScript 连 MARKER 都还没拿到，只能落到 CFG 同目录的
    定名文件 mmd2max_cfg.error.txt（见 max_import_obj.ms 的 mmd2maxErrorFilePath）。
    """
    for path in (error_file, cfg_error):
        if path.is_file():
            return f"{path}: {path.read_text(encoding='utf-8', errors='replace').strip()}"
    return None


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
    note = failure_note(error_file, base / CFG_ERROR_NAME)
    if note:
        # 先查 error：标记和场景都在也可能只是「后置步骤（截图/收尾）抛错」。
        # 这种轮次 .max 可用，但退出码不许报 0，否则 GUI 把半轮说成全绿。
        log(f"  脚本报错：{note}")
        if marker.is_file() and max_file.is_file():
            log(f"  注意：{max_file} 已落盘，但后置步骤抛错（可用但不算全绿）")
        return 4
    if marker.is_file() and max_file.is_file():
        log(f"完成：{max_file}")
        return 0
    log("失败：本轮没有可交付的装配结果（GUI 不许报「完成」）")
    if marker.is_file():
        log(f"  成功标记在，但场景文件缺失：{max_file}（saveMaxFile 没落盘）")
        return 6
    if dialog_seen:
        log(f"  MAXScript 报错弹窗（截图：{dialog_path}），Max 现场已保留")
        return 3
    log(f"  无成功标记、无 error.txt：超时或 Max 自行退出。日志：{logfile}")
    return 5


def verdict_gate(base: Path) -> int:
    """把本轮的规格表与报告表拼起来判一次，返回退出码。

    `0` 判据全过 / `7` 有 FAIL / `8` 两张表不可用（缺文件、列名分叉、报告比规格旧）。
    刻意不复用 result_code 的 3~6：那几个说的是「产物在不在」，这两个说的是
    「产物在，但没人判过」和「判了、没过」。混成同一个数，事后就分不出
    是 Max 没跑成还是跑成了但几何不对 —— 而这两种的下一步完全不同。
    """
    from check_verdicts import CHECK_NAME, check as join_verdicts
    code, lines = join_verdicts(base)
    for line in lines:
        log(line)
    if code == 0:
        log(f"判据通过，逐行状态见 {base / CHECK_NAME}")
    return code if code == 0 else (7 if code == 1 else 8)


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
    # 交互位：这一轮**有人坐在屏幕前**，所以脚本可以问、也绝不自己退出。
    # 上面两个 flag 都算：--manual 是「跑完给人看现场」，--gen-only 更硬 —— 脚本要
    # 在用户自己那个 Max 里跑，而 mmd2maxPrepareScene 的第一刀就是 `resetMaxFile #noPrompt`，
    # 非交互写法会把他没存的场景清干净且一声不吭。自动跑（两个 flag 都没有）必须保持
    # false：点弹窗的是 UIA 那几条白名单名字，任何额外模态框都会把 -U MAXScript 挡死。
    interactive = manual or gen_only
    log(f"skin={skin} physics={physics} shot={shot} manual={manual} gen_only={gen_only}")

    weights = base / f"{stem}_weights.tsv"
    # 文件第 1 行是表头（vertex_index / bone_weight_pairs），不是顶点，注释行也不算：
    # MMD2MAX_VERTS 必须等于权重**数据**行数，Max 端拿它核对网格顶点数（差 1 就整轮中止）。
    with weights.open(encoding="utf-8") as stream:
        vertex_count = sum(1 for line in stream
                           if line.strip() and not line.startswith("#")) - 1
    log(f"权重文件 {weights} 顶点数 {vertex_count}")

    script_text = build_script(base, stem, vertex_count,
                               skin=skin, suffix=suffix, shot=shot, physics=physics,
                               interactive=interactive)
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
    # 规格表行数打在操作日志里：跑完之后「这轮是按一张 121 行的表验收的」本身要留痕，
    # 不然只剩一句「完成」，事后没人知道判据有没有少装。
    spec_rows = (base / SPEC_NAME).read_text(encoding="utf-8").splitlines()[1:]
    log(f"判据规格 {base / SPEC_NAME}：{len(spec_rows)} 行")

    if gen_only:
        # 只交付脚本，完全不碰 Max 进程（用户自行在 Max 里 Run Script）。
        log(f"已生成脚本（gen-only，不启动 Max）：{out_script}")
        log("请在 3ds Max 菜单 File ▸ Run Script 载入该脚本，或在 MAXScript 监听器执行：")
        log(f'  fileIn @"{out_script}"')
        log("这一份按交互模式装好（MMD2MAX_INTERACTIVE=true）：场景里有东西会先问你要不要重置，"
            "跑完也不会自己退出 Max。")
        return 0

    max_exe = find_max_exe()
    if max_exe is None:
        # 走到这一步脚本、CFG、规格表都已落盘，只差启动 —— 把三条发现路径都念出来，
        # 让人一眼知道是「这台机的 Max 在别处」而不是「脚本坏了」。
        log(MAX_EXE_HINT)
        return 2
    found_year = max_year_of_exe(max_exe)
    log(f"3ds Max：{max_exe}（{found_year or '版本号读不出'}）")
    if found_year is not None and found_year != VERIFIED_MAX_YEAR:
        log(f"警告：全部探针与判据只在 {VERIFIED_MAX_YEAR} 上实测过；"
            f"{found_year} 的差异未经确认，结果不能当基线。")

    if manual:
        # 用 cmd /c start 把 Max 完全分离启动：即便本 Python 进程退出，Max
        # 也不会被 Bash 后台任务拆掉，全部交给人工。脚本保留不删除。
        launch_cmd = f'cmd /c start "" "{max_exe}" -U MAXScript "{out_script}"'
        try:
            subprocess.Popen(launch_cmd, shell=True)
            log(f"已分离启动 Max（手动模式）：{launch_cmd}")
        except Exception as error:  # noqa: BLE001
            log(f"启动 Max 失败：{error}")
        log(f"Max 现已独立运行，脚本保留于：{out_script}")
        log("请手动在 Max 中查看 / 截图 / 调试；如需重跑脚本，可在 MAXScript 监听器执行：")
        log(f'  fileIn @"{out_script}"')
        log("交互模式（MMD2MAX_INTERACTIVE=true）：跑完不会自己 quitMax，Max 带着场景留着给你看。")
        return 0

    marker = base / f"{name_part}.success.json"
    error_file = Path(str(marker) + ".error.txt")
    logfile = base / f"{name_part}_objrun.log"
    # CFG_ERROR_NAME 是所有轮次共用的定名文件（那会儿 MARKER 还没读到），不清陈旧
    # 就会让下一轮把上一轮的配置错误当成自己的失败。
    # 判据报告同理，而且更危险：Max 只在 mmd2maxReportInit 那一步重建它，那一步之前
    # 抛错（CFG 读不成、网格导入失败）时上一轮的 121 行读数还好好躺在盘上 ——
    # 校验器会把**上一轮**的绿读成这一轮的绿。这就是第二十一轮那个
    # 「doesFileExist 被上一轮同名旧图蒙过」（#21）的同一类，只是这次骗的是验收本身。
    cfg_error = base / CFG_ERROR_NAME
    for stale in (marker, error_file, cfg_error, logfile, base / REPORT_NAME):
        stale.unlink(missing_ok=True)

    # 清理可能残留的旧启动脚本，避免下次 Max 启动误执行
    for startup in max_startup_dirs(max_exe):
        try:
            (startup / "mmd2max_objrun.ms").unlink(missing_ok=True)
        except OSError:  # 允许失败：清理陈旧启动脚本失败不该拖停一轮正常装配
            pass

    # 通过 -U MAXScript 让 Max 启动后自动执行装配脚本
    # （UI/视口已就绪，适合截图；不依赖启动目录的时序猜测）
    proc = subprocess.Popen([str(max_exe), "-U", "MAXScript", str(out_script)])
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
                note = failure_note(error_file, cfg_error)
                if note:
                    log(f"error.txt: {note}")
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
                note = failure_note(error_file, cfg_error)
                if note:
                    log(f"失败：{note}")
                    break
                # 进程已经退出却既没标记也没 error.txt —— 最常见就是 CFG 没读成、
                # 脚本压根没跑起来。继续等只会白等满 3600 秒，还搭上兜底 taskkill。
                if proc.poll() is not None:
                    log("3ds Max 已退出，但本轮没有成功标记也没有 error.txt")
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
    code = result_code(base, name_part, dialog_seen, dialog_path)
    if code:
        return code
    # 产物齐不等于这轮成了。v3 交付过「.max 在、日志全绿、判据没人读过」的轮次
    # （#24 的 100 通道假成功）；现在把 M2b 的规格表和 M2c 的报告表拼起来判一次，
    # 任何 FAIL 或缺覆盖都换成非 0 退出码，GUI 就报不出「完成」。
    return verdict_gate(base)


if __name__ == "__main__":
    raise SystemExit(main())
