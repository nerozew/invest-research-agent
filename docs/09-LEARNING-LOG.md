# 学习记录（Learning Log）

> 本文件是项目的**个人学习笔记本**，非设计文档。
> 每完成一个路线任务（`docs/05-DEVELOPMENT-ROADMAP.md`），把当次交付报告中"要掌握的 3 个知识点"和"检查问题"沉淀到这里。
> 检查问题**不附答案**：学习模式要求先用你自己的话回答，再对照下一轮讨论核对。
> 记录顺序按任务完成先后；可在每个任务标题旁回填"已用自己的话复述 ✅"。

---

## P00-01：阅读 7 份设计文档，列出矛盾和待确认假设 ✅

**产物**：`docs/08-OPEN-QUESTIONS.md`

### 3 个知识点

1. **"先设计后实现"的文档驱动开发流程**：PRD → 架构 → 数据库 → 工作流 → 路线图是一条完整链条。P00-01 的价值就是检查链条有没有断点（例如架构列了 10 个关键数据对象，但工作流步骤 03 却要求一个没被记录的 `DocumentManifest`）。写代码之前先查链条，是工程师的核心素养。

2. **文档间一致性核查方法**：同一指标（如成功率 96/100）会出现在多份文档中（README、PRD、工作流可靠性文档）。通过交叉引用章节/行号，可以快速判断这是"口径一致"还是"真实冲突"。这也为以后代码审查时核对业务规则提供了同样的手段。

3. **问题状态分类而非一刀切**：发现的问题按严重程度分为"已确认 / 当前阻塞 / 后续任务再决定 / 风险提醒 / 未来改进"五类。只有真正阻塞的才立即处理，其余标记到对应任务节点再决定。这避免了"发现问题就全要解决"的无谓范围扩张。

### 检查问题（请用自己的话回答）
什么是"已确认口径"与"待确认假设"的区别？为什么"成功率 96/100"和"报告语言传递链"在 `08-OPEN-QUESTIONS.md` 中被放在不同的状态分类下？

---

## P00-02：初始化 Git 与基础 `.gitignore` ✅

**产物**：`.git/`、`.gitignore`（白名单模式）

### 3 个知识点

1. **Git 白名单 vs 黑名单**：常用 `.gitignore` 是黑名单（列出要忽略的文件）；但当目录混有大量无关文件时，白名单（`/*` 全忽略 + `!` 放行特定项）能精确限定"仓库边界是什么"。要点：Git 规则**不能重新包含已被忽略目录内的文件**，所以根目录层用 `/*` 再逐项 `!` 放行是最稳妥写法。

2. **`git check-ignore -v` 是验证利器**：它告诉你每一个被忽略的路径"是被哪一行规则、哪个位置匹配的"。这是排查"我明明忽略了为什么还在"或"我怎么没忽略成功"的首选工具，让忽略规则可验证、可追溯。

3. **`git -C <路径>` 不依赖当前目录**：跨目录执行 git 命令时，`-C` 显式指定仓库路径比 `cd` 更可靠（本次实践中 `cd /d` 在 cmd 未生效曾导致把仓库误建到别的目录）。命令要能明确、可复现地指向目标。

### 检查问题（请用自己的话回答）
为什么根目录使用 `/*` + `!` 白名单后，`.env.example` 仍能被放行（不会被前面的 `.env` 规则挡住），而 `.env` 却被正确忽略？两者在规则匹配上的区别是什么？

---

## P00-03：用 `uv` 建立 Python 3.12 项目 ✅

**产物**：`pyproject.toml`、`src/`、`tests/`、`uv.lock`、`.python-version`

### 3 个知识点

1. **src layout（src 布局）**：所有源码放在 `src/invest_research/` 下，`tests/` 放在外层。好处是：强制通过"安装包"来导入（而不是靠碰巧在源目录），避免测试运行时误导入本地目录而非已安装版本——这正是"我的电脑能跑，别人跑不了"问题的关键防线。

2. **依赖锁定（dependency locking）**：`uv.lock` 把每个依赖的精确版本固定下来，保证"今天能跑，三个月后、另一台电脑上也一样能跑"。这也是 README 中"成功率 >95% 必须靠可复现基准"能成立的基础——依赖一变，结果就不算数。

3. **开发依赖与运行时依赖分离**：`[dependency-groups] dev = ["pytest"]` 表示 pytest 只在开发/测试时需要，不进入生产依赖。这和控制"哪些文件进 Git"（`.gitignore`）是一类思想——**边界管理**：运行时、开发时、仓库内，每层都有清晰的边界。

### 检查问题（请用自己的话回答）
为什么 `uv run pytest` 能找到 `invest_research` 包并成功导入？这个过程中"src layout + uv 的可编辑安装"分别起了什么作用？

---

## P00-04：添加格式、lint、类型和测试配置（Ruff + mypy + pytest）✅（已用自己的话复述 ✅）

**产物**：`pyproject.toml`（新增 `[tool.ruff]`、`[tool.mypy]`、`[tool.pytest.ini_options]` 三段配置）

### 3 个知识点

1. **四类质量工具的职责分工**：
   - **Ruff Formatter**（`ruff format`）管"长得统一"——只改格式，不改逻辑；
   - **Ruff Linter**（`ruff check`）管"写得规范"——抓未用变量、潜在 bug、import 顺序；
   - **mypy** 管"传对类型"——静态检查类型标注是否正确，`strict = true` 让每个变量都必须有明确类型；
   - **pytest** 管"行为正确"——真正运行代码验证结果。
   这四者互补：格式化是先决的，lint/类型是约束，测试是最终验收。

2. **pyproject.toml 作为单一配置源**：工具配置统一写进 `pyproject.toml`，而不是各建一个 `ruff.toml`、`mypy.ini`、`pytest.ini`。这样项目根目录干净，`rootdir` 和配置自动关联，`uv run pytest` 时能直接读取 `testpaths`——所有配置一个文件可见、可审查。

3. **mypy strict 模式与"不全局忽略缺失 stub"**：`strict = true` 会用最严格的约束（并要求每个定义的函数、变量都带类型）。刻意**不设** `ignore_missing_imports = true`，是为了让第三方库缺类型 stub 的"缺失"显式暴露出来，而不是静默当作 Any 蒙混过关——这正是初学者最容易踩的坑：用宽松配置换来"类型检查通过"的假安全感。

### 检查问题（请用自己的话回答）
`ruff format`、`ruff check`、`mypy`、`pytest` 四条命令分别检查的是什么"维度"？如果只允许保留其中两个作为项目门禁，你会保留哪两个、为什么？

**用户复述记录（2026-08-11）**：ruff format 检查格式是否有问题；ruff check 检查整体代码是否有导包问题或其他问题；pytest 是正常 Python 测试工具。mypy 未明确点名（其回答中"输入输出格式参考问题"接近类型检查，但未用"类型"一词，需再强化 format 管排版格式 vs mypy 管数据类型 的区别）。

---

## P00-05：建立配置对象和 `.env.example` ✅

**产物**：`src/invest_research/settings.py`、`.env.example`

### 3 个知识点

1. **12-factor 配置（12-factor 配置）**：配置即"随环境变化的东西"（API 地址、密钥、数据库 URL），应与代码分离。`BaseSettings` 实现三级优先：**环境变量 > .env 文件 > 代码默认值**。这样"测试用测试配置、生产用生产配置"，同一份代码不动就能跑不同环境。缺失必需字段（如 `llm_api_key`）时抛 `ValidationError`，启动即报错，而不是运行到一半才炸。

2. **SecretStr 密钥保护**：密钥字段用 `SecretStr` 而不是普通 `str`。`str()`/`repr()`/打印配置对象时自动显示 `**********`，防止密钥被日志、异常信息、调试输出泄露。需要真正用值时才调 `.get_secret_value()`。`.env.example` 只放占位符，真实 `.env` 已被 `.gitignore` 拒绝入库——密钥风险从"别乱放"提升到"结构上就漏不出去"。

3. **框架感知 mypy 插件（而非全局忽略）**：Pydantic 的动态行为（环境变量填充字段、str 自动转 SecretStr）对静态分析是"魔法"，mypy 默认不认识，会报"缺参数/类型不匹配"。正确做法是启用官方插件 `plugins = ["pydantic.mypy"]` 让 mypy 理解框架，而不是粗暴地 `ignore_missing_imports = true` 把类型安全全部关掉。这也验证了 P00-04 的教训：遇到第三方库类型问题，先查它有没有官方 mypy 插件。

### 检查问题（请用自己的话回答）
为什么要用 `SecretStr` 而不是普通 `str` 存 API 密钥？如果密钥在 `str(settings)` 的输出里出现，会有什么实际危害？

**概念讲解记录（2026-08-11）**：
1. 12-factor 配置 —— 配置与代码分离，环境变量 > `.env` > 默认值三级覆盖；缺必需变量启动即报错。
2. SecretStr —— 密钥在日志/异常/调试输出自动显示 `******`，从"靠自觉"升级为"结构上漏不出去"。
3. mypy 插件 vs 全局忽略 —— 让检查器"听懂"框架的动态行为，而不是关掉类型安全。
**工作流确认（2026-08-11）**：用户确认理解"每次新增/修改代码后运行四类质量命令（format/check/mypy/pytest）判断是否合格"是编程规范，即"质量门禁（quality gate）"模式。该规范与 `.clinerules/02-engineering.md` 一致：只有测试、lint、类型检查真实通过才能标记任务完成。

