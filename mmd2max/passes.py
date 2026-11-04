"""pass 注册表（M3）：谁能修哪条判据、谁一跑就把谁的读数作废、循环该在哪一步停。

这一层治的是两类谎
------------------
1. **「循环结束了」被当成「收敛了」**。收敛循环原先的退出条件是 `rp[1] == 0`，也就是
   「这一遍没有任何骨需要转」——那是**写入侧的自述**（门控：夹角比容差小就不动它），
   不是场景读数。第十七轮实测：三遍的落位最大值 5.6995 一动不动、方向超标 4→5→5，
   最后是靠 `for passNo = 1 to 3` 撞上限退出的；日志里那句 `收敛 第3遍: …` 和「真达标」
   长得一模一样，没有任何东西能说区别。现在闸门只看打分读数，出口理由必须落进报告。
2. **把某个 pass 往前挪一格，判据反而更绿**（#32 那类）。4000 行脚本里 pass 的先后
   全靠代码块的物理位置表达，注释写着「必须跑在退出 Figure 模式之前」，但没人能在
   改完之后自动判一次。现在这份声明是可执行的：每个 pass 说清自己**作废哪些读数**，
   校验器拿报告表自带的行序（Max 只往里追加，行序就是时间轴）反查「这条读数是不是
   在最后一个能改动它的 pass 之前量的」。是 ⇒ STALE ⇒ 非 0 退出码。

声明的是因果，不是日志里发生过什么
----------------------------------
`requires`/`invalidates` 每一条都带物理理由（骨盒 #scale 会把子节点沿轴推走、
setTransform #pos 只搬 pivot 不改朝向……）。**不许**照着某轮报告的顺序把它们抄出来 ——
那正是本项目反复踩的恒等式：拿被验收的一方自己的输出当判据。报告顺序在这里只是
**证据**：注册表先立好，再用 `test_declared_order_matches_the_priced_round` 拿第二十二轮
的真实时间轴对撞一次，两边不一致就必须讲清楚是谁错了。

三种字段各自管什么
------------------
- `requires`   结构前提（不成立这段代码根本没法跑，比如蒙皮要先有网格）。
- `invalidates` 这个 pass 一跑，哪些 phase 的读数就**作废**，必须在它之后重测。
- `writes`     这个 pass 自己产出哪些 phase 的读数（拼表时的归属，也是 STALE 的参照）。
- `gate`       这个 pass 结束时必须成立的判据（`rule II`：要么登记谁能修它，要么当终局
               不变量）。目前只有收敛循环用，因为它是唯一「跑几遍由自己决定」的 pass。
- `optional`   这一遍可以合法地整段不发生（开关关掉 / 没有骨被拽走），报告行退成
               `if_present`：没出现不算失败，出现了仍然按 declared 判。
"""
from __future__ import annotations

from typing import NamedTuple

# 报告表里 pass 行的前两个字段（第三列是 pass id）。与 verdicts 的拼表键同一套词。
PASS_PHASE = "pass"
RAN_CHECK = "ran"

# 收敛循环的遍数上限。以前它是 `.ms` 里那句字面量 `for passNo = 1 to 3`，Python 侧
# 只能靠契约测试去「钉」一个自己看不见的数字（规则 IV 说的就是这种分裂）。
# 现在数值住在这里，经 MMD2MAX.CFG 下发（tools/obj_run.py），Max 只读不写。
CONVERGE_MAX_ITERS = 3

# 会被「挪动 Biped 节点」影响的读数种类：段长、落位、结构向、脚链连续与盒隙，
# 以及脚趾复摆的残差。
# 这些都是**在场景里量出来的**，任何一次 setTransform / #scale 都会让它们失效；
# 而 fit / pose / converge / thigh_roll 那些是 pass 自己的自述（干了几件事），
# 不存在「重测」一说，所以不进这张表 —— 把它们也算进去会让每个早期 pass 都"作废"
# 自己的读数，规则立刻变成噪音。
# toe_snap 进这张表的理由：#scale 把 Toe0 沿轴推走（#41 实测子节点位移 == 盒远端位移），
# 所以「扭转后复摆」那两条残差在「接趾」之后就已经不是最终场景的读数了 —— 接趾后面
# 必须再复测一次，否则残留的旧读数会被当成复摆通过。
# box_rb（M4c）进这张表有两重理由：① 它量的是骨盒在**长度轴之外**两根轴上的跨度，
# 而哪两根算宽/高是由「这根骨的轴现在离世界 Z 多近」决定的 —— 转角一次，判定就能对调，
# 对调之后报出去的「宽」其实是人家的高；② #scale 本身也会挪动骨盒。
# 于是任何一次挪动/缩放都必须在它之后重量一遍，否则「写入那一刻达成的截面」会被
# 当成「最终场景的截面」，那正是 #37/#41 反复的形状。
# bone_rb（#66）进这张表的理由和 box_rb 同一条，只是主语换成附属骨：附属骨建的时候
# 方向当然是对的（createBone 直接吃 head/tail），可它挂在一个**会被转动的父级**下面 ——
# 父级一次 setTransform，子骨的世界变换就跟着走（改挂那段正是靠「先记 worldTM、链接后
# 写回去」才没飞散）。所以任何一次挪动 Biped 都必须在它之后重量附属骨方向。
POSE_FACTS = ("bone_rb", "box_rb", "foot_chain", "placement",
              "segment", "struct_dir", "toe_snap")


