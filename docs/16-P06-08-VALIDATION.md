# P06-08 验证记录（实际执行结果）

> 本文件记录 P06-08「PostgreSQL 与工件备份恢复演练」**实际执行过的验证命令与真实结果**。
> 依据 .clinerules「只能汇报真正执行过的命令和真实结果」。

## 1. 单元测试（脚本离线，不依赖 Docker）

### 备份脚本单测

命令：
```bash
.venv\Scripts\python.exe -m pytest tests/test_backup_snapshot.py -q -p no:cacheprovider
```

结果：
```
5 passed in 0.16s
```

覆盖：manifest 生成、快照文件存在、checksum 一致、同时间戳冲突报错、缺 source-dir 报错、job_ids 排序收集。

### 恢复脚本单测

命令：
```bash
.venv\Scripts\python.exe -m pytest tests/test_restore_snapshot.py -q -p no:cacheprovider
```

结果：
```
5 passed in 0.18s
```

覆盖：verify 通过、目标 job 存在、缺 job 报错、篡改工件报错、缺 manifest 报错。

### 相关测试合集（备份+恢复+报告/工件）

命令：
```bash
.venv\Scripts\python.exe -m pytest tests/test_backup_snapshot.py tests/test_restore_snapshot.py tests/test_api_artifacts.py tests/test_final_report_artifacts.py -q -p no:cacheprovider
```

结果：
```
27 passed in 2.53s
```

## 2. 静态检查

### Ruff

命令：
```bash
.venv\Scripts\python.exe -m ruff check src tests scripts
```

结果：
```
All checks passed!
```

### mypy（项目约定只检查 src）

命令：
```bash
.venv\Scripts\python.exe -m mypy src
```

结果：
```
Success: no issues found in 113 source files
```

## 3. 完整测试套件

命令：
```bash
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

结果：
```
869 passed, 1 skipped, 2 warnings, 18 errors in 166.18s (0:02:46)
```

18 个 errors 全部为 **testcontainers 基础设施网络错误**（需从 Docker Hub 拉取 `testcontainers/ryuk:0.8.1` 守护镜像，当前网络无法连接 `registry-1.docker.io:443`），涉及文件均为既有 DB 集成测试（test_db_base/test_job_repository/test_migration_001~004/test_step_artifact_repository），本次未修改这些文件。

## 4. Docker 真实演练（FLOW_MODE=fake，备份→删除→恢复→API 验证）

### 4.1 基线

| 项 | 值 |
|---|---|
| 演练目标 job_id | `43ce04f2-2d8f-4380-9849-51572243e183` |
| 状态 | succeeded（research_profile=fast，耗时 0.332s） |
| steps | 8 个（00_request → 07_manifest），全部 succeeded / attempt_count=1 |
| artifacts | `08_report.md`（sha256=`a2cbdc60...c6e7171`，1260 B）、`09_report.pdf`（sha256=`7e7ed4e5...4da7990`，1,773,844 B） |

### 4.2 备份

命令：
```bash
.venv\Scripts\python.exe scripts\backup_snapshot.py --docker --backup-root backup
```

结果：
```
备份完成，manifest: backup\20260816T145124576575Z\backup_manifest.json
  job_ids: ['3a0b4f9b-...', '43ce04f2-...', '9596054e-...', 'a9891f6c-...', 'd652da2b-...']
  pg_dump: invest_db.dump (52229 bytes, sha256=888f5a6b6f27...)
```

覆盖 5 个 job，pg_dump 52,229 bytes。

### 4.3 校验

命令：
```bash
.venv\Scripts\python.exe scripts\restore_snapshot.py --verify backup\20260816T145124576575Z --job-id 43ce04f2-...
```

结果：校验通过（pg_dump checksum 一致、工件逐文件 checksum 一致、目标 job 在覆盖列表）。

### 4.4 破坏（模拟故障）

1. 删除 DB 行（级联删 steps/artifacts 元数据）：
   ```bash
   docker compose exec -T postgres psql -U invest -d invest \
     -c "DELETE FROM research_jobs WHERE id = '43ce04f2-...';" \
     -c "SELECT count(*) AS remaining FROM research_jobs WHERE id = '43ce04f2-...';"
   ```
   结果：`DELETE 1`、`remaining = 0`

2. 删除容器内工件目录：
   ```bash
   docker compose exec -T worker sh -c "rm -rf /app/artifacts/43ce04f2-..."
   ```
   结果：目录消失（ls 无该 job）

3. API 确认消失：
   ```bash
   curl -s -o nul -w "HTTP %{http_code}" http://localhost:8000/v1/research-jobs/43ce04f2-...
   ```
   结果：`HTTP 404`

### 4.5 恢复

1. 全库恢复 PostgreSQL：
   ```bash
   .venv\Scripts\python.exe scripts\restore_snapshot.py --restore-db backup\20260816T145124576575Z
   ```
   结果：`PostgreSQL 全库恢复完成`；DB 确认恢复（status=succeeded、8 steps、2 artifacts 元数据）。

2. 恢复该 job 工件：
   ```bash
   .venv\Scripts\python.exe scripts\restore_snapshot.py --restore-artifacts backup\20260816T145124576575Z --job-id 43ce04f2-...
   ```
   结果：恢复完成。

### 4.6 恢复后 API 验证与基线对比

| 项 | 基线 | 恢复后 | 一致 |
|---|---|---|---|
| status | succeeded | succeeded | ✅ |
| steps 数量 | 8 | 8 | ✅ |
| steps 状态 | 全部 succeeded | 全部 succeeded | ✅ |
| artifacts 数量 | 2 | 2 | ✅ |
| 08_report.md sha256 | a2cbdc60... | a2cbdc60... | ✅ |
| 09_report.pdf sha256 | 7e7ed4e5... | 7e7ed4e5... | ✅ |

MD/PDF 通过 API 实际下载并校验 sha256 与基线完全一致。

## 5. RPO / RTO 实测

- **RPO**：本演练为手动备份（无 cron），RPO = 距上次备份的业务数据窗口。
- **RTO**：全库恢复（pg_restore）+ 工件恢复均为秒级完成（dump 52KB + 工件 1.7MB），从执行恢复命令到 API 可完整查询该 job ≈ 秒级。

## 6. 质量门禁汇总

| 检查 | 结果 |
|---|---|
| pytest 备份/恢复单测 | 10 passed |
| pytest 相关测试合集 | 27 passed |
| pytest 完整套件 | 869 passed, 1 skipped（18 errors = testcontainers 网络问题，与本次改动无关） |
| ruff check src tests scripts | All checks passed |
| mypy src | Success: 113 files |
| Docker 真实演练 | 备份→校验→破坏（API 404）→恢复→API+sha256 与基线完全一致 |