---

## P00-06：建立统一命令入口 ✅

**产物**：`src/invest_research/quality.py`、`pyproject.toml`（注册 `invest-research-check` 入口）

### 3 个知识点

1. **单一入口命令（DRY）**：与其每次手动敲 4 条命令（format/check/mypy/pytest），不如封装成一条 `uv run invest-research-check`。命令的顺序固定、参数统一、退出码可判断——开发者和 CI 用同一套门禁，杜绝"人肉跑漏某一条"。这正是"可重复开发流程"的起点。

2. **`[project.scripts]` 注册可执行入口**：pyproject.toml 里 `invest-research-check = "invest_research.quality:check_all"` 把包内函数暴露为命令行入口。它复用 src layout 的"安装后导入"机制，命令在任何目录都能跑（配合 `uv run`），不需要额外写 .bat/.sh。

3. **编码边界：Windows GBK vs UTF-8**：跨平台脚本打印中文/emoji（✅❌）时，Windows 默认 GBK 终端会抛 `UnicodeEncodeError`。解决不是改终端，而是让**库代码输出保持 ASCII 安全**（[OK]/[FAIL]），把展示层与逻辑层分离。这是"写跨平台代码"的常见坑，早遇到早学会。

### 检查问题（请用自己的话回答）
为什么要把 4 条质量命令封装成一条命令而不是让每个人自己敲？`[project.scripts]` 与直接写一个 `.bat` 脚本比，优势在哪？

**概念讲解记录（2026-08-11）**：
讲解 `[project.scripts]` 的深度优点：①跨平台（Windows 生成 .exe shim、Linux/mac 生成 shell 脚本，同一份声明通吃）；②环境隔离（命令绑定项目 .venv，`uv run` 自动用项目依赖，不污染全局）；③生命周期跟随包（uv sync 自动注册/卸载，不会像 .bat 那样与代码版本脱节）；④入口即函数（`module:function` 可被 import、测试、复用）；⑤单一真相源（pyproject.toml 是 PEP 518 定义的项目配置总表，`[project]` 描述包、`[tool.*]` 给工具、`[project.scripts]` 声明命令，uv 从同一文件构建）。
对比 .bat 脚本：Windows-only、手工维护易脱节、不进 PATH、不绑定 venv、不可测试。

---

## P01-01：定义 Job/Step 状态枚举和错误码 ✅

**产物**：`src/invest_research/domain/status.py`、`src/invest_research/domain/errors.py`

### 3 个知识点

1. **StrEnum（字符串枚举）**：状态值以字符串存储（`pending`/`running`/...），与数据库 `workflow_steps.status` 的合法值完全对应，JSON 序列化天然一致；`is_terminal` 属性把"是否为终态"的判断集中到一处，避免各处散落 `if status in (...)`。

2. **显式状态机表**：用 `dict[状态, frozenset[可达状态]]` 声明合法迁移，再用纯函数 `can_transition_job/step` 拒绝非法迁移。把"规则"从一堆 if-else 变成**可测试、可审计的数据结构**——这正是 P00-01 在 08 文件记录的"显式状态机"学习点落地。

3. **错误分类的可重试性**：错误码用 StrEnum 定义 11 个；`is_retryable()` 用白名单集合判断；**未知错误码抛 ValueError 而非静默返回 False**（fail-fast），防止拼写错误被悄悄吞掉。

### 检查问题（请用自己的话回答）
为什么 Job 与 Step 的状态枚举要分开定义？`failed_retryable` 这个状态为什么只存在于 Step 层、而不存在于 Job 层？

**概念讲解记录（2026-08-11，用户要求附答案）**：

**一、为什么需要状态机（它们为谁服务）**
本项目实际是"两个状态机 + 一个错误分类规则"：
1. **Job 状态机** —— 服务对象是"用户/API"：回答"整个研究任务现在处于什么阶段"。用户在 `GET /v1/research-jobs/{id}`（P04-03）看到的 pending/running/succeeded/partial/failed/cancelled 就是它。只有到终态才允许对外展示"完成/部分/失败"，避免把"正在跑"误报成"已完成"。
2. **Step 状态机** —— 服务对象是"系统恢复执行"：回答"工作流的每一步（00~07）内部怎样"。这是断点续跑（FR-014）的根据：恢复时从 step 表找第一个非成功步骤，`succeeded/skipped` 的步骤跳过，`failed_retryable` 的步骤重跑。粒度是"工作流里单独一步"。
3. **错误码分类规则**（is_retryable）—— 是 Step 状态机的"驾驶员"：错误可重试 → 步骤转 `failed_retryable`；不可重试 → 步骤转 `failed_terminal`。P05-01 的重试策略、P05-03 的 stale recovery 都会读取它。

**二、为什么 Job 与 Step 分开定义**
因为"使用者、粒度、生命周期"都不同：
- **粒度**：Job = 一整个报告任务；Step = 任务内单独一步（00 请求校验/01 公司解析/02 信息搜集…）。
- **使用者**：Job 给用户/API 看，必须简洁明了；Step 给系统内部恢复用，必须精细到能判断"这一步能不能重试"。
- 底层思想：**对外展示简单、对内恢复精确**——用户不需要关心"第 3 步重试了 2 次"，但系统恢复时需要。

**三、为什么 failed_retryable 只存在于 Step 层**
- 步骤失败可能是**临时的**（网络超时、429 限流），重试**单个步骤**是安全的（有幂等键保护），所以 Step 需要一个中间态表达"这一步暂时失败但还有机会重试"。
- 但 **Job（整个任务）不会因为某个步骤在重试中就进入"可重试失败"态**：只要整体还在执行，Job 就保持 `running`（already covers"整体还在跑"）。只有当所有重试都耗尽、步骤最终变成 `failed_terminal` 时，Job 才整体转 `failed`。
- 一句话：**重试是"任务内部/步骤级"的行为，不代表任务整体失败**。所以 Job 只需要 `running`（还在整体跑）和 `failed`（彻底没救），不需要在 Job 层引入中间的"可重试失败"。

**生活类比**：Job 状态机="项目整体进度"（规划中→进行中→完成/取消/失败）；Step 状态机="项目里每个子任务"（待办→进行中→子任务成功/可重试失败/彻底失败）；错误分类="子任务失败时立刻重做还是直接放弃"。

**代码里后续用到的位置**：P03-11/12（Flow 写 Step 状态）、P01-06（状态转换 service）、P01-12/13（repository 条件更新）、P04-03（查询接口显示 Job 状态）、P05-01/03（重试与恢复）。

---

## P01-02：实现 `ResearchRequest` 和公司身份模型 ✅

**产物**：`src/invest_research/domain/models.py`、`tests/test_models.py`

### 3 个知识点

1. **Pydantic v2 声明式校验**：用 `Field(min_length/max_length)` + `field_validator` 把校验规则写进模型，非法输入（空公司名、未来日期、非 10 位 CIK、不支持语言）在对象创建时就抛 `ValidationError`，而不是运行到一半才发现——"fail fast"。

2. **frozen=True 不可变模型**：`ConfigDict(frozen=True)` 让领域对象创建后不可修改。这保护"数据契约"不被意外篡改，也与 `docs/04-WORKFLOW-RELIABILITY.md`"原始事实不可更新覆盖"的设计一致。

3. **工具链陷阱：中文路径写入**（本次实战教训）：Cline 的 write_to_file 对含中文的绝对路径存在截断 bug，导致文件被误写到 `D:\MyProjects\agent`、`D:\MyProjects\agent驱动的自动化` 等截断路径（tests 目录里的 test 文件因此未被 pytest 收集）。用 `replace_in_file`（基于内容匹配）或 Python `pathlib.Path.write_text`（原生 Unicode 路径）可绕开。**永远以"测试是否被收集/门禁全绿"为最终验收，而不是工具的回执**。

### 检查问题（请用自己的话回答）
`ResearchRequest` 为什么要在模型层就拦截"未来日期"和"空公司名"？如果把这些校验推迟到业务逻辑里，会有什么风险？

**概念讲解记录（2026-08-11）：项目分层与模型在其中的位置**

用户问题：model 层属于项目哪部分？和服务层、数据库层是什么关系？还有哪些层？

**项目分层（架构文档 §6 + .clinerules 依赖方向）**：
```
入口层（API / CLI）        ← 用户碰到的地方：FastAPI 路由、请求/响应 DTO
    ↓ 调用
应用层（application）       ← 指挥"做什么"：job service、workflow service（用例）
    ↓ 依赖
领域层（domain）           ← 业务真相（最底层、最稳）：Pydantic 模型、枚举、纯函数、错误码
```
基础设施（infrastructure）在"旁边"为上层服务：db/（SQLAlchemy、repository）、cache/（Redis）、queue/（Celery）、storage/（工件）、observability/（日志指标）。它同样**只朝下依赖** application 与 domain。

**依赖方向（铁律）**：`api/infrastructure → application → domain`，**domain 不得导入 CrewAI/FastAPI/SQLAlchemy/Redis/云 SDK**。

**domain 模型 ≠ 数据库表**：
- domain 模型（P01-02 做的）= 业务的形状（比如 ResearchRequest 必须有什么字段、什么校验）
- 数据库表 = 存储的形状（P01-07/08 才建 SQLAlchemy ORM 表）
- 中间由 **repository（P01-12/13）** 做翻译：把 domain 对象写入表、读表还原成 domain 对象
- 好处：业务规则（domain）不随存储技术（换 PostgreSQL→MySQL）变动；数据库只是"保存域对象的一种方式"