class Pass(NamedTuple):
    id: str
    title: str
    requires: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    invalidates: tuple[str, ...] = ()
    optional: bool = False
    max_iters: int = 1
    gate: tuple[str, str, str] | None = None
    # 这个 pass 亲手报出去的 (phase, check)。只有**同一个 phase 有多个作者**时才需要填：
    # toe_snap 的残差由「扭转后复摆」和「接趾后复摆」各量一次，光看 phase 分不出哪条
    # 归谁 —— 于是「接趾那一段把复摆的读数重报一遍」这种错抓不到。其余 pass 的 phase
    # 作者唯一，留空即可（少一份要维护的表），见 `authors_of`。
    reads: tuple[tuple[str, str], ...] = ()


def _pass(pass_id: str, title: str, *, requires: tuple[str, ...] = (),
          writes: tuple[str, ...] = (), moves: bool = False,
          invalidates: tuple[str, ...] = (), optional: bool = False,
          max_iters: int = 1, gate: tuple[str, str, str] | None = None,
          reads: tuple[tuple[str, str], ...] = ()) -> Pass:
    """`moves=True` 是「这个 pass 会挪动 Biped 节点」的缩写，展开成 POSE_FACTS 全作废。

    为什么用缩写而不是每个 pass 手抄六行：作废名单的内容由**物理**决定（节点一动，
    所有场景读数都得重测），手抄必漏一项，而漏掉的那一项正是 STALE 唯一能抓到的那种
    回归。只作废其中几条（比如某 pass 只改骨盒远端、只把趾残差带跑）就显式写
    `invalidates=("toe_snap",)`，并在上面的注释里给出「凭什么是这几条」的理由。
    """
    return Pass(pass_id, title, tuple(sorted(requires)), tuple(sorted(writes)),
                tuple(sorted(POSE_FACTS if moves else invalidates)),
                optional, max_iters, gate, tuple(reads))


