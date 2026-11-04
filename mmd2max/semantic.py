"""骨骼语义中间层：Biped / PMX 之间不直接互映射，统一走语义 ID。

设计依据（外部规范要点）：
1. 不要直接写 "Bip001 L UpperArm = 左腕"，而是 Biped/PMX 各自映射到语义 ID。
2. 骨骼分三类：required（缺失要提示）/ optional（缺失可继续）/ extended（不参与标准映射）。
3. 扭转骨（腕捩 / 手捩）是 extended，不能当普通关节骨。
4. IK 骨独立处理，不混入变形骨。
5. Bip001 是 Biped 根节点（COM），不应简单等同于 PMX 的「全ての親」。
6. Biped 的脊柱 / 手指 / 脚趾节数是可变的，不能写死 Spine/Spine1/Spine2。
7. 左右必须同时用名称 + 空间位置判断，不能只看屏幕坐标。

本模块的配置既可由 export_configs() 写出 JSON（供人工编辑），
也在运行时由 standard_pmx_names() / humanoid_kept_indices() 重新读取 JSON，
做到「JSON 是唯一事实来源」：修改 JSON 后无需改代码即可生效。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from mmd2max import coords

CONFIG_DIR = Path(__file__).resolve().parent / "configs"

# ---------------------------------------------------------------------------
# 骨架中线骨骼：语义 ID -> PMX 定义
# ---------------------------------------------------------------------------
CENTER_BONES = {
    "ROOT":       {"ja": "全ての親", "zh": "全体父骨", "cls": "root", "cat": "optional"},
    "CENTER":     {"ja": "センター", "zh": "中心骨", "cls": "core_control", "cat": "optional"},
    "GROOVE":     {"ja": "グルーブ", "zh": "位移辅助骨", "cls": "core_control", "cat": "optional"},
    "WAIST":      {"ja": "腰", "zh": "腰部辅助骨", "cls": "core_control", "cat": "optional"},
    "LOWER_BODY": {"ja": "下半身", "zh": "下半身", "cls": "body", "cat": "required"},
    "SPINE_1":    {"ja": "上半身", "zh": "上半身", "cls": "spine", "cat": "required"},
    "SPINE_2":    {"ja": "上半身2", "zh": "上半身2", "cls": "spine", "cat": "optional"},
    "SPINE_3":    {"ja": "上半身3", "zh": "上半身3", "cls": "spine", "cat": "optional"},
    "NECK":       {"ja": "首", "zh": "颈部", "cls": "head", "cat": "optional"},
    "HEAD":       {"ja": "頭", "zh": "头部", "cls": "head", "cat": "required"},
    "EYES":       {"ja": "両目", "zh": "双眼", "cls": "head", "cat": "optional"},
}

# 左右成对骨骼：语义 ID 后缀 -> PMX 日文名（不含左右前缀）
SIDED_BONES = {
    "SHOULDER":   {"ja": "肩",   "zh": "肩",   "cls": "arm", "cat": "optional"},
    "ARM_UPPER":  {"ja": "腕",   "zh": "上臂", "cls": "arm", "cat": "required"},
    "ARM_TWIST":  {"ja": "腕捩", "zh": "上臂扭转", "cls": "twist", "cat": "extended"},
    "ARM_LOWER":  {"ja": "ひじ", "zh": "前臂", "cls": "arm", "cat": "required"},
    "HAND_TWIST": {"ja": "手捩", "zh": "前臂扭转", "cls": "twist", "cat": "extended"},
    "HAND":       {"ja": "手首", "zh": "手腕", "cls": "arm", "cat": "optional"},
    "LEG_UPPER":  {"ja": "足",   "zh": "大腿", "cls": "leg", "cat": "required"},
    "LEG_LOWER":  {"ja": "ひざ", "zh": "小腿", "cls": "leg", "cat": "required"},
    "FOOT":       {"ja": "足首", "zh": "脚踝", "cls": "leg", "cat": "optional"},
    "TOE":        {"ja": "つま先", "zh": "脚尖", "cls": "leg", "cat": "optional"},
}

# 手指：参数化生成，不手写几十条模糊规则
FINGER_DEFS = [
    ("THUMB",  "親指", "拇指", ("０", "１", "２")),
    ("INDEX",  "人指", "食指", ("１", "２", "３")),
    ("MIDDLE", "中指", "中指", ("１", "２", "３")),
    ("RING",   "薬指", "无名指", ("１", "２", "３")),
    ("PINKY",  "小指", "小指", ("１", "２", "３")),
]
FINGER_ALIASES = {"INDEX": ("人差指",)}  # 食指的常见异名

SIDE_JP = {"L": "左", "R": "右"}
# 左右只由语义 ID 的后缀（_L/_R）决定，PMX 侧的名字一律走 DEFINITIONS 的 ja/aliases；
# 没有第二份「Left/left/l」别名表 —— 那种表只会被写进配置产物，匹配代码从不读它。

# ---------------------------------------------------------------------------
# Biped 侧定义：模式化，节数可变
# ---------------------------------------------------------------------------
BIPED_CENTER = {
    "ROOT": "Bip001",
    "CENTER": "Bip001",
    # 躯干整体下移一节：MMD 的「下半身」实际位于腰部，对应 Biped 最下面那节
    # 胸腔（Spine），而不是骨盆（Pelvis）。这样：
    #   1) 裙摆/饰品根骨（PMX 中挂在「下半身」下）会挂到「离骨盆最近的胸腔」，
    #      而不是直接吊在屁股上（用户反馈）；
    #   2) 「腰」正好补上 Pelvis，整条链
    #      腰 -> 下半身 -> 上半身 -> 上半身2
    #      与 Biped 的 Pelvis -> Spine -> Spine1 -> Spine2 一一对齐，共 3 节胸腔；
    #   3) 腿不受影响：Biped 的 Thigh 本来就固定在 Pelvis 下，
    #      而 Biped 节点我们从不设置 .parent。
    "WAIST": "Bip001 Pelvis",
    "LOWER_BODY": "Bip001 Spine",
    "SPINE_1": "Bip001 Spine1",
    "SPINE_2": "Bip001 Spine2",
    "NECK": "Bip001 Neck",
    "HEAD": "Bip001 Head",
    # GROOVE / EYES / SPINE_3 在 Biped 中没有直接对应，允许缺失
}
BIPED_SIDED = {
    "SHOULDER": "{side} Clavicle",
    "ARM_UPPER": "{side} UpperArm",
    "ARM_LOWER": "{side} Forearm",
    "HAND": "{side} Hand",
    "LEG_UPPER": "{side} Thigh",
    "LEG_LOWER": "{side} Calf",
    "FOOT": "{side} Foot",
    "TOE": "{side} Toe0",
}
# Biped 手指：<侧> Finger<指号><节号后缀>
BIPED_FINGERS = {"THUMB": "0", "INDEX": "1", "MIDDLE": "2", "RING": "3", "PINKY": "4"}


def biped_chain_pairs() -> list[tuple[str, str]]:
    """Biped 父子链（父节点名 -> 子节点名），按「从根到末梢」顺序。

    这是修「肢段长度算错」的关键事实来源：
    PMX 的骨骼 tail 往往指向扭转骨（腕捩/手捩），而 Biped 的一节肢骨对应的
    真实长度是「本关节 head -> 子关节 head」。只有 Biped 链能告诉我们谁是
    「子关节」，不能靠 PMX 的 tail 或「第一个后代」去猜。

    顺序有讲究：同一父节点多条子链时，靠前者优先用于「tail 方向」
    （例如 Spine2 优先指向 Neck，而不是斜向 Clavicle）。
    """
    pairs: list[tuple[str, str]] = [
        ("Bip001 Pelvis", "Bip001 Spine"),
        ("Bip001 Spine", "Bip001 Spine1"),
        ("Bip001 Spine1", "Bip001 Spine2"),
        ("Bip001 Spine2", "Bip001 Neck"),
        ("Bip001 Neck", "Bip001 Head"),
        # 锁骨/手臂
        ("Bip001 Spine2", "Bip001 L Clavicle"),
        ("Bip001 L Clavicle", "Bip001 L UpperArm"),
        ("Bip001 L UpperArm", "Bip001 L Forearm"),
        ("Bip001 L Forearm", "Bip001 L Hand"),
        ("Bip001 Spine2", "Bip001 R Clavicle"),
        ("Bip001 R Clavicle", "Bip001 R UpperArm"),
        ("Bip001 R UpperArm", "Bip001 R Forearm"),
        ("Bip001 R Forearm", "Bip001 R Hand"),
        # 腿
        ("Bip001 Pelvis", "Bip001 L Thigh"),
        ("Bip001 L Thigh", "Bip001 L Calf"),
        ("Bip001 L Calf", "Bip001 L Foot"),
        ("Bip001 L Foot", "Bip001 L Toe0"),
        ("Bip001 Pelvis", "Bip001 R Thigh"),
        ("Bip001 R Thigh", "Bip001 R Calf"),
        ("Bip001 R Calf", "Bip001 R Foot"),
        ("Bip001 R Foot", "Bip001 R Toe0"),
    ]
    # 手指：Biped 命名是 Finger0 / Finger01 / Finger02（第 1/2/3 节）
    for side in ("L", "R"):
        for index in BIPED_FINGERS.values():
            for link in range(3):
                parent = f"Bip001 {side} Finger{index}" + ("" if link == 0 else str(link))
                child = f"Bip001 {side} Finger{index}" + str(link + 1)
                pairs.append((parent, child))
    return pairs


def biped_chain_child_map() -> dict[str, str]:
    """父节点 -> 首个子节点（用于确定骨骼 tail 方向）。同父多子时取链表中靠前者。"""
    result: dict[str, str] = {}
    for parent, child in biped_chain_pairs():
        result.setdefault(parent, child)
    return result


def build_definitions() -> dict:
    """展开成完整的骨骼定义表（语义 ID -> 定义）。"""
    bones: dict = {}
    for sid, info in CENTER_BONES.items():
        bones[sid] = dict(info, sided=False, side=None, aliases=[info["ja"]])
    for side, jp in SIDE_JP.items():
        for base, info in SIDED_BONES.items():
            sid = f"{base}_{side}"
            bones[sid] = dict(info, ja=jp + info["ja"], sided=True, side=side,
                              aliases=[jp + info["ja"]])
        for fname, fja, zh, numerals in FINGER_DEFS:
            for n, numeral in enumerate(numerals):
                sid = f"FINGER_{fname}_{n}_{side}"
                aliases = [jp + fja + numeral]
                for alt in FINGER_ALIASES.get(fname, ()):
                    aliases.append(jp + alt + numeral)
                bones[sid] = {
                    "ja": jp + fja + numeral,
                    "zh": f"{jp}{zh}第{n + 1}节",
                    "cls": "finger", "cat": "optional",
                    "sided": True, "side": side,
                    "finger": fname, "segment": n,
                    "aliases": aliases,
                }
    return bones


DEFINITIONS = build_definitions()


def biped_name(semantic_id: str) -> str:
    """语义 ID -> Biped 节点名（找不到返回空串，表示允许缺失）。

    配置优先：若 DEFINITIONS 已注入 biped 字段（由 JSON 配置驱动），直接返回，
    使 mapping 可通过编辑 pmx_definition.json 的 biped 字段改变，无需改代码。
    """
    cfg = DEFINITIONS.get(semantic_id)
    if cfg is not None and cfg.get("biped") is not None:
        return cfg["biped"]
    if semantic_id in BIPED_CENTER:
        return BIPED_CENTER[semantic_id]
    for base, pattern in BIPED_SIDED.items():
        for side in ("L", "R"):
            if semantic_id == f"{base}_{side}":
                return "Bip001 " + pattern.format(side=side)
    for fname, index in BIPED_FINGERS.items():
        for side in ("L", "R"):
            for n in range(3):
                if semantic_id == f"FINGER_{fname}_{n}_{side}":
                    suffix = "" if n == 0 else str(n)
                    return f"Bip001 {side} Finger{index}{suffix}"
    return ""


# 把「Biped 目标节点名」与「角色」注入每个语义定义，使 JSON 配置成为唯一事实来源：
#   - biped: 该语义骨在 Biped 中的目标节点名（空串=不参与 Biped 映射，作为附属 Bone）
#   - role : skeleton=参与变形骨架 / control=控制骨(丢弃, 权重上提) / morph=表情驱动骨(丢弃)
# 这段留在模块顶层而没折进 build_definitions()：biped_name() 的「配置优先」分支要读
# DEFINITIONS 这个全局名，而 build_definitions() 正是在给它赋值 —— 挪进去就是自指。
for _sid, _info in DEFINITIONS.items():
    _info["biped"] = biped_name(_sid)
    _info.setdefault("role", "skeleton")


def pmx_to_biped() -> dict:
    """PMX 日文名 -> Biped 节点名（双向映射的 PMX 侧）。"""
    result: dict = {}
    for sid, info in DEFINITIONS.items():
        target = biped_name(sid)
        # 规范：Bip001 是 Biped 根（COM），不应等同于 PMX 的「全ての親」，
        # 因此 ROOT 不参与映射，避免出现两个源骨骼指向同一目标。
        if not target or sid == "ROOT":
            continue
        result[info["ja"]] = target
    return result


def required_ids() -> list:
    return [sid for sid, info in load_config_definitions().items() if info["cat"] == "required"]


def _load_json_pmx_names(include_extended: bool = False) -> set | None:
    """configs/pmx_definition.json 里的 PMX 名集合（含别名）；没有配置文件则返回 None。"""
    bones = _config_bones()
    if bones is None:
        return None
    names: set = set()
    for info in bones.values():
        if info["cat"] == "extended" and not include_extended:
            continue
        names.add(info["ja"])
        for alias in info.get("aliases", []):
            names.add(alias)
    return names or None


def standard_pmx_names(include_extended: bool = False) -> set:
    """标准人形骨骼的 PMX 日文名集合（不含 IK / 物理 / 头发裙摆骨）。

    运行时优先以 JSON 配置为准：若 configs/pmx_definition.json 存在且合法，
    则使用其中的骨骼名（含别名）；否则回退到本模块常量。
    """
    json_names = _load_json_pmx_names(include_extended)
    if json_names is not None:
        return json_names
    names: set = set()
    for info in DEFINITIONS.values():
        if info["cat"] == "extended" and not include_extended:
            continue
        names.add(info["ja"])
        for alias in info.get("aliases", []):
            names.add(alias)
    return names


class ConfigUnusable(ValueError):
    """`configs/pmx_definition.json` 在，但读不出一份能用的定义。

    区别在「在」这两个字：缺文件是可以回落的状态（`export_configs()` 随时能重写，
    内置 DEFINITIONS 就是它的来源）；文件在而读不出，回落到内置就等于**把用户改过的
    那张映射表当成不存在** —— 程序照常跑、数字照常报，谁都看不出自己那一条改动没生效。
    """


def _config_bones() -> dict | None:
    """读配置文件里的 bones 表；文件不存在返回 None，读不出可用的表则抛。

    三个解析器曾经各写一遍「except: 回落」，所以修一处会漏两处（第 46 条审的就是这种
    分叉），这里收成唯一入口。校验只认「能不能拿这张表干活」：`ja` 是匹配用的名字、
    `cat` 是分类，缺任一条的那一行在进入映射时会整条被丢，映射表于是静默变短。
    """
    path = CONFIG_DIR / "pmx_definition.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigUnusable(
            f"{path} 解析失败（{exc.__class__.__name__}: {exc}）。删掉它可以改用内置定义；"
            "留着它，你在这份文件里改的映射一条都不会生效。") from exc
    bones = data.get("bones") if isinstance(data, dict) else None
    if not isinstance(bones, dict) or not bones:
        raise ConfigUnusable(
            f"{path} 里没有非空的 bones 表（顶层类型 {type(data).__name__}）："
            "这份配置不会改变任何映射，但它会被当成「配置已生效」。")
    broken = [sid for sid, info in bones.items()
              if not isinstance(info, dict) or not info.get("ja") or not info.get("cat")]
    if broken:
        raise ConfigUnusable(
            f"{path} 有 {len(broken)} 条骨骼缺 ja 或 cat（前几个：{', '.join(str(s) for s in broken[:3])}）："
            "这些条目在匹配时会被整条丢掉，骨骼分类与 Biped 映射会静默变短。")
    return bones


def load_config_definitions() -> dict:
    """加载骨骼语义定义：有配置文件就用它，没有则用内置 DEFINITIONS。

    这样「映射配置」是单一事实来源——用户编辑 JSON 的 ja/aliases/cat/role/biped
    字段即可改变骨骼分类与 Biped 映射，无需改代码。内置 DEFINITIONS 由 export_configs()
    写回该 JSON，二者始终一致。文件在但读不出可用定义时抛 `ConfigUnusable`，不静默回落。
    """
    bones = _config_bones()
    if bones is None:
        return DEFINITIONS
    result: dict = {}
    for sid, info in bones.items():
        entry = dict(info)
        if entry.get("biped") in (None, ""):
            entry["biped"] = biped_name(sid)
        entry.setdefault("role", "skeleton")
        result[sid] = entry
    return result


def _name_to_sid() -> dict:
    """PMX 骨骼名（含别名） -> 语义 ID（配置驱动）。"""
    defs = load_config_definitions()
    mapping: dict = {}
    for sid, info in defs.items():
        mapping.setdefault(info["ja"], sid)
        for alias in info.get("aliases", []):
            mapping.setdefault(alias, sid)
    return mapping


def humanoid_kept_indices(model) -> tuple:
    """返回 (保留的旧序号列表, 旧->新映射, 缺失的 required 语义ID, 冲突语义ID)。

    匹配规则（审查项 P1#13）：
      - 同时匹配 bone.name 与 bone.name_en（若提供）；
      - 命中配置定义的 ja 与 aliases（含左右变体、食指异名等）；
      - role 为 control/morph 的语义骨不参与人形骨架（被当作无用/控制骨剔除）；
      - required 骨骼若未被任何 PMX 骨骼命中，列入 missing，由调用方报错；
      - 多个 PMX 骨骼命中同一语义 ID 时，列入 conflicts，由调用方报错。
    """
    defs = load_config_definitions()
    name_to_sid = _name_to_sid()
    matched: dict = {}          # bone index -> semantic id
    conflict_set: set = set()
    for i, bone in enumerate(model.bones):
        sid = name_to_sid.get(bone.name)
        if sid is None and bone.name_en:
            sid = name_to_sid.get(bone.name_en.strip())
        if sid is not None:
            role = defs[sid].get("role", "skeleton")
            if role in ("control", "morph"):
                continue
            if sid in matched.values():
                conflict_set.add(sid)
            matched[i] = sid
    kept = sorted(matched.keys())
    old_to_new = {old: new for new, old in enumerate(kept)}
    missing = [sid for sid in required_ids() if sid not in matched.values()]
    conflict_names = [defs[sid]["ja"] for sid in conflict_set]
    return kept, old_to_new, missing, conflict_names


def classify_bone_roles(model) -> dict:
    """把每根 PMX 骨骼分类为 skeleton / control / morph。

    分类依据（优先级从高到低，且可由 JSON 配置覆盖）：
      1) 配置命中：语义定义里的 role 字段（skeleton/control/morph）。
      2) IK 标志 (flags & 0x0020) → control（IK 控制骨从不蒙皮）。
      3) 名称模式（日文/英文）：
         - 含 "IK"                       → control
         - 含 ダミー/デュミー/物理/剛体/Dummy/dummy → control（物理锚点）
         - 含 おおよそ/やじるし/矢印/操作中心/センター補助/Ctrl → control（调试/辅助）
         - 含 表情 或 名为 両目        → morph（表情驱动骨，转 Morpher）
      4) 其余 → skeleton（参与变形骨架 / Biped 或 Bone）。

    说明：D 变形骨（足D/ひざD/足首D/足先EX）、扭转骨（腕捩/手捩）、补助骨仍属骨架，
    保留为 skeleton（腿部权重常落在 D 骨上，丢弃会丢权重）。
    """
    name_to_sid = _name_to_sid()
    roles: dict = {}
    for i, bone in enumerate(model.bones):
        # 1) 配置 role
        sid = name_to_sid.get(bone.name)
        if sid is None and bone.name_en:
            sid = name_to_sid.get(bone.name_en.strip())
        if sid is not None:
            cfg_role = DEFINITIONS[sid].get("role")
            if cfg_role in ("control", "morph"):
                roles[i] = cfg_role
                continue
        # 2) IK 标志
        if bone.flags & 0x0020:
            roles[i] = "control"
            continue
        # 3) 名称模式
        nm = bone.name
        if re.search(r"IK", nm):
            roles[i] = "control"
            continue
        if re.search(r"ダミー|デュミー|物理|剛体|Dummy|dummy", nm):
            roles[i] = "control"
            continue
        if re.search(r"おおよそ|やじるし|矢印|操作中心|センター補助|Ctrl", nm):
            roles[i] = "control"
            continue
        # 扭转骨：腕捩/手捩 及其 1/2/3 序号变体。
        # 它们只做「肘到手之间的扭转分配」，而 Biped 的 UpperArm/Forearm 直接控制
        # 整段前臂，保留它们只会多出十几根没人动的 Bone（用户明确要求去掉）。
        if "捩" in nm:
            roles[i] = "control"
            continue
        # MMD 的肩部旋转技巧骨：肩P（父）/肩C（子）。
        # 它们成对夹在 上半身2 -> 肩 -> 腕 之间做「肩不跟随手臂」的把戏，
        # 在 Biped 里由 Clavicle 一并负责，属于冗余骨。
        if re.search(r"肩[PＣC]$", nm):
            roles[i] = "control"
            continue
        # MMD 的根控制骨：全ての親（全体根）/ グルーブ（组根）。
        # 它们不参与蒙皮，只用来整体位移/旋转整副模型；Biped 有 Pelvis 作根，
        # 保留它们只会多出两根空转的 Bone（用户明确要求去掉）。
        if re.search(r"全ての親|すべての親|グルーブ|グループ|AllParent", nm):
            roles[i] = "control"
            continue
        if "表情" in nm or nm == "両目":
            roles[i] = "morph"
            continue
        roles[i] = "skeleton"
    return roles


def measure_forward(model):
    """实测角色朝向（Max 坐标系下的水平单位向量）。

    优先用「目先 - 目」（视线方向最可靠），其次「つま先 - 足首」（脚尖方向）。
    水平分量一律由 `coords.horizontal` 给出，避免手性约定在这里再抄一遍。
    返回 None 表示无法判断。
    """
    bones = {bone.name: bone for bone in model.bones}
    candidates = (("左目先", "左目"), ("右目先", "右目"),
                  ("左つま先", "左足首"), ("右つま先", "右足首"))
    for tip_name, base_name in candidates:
        tip = bones.get(tip_name)
        base = bones.get(base_name)
        if tip is None or base is None:
            continue
        d = [tip.position[i] - base.position[i] for i in range(3)]
        fx, fy = coords.horizontal(d)
        norm = math.hypot(fx, fy)
        if norm > 1e-6:
            return (fx / norm, fy / norm)
    return None


def _swap_lr(target: str) -> str:
    """交换 Biped 节点名中的左右（" L " <-> " R "）。"""
    if " L " in target:
        return target.replace(" L ", " R ")
    if " R " in target:
        return target.replace(" R ", " L ")
    return target


def _lr_is_mirrored(model, mapping: dict) -> bool:
    """判断「左右标签」是否与几何左右相反（正确手性下应当永远返回 False）。

    right = forward × up（up=+Z）在 Max 水平面下等于 (f_y, -f_x)。
    若被映射为 Biped 左脚的那根骨其实落在 right 一侧，说明这个模型自身的命名是反的，
    交换 L/R 兜底。

    注意：v2 之所以经常触发这里，是因为它用的 (-x, z, y) 行列式为 +1，整张图是 MMD
    的镜像，几何左右与标签恒相反 —— 那是在用交换掩盖手性错误。手性改对（见
    `mmd2max/coords.py`）之后，这段只剩「模型自身命名不规范」的兜底作用。
    """
    forward = measure_forward(model)
    if forward is None:
        return False
    fx, fy = forward
    rx, ry = fy, -fx
    # 找到被映射为 Biped 左脚的骨
    left_idx = None
    for idx, target in mapping.items():
        if target == "Bip001 L Foot":
            left_idx = idx
            break
    if left_idx is None:
        return False
    # 参考中心：优先 センター，否则取所有被映射骨的平均
    center = None
    for bone in model.bones:
        if bone.name == "センター":
            center = bone.position
            break
    if center is None:
        pts = [model.bones[i].position for i in mapping]
        if not pts:
            return False
        center = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
    pos = model.bones[left_idx].position
    ox, oy = coords.horizontal([pos[i] - center[i] for i in range(3)])
    return (ox * rx + oy * ry) > 0.0


_FINGER_TARGET_RE = re.compile(r"^Bip001 ([LR]) Finger(\d)(\d*)$")


def _compact_fingers(mapping: dict) -> dict:
    """把 Biped 手指编号压紧成 0..n-1。

    为什么需要：`biped.createNew` 按 `fingers:N` 建指头，编号固定是 Finger0..Finger(N-1)。
    但模型可能只有 3 根手指（本模型：親指/人指/薬指 对应 Biped 的 0/1/3），
    若仍按 0/1/3 映射，`fingers:3` 时 Finger3 根本不存在，而 Finger2 建出来没人用。
    做法：取出实际用到的指号升序去重，第 k 个指号映射成 k。
    """
    digits = sorted({int(m.group(2)) for t in mapping.values()
                     if (m := _FINGER_TARGET_RE.match(t))})
    if not digits or digits == list(range(len(digits))):
        return mapping
    remap = {d: i for i, d in enumerate(digits)}
    out = {}
    for idx, target in mapping.items():
        m = _FINGER_TARGET_RE.match(target)
        if not m:
            out[idx] = target
            continue
        side, digit, suffix = m.group(1), int(m.group(2)), m.group(3)
        out[idx] = f"Bip001 {side} Finger{remap[digit]}{suffix}"
    return out


def finger_layout(mapping: dict) -> tuple[int, int]:
    """从映射表反推 (指头数, 每指节数)，供 MaxScript 的 createNew 使用。

    指头数 = 压紧后出现的最大指号 +1；节数 = 最大节号 +1。
    Biped 的合法范围是 fingers 1~5、fingerLinks 1~3，这里做夹取。
    """
    digits = set()
    links = 0
    for target in mapping.values():
        m = _FINGER_TARGET_RE.match(target)
        if not m:
            continue
        digits.add(int(m.group(2)))
        # 后缀是「第几节」的序号：Finger0="" / Finger01="1" / Finger02="2"
        suffix = m.group(3)
        links = max(links, (int(suffix) if suffix else 0) + 1)
    count = (max(digits) + 1) if digits else 5
    count = max(1, min(5, count))
    links = max(1, min(3, links or 3))
    return count, links


def biped_targets(model) -> tuple:
    """返回 (mapping, missing, conflicts)：骨序号 -> Biped 节点名。

    与 humanoid_kept_indices 用同一套匹配规则，但直接给出 Biped 目标节点名，
    供 OBJ 链路生成 biped_map.tsv。

    设计要点（审查/实跑经验）：
      - 用「骨序号」而不是「骨名」做键。PMX 骨名是日文，MaxScript 侧若按
        本地代码页(GBK)解析会出现乱码导致 getNodeByName 找不到节点；
      - ROOT 不参与映射（Bip001 是 Biped 根 COM，不允许两个源骨指向同一目标）；
      - role 为 control/morph 的语义骨不参与映射（作为无用/控制骨剔除）；
      - 只有能解析出 Biped 节点名的语义才进入 mapping，其余骨骼保持为附属 Bone。
    """
    defs = load_config_definitions()
    name_to_sid = _name_to_sid()
    matched: dict = {}
    conflict_set: set = set()
    for i, bone in enumerate(model.bones):
        sid = name_to_sid.get(bone.name)
        if sid is None and bone.name_en:
            sid = name_to_sid.get(bone.name_en.strip())
        if sid is not None:
            role = defs[sid].get("role", "skeleton")
            if role in ("control", "morph"):
                continue
            if sid in matched.values():
                conflict_set.add(sid)
            matched[i] = sid
    mapping: dict = {}
    for index, sid in sorted(matched.items()):
        target = biped_name(sid)
        if target and sid != "ROOT":
            mapping[index] = target
    # 躯干链去重（见 _dedupe_torso_chain）：避免 Pelvis / Spine 完全重叠
    mapping = _dedupe_torso_chain(model, matched, mapping)
    # 左右自动校正：若「左右标签」与几何左右相反，交换 L/R，
    # 否则 Biped 会为迁就标签而朝向角色反面。
    if _lr_is_mirrored(model, mapping):
        mapping = {i: _swap_lr(t) for i, t in mapping.items()}
    # 手指编号压紧（模型可能不是 5 指，如本模型只有 3 指）
    mapping = _compact_fingers(mapping)
    missing = [sid for sid in required_ids() if sid not in matched.values()]
    conflict_names = [defs[sid]["ja"] for sid in conflict_set]
    return mapping, missing, conflict_names


# 躯干中轴：从下往上。Biped 侧对应 Pelvis / Spine / Spine1 / Spine2。
CENTER_CHAIN_SIDS = ("WAIST", "LOWER_BODY", "SPINE_1", "SPINE_2")
CENTER_CHAIN_TARGETS = ("Bip001 Pelvis", "Bip001 Spine", "Bip001 Spine1", "Bip001 Spine2")


def _center_chain_plan(model) -> tuple:
    """返回 (被并掉的躯干骨原始序号集合, 保留并排序的躯干骨原始序号列表)。

    只处理 WAIST / LOWER_BODY / SPINE_1 / SPINE_2 四级；按高度（1cm 量化）
    升序，同高时按 CENTER_CHAIN_SIDS 的顺序（腰在最前，它是身体根）。
    """
    name_to_sid = _name_to_sid()
    defs = load_config_definitions()
    found: dict = {}
    for i, bone in enumerate(model.bones):
        sid = name_to_sid.get(bone.name)
        if sid is None and bone.name_en:
            sid = name_to_sid.get(bone.name_en.strip())
        if sid not in CENTER_CHAIN_SIDS:
            continue
        if defs.get(sid, {}).get("role") in ("control", "morph"):
            continue
        found.setdefault(sid, i)
    if not found:
        return set(), []

    order = {sid: rank for rank, sid in enumerate(CENTER_CHAIN_SIDS)}
    entries = []
    for sid, idx in found.items():
        height = round(model.bones[idx].position[1], 2)   # 1cm 量化，压掉浮点噪声
        entries.append((height, order[sid], idx))
    entries.sort()

    kept: list = []
    dropped: set = set()
    for _h, _o, idx in entries:
        if kept:
            prev = model.bones[kept[-1]].position
            pos = model.bones[idx].position
            if sum((pos[k] - prev[k]) ** 2 for k in range(3)) < 1e-6:
                dropped.add(idx)
                continue
        kept.append(idx)
    return dropped, kept


def dropped_torso_indices(model) -> set:
    """被「同坐标躯干骨」合并掉的原始骨序号（应从骨架里彻底剔除）。

    只从 Biped 映射里去掉是不够的：它仍会作为 Bone 留在场景里，
    裙摆等附属骨就还挂在它下面，看起来仍然是「挂在 Pelvis 上」。
    """
    return _center_chain_plan(model)[0]


_TOE_EX_RE = re.compile(r"(足先EX|つま先EX)")


def dropped_toe_ex_indices(model) -> set:
    """脚趾 EX 辅助骨（左/右足先EX 及其整段子链）的原始骨序号。

    为什么删：MMD 用 足先EX 弯脚趾，它就在 つま先 前方几厘米；Max 端 Biped 的
    Toe0 已按鞋长撑满整个鞋头，这两根骨会重叠在鞋头里（用户反馈「多余，直接删掉」）。
    删除是安全的：蒙皮权重与物理刚体都按父链向上归并到最近的保留骨
    （つま先 / 足首），不丢顶点权重、也不产生悬空刚体。
    """
    roots = [i for i, bone in enumerate(model.bones)
             if _TOE_EX_RE.search(bone.name or "")]
    if not roots:
        return set()
    children: dict = {}
    for i, bone in enumerate(model.bones):
        if bone.parent_index >= 0:
            children.setdefault(bone.parent_index, []).append(i)
    dropped: set = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in dropped:
            continue
        dropped.add(node)
        stack.extend(children.get(node, []))
    return dropped


def guide_bone_indices(model, biped_mapping: dict | None = None) -> set:
    """「纯辅助骨骼」的原始骨序号 —— MMD 里用来摆位/撑形状、导出后什么都不干的骨。

    用户 2026-11-03 点名要能一键关掉它们：「可以加一个按钮开启或者关闭保留这些辅助
    骨骼（センター2 WeaponR 右親指先 右小指先 右人指先 等，左边的也一样）」。
    它们在 Max 里就是一堆没人会去动的 Bone gizmo，把视口和运动列表挤满。

    判据（probes/breast_axis_probe.py 在香菱 356 根骨上实测：可丢 63 / 保留 293）：
    **四者皆无**才算辅助骨 ——
      1) 不是 Biped 目标（否则它是被自动骨架接管的关节）；
      2) 没有任何蒙皮权重（否则删了要丢顶点权重）；
      3) 没被刚体/关节引用（否则物理代理会悬空）；
      4) 不是 IK 骨（flags & 0x0020；IK 骨由 control 角色另行剔除，这里只是不重复判）。

    实测落在这条判据两边的东西都对：丢掉的正好是 センター2、WeaponL/R、十根指先、
    操作中心、全ての親、グルーブ、肩P/肩C、両目、腰キャンセルL/R、足補、足IK親
    以及第二层布料链的末端；保留的有 センター（Biped 目标）和 左胸/右胸
    （蒙皮权重 23.5 / 21.2）。

    为什么不用 PMX 的「非显示」标志位（flags & 0x0008）：量过了，香菱有 291 根
    被标隐藏、65 根显示，且和用户点名的那批**对不上** —— 那是 MMD 视口的显示偏好，
    不是「这根骨有没有用」。
    """
    weights: dict = {}
    for vertex in model.vertices:
        for index, weight in vertex.weights:
            if weight > 0.0:
                weights[index] = weights.get(index, 0.0) + weight
    physics_bones = {rb.bone_index for rb in model.rigid_bodies}
    for joint in model.joints:
        for rb_index in (joint.rigid_a, joint.rigid_b):
            if 0 <= rb_index < len(model.rigid_bodies):
                physics_bones.add(model.rigid_bodies[rb_index].bone_index)
    guides = set()
    for i, bone in enumerate(model.bones):
        if biped_mapping and biped_mapping.get(i):
            continue
        if weights.get(i, 0.0) > 0.0:
            continue
        if i in physics_bones:
            continue
        if bone.flags & 0x0020:
            continue
        guides.add(i)
    return guides


def _dedupe_torso_chain(model, matched: dict, mapping: dict) -> dict:
    """把「同一坐标上的多根躯干骨」压缩成一级，避免 Biped 骨重叠。

    典型情形（本模型实测）：センター / 腰 / 下半身 三根骨的 head 全是
    (0, 2.04, 72.04)。若按语义分别映射成 Pelvis 和 Spine，两根 Biped 骨会
    完全叠在同一个点上（用户截图指出「Pelvis 和 Spine 中心重合」）。

    做法：
      1. 按高度排序（由 _center_chain_plan 完成），相邻且重合的只保留一根；
      2. 其余退出 Biped 映射，并由 export 从骨架里彻底剔除
         （权重归并到最近的保留祖先，不丢顶点）；
      3. 剩下的骨干按顺序重新分配 Pelvis / Spine / Spine1 / Spine2，
         这样 Pelvis(72.04) 上面就真的接着 Spine(83.96)，不再重叠。
    """
    dropped, kept = _center_chain_plan(model)
    if not kept and not dropped:
        return mapping

    result = dict(mapping)
    for idx in dropped:
        result.pop(idx, None)
    for i, idx in enumerate(kept):
        if i < len(CENTER_CHAIN_TARGETS):
            result[idx] = CENTER_CHAIN_TARGETS[i]
        else:
            result.pop(idx, None)
    return result


def spine_link_count(mapping: dict) -> int:
    """Biped 需要几节脊柱（Pelvis 之上的 Spine/Spine1/Spine2 数量）。

    必须与 biped.createNew 的 spineLinks 参数一致，否则脚本会去找不存在的
    Bip001 Spine2（或白白多出一节没人用的脊柱骨）。
    """
    targets = set(mapping.values())
    count = 0
    for name in ("Bip001 Spine", "Bip001 Spine1", "Bip001 Spine2"):
        if name in targets:
            count += 1
    return max(1, count)


def export_configs(directory: Path | None = None) -> dict:
    """写出三份 JSON 配置，便于人工编辑。"""
    directory = directory or CONFIG_DIR
    directory.mkdir(parents=True, exist_ok=True)

    pmx_def = {
        "metadata": {
            "name": "MMD_PMX_Definition",
            "version": "1.0",
            "note": "PMX 标准 / 次标准 / 扩展骨骼定义。cat: required|optional|extended",
        },
        "categories": {
            "required": "缺失时必须提示",
            "optional": "缺失时允许继续",
            "extended": "存在但不参与标准映射（扭转骨等）",
        },
        "bones": DEFINITIONS,
    }
    biped_def = {
        "metadata": {
            "name": "3dsMax_Biped_Definition",
            "version": "1.0",
            "note": "Biped 节点名按模式生成；脊柱/手指/脚趾节数可变，不可写死。",
        },
        "root": "Bip001",
        "center_nodes": BIPED_CENTER,
        "sided_patterns": BIPED_SIDED,
        "finger_index": BIPED_FINGERS,
        "variable_links": [
            "Spine Links", "Neck Links", "Leg Links", "Fingers",
            "Finger Links", "Toes", "Toe Links", "Tail Links",
            "Forearm Twist Links", "Upper Arm Twist Links",
        ],
        "special_nodes": {
            "HorseLink": "腿部链特殊节点，不可当作脚踝骨",
            "Twist": "扭转节点，不可当作关节骨",
        },
    }
    mapping = {
        "metadata": {
            "name": "Biped_to_PMX_Mapping",
            "version": "1.0",
            "note": "以语义 ID 为中间层。ROOT 刻意不映射：Bip001(COM) 不等同于 全ての親。",
        },
        "pmx_to_biped": pmx_to_biped(),
        "unmapped_semantic_ids": ["ROOT"],
        "hard_conflict_rules": [
            "左右侧不一致时拒绝自动匹配",
            "一个语义 ID 只能接受一个源骨骼",
            "扭转骨不得占用关节骨语义 ID",
            "IK 骨不得混入变形骨映射",
        ],
    }

    files = {
        "pmx_definition.json": pmx_def,
        "biped_definition.json": biped_def,
        "biped_to_pmx_mapping.json": mapping,
    }
    for name, payload in files.items():
        (directory / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {name: directory / name for name in files}


if __name__ == "__main__":
    written = export_configs()
    print("标准人形骨骼（不含扩展）：", len(standard_pmx_names()))
    print("必需骨骼：", required_ids())
    for name, path in written.items():
        print(f"  已写出 {path}")