**项目还有什么层**：agents/（CrewAI Agent 配置）、flows/（CrewAI Flow 状态与编排）、tools/（typed tool 适配器）、financial/（concept 映射与公式）、prompts/（版本化提示词）。

**一句话**：domain 是"最内层、最不依赖别人"的业务核心；API/CLI 在最外层接用户；application 居中调度；infrastructure 负责把 domain 持久化到数据库并提供缓存/存储。所有依赖都朝内指，domain 谁也不依赖。

**概念讲解记录（2026-08-11）：模型层校验 vs 业务层校验（用户追问）**

用户问题：在业务层（真正的业务逻辑）判断"空公司名"等，和在模型中直接判断，有什么区别？模型校验有什么好处？

**核心答案：模型校验 = 把「对象必须合法」变成「对象本身不可变的基本属性」，校验收敛在唯一入口。**

区别与好处：
1. **Fail-fast（一创建就失败）**：模型校验在 `ResearchRequest(...)` 构造瞬间抛错。若推迟到业务层，一个请求可能穿过多个 service、写一半数据库才炸，"脏数据"已经污染状态。模型先挡住，非法对象根本不存在。
2. **单点规则（DRY）**：规则只在模型里写一次。所有入口（API、CLI、内部复用、未来新入口）都自动受同一份约束；若散在业务层，每个 service 各写一遍 if，极易漏一处或写不一致。
3. **防御不可变契约**：结合 frozen=True，模型即"一旦创建必定合法且不可改"。业务层不再需要反复校验，可以信任拿到的对象是干净的——降低心智负担，减少重复代码。
4. **边界能力校验 vs 流程校验的分工**：模型负责"这个对象本身合不合法"（空名、未来日期、非法 CIK——任何入口都会错的）；业务层负责"这个对象在流程里允不允许"（比如该 job 是否可取消、状态转换是否合法——依赖上下文的状态规则）。两者不冲突，前者保证"生成即合法"，后者保证"使用即合规"。
5. **可测试性**：模型规则 14 个独立测试秒级跑完；若藏在业务层，测试必须搭起整个 service 环境才能验证一条校验。

**一句话**：模型校验是"生成即合法"的第一道闸门，业务校验是"使用即合规"的流程闸门；前者防脏数据源头，后者防流程滥用，缺一不可。推迟到业务层 = 每个入口重复写、容易漏、脏数据可能已扩散。

**概念讲解记录（2026-08-11）：完整数据模型、是否 RBAC、设计时机（用户追问）**

用户问题：①表总不能只有两种数据？②是不是要建 RBAC 设计？表与表怎么关联？③这些该在 model 层想还是等到数据库层？

**A. 完整数据模型是多少张表？**
03-DATABASE.md 定义了 **14 张表**（不是 2 张！P01-02 只是最先做的 2 个 domain 模型）：
- 公司/任务：companies、research_jobs、workflow_steps
- 来源/文档：sources、job_sources、filings、documents、document_chunks
- 财务/报告：financial_facts、computed_metrics、reports、citations
- 审计/工件：tool_invocations、artifacts
代码没有的只是还没实现——P01-03~05 建 domain 模型，P01-08~11 建数据库表，逐步补齐。

**B. 需要 RBAC 吗？—— 不需要（MVP 无用户体系）**
RBAC（基于角色的访问控制）用于"谁能访问什么"（用户/角色/权限）。本系统 PRD/架构 **MVP 明确没有账号、登录、多用户权限**：CLI/FastAPI 本地运行，不做鉴权。所以：
- 不需要 user/role/permission 表；
- 未来若加多用户/云部署，是独立升级（架构 §9 已声明云不在当前范围），届时再补 RBAC。
- .clinerules/01-project.md 也把范围钉死在"不提供个性化服务/本地复现"，权限不是核心。

**C. 表与表的关联（03-DATABASE §2 ER 图）**
核心关系：
- companies 1—N research_jobs（一家公司多次研究）；companies 1—N filings（多次申报）
- research_jobs 1—N workflow_steps（一个任务多个步骤）
- research_jobs N—N sources 经 job_sources 关联（多对多：一个任务用多个来源，一个来源被多个任务用）
- sources 1—N documents；filings 1—N documents（来源/申报都有文档）
- research_jobs 1—N financial_facts 由 filings 提供；research_jobs 1—N computed_metrics
- research_jobs 1—1 reports；reports 1—N citations
- research_jobs 1—N tool_invocations（步骤调用审计）；research_jobs 1—N artifacts

**D. 该在 model 层想还是数据库层想？—— 现在（domain 层）就要定"业务形状"，数据库层只负责落地**
设计顺序（文档驱动）：
1. PRD/架构/03-DATABASE 已把表和 ER 定好（设计先行）；
2. **domain 层（现在 P01-02~05）**：把"业务对象长什么样、彼此什么关系"用 Pydantic 模型表达（比如 CompanyIdentity 的 cik 字段、将来 Source/filing 模型怎么引用 company_id）；
3. **数据库层（P01-07~11）**：用 SQLAlchemy ORM + Alembic 把表落库，字段/外键/唯一约束按 03-DATABASE 的 DDL 基线实现。
- 关联关系**先在 domain 设计文档定**（ER 图），实现时 domain 模型（业务形状）与 DB 表（存储形状）是两套但**字段一一对应**，由 repository 翻译。
- 换句话说：**ER 关系在文档层已定，不必等数据库层才开始想**；domain 模型现在就要反映这些关系（用引用的字段表达），数据库层只是按同一设计落地。

**一句话**：全项目 14 张表，分 4 组（任务/来源/财务/审计）；MVP 无用户体系所以不需 RBAC；表间关系由 03-DATABASE 的 ER 图统一约定，domain 模型现在就把这些关系建模出来，数据库层沿同一设计用 SQLAlchemy 落库，两者通过 repository 对接。

---

## P01-03：实现来源、申报、文档模型 ✅

**产物**：`src/invest_research/domain/models.py`（新增 `Source`、`JobSource`、`Filing`、`Document`、`SourceType`、`ParseStatus`）

### 3 个知识点

1. **JSON round-trip（往返无损）**：`model_dump_json()` 序列化 → `model_validate_json()` 反序列化，两者应得到相等对象。这是"不可变数据契约"的机器证明：只要模型字段是 JSON 可表示的（date/StrEnum/数值/布尔），Pydantic 就能无损往返。日期和 StrEnum 在 JSON 中存字符串，反序列化时自动还原类型。

2. **StrEnum 对齐数据库 CHECK 约束**：`SourceType`（5 值）、`ParseStatus`（4 值）与 03-DATABASE 的 `sources.source_type`、`documents.parse_status` CHECK 取值完全一致。枚举既是领域层的"合法值字典"，又是未来建表时 CHECK 约束的蓝本——一处定义，多处复用。

3. **引用 id 表达表间关系**：`Document.source_id`/`filing_id`、`JobSource.job_id`/`source_id` 用 id 字段表达关联（对应外键），但 domain 模型 **不建真实外键**（那是数据库层的事）。这体现"领域层表达业务形状、数据库层落地存储约束"的分工——P01-07/08 建表时外键/唯一约束才来对齐。

### 检查问题（请用自己的话回答）
`Source` 的 `canonical_url` 在 models.py 里用字段 + validator 保证"非空且去空白"。如果把这个唯一性/去重约束放到数据库层（UNIQUE 约束），两层各负责哪一部分？为什么不能只靠其中一层？

**概念讲解记录（2026-08-11，用户要求）：3 个知识点详细讲解 + 检查问题答案**

**知识点 1：JSON round-trip（往返无损契约）**
`model_dump_json()` 把对象变成 JSON 字符串（序列化），`model_validate_json()` 把 JSON 字符串变回对象（反序列化）。round-trip 测试断言"两次变换后 `restored == source`"。它证明：这个模型可以被安全地存进文件/数据库/消息队列，再读出来用——中间不丢字段、不改类型。这正是"不可变数据契约"的机器证明：**只要序列化不丢东西，未来在 worker 之间传递、断点续跑时还原工件（04-WORKFLOW-RELIABILITY §2）就可靠**。注意：round-trip 不是泛泛的"能转"，而是"转回来要相等"——不等就失败。

**知识点 2：StrEnum 对齐数据库 CHECK 约束**
`SourceType`（sec_filing/sec_xbrl/web/company_ir/uploaded）、`ParseStatus`（pending/parsed/failed/unsupported）的值，与 03-DATABASE 里 `sources.source_type`、`documents.parse_status` 列的 CHECK 约束完全一致。价值：①一处定义多处分红——领域层拿枚举校验，建表时拿枚举值写 CHECK，不会"两边各写一套字符串谁对不上"；②合法值字典——以后代码里写 `SourceType.WEB` 而不是裸字符串 "web"，拼错会立刻报错（枚举不存在）；③JSON 存字符串、反序列化自动还原成枚举成员，round-trip 也因此成立。

**知识点 3：引用 id 表达表间关系**
`JobSource.job_id/source_id`、`Document.source_id/filing_id` 是"指向别的对象的 id"，对应数据库的外键。但 domain 模型**不建真实外键对象**（不持有另一个模型实例），只存 id 字符串。分工：领域层用 id 表达"和谁有关系"这个业务形状；数据库层（P01-07/08）才用真实外键/唯一约束落地一致性。好处：领域模型轻、可单独测试、不依赖数据库连接；换存储也不改领域层。