# 顺序 = 一次装配里真实的执行顺序（顶层 mmd2maxMain + Biped 装配内部的段落）。
# id 一律 ASCII：这些字符串要进 MMD2MAX.REPORT 的第三列，也会被人在日志里 grep。
PASSES: tuple[Pass, ...] = (
    _pass("scene_prepare", "清场与单位设置", writes=()),
    _pass("mesh_import", "导入 OBJ 网格", requires=("scene_prepare",)),
    _pass("biped_create", "建 Biped 并进入 Figure 模式",
          requires=("mesh_import",)),
    # 回滚自检（M4d）：拍基准 → 存 .fig → 把一根骨的骨盒撑大 30% → 读图 → 三列差值回 0。
    # 排在第一次真正的 #scale 之前：这一刻的 Biped 还没被任何一刀写过，基准最干净。
    # 刻意**不**声明 moves —— 它扰动完就退回自己刚存的图，退不干净由 rollback/pos_cm、
    # box_cm、scale_cm 三行 gate 判红（期望 0），而不是靠把后面所有读数作废来表达
    # 「也许没退干净」。声明作废是给「留下的场景和进来时不一样」那种 pass 用的。
    _pass("rollback_selftest", "回滚载体自检（.fig 存/读）",
          requires=("biped_create",), writes=("rollback",), optional=True,
          reads=(("rollback", "saved"), ("rollback", "loaded"),
                 ("rollback", "perturb_cm"), ("rollback", "pos_cm"),
                 ("rollback", "box_cm"), ("rollback", "scale_cm"),
                 ("rollback", "snap_cost_cm"), ("rollback", "perturbed_bone"),
                 ("rollback", "angle_deg"))),
    # 肢段闭环缩放：#scale 沿长度轴推走子节点，所以后面所有场景读数都得重测。
    _pass("fit_segments", "肢段长度闭环", requires=("biped_create",),
          writes=("fit",), moves=True),
    # 手掌/手指横截面：只动非长度轴，看着像「不挪 pivot」，实际会挪 —— 子节点的世界位置
    # = 父节点 TM（含非等比 scale）× 自己的局部偏移，而指根挂在手掌的横向上，不在长度轴上。
    # 所以缩手指的粗细会把整条指链在世界里推开，段长/落位/结构向全部作废。
    _pass("apply_widths", "手掌/手指横截面", requires=("fit_segments",),
          moves=True, optional=True),
    _pass("pose_initial", "Biped 初摆位 + 骨盆闭环", requires=("fit_segments",),
          writes=("pose",), moves=True),
    # 踝复核（Toe0 顶回去那 1.8cm）与脚趾两段/足背截面：都在收敛循环之前。
    _pass("ankle_recheck", "踝关节优先复核", requires=("pose_initial",), moves=True),
    _pass("toe_box", "脚趾两段骨盒 + 足背截面", requires=("ankle_recheck",),
          moves=True),
    # 收敛循环：唯一「跑几遍由读数决定」的 pass。gate 的第三个字段是**期望的出口理由**
    # ——`gate` 表示因为判据达标而停，`ceiling` 表示撞上限。撞上限对香菱是已知现实
    # （dir_over 4→5→5），所以这里登记的是「出口理由与遍数、达标位必须自相一致」，
    # 具体达没达标由 converge/dir_over 那几条 lock 负责（见 mmd2max/verdicts.py）。
    _pass("converge", "摆位↔转角收敛循环", requires=("toe_box",),
          writes=("converge",), moves=True, max_iters=CONVERGE_MAX_ITERS,
          gate=("converge", "exit_reason", "gate")),
    _pass("thigh_roll", "大腿扭转闭环（#36）", requires=("converge",),
          writes=("thigh_roll",), moves=True),
    # 收尾是**两道写**（先全摆、再只转方向优先的骨），算一个 pass：它可能整段不跑
    # （没有任何骨被转走或被拽走时本就不该跑），所以 optional。
    _pass("finish_pose", "收尾重摆 + 收尾转角", requires=("thigh_roll",),
          writes=("pose_end", "rotate_end"), moves=True, optional=True),
    _pass("toe_snap_roll", "扭转后脚趾复摆", requires=("thigh_roll",),
          writes=("toe_snap",), moves=True,
          reads=(("toe_snap", "residual_after_roll_cm"),)),
    # 足盒接趾（#41）：#scale 把 Toe0 沿轴推走 ⇒ 复摆必须跟在它后面重测。
    _pass("foot_reach", "足盒远端拉到 PMX 脚掌", requires=("toe_snap_roll",),
          moves=True, optional=True),
    # 接趾守卫（M4d）：比「动手前的盒隙」和「动手后的盒隙」，推过头就把整段接趾退回
    # 动手前拍的 .fig 基准。moves=True 的理由和 fit_segments 同类：退回的那一刻，
    # 骨盒尺寸与趾骨的 pivot 一起被 load 改写，接趾之后量的任何场景读数都不再描述
    # 最终场景 —— 而这里唯一重测它的人是紧跟其后的 toe_snap_reach，所以本 pass 必须
    # 排在它前面（注册表顺序与脚本里的代码顺序是同一条声明，改一边就红）。
    # 只在 MMD2MAX_ROLLBACK 且基准拍得出来时才跑，所以 optional。
    _pass("foot_reach_guard", "接趾超差退回基准", requires=("foot_reach",),
          writes=("rollback",), moves=True, optional=True,
          reads=(("rollback", "guard_saved"), ("rollback", "foot_before_cm"),
                 ("rollback", "foot_after_cm"), ("rollback", "foot_worse_bones"),
                 ("rollback", "guard_fired"), ("rollback", "foot_restored"),
                 ("rollback", "foot_restore_pos_cm"),
                 ("rollback", "foot_restore_box_cm"),
                 ("rollback", "foot_restore_scale_cm"),
                 ("rollback", "foot_restore_angle_deg"))),
    _pass("toe_snap_reach", "接趾后脚趾复摆", requires=("foot_reach",),
          writes=("toe_snap",), moves=True, optional=True,
          reads=(("toe_snap", "residual_after_reach_cm"),)),
    # 趾盒收尾（#78）：把趾骨骨盒的三根轴在**最终帧**重收一次。
    # 早帧那次（toe_box）量的轴号到终帧已经不作数了 —— 收敛循环与两次复摆会把趾骨
    # 转过约 90 度，于是「薄边」写在了一根不再指向脚尖的轴上，而 #75 的盒账忠实地
    # 把那个错轴带进回读，读数是自洽的、几何是错的。规矩与 #41 同一条：会改骨盒的写
    # 必须排在最后挪动这根骨的 pass 之后。
    # 这里不写 moves=True 而是留空：缩的是**链尾**那根骨，它没有子节点，父→子距离、
    # 落位、脚链朝向都不受一次 #scale 影响（#41 之所以要 moves=True，是因为它缩的是
    # 父骨 Foot，把 Toe0 沿轴推走了）。反过来，唯一读骨盒尺寸的人（box_verify）排在
    # 它后面，量到的必然是重收之后的样子 —— 声明作废一条没人需要在它之后重测的读数，
    # 等于给 audit 造一条永远满足不了的规则（注册表校验会直接拒绝 moves=True：
    # toe_snap 的两位作者都在它之前）。哪天趾骨下面挂了东西（例如鞋带骨），这条必须
    # 改回 moves=True。
    _pass("toe_box_final", "趾盒截面（最终帧重收）", requires=("toe_snap_reach",),
          invalidates=()),
    # 探针实测：退出 Figure 模式前后，Toe0 目标位与踝 pivot 逐字相同（B1 vs B2），
    # 所以这里刻意**不**声明作废任何读数 —— 声明了就是把「几何中性的退出」写成了
    # 一次移动，下一轮谁把这段往前挪都会被抓成 STALE 的假警报。
    _pass("figure_exit", "退出 Figure 模式", requires=("toe_box_final",)),
    # 以下五道是终局复核：它们只读不写，所以 invalidates 全空，但要排在
    # figure_exit 之后（骨盒朝向在 Figure 模式内读出来是另一套数）。
    _pass("foot_chain_check", "脚链向 / 盒隙复核（偏航只记日志）",
          requires=("figure_exit",), writes=("foot_chain",)),
    _pass("segment_check", "段校验（两 pivot 实距）",
          requires=("foot_chain_check",), writes=("segment",)),
    _pass("placement_check", "关键关节落位复核",
          requires=("segment_check",), writes=("placement",)),
    _pass("struct_dir_check", "结构向复核",
          requires=("placement_check",), writes=("struct_dir",)),
    # 骨盒回读（M4c）：读的是**最终场景**的本地包围盒跨度与 #scale，期望值来自
    # limbs.tsv 脚段第 10~18 列与 widths.tsv（Python 在这边算），所以它不是
    # 「足背 / 脚趾 / 手部粗细」那三行日志的复读 —— 那三行量的是写入那一刻。
    # 排在四道终局复核之后、附属骨之前：附属骨 pass 只新建 BoneGeometry，不动 Biped
    # 的 #scale，所以这里量完就是保存下来的样子（这条前提记在 accessory_build 那里）。
    _pass("box_verify", "骨盒回读复核（横截面/长度）",
          requires=("struct_dir_check",), writes=("box_rb",)),
    # 附属骨 pass 只新建 BoneGeometry 并按 max_parent 改挂**自己那些**骨，
    # 不碰 Biped 的 pivot/朝向 —— 这是「段校验排在它前面仍然有效」的唯一理由。
    # 哪天这段开始挪 Biped（例如为了对齐衣服挂点），必须把 moves=True 加上，
    # 否则上面四道复核全都成了陈旧读数，而只有这条注释会提醒我们。
    _pass("accessory_build", "附属骨（BoneGeometry）与改挂",
          requires=("biped_create",), writes=("accessory",), invalidates=()),
    _pass("skin_apply", "蒙皮（Skin 修改器）",
          requires=("accessory_build", "mesh_import"),
          writes=("mesh", "skin"), optional=True),
    # 蒙皮回读（M4b）：读的是场景里那个 Skin 修改器，期望值来自 weights.tsv / bones.tsv。
    # 它排在 skin_apply 之后、morpher_build 之前，因为 `writes` 里的 skin_rb 只能由这一段报，
    # 而 Morpher 会往同一个网格上叠通道 —— 回读必须在通道建立之前量，量的才是蒙皮那一刻的权重。
    # optional 的理由只有 `--no-skin`：蒙皮整段跳过时这里无事可读。
    _pass("skin_verify", "蒙皮回读复核（场景 Skin 读回）",
          requires=("skin_apply",), writes=("skin_rb",), optional=True),
    _pass("morpher_build", "表情（Morpher 通道）", requires=("mesh_import",),
          writes=("morpher",), optional=True),
    _pass("physics_build", "物理刚体与关节", requires=("accessory_build",),
          optional=True),
    # 附属骨方向回读（#66）：最后一道，排在 saveMaxFile 之前（脚本里 mmd2maxMain 的
    # 那一段）。物理代理改父级、改挂写回 worldTM 都已经发生完了，这一刻量到的
    # transform.row1 就是存进 .max 的那个方向 —— 胸部骨那一刀的 tail 是从网格量出来的
    # 乳头点，不回读就只有一句「建骨时传对了」。
    _pass("bone_dir_verify", "附属骨方向回读（保存前）",
          requires=("accessory_build",), writes=("bone_rb",)),
    # 截图（#69）：脚本里它排在 saveMaxFile 与全部回读之后 —— 「成功标记写在截图之前」
    # 那段注释说的是同一件事：截图坏不许拖垮已经成功的装配，所以它是 optional。
    # 它 requires bone_dir_verify 的物理理由不是「需要那两根骨」，而是**画面必须描述
    # 保存下来的那个场景**：在最后一道几何写刀之前抓的图，判的是别的场景。
    # 这一段的读数（fill_pct / tags / projection_fail）量的是取景本身 ——
    # 「特写够不够特写」以前只能用眼睛判，而眼睛判的是「图里有没有头」。
    _pass("shot_capture", "视口截图与取景填充率",
          requires=("bone_dir_verify",), writes=("shot_rb",), optional=True),
)

