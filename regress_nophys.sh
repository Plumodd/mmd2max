#!/usr/bin/env bash
set -u
cd /c/Users/Administrator/Desktop/mmd2max_v5
LOG=regression_nophys.log
: > "$LOG"
MODELS=(
  "D:/Backup/Downloads/茜特拉莉皮肤_by_原神_3107c8dfe7f23e08298df9d4d577b7de/茜特拉莉_夏日改.pmx|茜特拉莉_夏日改"
  "D:/Backup/Downloads/瓦雷莎_by_原神_c8d072042bcb97ad2cf6ef698ebdbc62/瓦雷莎.pmx|瓦雷莎"
  "D:/Backup/Documents/林尼.pmx|林尼"
  "C:/Users/Administrator/Documents/Qoder/2026-11-01-bbf139b8/Temp/spec119/香菱_无附属骨/香菱_无附属骨.pmx|香菱_无附属骨"
)
for entry in "${MODELS[@]}"; do
  SRC="${entry%%|*}"; NAME="${entry##*|}"
  echo "===== $NAME (no physics, chest constraint) =====" | tee -a "$LOG"
  python tools/run_model.py "$SRC" 2>&1 | tail -3 | tee -a "$LOG"
  python tools/obj_run.py "test_runs/$NAME" "$NAME" --shot --quit 2>&1 | grep -iE "胸部约束|判据汇总|失败 [0-9]|崩溃|drift|漂移|!!" | tee -a "$LOG"
done
echo "ALL DONE" | tee -a "$LOG"