**检查问题答案：模型层校验 vs 数据库层约束，各负责什么？为什么不能只靠一层？**
- **模型层（validator 去空白）负责：单条数据的"形状合法性"**——在对象进入系统那一刻就保证"这个 url 非空且格式干净"。它快、在所有入口通用、不依赖数据库存在。
- **数据库层（UNIQUE 约束）负责：跨记录的"全局唯一性"**——只有数据库做得到"并发下两个任务同时插入同一个 url 时，只有一个成功"。模型层无法保证唯一，因为它只看见自己这一条，看不见别的记录；而并发场景下数据库的 UNIQUE 才是最终防线（03-DATABASE §6：Redis 锁只是优化，数据库唯一约束才是最终一致性防线）。
- **为什么不能只靠一层**：只靠模型层 → 无法防重复（两条相同 url 都能入库，因为每个模型各自合法）；只靠数据库层 → 非法形状（空/带空白 url）会先写进库再被数据库拒绝，报错晚、还可能已污染其他步骤，且校验逻辑散落在 SQLAlchemy/DDL 里无法复用给 API/CLI 校验。
- 结论：**模型层管"生成即合法"（单条形状），数据库层管"全局唯一"（跨记录约束）**，两层叠起来才完整。这也是分层设计的直接体现。

---

## P01-04：实现财务事实和指标模型 ✅

**产物**：`src/invest_research/domain/models.py`（新增 `FinancialFact`、`MetricResult`、`MetricStatus`）

### 3 个知识点

1. **Decimal（十进制）替代 float**：财务金额/比例用 `Decimal` 而不是 `float`。`0.1 + 0.2` 在二进制浮点里是 `0.30000000000000004`，用 Decimal 则精确等于 `0.3`。Pydantic 收到字符串（如 `"1000000.00"`）会自动转 Decimal。这是 PRD/架构的硬性要求："LLM 不得负责财务算术，必须用带版本的确定性工具 + Decimal"。

2. **model_validator（跨字段校验）**：`@model_validator(mode="after")` 用于"多个字段联合起来才成立的规则"——比如 `FinancialFact` 的"期间（period_start/end）与时点（instant_date）必须二选一"，以及 `MetricResult` 的"status=computed 必须带 value、非 computed 必须 value 为空"。这比逐字段 validator 更能表达对象级不变量。

3. **FinancialFact 与 MetricResult 的"事实 vs 派生"分离**：`FinancialFact` 是 XBRL 原始事实（不可改、fact_version 版本化）；`MetricResult` 是由公式算出的派生值（formula_version、inputs_json 记录输入与口径）。原始事实不覆盖、派生值可版本化——这正是 03-DATABASE"事实与派生数据分离"的领域表达，也是质量门禁"每个派生指标可追溯输入"的基础。

### 检查问题（请用自己的话回答）
`FinancialFact` 为什么要把"期间型"（period_start/end）和"时点型"（instant_date）做成二选一？如果一个事实同时又期间又时点，或两者都没有，对后续指标计算会有什么危害？

**概念讲解记录（2026-08-11，用户要求）：3 个知识点详细讲解 + 检查问题答案**

**知识点 1：Decimal（十进制）替代 float —— 为什么精确这么重要**
计算机里的 float（浮点数）用的是"二进制小数"，而人类财务用的是"十进制小数"。二进制无法精确表示 0.1/0.2 这类十进制小数，所以：
- `0.1 + 0.2` 在 float 里结果是 `0.30000000000000004`（不是 0.3！）
- `1.0 - 0.9` 结果是 `0.09999999999999998`
单个误差极小，但财务计算层层累加（收入×税率、比率×金额…）误差会被放大，最后报告里出现"数字差一分钱"——这在财务质检（04-WORKFLOW-RELIABILITY 硬门禁：报告数字与结构化 pack 在规范化精度下完全一致）会被判定失败。`Decimal` 用"十进制小数"精确表示：`Decimal("0.1") + Decimal("0.2") == Decimal("0.3")` 成立。所以 `value: Decimal` 是把"财务算术必须精确"变成"类型层面的强制"——想用 float 传值，Pydantic 会拒绝或转成 Decimal。

**知识点 2：model_validator（跨字段校验）—— 单个字段管不了全局规则**
之前的 `field_validator` 只检查"一个字段"（比如 CIK 必须 10 位）。但有些规则是"**几个字段合起来**才成立的"：
- `FinancialFact`：一个事实要么是"期间型"（给 period_start + period_end），要么是"时点型"（给 instant_date），不能又期间又时点、也不能都没有。
- `MetricResult`：状态是 computed 就必须有值；不是 computed 就必须没值。
这种"对象级不变量"用 `@model_validator(mode="after")`——它等所有字段都校验完、对象构建好后跑一次，检查跨字段关系，不合法就抛 `ValueError`。好处：规则集中、可用 `pytest.raises(ValidationError)` 直接测试。

**知识点 3：FinancialFact 与 MetricResult 的"事实 vs 派生"分离 —— 为什么不能混在一起**
- **FinancialFact = 原始事实**：从 SEC XBRL 拿到的一手数字（某年收入、某时点资产）。它**不可改**——公司官方数据就是那个样，修正只能新增 `fact_version` 版本，绝不能覆盖原始值（对齐"原始事实不可更新覆盖"的 ADR/03-DATABASE）。
- **MetricResult = 派生指标**：由公式算出来的（毛利率 = 毛利润/收入）。它有 `formula_version`（公式版本）和 `inputs_json`（用哪些 fact、什么口径）——因为公式可能升级（v1→v2），算出来的值要能追溯"我用什么版本算的、喂了什么输入"。
两者分开的价值：原始数据永远可信、可复查；派生结果可审计、可对账。这也正是质量门禁"每个派生指标输入、单位、期间、公式版本完整"能成立的基础。

**检查问题答案：期间型 vs 时点型为什么必须二选一？**
SEC 财务数据天然分两类：
- **期间型（duration）**：描述"一段时间内发生的量"——收入、费用、现金流。必须知道起止（period_start + period_end），否则"这 10 亿收入是一季度还是三年？"，无法同比/环比。
- **时点型（instant）**：描述"某一时刻的状态"——总资产、总负债、股东权益。只有一个日期（instant_date），没有"起止"。
危害分析：
- **又期间又时点**：这一条 fact 到底是"流量"还是"存量"？指标计算器无从下手——把它当收入参与毛利公式是错的（它可能是个余额），把它当资产参与 ROA 也错。数据语义自相矛盾，任何下游公式都会算出荒谬值。
- **两者都没有**：这条 fact 连"发生在什么时候"都不知道，无法与另一期对比、无法放进任何 period_end 对齐的指标表，等于一条废数据——还会让"可比期间选择器"（P02-14）和"公式输入校验"（P02-15）直接卡死。
所以模型层用 XOR（严格二选一）在字段进系统那一刻就把这两种"脏数据"挡在门外——这正是"生成即合法"的又一次落地。

---

## P01-05：实现 Research/Analysis/Report/Quality pack schema ✅

**产物**：`src/invest_research/domain/models.py`（新增 `ResearchPack`、`FinancialAnalysisPack`、`ReportDraft`、`QualityReport`）

### 3 个知识点

1. **pack 即"结构化 Agent 输出契约"**：Agent（LLM）不输出自由文本，必须输出能通过 `ResearchPack`/`FinancialAnalysisPack` 等 Pydantic 模型校验的结构化对象——对应 04-WORKFLOW-RELIABILITY §2 步骤表里的输出 schema。这让下游代码（质量门禁、报告模板）可以确定性地读取 Agent 产出，而不是解析自由文本。这也是 P03-05~07"fake LLM 任务"的契约基础。

2. **必备 version 字段（可追溯/可失效重算）**：每个 pack 都有 `version`（如 `research_pack_v1`）。配合 P05-04"输入 hash 与下游失效"：schema 版本变化 → 旧 pack 失效 → 下游必须重算。这让"提示词/模型/公式变化后重算"在领域层就有据可依。

3. **组合已有 domain 模型（模型组装）**：`ResearchPack` 内嵌 `CompanyIdentity` + `list[Source]`；`FinancialAnalysisPack` 内嵌 `list[FinancialFact]` + `list[MetricResult]`。packs 不是新造数据结构，而是"把之前的领域对象组装成 Agent 的输入/输出边界"——体现依赖方向：pack 依赖底层模型，底层模型不依赖 pack。

### 检查问题（请用自己的话回答）
为什么 Agent 的输出必须绑定 Pydantic 模型（pack），而不是让它自由写 JSON？`version` 字段对"提示词升级后下游要重算"有什么作用？

**概念讲解记录（2026-08-11，用户要求）：Pydantic / pack / version 三者的关系**

**一、pack 和 Pydantic 是什么关系？**
Pydantic 是我们的校验引擎（python 库，pydantic 2.13.4）；它提供的 `BaseModel` 是所有领域模型的基类（ResearchRequest、CompanyIdentity、Source…都继承它）。而 "pack"（ResearchPack 等 4 组）特指"**专门给 Agent 当输入/输出边界的那几个模型**"。所以：**每个 pack 都是一个 Pydantic 模型（继承 BaseModel），Pydantic 是引擎，pack 是用引擎定义的"Agent 契约"**。"自带检查"就是指 BaseModel 的自动校验：模型声明了字段类型、必填、范围、跨字段规则，创建对象时 Pydantic 自动跑校验，不合格抛 ValidationError。