BY_ID: dict[str, Pass] = {p.id: p for p in PASSES}

# 每个 phase 由哪个 pass 产出读数（写两侧对撞用；同一个 phase 允许多个作者，
# 例如 toe_snap 由两道复摆各测一次，那时归因规则会把每一行分给各自那一段）。
_authors: dict[str, list[str]] = {}
for _p in PASSES:
    for _phase in _p.writes:
        if _p.id not in _authors.setdefault(_phase, []):
            _authors[_phase].append(_p.id)
PHASE_AUTHORS: dict[str, tuple[str, ...]] = {
    k: tuple(sorted(v)) for k, v in _authors.items()}
del _authors, _p, _phase


def pass_ids() -> tuple[str, ...]:
    return tuple(p.id for p in PASSES)


# 声明的执行顺序（注册表里的位置）。归因消息要用它说「谁排在谁后面」。
ORDER_BY_ID: dict[str, int] = {p.id: i for i, p in enumerate(PASSES)}


def order_of(pass_id: str) -> int:
    return ORDER_BY_ID[pass_id]


def max_iters(pass_id: str) -> int:
    """收敛循环的遍数上限走同一条路：Python 与 Max 都从这里取值。"""
    return BY_ID[pass_id].max_iters


def spec_rows(when_override: dict[str, str] | None = None
              ) -> list[tuple[str, str, str, str, str, str, str]]:
    """注册表 → 规格行：每个 pass 一行 `pass/ran/<id>`，期望恰好跑到 1。

    `expected=1, tol=0` 的含义是「这一遍**恰好跑一次**」：跑到 2 是重做（同一根骨被
    摆两遍会吃掉上一遍的转角），跑到 0 是整段没跑。两种都必须红。
    optional 的 pass 退成 `if_present`：没跑不算失败，但跑了就必须是 1。

    `when_override` 给装配侧**当场就知道**的开关用（`--no-skin`、`FOOTREACH=false`、
    表情表为空）：这些 pass 这一轮注定不跑，规格行提前退成 `if_present`，而不是
    「先按 always 装好、等实跑缺了再红一句」——后者会把已知合法的跳过混进真故障里。
    """
    rows = []
    for p in PASSES:
        when = "if_present" if p.optional else "always"
        when = (when_override or {}).get(p.id, when)
        if when not in ("always", "if_present"):
            raise ValueError(f"{p.id} 的 when 只能是 always/if_present，拿到 {when!r}")
        rows.append((PASS_PHASE, RAN_CHECK, p.id, "1", "0", "gate", when))
    return rows


