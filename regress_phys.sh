cd "C:/Users/Administrator/Desktop/mmd2max_v5"
LOG="C:/Users/Administrator/Desktop/mmd2max_v5/regression_phys.log"
: > "$LOG"
models=(
 "D:/Backup/Downloads/茜特拉莉皮肤_by_原神_3107c8dfe7f23e08298df9d4d577b7de/茜特拉莉_夏日改.pmx|茜特拉莉_夏日改"
 "D:/Backup/Downloads/瓦雷莎_by_原神_c8d072042bcb97ad2cf6ef698ebdbc62/瓦雷莎.pmx|瓦雷莎"
 "D:/Backup/Documents/林尼.pmx|林尼"
 "C:/Users/Administrator/Documents/Qoder/2026-11-01-bbf139b8/Temp/spec119/香菱_无附属骨/香菱_无附属骨.pmx|香菱_无附属骨"
)
for entry in "${models[@]}"; do
  pmx="${entry%%|*}"; stem="${entry##*|}"
  echo "########## $stem : re-export (Stage0 breast re-aim) ##########" >> "$LOG"
  python tools/run_model.py "$pmx" >> "$LOG" 2>&1
  echo "  run_model exit=$?" >> "$LOG"
  echo "########## $stem : max run (keep-open --shot --physics) ##########" >> "$LOG"
  # 用户 2026-11-09 要求：跑完保留 3ds Max 现场供查看，禁止自动退出（不加 --quit）。
  python tools/obj_run.py "test_runs/$stem" "$stem" --shot --physics >> "$LOG" 2>&1
  echo "  obj_run exit=$?" >> "$LOG"
done
echo "ALL DONE" >> "$LOG"