**二、为什么不允许 Agent 自由写 JSON？（"防止格式不对"只是表面）**
1. **Schema 即契约**：模型规定"必须有哪些字段、什么类型、什么范围"（如 sources 至少 1 个、version 非空、金额是 Decimal）。Agent 输出必须 100% 匹配，不匹配 → 这步立即失败、可重试，而不是半路才炸。
2. **类型自动转换与校验**：Agent 给 "1000000"（字符串）→ Pydantic 自动转 Decimal；给错类型（version=123）→ 直接拒绝。下游拿到的永远是类型正确的对象。
3. **Fail-fast（及早失败）**：非法产出在这步就暴露 → 触发 04 的 SCHEMA_INVALID + guardrail 修复（P03-09）。允许自由 JSON 会让坏结构一路漏到质量门禁、报告模板，错误被放大且难定位。
4. **确定性读取**：下游只需 `pack.sources`、`pack.metrics` 取字段，不用写一堆 if-else 去解析自由文本/非标 JSON。
5. **工件可往返**：模型可 dump_json 存工件、validate_json 还原；自由 JSON 无法保证这种契约。
一句话：**把 Agent 的"自由发挥"关进"结构化的笼子"，让链路每一步可验证、可追溯、可重试**。

**三、version 字段的真正作用（纠正"唯一性"的猜测）**
你猜"确保唯一性/当前版本"——方向对了一半（它确实是版本号），但**不是"唯一性"**（对象的唯一性由数据库 UNIQUE 约束管，不是 version 管）。version 真正的作用：
1. **语义版本（schema 契约版本）**：`research_pack_v1` = "这个 pack 形状是 v1"；将来改 model（加字段/改类型）→ 升 v2。
2. **可追溯**：工件是 v1 还是 v2？报告用哪个版本的 pack？看 version 便知。
3. **可失效重算（P05-04 输入 hash）**：提示词/schema/公式版本变化 → 旧 version pack 不再可信 → 下游必须重算，不能复用旧工件（正是断点续跑"失效"的判断依据，04 §6）。
4. **不是唯一性**：同一 job 里可有多个 source，无需 version；version 管的是"schema/口径的版本"，不是"对象 ID"。

**生活类比**：pack = 一张"表格模板"（Pydantic 校验 = 表格栏位严格核查）；version = 表格模板的版本号（V1.0→V2.0），能追溯"谁用的哪版模板、改了哪些栏位 → 旧表格作废重填"。



---

## P01-06：编写纯函数 Job/Step 状态转换器 ✅

**产物**：`src/invest_research/domain/transitions.py`、`tests/test_transitions.py`

### 3 个知识点

1. **"规则表"与"执行器"分离**：`status.py` 的 `JobTransitions`/`StepTransitions` 是"规则表"（数据：哪些迁移合法）；`transitions.py` 的 `transition_job/step` 是"执行器"（行为：查表+裁决）。规则是数据、执行是函数，各自独立、都可测试——这正是"显式状态机"的完整落地。

2. **抛异常 vs 返回布尔（fail-fast）**：P01-01 的 `can_transition_*` 返回 False（"能不能"询问）；P01-06 的 `transition_*` 非法时抛 `InvalidTransitionError`（"执行"必保成功）。选抛异常是因为"执行状态迁移"是不可回退的关键动作——调用方若不处理 False 就会继续往下走、把状态搞坏；异常强制调用方处理。异常带 `current`/`target` 属性 + `pending -> succeeded` 信息，排障清晰。

3. **领域异常 vs 通用异常**：`InvalidTransitionError` 继承 `ValueError`（仍是纯 Python 异常，领域层零外部依赖），但语义明确——用它而不是裸 `ValueError`/`AssertionError`，让上层（repository/Flow）能精确捕获并映射到错误码（如 INTERNAL_BUG），而不是 catch-all。

### 检查问题（请用自己的话回答）
`transition_job` 对非法转换是抛异常，而 P01-01 的 `can_transition_job` 是返回 False。两种 API 各自适合什么场景？为什么执行迁移时选"抛异常"更安全？

**概念讲解记录（2026-08-11，用户要求）：抛出异常 vs 返回 False 场景区分 + 领域异常与通用异常**

**一、什么时候返回 False、什么时候抛异常？**
判断标准一句话：**"询问/查询"返回布尔，"执行/命令"抛异常。**
- 返回 False 的场景（查询）：函数回答"能不能、是否允许"，失败是**正常业务分支之一**，调用方会主动判断并分别处理。例：`can_transition_job(PENDING, SUCCEEDED)` 返回 False，调用方据此决定"不显示已完成"——不需要中断执行。
- 抛异常的场景（执行）：函数承诺"去做一件事并成功"，失败意味着**业务前提被破坏或程序出错了**，调用方如果再往下走就会搞坏状态，所以必须被强制处理。例：`transition_job(PENDING, SUCCEEDED)` 抛 `InvalidTransitionError`——这是非法迁移，绝不允许静默跳过。
- 类比：红灯是"查询"（can 过？False）→ 你不会把车开过去；直接闯过去是"执行" → 被拦下（抛异常）。查询允许你绕路，执行不允许你违规通过。
- 编码习惯：**能早失败就抛异常（fail-fast）**；只有"False 是合法分支之一"时才用布尔。把"执行"函数悄悄返回 False 是最危险的——调用方忘了检查，状态就损坏了。

**二、领域异常 vs 通用异常**
- **通用异常**（Python 内置：ValueError、KeyError、RuntimeError…）：语义笼统。catch 到 ValueError 不知道是"业务规则被违反"还是"参数本身错了"，上层难以精确处理。
- **领域异常**（项目自己定义：InvalidTransitionError）：用类名精确表达业务语义。上层可以 `except InvalidTransitionError` 精确捕获，并映射到项目错误码（04 §4 的 INTERNAL_BUG 或专门错误），而不是大范围 catch-all。
- 为什么 InvalidTransitionError 仍继承 ValueError：既保留"这是非法参数/非法调用"的通用性质（能被通用逻辑兜底），又用类名给出领域语义——两全。



---

## P01-07：启动 PostgreSQL 测试容器与 SQLAlchemy base ✅

**产物**：`src/invest_research/infrastructure/db/base.py`、`tests/test_db_base.py`

### 3 个知识点

1. **Testcontainers（测试容器）**：集成测试要"真实数据库"但不想污染开发库——`PostgresContainer` 自动拉取/启动一个临时 PostgreSQL 容器，测试结束自动销毁。测试真正连的是 Docker 里的数据库，而非 mock。本任务在 VS Code Remote-SSH 的 Ubuntu（有 `/var/run/docker.sock`）上跑出 `2 passed`，无 skip。

2. **SQLAlchemy 2 DeclarativeBase + engine 工厂**：`Base(DeclarativeBase)` 是所有 ORM 映射（P01-08 起）的基类；`create_db_engine` 用 `pool_pre_ping=True`（取连接前 ping，防陈旧连接）统一建连；`create_session_factory` 统一 session。DB 连接集中在一处，上层只拿 URL/engine 用。

3. **事务回滚可被测试证明**：测试先提交 1 行；再在另一事务里插入第 2 行并 `rollback()`；最后断言 COUNT 仍为 1——证明"回滚后不生效"。这是 04-WORKFLOW-RELIABILITY"原子成功提交、失败不留下半成品"的基础能力验证。

### 检查问题（请用自己的话回答）
为什么集成测试要用 Testcontainers 起临时容器，而不是"连一个长期运行的本地 PostgreSQL"？`session.begin()` + `session.rollback()` 能证明什么？

**概念讲解记录（2026-08-11，用户要求）：P01-07 各名词概念 + 检查问题答案**

**一、先翻译名词（通俗版）**
- **集成测试（Integration Test）**：不是"测单个函数"（那是单元测试），而是把**真实组件拼起来**测"它们能不能配合"——这里是"Python 代码能不能真的连上 PostgreSQL 并执行事务"。
- **Testcontainers**：一个"测试专用容器管理库"。跑测试时它自动用 Docker 拉一个临时 PostgreSQL 容器，测试结束自动停止+删除。
- **SQLAlchemy**：Python 的数据库工具库（ORM）。ORM = 把"Python 对象"↔"数据库表行"互相映射；SQLAlchemy 2 是当前大版本。
- **DeclarativeBase**：SQLAlchemy 2 的"声明式基类"。将来每个 ORM 表模型类都 `class 表名(Base)`，SQLAlchemy 就知道它是数据库表的映射。
- **Engine / Session / 连接池 / pool_pre_ping**：
  - Engine = 连接管理器（内含连接池，复用一个连接避免每次新建）；
  - Session = 你操作数据库的工作单元（增删改查都在 session 上，最后 commit 或 rollback）；
  - pool_pre_ping = 取连接前先 ping，防止拿到已断开的"陈旧连接"。
- **事务（Transaction）**：一组数据库操作打包成"原子单元"——要么全部 commit（生效），要么全部 rollback（废弃），不存在"改了一半"。

**二、事务回滚到底在讲什么**
生活类比（转账）：扣 A 账 + 加 B 账，两步必须一起成/一起败。commit=盖章生效；rollback=废弃重来，好像从没发生过。
测试里：先建表插 1 行并 commit（已持久化，count=1）→ 开新事务插入第 2 行（未提交）→ rollback() 撤销 → 再查 count 仍为 1。**证明：被回滚的改动不会留在数据库里。**