def validate_registry() -> list[str]:
    """注册表自身的一致性（导入期就能判，不需要跑 Max）。

    - id 唯一且 ASCII；
    - requires / invalidates / writes / reads 里不许出现没登记过的 pass id 或 phase；
    - 依赖图无环（有环的话「拓扑排序」根本不存在，声明就是假的）；
    - 声明为作废某读数的 pass，必须排在**该读数至少一个作者之前** —— 否则这条声明在
      当前脚本里就已经不成立：它说「我之后必须重测」，可我之后没人测，等于一进场就自带
      一条 STALE。（是「至少一个」而不是「全部」：toe_snap 有两位作者，接趾那一段排在
      复摆之后、接趾复摆之前，这条就是成立的。）
    """
    problems: list[str] = []
    ids = pass_ids()
    if len(set(ids)) != len(ids):
        problems.append(f"pass id 有重复：{sorted(ids)}")
    claimed: set[tuple[str, str]] = set()
    for p in PASSES:
        if not p.id.isascii() or not p.id.replace("_", "").isalnum():
            problems.append(f"pass id 必须是 ASCII 字母数字下划线：{p.id}")
        for dep in p.requires:
            if dep not in BY_ID:
                problems.append(f"{p.id} requires 了没登记的 pass：{dep}")
        for phase in p.writes:
            if phase == PASS_PHASE:
                problems.append(f"{p.id} 不许把 pass 自己当读数")
        for phase in p.invalidates:
            if phase not in PHASE_AUTHORS:
                problems.append(f"{p.id} invalidates 了没人产出的 phase：{phase}")
        for pair in p.reads:
            if len(pair) != 2 or not all(pair):
                problems.append(f"{p.id} 的 reads 项必须是 (phase, check)：{pair}")
                continue
            if pair[0] not in p.writes:
                problems.append(f"{p.id} 认领了读数 {pair[0]}/{pair[1]}，"
                                f"但它的 writes 里没有 {pair[0]}")
            if pair in claimed:
                problems.append(f"{pair[0]}/{pair[1]} 被两个 pass 同时认领（另一个是 {p.id}）")
            claimed.add(pair)
        if p.gate and p.gate[0] not in PHASE_AUTHORS:
            problems.append(f"{p.id} 的 gate 指向没人产出的 phase：{p.gate[0]}")

    order = ORDER_BY_ID
    # 成环检测：只在 requires 边上做深度优先。
    seen: set[str] = set()
    visiting: set[str] = set()

    def walk(node: str, trail: tuple[str, ...]) -> None:
        if node in seen:
            return
        if node in visiting:
            problems.append(f"requires 有环：{' -> '.join(trail + (node,))}")
            return
        visiting.add(node)
        for dep in BY_ID[node].requires:
            if dep in BY_ID:
                walk(dep, trail + (node,))
        visiting.discard(node)
        seen.add(node)

    for p in PASSES:
        walk(p.id, ())

    for p in PASSES:
        for dep in p.requires:
            if dep in order and order[dep] > order[p.id]:
                problems.append(f"声明的顺序与 requires 矛盾：{dep} 必须在 {p.id} 之前，"
                                f"但注册表里它排在后面（第 {order[dep]} 位 vs 第 {order[p.id]} 位）")
        for phase in p.invalidates:
            if phase in p.writes:
                continue
            live = [order[a] for a in PHASE_AUTHORS.get(phase, ()) if a in order]
            if live and max(live) < order[p.id]:
                problems.append(
                    f"{p.id}（第 {order[p.id]} 位）声明作废 {phase}，但 {phase} 最晚的"
                    f"作者在" + "、".join(
                        f"{a}（第 {order[a]} 位）"
                        for a in sorted(PHASE_AUTHORS[phase], key=lambda x: order[x])) +
                    f" —— 它之后没人再量，这条声明在当前脚本里就不成立")
    return problems


