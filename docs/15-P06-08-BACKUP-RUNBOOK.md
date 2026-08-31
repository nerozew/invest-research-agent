# P06-08 备份与恢复演练 Runbook

> 目标（docs/05-DEVELOPMENT-ROADMAP.md P06-08）：从备份恢复一个完整 job、步骤和报告。
> 设计依据：docs/03-DATABASE.md §7「版本化 pg_dump、工件 checksum 和恢复演练验证备份可用性」。
> 范围：PostgreSQL（`postgres_data` 卷）+ 工件（`artifacts_data` 卷，容器路径 `/app/artifacts/<job_id>/`）。

---

## 1. 备份介质

| 介质 | 方式 | 产出 |
|---|---|---|
| PostgreSQL | `pg_dump --format=custom`（全库逻辑备份） | `<backup>/<timestamp>/invest_db.dump` |
| 工件 | `docker compose cp` 复制 `artifacts_data` 卷内容 | `<backup>/<timestamp>/artifacts/<job_id>/...` |
| 元数据 | `backup_manifest.json` | 备份时间、pg_dump sha256、工件逐文件 sha256、覆盖的 job_id 列表 |

对齐方式：DB `artifacts.storage_uri`（如 `43ce04f2-.../08_report.md`）与快照目录结构按 job_id 一一对应。

---

## 2. 备份命令

```bash
# 前提：compose 栈运行中（postgres/worker healthy）
python scripts/backup_snapshot.py --docker --backup-root backup
```

输出示例：

```
备份完成，manifest: backup/20260816T142900123456Z/backup_manifest.json
  job_ids: ['43ce04f2-2d8f-4380-9849-51572243e183', ...]
  pg_dump: invest_db.dump (xxxxx bytes, sha256=abc123...)
```

离线验证（无 Docker）：`python scripts/backup_snapshot.py --local --source-dir <dir> --backup-root backup`

---

## 3. 校验命令（只读）

```bash
# 校验整个快照完整性（manifest 存在、pg_dump/工件 checksum 一致）
python scripts/restore_snapshot.py --verify backup/<timestamp>

# 校验且确认覆盖了目标 job
python scripts/restore_snapshot.py --verify backup/<timestamp> --job-id 43ce04f2-2d8f-4380-9849-51572243e183
```

失败即抛错并返回非零退出码。

---

## 4. 恢复命令

### 4.1 全库恢复 PostgreSQL（灾难恢复）

```bash
python scripts/restore_snapshot.py --restore-db backup/<timestamp>
```

- 执行 `pg_restore --clean --if-exists`：先 DROP 再 CREATE 所有对象；
- **危险操作**：会覆盖当前库全部数据，确认后再执行；
- 恢复后重启应用无需改配置（表结构/数据均回到备份点）。

### 4.2 恢复单个 job 的工件

```bash
python scripts/restore_snapshot.py --restore-artifacts backup/<timestamp> --job-id 43ce04f2-...
```

- 先 `rm -rf /app/artifacts/<job_id>`，再从快照 `docker compose cp` 回容器；
- 只动演练 job 的数据，不影响其他 job（符合「不 down -v」约束）。

---

## 5. RPO / RTO 说明（学习点）

| 指标 | 定义 | 本演练实测（见 docs/09-LEARNING-LOG.md P06-08） |
|---|---|---|
| RPO（Recovery Point Objective） | 最大可接受数据丢失窗口 | 取决于备份频率；本工具为手动触发，RPO = 距上次备份的时长 |
| RTO（Recovery Time Objective） | 最大可接受恢复耗时 | 从执行恢复命令到 API 可查到完整 job 的时间 |

- 当前为手动备份（无定时器）；生产化需 cron 定期执行 `backup_snapshot.py --docker`。
- 全库恢复含 `pg_restore`（custom 格式，数据量小）与工件 `cp` 两步，耗时主要为 dump 文件大小与磁盘 IO。

---

## 6. 演练步骤（FLOW_MODE=fake）

1. **基线**：新建 fake 任务（AAPL/MSFT），记录 job_id；API 查询 steps 数量/状态、artifacts 清单（应含 08_report.md / 09_report.pdf）。
2. **备份**：`python scripts/backup_snapshot.py --docker --backup-root backup`
3. **校验**：`python scripts/restore_snapshot.py --verify <backup_dir> --job-id <job_id>`
4. **破坏**：故意删除该 job —— DB 删除 `research_jobs` 行（级联删 steps/artifacts 元数据）+ 删容器工件目录 `/app/artifacts/<job_id>/`（不 down -v、不动其他 job）。
5. **恢复**：
   - 全库恢复：`python scripts/restore_snapshot.py --restore-db <backup_dir>`
   - 工件恢复：`python scripts/restore_snapshot.py --restore-artifacts <backup_dir> --job-id <job_id>`
6. **验证**：API 查询该 job_id → steps 数量/状态、artifacts 列表（含 08/09）均与基线一致；MD/PDF 可下载且 checksum 匹配。

---

## 7. 约束与注意事项

- 不执行 `docker compose down -v`（不销毁 volume，只动演练 job 的数据）。
- 不调用真实 LLM/SEC/Serper（FLOW_MODE=fake）。
- 备份目录（如 `backup/`）应在 `.gitignore` 中，不提交快照内容。