**三、检查问题答案**
Q1 为什么用 Testcontainers 临时容器，而不是连长期本地库？
- 长期库有"状态残留"：上次测试的数据还在，测试不稳定（第一次 vs 第二次环境不同）；
- 手动清理麻烦、易漏；
- Testcontainers 让每个测试拿到**干净的全新 PostgreSQL**，自包含、可重复、完全隔离；代价是要 Docker、首次拉镜像较慢。

Q2 session.begin() + session.rollback() 能证明什么？
- 证明"数据库确实具备事务语义"：回滚后数据不残留。这是后续"原子成功提交、失败不留下半成品"（04 §2/§3 与 P01-12/13 repository）能力的地基——若数据库连回滚都做不到，那些"失败时清理半成品"的保障都是空谈。



---

## P01-08：建 Alembic 并迁移 company/job/step 表 ✅

**产物**：`migrations/`（alembic.ini、env.py、script.py.mako、versions/0001）、`src/invest_research/infrastructure/db/models.py`（ORM：Company/ResearchJob/WorkflowStep）、`tests/test_migration_001.py`

### 3 个知识点

1. **Alembic = 数据库 schema 的版本控制（Git for 数据库）**：`migration 001` 记录"表结构从一个版本到下一个版本的增量变化"。`upgrade` 前进一步、`downgrade` 后退一步，都能执行、都幂等。验收"upgrade/downgrade/upgrade 均通过"就是证明**迁移可逆**——万一出问题能无损回滚到旧结构，这是生产环境敢改 schema 的前提。

2. **ORM 模型（业务形状）与 migration（存储落地）分工**：ORM 用 `Mapped[类型]` 声明式描述表结构（Python 对象 ↔ 表行映射）；migration 用 `op.create_table/drop_table` 写数据库实际改动。两份都要维护且一致——`Base.metadata`（env.py 里）能自动给 Alembic 提供元数据，但 database migration 仍是**显式、可回滚**的标准做法。

3. **元数据与约束对齐（真实失败复盘）**：本次踩坑根因——**psycopg2 与 psycopg3 驱动前缀不匹配**。Testcontainers 返回 `postgresql+psycopg2://`，而项目装的是 psycopg3（前缀 `psycopg`），导致 Alembic/`create_engine` 加载 psycopg2 失败 `ModuleNotFoundError`。教训：**连接 URL 的 dialect 前缀必须与已安装驱动一致**（`psycopg` vs `psycopg2` 是两个不同的包）；Testcontainers 默认暴露 psycopg2 前缀，需手动规整为 `_normalize_psycopg()`。
  另踩：`metadata` 列名与 `DeclarativeBase.metadata` 冲突 → 用 `metadata_` 属性 + 显式列名 `"metadata"`；`Mapped[dict]` 需带泛型 `dict[str, Any]` 过 mypy strict。

### 检查问题（请用自己的话回答）
为什么数据库 schema 变更要用 Alembic migration（可回滚），而不是直接手写 `CREATE TABLE` 改数据库？`upgrade` 与 `downgrade` 各自的作用是什么？

**验收讲解（用户问"我们到底在干什么"）**：
P01-08 不是在"建立真正的投资数据"，而是在**验证"数据库建表的工程流程本身合格"**：
1. 用 Testcontainers 临时拉一个全新 PostgreSQL（不污染你的开发/生产库）；
2. 运行 `alembic upgrade head` → 执行 migration 001 → 创建 companies/research_jobs/workflow_steps 三张表；
3. 断言三张表存在、字段/约束正确（唯一、外键）；
4. `downgrade base` → 表被删除（证明可回滚）；
5. 再 `upgrade head` → 表重建（证明可重复执行）。
所以：**测试通过 = "这套迁移机制+迁移脚本是合格、可逆、可复现的"**。真正的数据写入要等 P01-12/13 repository 才做。

---



**概念讲解记录（2026-08-11，用户要求）：alembic.ini 是什么、建表和 ORM 怎么进行、用了哪些包**

**一、alembic.ini 是什么**
- 它是 **Alembic 的配置文件**（INI 格式，类似 Windows 的 .ini）。Alembic 启动时先读它，获得"迁移脚本放哪、连哪个数据库、日志怎么打"等设置。
- 我们文件里关键几行：
  - `script_location = migrations` → 迁移脚本目录；
  - `sqlalchemy.url = postgresql+psycopg://...` → 默认数据库连接串（本项目里仅用作离线生成 SQL / 默认；测试时被 Testcontainers 的真实 URL 覆盖）；
  - `prepend_sys_path = .` + `path_separator = os` → 让 Alembic 能 import 项目包（env.py 里用的到）。
- 注意：**alembic.ini 不是"数据库"**，它是"Alembic 工具自身的设置"；真正的数据库是 PostgreSQL（测试里由容器提供）。

**二、整个"建表 + ORM"的流程（从代码到真实表）**
步骤串联：
1. Python 里用 SQLAlchemy ORM 写表模型（models.py：class Company(Base) 等）——这是"Python 形状"；
2. Alembic env.py 把 Base.metadata 暴露给 migration；
3. migration 001 用 op.create_table(...) 描述"如何建表"（SQL 之外的另一份说明）；
4. 跑 `alembic upgrade head` → Alembic 读 migration → 连数据库 → 执行 CREATE TABLE；
5. 测试验证：表是否存在、外键/唯一约束是否建对、能否 downgrade 删掉再 upgrade 建回。
- ORM（Object-Relational Mapping）= 把"Python 对象"和"数据库表行"互相翻译。写 `session.add(company)` 它会转成 INSERT；`session.query(...)` 会转成 SELECT。

**三、用到哪些库/工具（按角色分）**
| 包/工具 | 角色 |
|---|---|
| PostgreSQL（容器里） | 真实数据库，存表和数据 |
| SQLAlchemy 2 | ORM 库：Mapped/mapped_column 建表模型、create_engine 连接、Session 操作 |
| psycopg (v3) | PostgreSQL 的 Python 驱动：SQLAlchemy 通过它和 PostgreSQL 通信（URL 前缀 +psycopg） |
| Alembic | 基于 SQLAlchemy 的迁移工具：alembic.ini + migrations/ 管理 schema 版本 |
| dish 包 | 用 alembic upgrade/downgrade 命令执行/回滚迁移 |
| Testcontainers + docker | 测试时临时拉 PostgreSQL 容器，隔离环境 |
| pytest | 驱动测试断言"表建对了、可回滚" |

## P01-09：迁移 source/filing/document 表 ✅

**产物**：`migrations/versions/0002_source_filing_document.py`、`src/invest_research/infrastructure/db/models.py`（追加 Source/JobSource/Filing/Document/DocumentChunk）、`tests/test_migration_002.py`

### 3 个知识点

1. **drop 顺序陷阱（真实踩坑）**：downgrade 必须先 `DROP INDEX` 再 `DROP TABLE`。原因：**PostgreSQL 删表时会自动连带删掉表上的索引**，若先删表再 `DROP INDEX` 会报 `index does not exist`。教训：离线 `alembic downgrade <from>:<to> --sql` 可在不连库时提前验证迁移脚本的 SQL 顺序。

2. **连接生命周期（真实踩坑）**：`with engine.connect() as conn:` 块结束时连接已关闭；在 `with` 外再使用 `insp.get_unique_constraints()` 会抛 `ResourceClosedError`。必须在连接存活期内取完数据，退出后再断言。教训：**数据库连接是有界的资源，取值与关闭的边界要心里有数。**

3. **约束/索引的验证方式**：`inspect(conn)` 的 `get_unique_constraints` / `get_indexes` 可读取库里真实建出的约束与索引，用它们断言"表结构真的建对了"，而不只是"表存在"。

### 检查问题（请用自己的话回答）
为什么 `downgrade` 要先删索引再删表？如果顺序反了会报什么错？`with engine.connect()` 块结束后还能用 `insp` 吗？

**用户疑问解答："本机测试通过了，以后还需要在 Linux 上再测吗？"**
- 结论：**这套数据库集成测试（P01-07/08/09 的 skipif 测试）以后只需要在"有 Docker 的机器"上跑一次确认即可，不需要每次都在 Linux 上重测。**
- 原因：测试是**同一份代码**，Testcontainers 拉同一个 `postgres:16-alpine` 镜像、在隔离容器里跑——**结果只取决于代码和镜像，不取决于宿主 OS 是 Windows 还是 Linux**。Linux 上通过 = Windows 上也会通过（前提 Windows 装了 Docker）。
- 目前你 Windows 没有 Docker，所以这些 DB 测试在本机被 skip；真正判定"通过"就用一台有 Docker 的机器（你的 milnus Linux）跑一次即可。以后如果 Windows 装上了 Docker Desktop，本机也能跑出同样的结果。
- 简化建议：把"有 Docker 机器跑 `uv run pytest tests/test_migration_*.py`"当作一项固定的验收动作（这和 CI 在 Linux runner 上跑数据库测试是一样的道理）。

---



**概念讲解记录（2026-08-12，用户要求）：索引是什么 / 为何先删索引再删表 / 与 MySQL 是否不同**