def report_slot(row: dict[str, str]) -> tuple[int, int]:
    """这一行在**声明的时间轴**上属于哪一段、段内排在读数位还是收工位。

    `(段序号, 段内位置)`：`pass/ran/<id>` 排在自己那段的最后（ran 是收工，不是开工），
    读数排在段内。没有作者的行给到最后一段之后，让归因/orphan 规则去判红而不是静默通过。
    """
    phase, check, subject = row.get("phase", ""), row.get("check", ""), row.get("subject", "")
    if phase == PASS_PHASE and check == RAN_CHECK:
        return (ORDER_BY_ID[subject], 1) if subject in ORDER_BY_ID else (len(PASSES), 1)
    authors = [order_of(a) for a in authors_of(phase, check) if a in ORDER_BY_ID]
    return (min(authors), 0) if authors else (len(PASSES) + 1, 0)


def report_order(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """按注册表声明的时间轴给报告行排序（稳定排序）。

    两个用处：① 测试夹具靠它造「Max 真会写出来的那种顺序」，而不是把规格表顺序照抄一遍
    —— 后者会让归因规则一路报假红，于是没人再信它；② 冻结轮对撞：拿真 Max 写出的报告
    排一遍，顺序若发生变化就说明注册表声明的时间轴和实跑分叉了（`test_passes` 里那条）。
    """
    return [row for _, row in sorted(enumerate(rows),
                                     key=lambda t: (report_slot(t[1]), t[0]))]


def audit(report_rows: list[dict[str, str]]) -> list[str]:
    """拿报告表的**行序**审一遍时间轴，返回问题文本（空列表 = 没问题）。

    报告是 Max 一路追加写出来的，所以行序就是执行顺序 —— 这是免费拿到的时间轴，
    不需要让 Max 打时间戳（时间戳会被 diff_round 当噪音遮掉，也更难对撞）。

    五条规则：
    1. 覆盖：声明为非 optional 的 pass 必须有 `pass/ran` 行；反过来报告里有、注册表
       没登记的 pass id 也报出来（拼表时它会先以 orphan 身份变红，这里给一句人话）。
    2. 恰好一次：同一个 pass 报了两行 = 那段跑了两次，声明里没有任何 pass 允许这样。
    3. 拓扑：`requires` 里那些**这一轮真跑了**的 pass 必须排在前面。
    4. 陈旧：某个 phase 的最后一条读数如果排在「能作废它的那个 pass」之前 ⇒ STALE。
    5. 归因 + 闸门自相一致：每条读数必须落在声明它的 pass 那一段里（`audit_attribution`），
       带 gate 的 pass 还要过 `audit_gate`。
    """
    problems: list[str] = []
    ran: dict[str, list[int]] = {}
    phase_rows: dict[str, list[int]] = {}
    read_rows: list[tuple[int, str, str]] = []     # (行号, phase, check)
    for index, row in enumerate(report_rows):
        phase = row.get("phase", "")
        if phase == PASS_PHASE and row.get("check") == RAN_CHECK:
            ran.setdefault(row.get("subject", ""), []).append(index)
        elif phase:
            phase_rows.setdefault(phase, []).append(index)
            read_rows.append((index, phase, row.get("check", "")))

    for p in PASSES:
        hits = ran.get(p.id, [])
        if len(hits) > 1:
            problems.append(f"pass {p.id} 报了 {len(hits)} 行 ran（行号 "
                            f"{hits}）：注册表说它恰好跑一次")
        if not hits and not p.optional:
            problems.append(f"pass {p.id}（{p.title}）没有 ran 行：这一轮它整段没跑，"
                            "但注册表把它列为必经步骤")

    for unknown in sorted(set(ran) - set(BY_ID)):
        problems.append(f"报告里出现注册表不认识的 pass：{unknown}")

    for p in PASSES:
        if p.id not in ran:
            continue
        here = ran[p.id][0]
        for dep in p.requires:
            if dep in ran and ran[dep][0] > here:
                problems.append(
                    f"顺序错：{p.id}（第 {here} 行）跑在它的前提 {dep}"
                    f"（第 {ran[dep][0]} 行）之前 —— 声明里 {dep} 必须先完成")

    for p in PASSES:
        if p.id not in ran:
            continue
        here = ran[p.id][0]
        for phase in p.invalidates:
            # 一个 pass 自己的读数是它干活的一部分，不可能排在它自己之后 —— 不豁免
            # 就会每次都用 `ran` 行把自己判成 STALE（复摆那两条最先撞上）。
            if phase in p.writes:
                continue
            rows = phase_rows.get(phase)
            if not rows:
                continue      # 这个 phase 本轮没读数，由覆盖规则去管，不在这里重复报
            if max(rows) < here:
                problems.append(
                    f"STALE：{phase} 的读数最晚停在第 {max(rows)} 行，而声明要作废它的 "
                    f"{p.id}（{p.title}）在第 {here} 行才跑 —— 这条读数量的已经不是"
                    "最终场景，它不能算验收通过")

    problems.extend(audit_gate(report_rows, ran))
    problems.extend(audit_attribution(ran, read_rows))
    return problems


def authors_of(phase: str, check: str = "") -> tuple[str, ...]:
    """这条读数的作者是谁。

    有 pass 在 `reads` 里精确认领了这个 `(phase, check)` 就按它 —— 那是同一个 phase
    有多个作者、按 phase 分不出归属的情形（toe_snap 的两道复摆）。否则退回 phase 级。
    """
    exact = tuple(sorted(p.id for p in PASSES if (phase, check) in p.reads))
    return exact or PHASE_AUTHORS.get(phase, ())


def audit_attribution(ran: dict[str, list[int]],
                      read_rows: list[tuple[int, str, str]]) -> list[str]:
    """把报告表切成一段一段，检查每条读数落在声明它的那个 pass 的区间里。

    `ran` 行排在该 pass **收工**那一刻，所以 `ran` 行的行号天然就是时间轴上的分段边界：
    一条读数落在边界 X 之前、上一条边界之后 ⇒ 它就是 X 那一段量的。要求 X 必须是这条
    读数声明的作者之一（`authors_of`：先看 `(phase, check)`，退回 phase）。

    这条把 pass 边界从「一堆自报家门」升级成时间轴的骨架：报告里每一行都被钉进某一段。
    治的是「读数量错了地方」——例如把 `mesh/vertex_count` 顺手挪到清场阶段去报，
    数值照样对，但它量的已经不是蒙皮时那个网格了（#13 那类权重错位只有在这段里量才看得见）。
    """
    problems: list[str] = []
    boundaries = sorted((index, pid) for pid, hits in ran.items() for index in hits)
    if not boundaries:
        return problems        # 一段都没声明跑过 —— 覆盖规则已经报过了，这里不重复

    def owner(index: int) -> str | None:
        for spot, pid in boundaries:
            if index < spot:
                return pid
        return None            # 排在最后一段之后：这一段之后没人量过东西

    for index, phase, check in read_rows:
        authors = authors_of(phase, check)
        if not authors:
            continue           # 没人认领的 phase 由拼表那条 orphan 规则判红，这里不重复
        who = owner(index)
        label = f"{phase}/{check}"
        if who is None:
            problems.append(
                f"{label} 第 {index} 行落在所有 pass 区间之后：报告最后一段收工之后"
                "不该再出现读数")
        elif who not in authors:
            later = (f"（{BY_ID[who].title}排在它们后面，量的是没修完的场景）"
                     if who in BY_ID and order_of(who) > max(
                         [order_of(a) for a in authors if a in BY_ID]) else "")
            problems.append(
                f"{label} 第 {index} 行是由 {who} 那一段量的，但注册表说它的作者是 "
                f"{'、'.join(authors)}{later}")
    return problems


def audit_gate(report_rows: list[dict[str, str]],
               ran: dict[str, list[int]] | None = None) -> list[str]:
    """闸门自相一致：出口理由、遍数、达标位三者不许互相打脸。

    这三条数都是 Max 报的**读数**，期望值不在这里 —— 这里只判它们互相之间是否自洽。
    为什么值得单独判：撞上限这件事以前在日志里没有任何痕迹（第 3 遍的日志行和真达标
    的第 3 遍一模一样），所以「循环停了」到底是不是「判据过了」根本无从分辨。
    """
    if ran is None:
        ran = {}
    values = {(r.get("phase"), r.get("check"), r.get("subject")): r.get("actual", "")
              for r in report_rows}
    got = lambda phase, check: values.get((phase, check, GLOBAL_SUBJECT))  # noqa: E731

    problems: list[str] = []
    for p in PASSES:
        if not p.gate or p.id not in ran:
            continue
        phase, check, want = p.gate
        reason = got(phase, check)
        if reason is None:
            problems.append(f"{phase}/{check} 没有读数：带闸门的 pass {p.id} "
                            "必须报它到底是为什么停的")
            continue
        iters = got(phase, "iters")
        if iters is None:
            problems.append(f"{phase}/iters 没有读数：没法核对 {p.id} 的出口理由")
            continue
        try:
            used = int(float(iters))
        except ValueError:
            problems.append(f"{phase}/iters 不是数字：{iters!r}")
            continue
        if used > p.max_iters:
            problems.append(f"{p.id} 跑了 {used} 遍，超过注册表声明的上限 "
                            f"{p.max_iters}：Max 那边的循环次数与 MMD2MAX.CFG 分叉了")
        if used < 1:
            problems.append(f"{p.id} 的 iters={iters} 而不是 ≥1：循环一次都没跑，"
                            "却还是报出了出口理由")
        if reason == "ceiling" and used != p.max_iters:
            problems.append(f"{p.id} 报出口=ceiling 却只跑了 {used} 遍"
                            f"（上限 {p.max_iters}）：不是撞上限停的，这句是假的")
        if reason == "gate":
            satisfied = got(phase, "gate_satisfied")
            if satisfied != "true":
                problems.append(f"{p.id} 报出口=gate（判据达标）但 gate_satisfied="
                                f"{satisfied!r}：这正是「循环结束了被当成收敛了」")
        if reason not in ("gate", "ceiling"):
            problems.append(f"{phase}/{check} 的读数 {reason!r} 不是 gate/ceiling 之一")
    return problems


# 报告表里全模型范围那列的写法，与 verdicts.GLOBAL 同一个词（两边不许各写一份）。
GLOBAL_SUBJECT = "-"

# 导入期就把注册表自己的矛盾报出来：一张自相矛盾的注册表比没有更糟，
# 因为它会让 audit 的每条结论都失去意义。
_PROBLEMS = validate_registry()
if _PROBLEMS:
    raise ValueError("pass 注册表不成立：\n  " + "\n  ".join(_PROBLEMS))
del _PROBLEMS