**一、索引（Index）是什么？**
- 索引 = 数据库表的"目录"（类比书的目录/检索引擎）。没有索引，查一行要**全表扫描**（一行行翻）；有索引，能直接跳到目标位置。
- 我们建的 `ix_filings_company_form_period` = 在 `filings` 表的 `(company_id, form_type, report_period)` 三列上建的一个索引，用途是"按公司+表单+报告期快速查询"。
- 索引是**独立于表数据**的额外结构：建索引额外占空间；每次插入/修改/删除时数据库还要同步更新索引（所以索引不是越多越好，是有代价的）。

**二、为什么先删索引再删表？**
- 索引是"依附表"的对象。**关系型数据库执行 `DROP TABLE` 时，会自动把该表上的所有索引一并删除**。
- 所以如我们脚本那样先 `DROP TABLE` 再 `DROP INDEX`，索引已随表消失 → 报 `index does not exist`。
- 正确做法（二选一）：
  1. 先 `DROP INDEX`（显式删索引）→ 再 `DROP TABLE`（删表）；
  2. 或干脆只 `DROP TABLE`，让数据库连带删所有索引（但我们 migration 里选择显式删，更清晰）。

**三、和 MySQL 不同吗？**
- **不是 PostgreSQL 特有——MySQL 行为一模一样**：`DROP TABLE innodb表` 也会连带删除表上的索引（普通/唯一/主键的都删）。
- 所以这个"先删索引再删表 / 或只删表"的教训在 MySQL 同样适用。
- 真正的差异点不在"删表是否连带索引"，而在其他层面（如类型系统、SEQUENCE/自增、CHECK 约束执行力度等），与本问题无关。



**概念讲解记录（2026-08-12，用户追问）：既然删表能删索引，为什么还先删索引？MySQL 是不是自动建索引？**

**一、为什么"删表连带删索引"了，脚本里还要显式先 DROP INDEX？**
- 从"功能"看：只 `DROP TABLE` 也完全够（索引随表消失）。但我们仍写显式 `DROP INDEX`，原因：
  1. **可读/可审计**：迁移脚本是团队/历史要回看的工程文档。`先 DROP INDEX 再 DROP TABLE` 让读者一眼看出"这个表上有什么索引、按什么顺序撤销"，而不是把一个结构隐式带过。
  2. **与 upgrade 严格互逆**：upgrade 是 `CREATE TABLE ... → CREATE INDEX ...`；downgrade 反向即 `DROP INDEX ... → DROP TABLE ...`（对称、可预测，也符合 Alembic autogenerate 的生成习惯）。
  3. **通用性**：某些索引（如表达式索引、跨表的部分索引）删除顺序更关键；总是"显式先删索引"能避免边界情况。
- 一句话：**不是必须，而是更规范、更可维护**——工程上我们倾向"显式表达每一步"。

**二、MySQL 里"没见过索引"，是数据库自动建的吗？我们是按自己规则建的吗？**
- **部分索引 MySQL 会自动建**：InnoDB 里，**主键（PRIMARY KEY）自动产生一个聚簇索引；唯一约束（UNIQUE）也会自动为其列建索引**。你在 MySQL 里用主键/唯一键时，背后其实已有索引，只是你没显式看到（SHOW INDEX 才看得到）。
- **普通索引不会自动建**：像我们 `ix_filings_company_form_period` 这种"为了查询加速显式建的普通二级索引"，MySQL 里也必须显式 `CREATE INDEX ...`，数据库不会替你建。
- **我们确实是"按自己的规则"建**：migration 里每个 `op.create_index` 都来自项目设计文档（03-DATABASE §4 的索引规划），不是数据库自动行为——建不建普通索引、建在哪些列，是由"预期查询模式"决定的工程决策。
- 所以：主键/唯一键 → 数据库自动给你索引；普通查询索引 → 必须开发者显式建（我们显式做了）。

## P01-10：迁移 fact/metric/report/citation 表 ✅

**产物**：`migrations/versions/0003_fact_metric_report_citation.py`、models.py 追加 4 类、`tests/test_migration_003.py`

### 3 个知识点

1. **Decimal 精度落地**：financial_facts.value / computed_metrics.value 用 `Numeric(38,10)`（数据库层精确十进制），与 domain 层 `Decimal`、03-DATABASE DDL 一致。测试用 `inspect().get_columns()` + `isinstance(type, Numeric)` 断言真实列的 precision=38 / scale=10——不只"表存在"，还验证**列类型正确**。

2. **外键断言（真实踩坑）**：`get_foreign_keys(table)` 返回的 `fk["referred_table"]` 是**单个目标表名字符串**（如 `"companies"`），不是列表。一开始写成 `for ref in fk["referred_table"]` 会把字符串拆成字母（集合变成 `{'a','c','e',...}`）导致断言失败。教训：**读第三方库回传结构前，先打印确认字段形状**。

3. **迁移链可回退**：003 的 downgrade 到 0002（任何中间版本都行，不只 base）→ 4 表消失、前 8 表保留。这验证"多版本迁移链"的每一步回退都干净，而不只整体 base。

### 检查问题（请用自己的话回答）
为什么 `get_foreign_keys()` 的 `referred_table` 要按字符串而不是列表处理？`Numeric(38,10)` 的 precision 和 scale 分别代表什么？

---

## P01-11：迁移 invocation/artifact 表 ✅

**产物**：`migrations/versions/0004_invocation_artifact.py`、models.py 追加 ToolInvocation/Artifact、`tests/test_migration_004.py`

### 3 个知识点

1. **幂等唯一约束是"最终防线"**：`tool_invocations`（job_id+tool_name+invocation_key+attempt_no）与 `artifacts`（job_id+artifact_key）的唯一约束，保证**并发/重试下同一键不会被写两次**。测试直接插入两次同键 → 第二次被数据库以 `UniqueViolation` 拒绝——证明约束真实生效，而不只看 schema 声明。

2. **SQLAlchemy 异常包装（真实踩坑）**：用 SQLAlchemy 操作时，底层驱动异常（如 psycopg 的 `UniqueViolation`）会被 SQLAlchemy **包装成 `sqlalchemy.exc.IntegrityError`**。测试要捕获的是包装后的异常，而不是裸驱动异常。教训：**用哪个 ORM/客户端，就捕获它自己的异常层**。

3. **downgrade 表序与外键**：`artifacts`→`tool_invocations` 顺序删除正确，因为先删依赖（无外键指向它的表），再删其引用对象。删表顺序要按"被引用者后删"。

### 检查问题（请用自己的话回答）
为什么幂等键的唯一约束要放在数据库层，而不是只靠应用代码判断？SQLAlchemy 何时把底层 `UniqueViolation` 包装成 `IntegrityError`？

---

## P01-12：实现 Job repository ✅

**产物**：`src/invest_research/infrastructure/db/repositories.py`、`tests/test_job_repository.py`

### 3 个知识点

1. **Repository 模式 = 上层不碰 SQLAlchemy**：JobRepository 对外只暴露 `create/get/update_status`，application/service 不需要知道 session、commit 等细节。依赖方向 `infrastructure -> domain`（用 `JobStatus` 枚举）。

2. **乐观锁式条件更新**：`update_status(job_id, from_status, to_status)` 用 `UPDATE ... WHERE id=? AND status=from` —— 仅当当前状态匹配才更新，否则返回 False 且**不覆盖**。这防"过期写入"：比如两处都以为任务还是 pending，各自推进，条件更新保证只有一个成功。

3. **mypy 严格下的 ORM 细节**：`session.get()` 返回值、`session.execute(update()).rowcount` 都要 `cast` 成明确类型（CURSORResult）才能过 strict；fixture 需要显式返回类型（`RepoEnv` 别名）。

### 检查问题（请用自己的话回答）
为什么"条件更新"而不是先查后改？如果两个 worker 同时把任务从 running 推为 succeed，条件更新如何避免状态被错误覆盖？

---



**概念讲解记录（2026-08-12，用户要求）：乐观锁 / 条件更新 / 并发 worker 场景**

**一、什么是乐观锁（Optimistic Locking）？**
- "锁"的目的是防并发写坏。乐观锁和悲观锁是两种风格：
  - 悲观锁（Pessimistic）：先 `SELECT ... FOR UPDATE` 把行锁住，防止别人动，再按自己想法改——像"进门先上锁"。
  - 乐观锁（Optimistic）：不锁，直接改，但**在改的时候带一个"你相信的旧状态"**（如 status='pending'），数据库只在行当前确实是这个旧状态时才更新——像"提交时核对手续是否齐全"。
- 我们的 `update_status(job_id, from_status, to_status)` 就是乐观锁：SQL 是
  `UPDATE research_jobs SET status='running' WHERE id=? AND status='pending'`
  只有当该行此刻真的是 pending 才更新成功，否则影响 0 行 → 返回 False。
- 特点：不加锁、性能好；失败的代价是"这次没改成"，由调用方处理。

**二、为什么"条件更新"而不是"先查后改"？**
- 先查后改（读-改-写）在并发下有**竞态（race condition）**：
  1. Worker A 读：job.status = pending
  2. Worker B 读：job.status = pending
  3. A 写：SET status=running（成功）
  4. B 写：SET status=succeeded —— 它会覆盖 A 的结果，因为 B 用的是它自己读到的旧信息
- 条件更新把"判断"和"写"合成**一条原子 SQL**：数据库保证 "WHERE 条件成立才更新" 在单个语句里是原子的。B 用 `status='pending'` 条件更新时，因为行已经变成 running，条件不成立 → B 更新失败 → 不会覆盖 A。
- 一句话：**先查后改是"边读边写两步"（中间可能被插队），条件更新是"一次原子写入"（数据库替你检查）**。

**三、两个 worker 同时把 running 推为 succeed，条件更新如何避免覆盖？**
场景：任务已是 running，两个 worker 都想把它推成 succeeded。
- Worker A：`update_status(id, from=running, to=succeeded)`
- Worker B：`update_status(id, from=running, to=succeeded)`
执行：
1. A 的 UPDATE 先执行 → `WHERE id=? AND status='running'` 成立 → 成功，行变 succeeded，影响 1 行 → A 返回 True。
2. B 的 UPDATE 后执行 → `WHERE id=? AND status='running'` 现在不成立（已是 succeeded）→ 影响 0 行 → B 返回 False。
结果：**只有 A 成功，B 明确得知"这次没抢到"**。若用先查后改，A、B 都读到 running，A 改 succeeded 后 B 又…… 同一个目标还好，但若 B 想推 failed，就会把 succeeded 覆盖成 failed——条件更新杜绝了这个。

**四、三个知识点的呼应**
1. Repository 封装 SQLAlchemy → 上层不用了解锁/事务细节；
2. 乐观锁 = 用"旧状态条件"做原子更新，不加锁防并发；
3. mypy 严格 → 注意 CursorResult.rowcount 的类型，保证"影响行数"判断可靠（返回 bool 语义 = 是否真的抢到了这次更新）。



**概念讲解记录（2026-08-12，用户追问）：锁的更多应用 / 并发都满足条件会矛盾吗 / 死锁是什么**

**一、锁的用途不只是"防并发篡改"**
锁/条件更新的核心是"让并发环境下的写入互不干扰"，具体落地形态很多：
1. 防覆盖（状态污染）——本项目乐观锁在做的；
2. 幂等/防重复处理——如任务只消费一次（P01-11 的唯一约束也是防重）；
3. 资源互斥——两个 worker 不能同时处理同一个 job（拿锁再干活）；
4. 队列/限流——Redis 锁 + 计数器防击穿；
5. 缓存一致性——写库后失效缓存，避免读旧值。
一句话：凡是"多个执行者可能碰到同一份数据"，都需要某种锁/原子保证。

**二、悲观锁 vs 乐观锁（修正一个直觉）**
- 用户理解"悲观锁一定能达到效果"基本对，但要注意：**悲观锁也可能死锁**（两个事务互相等对方持锁的行）；而且悲观锁持锁期间其他读会阻塞，性能代价高。
- 乐观锁"不锁也能防覆盖"靠的是"旧状态条件"：数据库保证每条 UPDATE 是原子的，两个并发 UPDATE 必有一个先执行、一个后执行；后执行的那个 WHERE 条件会失效（行已被改）。
- 所以乐观锁的"判定"不是两个都先读再各自判——而是**由数据库串行化这两个 UPDATE，后者的条件天然变假**。不会有"两个都满足同时改"的矛盾。

**三、那如果两个都写"同一个成功状态"呢？**
即使 A、B 都想把 running 推到 succeeded：A 成（1 行），B 条件失效返回 False（0 行）——结果仍是 succeeded，无矛盾；只是 B"白抢了一场"。若 B 想推 failed，也被条件挡住，不会把 succeeded 覆盖成 failed。**数据库的原子性保证"只有一个判定真正生效"**。

**四、死锁（deadlock）是什么（若你问的是它）**
- 悲观锁场景：事务 T1 锁住行 A 想拿行 B；事务 T2 锁住行 B 想拿行 A。两者互相等对方释放 → 永久卡住 → 数据库检测到后**抛死锁异常**并回滚其中一个，让另一个继续。
- 乐观锁**天然避开了死锁**（不持锁，所以不存在"互相等锁"）。
- 本项目用乐观锁，所以不会出现行级死锁；若未来在别处用 `SELECT FOR UPDATE`，就要小心中间的锁顺序不一致导致死锁。

**五、"静态"若指静态分析与动态相对的 static**
若你问的是名词"静态"（static，非死锁）：静态分析 = 不运行代码就检查（如 mypy/ruff 类型与规范检查）；动态 = 运行时行为（如集成测试真连库）。项目里两者都在用：静态=mypy/ruff，动态=Testcontainers 集成测试。


**概念讲解记录（2026-08-12，用户澄清：想问"竞争条件/竞态"）**

**一、竞态（race condition / 竞争条件）是什么**
- 定义：多个执行者（线程/进程/worker）并发访问同一份共享数据，**最终结果取决于谁先谁后、以及执行到一半被打断的时序**——这种"时序敏感导致结果不确定"就叫竞态。
- 类比：两个人同时往一个账户写钱，若双方都先读出余额再写回，后写的人会用旧余额覆盖前者——谁的写入后到，谁的覆盖生效，结果不可预测。

**二、竞态从哪来：读-改-写不是原子的**
我们的更新本质是三步：读旧值 → 改内存 → 写回库。
- 若这三步被另外的执行者"插队"（另一个也读-改-写一遍），就会产生丢失更新/覆盖。
- 数据库的每条 SQL 是原子的，但"读-改-写"是跨多条 SQL 的过程——这个过程可以被并发打乱 → 竞态。

**三、竞态导致什么**
1. 丢失更新：A 的修改被 B 覆盖（两人基于同一旧值，各自写回）；
2. 脏读：读到还未提交的半成品状态；
3. 不一致：状态机中途被别人改成别的路径。

**四、怎么消除竞态**
- 悲观锁：把"读-改-写"整体锁住，别人期间不能碰 → 消除竞争；
- 条件更新/乐观锁：把"判断+写"合成**一条原子 UPDATE**，数据库保证这条语句期间别人插不进来 → 竞争中被延迟的那一方条件失效，结果确定；
- 数据库唯一约束/幂等键：从数据层面禁止重复 → 也防一类竞态。

**五、和本项目的关系**
`update_status` 的 `WHERE status=from_status` 正是把"先判断状态机合不合法、再写"合并成一条原子 SQL——它把可能被插队的"读-改-写"竞态，变成数据库串行化的"原子条件写"，从而确定性避免覆盖。P01-11 的唯一约束同理是"防重复"层面的竞态防护。
## P01-13：实现 Step/Artifact repository ✅

**产物**：`repositories.py`（StepRepository / ArtifactRepository）、`tests/test_step_artifact_repository.py`

### 3 个知识点

1. **StepRepository 复用乐观锁**：步骤状态也用条件更新（`WHERE status=from_status`）；`list_by_job` 按 sequence_no 升序返回——既保证顺序执行语义，也让"恢复执行"能按序找第一个未成功步骤。

2. **ArtifactRepository 依赖数据库唯一约束**：`create()` 不做应用层判重，直接交给 `uq_artifacts_job_key`；同 (job_id, artifact_key) 二次写 → 数据库抛 IntegrityError。这比应用判重更可靠（并发下应用层 check-then-insert 有竞态）。

3. **flush 与 commit 的区别（真实踩坑）**：同事务内要引用新行 id 时，`session.add(x)` 后必须先 `session.flush()`（把 INSERT 发到 DB 拿到 id，但不提交）；都就绪后再一起 `commit()` 保证原子。若忘了 flush，`x.id` 仍是 None → 引用它的行插入 null → NotNullViolation。

### 检查问题（请用自己的话回答）
同一个 worker 的 `update_status` 返回 False 意味着什么？这是"任务做错了吗"还是"这次更新没抢到"？调用方该怎么处理？

**用户疑问解答（2026-08-12）：第二个 worker 返回 False 是不是"做错了"？**
- 语义澄清：`update_status` 返回 False = **这次状态更新没被接受**，而不是"任务丢失/做错了"。任务本身状态仍正确（被第一个 worker 推进成 succeeded 等）。
- 类比：两个 worker 都想去"抢"同一个还没盖章的窗口；乐观锁保证只有先到的 A 成功，B 被明确告知"没抢到"。
- 调用方该怎么办：**拿到 False 不是吞掉继续当成功**，而要处理为"并发冲突"——可选：①重试（重新读取最新状态后再条件更新）；②放弃（若本次动作已无意义）；③进入冲突处理/告警。总之 False 是**信号**：指示"基于旧状态的写入失败了，请基于新状态重新决策"。
- 在我们的流程里，worker 完成某步后调用 update_status 若得 False，说明另一处已推进过该任务——应重新读取当前 job 决定是否继续，而不是盲目回滚或报成功。

---

## 待复述清单（完成复述后打勾）

- [ ] P00-01 检查问题已复述
- [ ] P00-02 检查问题已复述
- [ ] P00-03 检查问题已复述
- [x] P00-04 检查问题已复述
- [ ] P00-05 检查问题已复述
- [ ] P00-06 检查问题已复述
- [ ] P01-01 检查问题已复述
- [ ] P01-02 检查问题已复述
- [ ] P01-03 检查问题已复述
- [ ] P01-04 检查问题已复述
- [ ] P01-05 检查问题已复述
- [ ] P01-06 检查问题已复述
- [ ] P01-07 检查问题已复述
- [ ] P01-08 检查问题已复述
- [ ] P01-09 检查问题已复述
- [ ] P01-10 检查问题已复述
- [ ] P01-11 检查问题已复述
- [ ] P01-12 检查问题已复述
- [ ] P01-13 检查问题已复述
