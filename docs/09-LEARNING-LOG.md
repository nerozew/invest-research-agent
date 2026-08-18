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

## P02-01：建立 Tool Protocol、统一结果和错误对象 ✅

**产物**：`src/invest_research/tools/__init__.py`、`src/invest_research/tools/base.py`、`tests/test_tool_protocol.py`

### 3 个知识点

1. **依赖倒置（Dependency Inversion）+ 泛型 Protocol**：`Tool[RequestT, ResponseT]` 用 `typing.Protocol` 定义抽象契约（只有 `name` 和 `execute(request) -> ToolResult[ResponseT]`）。Agent/Flow 只依赖这个抽象，不依赖具体工具类；测试里的 `FakePingTool` 没有继承任何类，仅凭"结构上具备这些成员"就满足契约（结构性类型系统）——这就是"依赖倒置"的代码落地：高层定义契约、低层实现契约、控制流反转。生产环境里这也让"把 fake 换成真实工具时调用方零改动"成为了可能。

2. **泛型 Protocol 的类型变体规则（typed tools 的关键细节）**：Protocol 的泛型参数出现在 `execute` 的参数位（消费输入）时，应声明 `contravariant=True`（逆变）；出现在返回位（产出结果）时，应声明 `covariant=True`（协变）。写反或写成不变量，mypy 会报 `Invariant type variable "RequestT" used in protocol where contravariant one is expected`。这是让"工具契约的类型"真正能被静态检查的必要条件——typed tools 的"typed"就体现在这里。

3. **统一结果对象 = 成功与失败互斥的分离模型**：`ToolSuccess[ResponseT]`（携带类型明确的 Pydantic `value`）与 `ToolFailure`（携带 `ToolError`）是两个独立模型，`ToolResult[ResponseT] = ToolSuccess[ResponseT] | ToolFailure` 联合；两者都用 `extra="forbid"`，`kind` 判别字段分别为 `"success"`/`"failure"`，所以"既成功又失败"的对象在类型与结构上都无法构造。`ToolError.error_code` **复用 `domain.errors.ErrorCode`**，`is_retryable` 委托 `errors.is_retryable`——全项目只有一套错误分类，工具层不建第二套错误码。

### 检查问题（请用自己的话回答）
`tools/base.py` 里的 `Tool` 为什么用 `Protocol` 而不是 ABC？泛型参数 `RequestT`/`ResponseT` 为什么分别声明为逆变/协变？如果 `ToolSuccess` 和 `ToolFailure` 合并成一个带 `ok: bool` 字段的模型，会出现什么"既成功又失败"或"结果歧义"的漏洞？

**概念讲解记录（2026-08-12，用户追问 Protocol/逆变协变/合并模型的漏洞）**：

**一、Protocol vs ABC**
- ABC（抽象基类）= 登记制：子类必须显式 `class X(ToolABC)` 继承，才算"是工具"。
- Protocol = 长相制（结构类型/鸭子类型）：不用继承，只要类有 `name` + `execute(request) -> ToolResult[...]`，就自动被认为满足契约（配合 `@runtime_checkable`，`isinstance(fake, Tool)` 也能通过）。
- 用 Protocol 的好处：测试里的 fake 可零绑定；高层（Agent/Flow）只依赖"形状"，不依赖具体类——依赖倒置更彻底。一句话：ABC 要血缘，Protocol 看长相。

**二、逆变/协变（吃逆变、吐协变）**
前提：子类型概念，`Dog ⊂ Animal`。
- 协变（covariant）：方向一致。`返回 Dog 的函数` 可当 `返回 Animal 的函数` 用（狗确实是动物，读者按动物收也安全）→ 出现**返回位**。
- 逆变（contravariant）：方向相反。`接受 Animal 的函数` 可当 `接受 Dog 的函数` 用（能处理所有动物的人当然能处理狗）→ 出现**参数位**。
- 不变（invariant）：只能严格匹配（可变容器如 list，无安全替换）。
- 口诀：**吃（参数）得宽 → 逆变；吐（返回）得窄 → 协变。**
- 对应 `Tool`：`RequestT` 在参数位 → `contravariant=True`；`ResponseT` 在返回位 → `covariant=True`。不写 mypy 报 "Invariant type variable used in protocol where contravariant one is expected"——这就是 typed tools 能被机器检查的原因。
- 用户直觉确认：输入更宽泛（父类 schema 也能满足）✅、输出更具体/受限 ✅——方向正确。

**三、为什么"分开的两个类 + 联合类型"比"合并成一个带 ok: bool 的模型"安全？**
合并版（假想的坏设计）：
```python
class ToolResult(BaseModel):
    ok: bool
    value: ResponseT | None = None
    error: ToolError | None = None
```
漏洞不是"布尔二选一"（布尔确实二选一），而是**数据字段不受布尔约束**：
```python
ToolResult(ok=True, error=ToolError(...))  # ① 成功却带错误 → 矛盾
ToolResult(ok=False, value=PingResponse(echo="x"))  # ② 失败却带成功数据 → 矛盾
ToolResult(ok=True)  # ③ 成功却没有 value → 调用方拿到 None
```
- `value`/`error` 是彼此独立、全可选字段，Pydantic 不会自动要求"ok=True 时 value 必有、error 必无"。
- 调用方每次要写 `if result.ok and result.value is not None`，类型从 `ResponseT` 退化 `ResponseT | None`。
- 分离模型（我们采用的）：`ToolSuccess` 必有 `value`、`ToolFailure` 必有 `error`、`ToolResult = ToolSuccess | ToolFailure`。类型系统从结构上保证"两样永不同时出现"；再加 `extra="forbid"`，往 `ToolSuccess` 塞 `error` 直接 `ValidationError`（测试 `test_success_and_failure_are_mutually_exclusive` 验证的就是它）。
- 一句话：**布尔管"成功/失败标记"，管不了"成功数据与失败数据不能共存"；类型系统靠两个类 + 联合把这条规则做死。**

---

## P02-02：建立共享 httpx client 与显式 timeout ✅

**产物**：`src/invest_research/infrastructure/http/__init__.py`、`src/invest_research/infrastructure/http/client.py`、`tests/test_http_client.py`、`settings.py`/`.env.example`（新增 HTTP 配置）、`pyproject.toml`（新增 httpx）

### 3 个知识点

1. **显式超时是"可靠性"的第一个硬约束**：httpx 的 `Timeout` 对象必须显式设置全部四个参数（connect/read/write/pool），否则 httpx 直接抛 `ValueError: must either include a default, or set all four parameters explicitly`。这正好把 docs/04-WORKFLOW-RELIABILITY §5.1 的"连接超时 5s、读取超时 20–90s"变成代码层面的强制——不允许出现"没设超时的网络请求"。共享 client 由工厂 `build_http_client()` 统一创建，携带显式 User-Agent（SEC EDGAR 合规）、跟随重定向，后续所有工具都复用它，不再各自 new client。

2. **HTTP 状态码/异常 → 统一 ErrorCode 映射（复用 domain 错误码）**：对外部 I/O 的错误**分类**集中在两个纯函数：`classify_status_code()`（401/403→AUTH_ERROR、429→RATE_LIMITED、408→NETWORK_TRANSIENT、5xx→UPSTREAM_5XX、其它 4xx→INPUT_INVALID、2xx/3xx→None）与 `classify_http_exception()`（超时/连接错误→NETWORK_TRANSIENT、HTTPStatusError 按状态码、未知→INTERNAL_BUG）。这样工具层拿到的永远是 `domain.errors.ErrorCode`，可重试性直接委托 `is_retryable()`——为 P05-01 重试策略打底，且全项目只有一套错误码。

3. **基础设施层依赖方向（铁律再验证）**：`infrastructure/http/` 只导入 `domain.errors.ErrorCode` 和 httpx，不导入 tools/CrewAI/FastAPI/SQLAlchemy。错误分类是"纯函数 + 现有枚举"，可完全离线单测（用 `httpx.Request`+`httpx.Response` 直接构造异常对象，不发起真实网络请求）。

### 检查问题（请用自己的话回答）
为什么要用"共享 client 工厂 + 统一错误映射"，而不是让每个工具自己创建 httpx client、自己判断错误？"显式设置全部四个超时参数"和"复用同一套 ErrorCode"分别解决了什么问题？

**概念讲解记录（2026-08-12，用户追问：HTTP client 是什么/是不是模拟前端/为什么要共享）**：

**一、这个 HTTP client 是干嘛的？—— 它不是"模拟前端"！**
- 它是**向外发起请求的"打电话机"**：本项目系统要主动去 `data.sec.gov`、搜索 API、DeepSeek 等**外部服务**要数据，`httpx.Client` 就是负责发这些请求、收响应的工具。
- 方向与"前端"完全相反：
  - **HTTP client（本任务）** = 我们作为**调用方**，主动求外部服务给数据；
  - **前端/API（P04 才有）** = 我们是**被调用方**，等别人（浏览器/分析师）来访问 `GET /v1/research-jobs`。
- 打个比方：HTTP client 像"你要给 SEC 打电话查资料"的电话机；前端是"别人打到你公司总机的分机"。一个是打出去，一个是接进来。
- 所以这个项目"没有前端"不影响 HTTP client——它跟浏览器无关，是**后端去消费外部 API 的通道**。等 P04 补 FastAPI 时，那是"接进来"的另一套东西。

**二、为什么所有工具"共享同一个 client 工厂"，而不是各自 new？**
每个工具各自 `httpx.Client()` **不会"并发打起来"**（并发冲突不是主因），真正的原因是三个工程问题：
1. **配置统一（一处管全部）**：SEC 合规要求 UA 必须带联系邮箱；超时必须有值。如果每个工具各自建，有的忘了设 UA、有的设成 3 秒、有的设成 90 秒——**配置漂移**，行为不一致。
2. **连接池复用（性能）**：`httpx.Client` 内部维护连接池。共享同一个 client，多次请求可**复用 TCP 连接**；每个工具各自 new，每次请求都重新建连，慢且耗资源。
3. **错误语义统一（重试决策一致）**：P05 的重试器只会看 `ErrorCode`。如果 SEC 工具把 429 当"可重试"、搜索工具把 429 当"直接放弃"，重试策略就没法统一写。共享映射函数保证：**429 到哪儿都是 RATE_LIMITED，超时到哪儿都是 NETWORK_TRANSIENT**。

补充：`httpx.Client` 是**线程安全、可跨协程共享**的——共享不会增加"并发混乱"，反而把"怎么配、怎么判错"收敛到一处。这正是 P02-02"共享工厂 + 统一错误映射"的价值：**配置、连接、错误判断三个维度都只写一次**。

**概念澄清记录（2026-08-12，用户追问：HTTP client 是连接 VPN 吗？HTTP client 与前端职责如何划分？）**：

**一、不是 VPN！是调用 "Web API"（REST 接口）**
- VPN 是**网络层的隧道**（把两台机器/两个网络连成一张"私网"，常见于翻墙/连公司内网）——它管"怎么把网络包送过去"。
- HTTP client 是**应用层的 API 调用**：用 HTTP 协议向某个服务的 URL（如 `https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json`）发请求、拿响应（通常是 JSON/HTML）——它管"业务上要什么数据、怎么要"。
- 两者完全不同层：VPN 是"修路"，HTTP API 调用是"在路上跑的一辆送数据的车"。本项目不涉及 VPN。

**二、"操控其他 Web API 吗？" —— 对，但不叫操控，叫"消费/调用"**
- 更准确的说法：我们的后端用 HTTP client **消费（consume）外部 Web API**——只读地要数据（SEC 的公开财报、搜索结果），"操控"通常暗示有写权限/改状态，我们主要是 GET 拉数据。

**三、HTTP client 和前端的职责边界（本项目架构）**
用户理解"前端收集数据→请求后端→后端返回→前端渲染"大体正确，但有一个关键修正：
- **前端不直接连外部 API**（不直接调 `data.sec.gov` 或搜索 API）。原因：
  1. **密钥安全**：API key 若放前端 JS，任何人打开浏览器就能看到——这是致命泄露；
  2. **CORS 限制**：第三方 API 通常禁止浏览器跨域直接调用；
  3. **治理集中**：缓存、限流、脱敏、错误重试（P05）必须在后端统一做，前端做不到也不该做。
- 所以正确链路是：**前端/CLI → 我们的后端（FastAPI，P04 才有）→ HTTP client → 外部 Web API**。外部数据先到后端，后端整理/存库/脱敏后再给前端渲染。
- 一句话分层：**前端管"展示与交互"、后端管"业务与外部数据"、HTTP client 是后端的手去摸外部 API**。这个 client 属于后端内部细节，前端完全接触不到。

---

## P02-03：实现 SEC User-Agent 与全局限流器 ✅

**产物**：`src/invest_research/infrastructure/http/ratelimit.py`、`tests/test_ratelimit.py`、`settings.py`/`.env.example`（新增 `sec_rate_limit_per_second`）、`http/__init__.py`（导出限流器）

### 3 个知识点

1. **Token Bucket（令牌桶）限流原理**：桶容量 = 每秒钟速率，`acquire()` 消耗 1 个 token；token 随时间按速率补充但不超过桶容量。因此它允许"突发"（初始满桶可连发 rate 次），但长期平均速率被严格限制为 rate/s——既满足 SEC 合规（5 req/s 项目安全上限），又允许短时突发提升吞吐。这是与"固定窗口/漏桶"不同的一种平滑限流。

2. **依赖注入时钟（模拟时钟可测）**：`SleepableClock` Protocol 提供 `time()/sleep()`，真实实现 `RealClock` 委托 `time.monotonic()`；测试用 `FakeClock` 手动推进时间，**不依赖真实 sleep**（对齐 docs/04 §5.1"测试不得依赖真实等待时间"）。这让"匀速 20 次请求永不超限""突发被限后推进时间可恢复"等时序断言秒级跑完、完全确定。

3. **SEC 合规是显式上限而非官方默认**：官方 SEC 当前上限 10 req/s，但项目自设安全上限 5 req/s（`sec_rate_limit_per_second=5.0`），留出一半余量避免触发官方 429。限流器是"全局"的——所有 SEC 工具共享同一个实例，避免"每个工具各自限流导致总并发超限"。

### 检查问题（请用自己的话回答）
Token Bucket 凭什么能"允许短时突发"又不违反长期平均速率？为什么测试要用可注入的 `FakeClock` 而不是真实 `time.sleep`？项目为什么要把 SEC 限流设成 5 req/s 而不是直接用官方的 10 req/s？

**用户复述记录（2026-08-12，三个问题均未掌握，故先给出完整通俗解答）**：
1. **什么是 SEC**：SEC = 美国证券交易委员会，公开财报的官方出处（data.sec.gov）。我们的 SEC 工具要主动去它那里拉公司财报数据。
2. **Token Bucket 为什么"允许突发但不违反长期平均"**——用"往杯子里接水"类比：
   - 杯子容量 = 速率 5 = 最多装 5 滴水（token=许可）；
   - 每发一个请求喝掉 1 滴水；水龙头以每秒 5 滴的速度补充，但杯子最多只能装 5 滴（满了就溢出，多余的存不下来）；
   - 所以"长时间不用 → 杯子满 → 突然连发 5 个请求"是允许的（突发）；但一直连续发就会很快喝干，必须等水龙头补充——**长期来看平均就是每秒 5 个**。
   - 一句话：**突发 = 消耗平时攒下的"存款"；长期平均 = 收入（补充速率）被锁死**。
3. **为什么用 FakeClock 不用真实 sleep**：
   - `FakeClock` 是**把"时间"模拟出来的假时钟**——不真等，直接让 `sleep(1.0)` 假装过了 1 秒；
   - 好处：测试想验证"等 1 秒后恢复"，真实 sleep 要真等 1 秒，20 次测试就等很多秒；FakeClock 一秒都不用等、结果完全可预测（不依赖机器快慢），对齐"测试不得依赖真实等待时间"；
   - 类比：验证"水龙头一分钟能流多少水"，FakeClock = 直接拨快表，不用真等一分钟。
4. **为什么 SEC 设 5 而不是 10 req/s**：
   - SEC 官方上限 10 req/s（超过就 429 封禁），但那是**天花板**，不是建议值；
   - 我们的系统还有重试、多个工具并发、网络波动——贴着天花板容易一超就踩雷；
   - 设 5 req/s 留一半余量：正常跑够用，突发/重试时也不容易碰到 429。工程上叫"留安全边际"。
→ 用户要求：先给答案，再进入下一步（P02-04）。

---

## P02-04：实现 `CompanyResolverTool`（名称/ticker → 10 位 CIK；歧义返回候选） ✅

**产物**：`src/invest_research/tools/company_resolver.py`（`ResolveCompanyRequest`/`ResolveCompanyResponse`/`CompanyIndex`/`CompanyResolverTool` + `_SEC_TICKER_FIXTURE`）、`tests/test_company_resolver.py`（8 测试）、`tools/__init__.py`（导出）

### 3 个知识点

1. **实体解析的"歧义不猜测"铁律**：`lookup()` 命中多个候选时返回 `ResolveCompanyResponse(resolved=False, candidates=[...])`，交用户选择，绝不静默挑第一个（对齐 PRD FR-002 与 .clinerules"歧义返回候选"）。这与错误码 `COMPANY_AMBIGUOUS` 的语义呼应——歧义不是"失败"而是"需要用户决策"的结果。

2. **本地 fixture 先行（外部服务 mock/fixture）**：P02-04 验收是"MSFT → 10 位 CIK"，用内置 `_SEC_TICKER_FIXTURE` 驱动，**不发起真实网络请求**——真实 SEC submissions 调用留给 P02-05。这符合 .clinerules"外部服务必须使用 mock/fixture"，也让契约测试完全离线、秒级完成。分析出"范围该拆到哪"本身就是设计能力。

3. **工具=数据索引 + 策略的组合**：`CompanyIndex` 是纯数据索引（大小写不敏感 key → 候选列表），`CompanyResolverTool` 是编排策略（唯一→成功、歧义→候选、无→失败）。两者分离让索引可复用、策略可单测，且 `CompanyResolverTool` 满足 P02-01 Tool 契约（`name` + `execute` → `ToolResult`）。

### 检查问题（请用自己的话回答）
为什么"歧义"返回成功（`ToolSuccess` + `resolved=False`）而不是失败（`ToolFailure`）？`CompanyIndex` 与 `CompanyResolverTool` 分开设计有什么好处？P02-04 为什么用本地 fixture 而不是直接调真实 SEC API？

**用户复述记录（2026-08-12，重点讲解：工具定位/歧义语义/索引与策略分离/为何本地 fixture）**：
1. **工具定位**：`CompanyResolverTool` 是业务层（application/domain）可调用的一个"实体解析工具"，输入公司名/ticker，输出 CIK 或候选——不是前端组件，是后端业务处理时"左手"的一个工具，服务端处理业务时会调用它。✅（用户理解正确）
2. **歧义为什么返回成功而非失败**：歧义不是"失败"，是"需要用户决策"的合法结果——`ToolFailure` 表示"这事儿没做成/出错了"；而歧义我**确实拿到了候选**，只是需要用户挑。所以返回 `ToolSuccess` + `resolved=False` + 候选列表，让调用方知道"去问用户选哪个"。✅（用户理解正确："是歧义不是失败，交给用户自己处理"）
3. **CompanyIndex 与 CompanyResolverTool 分开的好处**：
   - `CompanyIndex` 是**纯数据容器**（只负责"查表"：key→候选），可被其他工具/服务复用，不掺杂业务决策；
   - `CompanyResolverTool` 是**策略/编排**（负责"拿到结果怎么判定"：唯一→成功、多→候选、无→失败）；
   - 好处：①各自可独立测试（数据索引测命中，策略测判定）；②换数据源只改 Index（如换真实 SEC 数据），策略不用动；③职责单一、可复用——"数据是什么"与"数据怎么用"解耦。
4. **为什么 P02-04 不直接调真实 SEC API**（纠正用户两个小误解）：
   - **HTTP client 其实已经搭好了**（P02-02 的 build_http_client / P02-03 的限流器都存在）——不是"没搭好"；
   - 真正原因是 .clinerules 规定："**外部服务必须使用 mock 或 fixture**"，除非任务明确要求测真实服务。P02-04 的范围就是"本地实体解析 + 契约验证"，真实 SEC submissions 调用属于 **P02-05**；
   - **限流器并非"没意义"**：它已独立写好、有模拟时钟测试覆盖；等 P02-05 真调 SEC 时直接复用即可——这就是"先把能力造好、再组合使用"的渐进式开发。✅
→ 用户要求：重点解答后进入下一步（P02-05，真实 SEC submissions 调用）。

---

## P02-05：实现 `SECSubmissionsTool`（截止日过滤 + 10-K/10-Q 选择） ✅

**产物**：`src/invest_research/tools/sec_submissions.py`、`tests/fixtures/sec_submissions_msft.json`、`tests/test_sec_submissions.py`（4 测试）、`tools/__init__.py`（导出）

### 3 个知识点
1. **依赖倒置注入 client**：`SECSubmissionsTool(client)` 构造时注入 `httpx.Client`——生产用 `build_http_client()`，测试用 `httpx.MockTransport`。工具不自己建 client、不读 settings，可完全离线、可替换。
2. **截止日语义**：`filing_date <= as_of_date`（"截至某日可获得的申报"）；表单**精确等于** 10-K/10-Q（`10-K/A` 不算 10-K）；结果按申报日期降序。
3. **URL 构造与错误归一**：primary URL = Archives/edgar/data/{无前导零CIK}/{accession去连字符}/{primaryDoc}；HTTP 错误经 `classify_http_exception`/`classify_status_code` 归一到 `ErrorCode`。

### 检查问题（请用自己的话回答）
为什么工具构造时注入 client 而不是自己创建？"截止日当天或之前"用 `<=` 而非 `<` 的语义是什么？为什么 `10-K/A` 不能当 `10-K` 用？

---

## P02-06：实现 `SECCompanyFactsTool`（XBRL 财务事实保真解析） ✅

**产物**：`src/invest_research/tools/sec_company_facts.py`、`tests/fixtures/companyfacts_msft.json`、`tests/test_sec_company_facts.py`（5 测试）、`tools/__init__.py`（导出）

### 3 个知识点
1. **XBRL/taxonomy/concept 保真解析**：Company Facts 返回的是"某分类法（如 us-gaap）下每个 concept（收入/资产…）在各单位/期间的值"。工具只做**保真映射**——把 taxonomy/concept/unit/period/form 原样放进 `domain.FinancialFact`，不做任何计算（计算留给 P02-15/16 的指标工具）。
2. **期间型 vs 时点型（XOR）**：有 `start`+`end` 的 fact 是 duration（`period_start/end`）；只有 `end` 的是 instant（`instant_date`）。解析时严格二选一，正好复用 `FinancialFact` 模型自带的 XOR 校验。
3. **脏数据容错**：单条 fact 缺 `val`/日期非法时跳过（`try/except`），不影响整体解析——外部数据源可能混入坏行，工具要"整体可解析"而非"一条坏全崩"；错误仍经 `classify_http_exception`/`classify_status_code` 归一。

### 检查问题（请用自己的话回答）
为什么"保真解析"强调不做计算？期间型（start+end）与 时点型（仅 end）在 XBRL 里分别代表什么财务语义？为什么解析时遇到单条脏数据要跳过而不是整体失败？

---

## P02-07：实现 URL 规范化与来源去重（纯函数） ✅

**产物**：`src/invest_research/tools/urls.py`（`canonicalize_url`/`deduplicate_sources`）、`tests/test_urls.py`（6 测试）

### 3 个知识点
1. **canonical URL（规范化 URL）**：同一页面的不同写法（带 utm、大小写 host、默认端口、乱序 query、片段）应归一到同一串，才能做"来源去重"——对应 `sources.canonical_url` UNIQUE 约束的前置。`canonicalize_url` 用 `urllib.parse` 纯函数完成：去 tracking 参数、host 小写、去默认端口、query 按键排序、去片段。
2. **去重策略（保留首个，剔除无效）**：`deduplicate_sources` 按 canonical URL 去重并保持首次出现顺序；空/无法解析的 URL 返回 None 被剔除。这是"来源目录唯一"的领域层保障（数据库 UNIQUE 是最终防线，先规范化再入库）。
3. **纯函数可测性**：本模块不依赖网络/框架，`urllib.parse` 是标准库——6 个测试秒级跑完，覆盖 tracking 剔除、host/端口、片段、query 排序、非法输入、去重。

### 检查问题（请用自己的话回答）
为什么"去重"必须先做 URL 规范化而不是直接比较字符串？`canonicalize_url` 返回 `None` 表示什么？query 参数为什么要按键排序？

---

## P02-08：实现 `FilingDownloaderTool`（安全下载 + 校验） ✅

**产物**：`src/invest_research/tools/sec_downloader.py`、`tests/test_sec_downloader.py`（4 测试）

### 3 个知识点
1. **安全下载四道闸**：注入 client 请求 → 非 2xx 归一错误 → 超大小上限拒绝（INPUT_INVALID）→ 媒体类型白名单拒绝（DOCUMENT_UNSUPPORTED）→ 成功返回内容+sha256 checksum（供去重/校验）。
2. **checksum（校验和）**：sha256 对内容生成固定指纹；同内容必有同 checksum，可用于来源去重、损坏检测、工件完整性（对应 sources.content_checksum / artifacts.content_checksum）。
3. **注入 client + MockTransport**：工具自己不建 client；测试用 MockTransport 返回内存字节，不写盘、不联网，秒级验证成功/超限/类型/429 四路径。

### 检查问题（请用自己的话回答）
为什么下载要同时限制"大小上限"和"媒体类型白名单"？sha256 checksum 有什么用？工具为什么要注入 client 而不是自己创建？

---

## P02-09：实现 SEC HTML 解析器（标准库、保留标题/文本/locator） ✅

**产物**：`src/invest_research/tools/sec_html_parser.py`、`tests/test_sec_html_parser.py`（4 测试）

### 3 个知识点
1. **标准库 `html.parser` 实现**：零新增依赖；回调 `handle_starttag/endtag/data` 收集可见文本，识别 h1-h6 为标题块；用 `_skip_depth` 跳过 script/style 内容。
2. **保留结构化信息**：每块带 `is_heading`（是否标题，对应章节）与 `location`（累计偏移 locator），供 citation/locator（P02-19）与 document_chunks 检索分块使用。
3. **空白规范 + 跳脏**：连续空白/换行归一为单空格；script/style 脚本不进入正文块——"正文可读、脚本隔离"。

### 检查问题（请用自己的话回答）
为什么解析器要跳过 `<script>`/`<style>`？`location`（累计偏移）有什么用？标准库 `HTMLParser` 与第三方库（如 BeautifulSoup）的取舍是什么？

---

## P02-10：实现 PDF 解析器主路径（PyMuPDF） ✅

**产物**：`src/invest_research/tools/pdf_parser.py`、`tests/test_pdf_parser.py`（3 测试）、`pyproject.toml`（新增 pymupdf）

### 3 个知识点
1. **PyMuPDF（fitz）主路径**：用 `fitz.open(stream=bytes, filetype="pdf")` 在内存打开 PDF；`page.get_text()` 逐页取文本，保留 1-based 页码（供 citation locator）。
2. **错误边界**：无效/非 PDF 字节 → `PDFParseError`；加密 PDF → `PDFParseError`；空白页（无文字）→ 跳过不报错。让"解析失败"与"解析为空"可区分。
3. **按需类型豁免**：PyMuPDF 无 mypy stub，在导入处 `# type: ignore[import-untyped]` 精准豁免（沿用"不全局忽略缺失 stub"的工程决策）。

### 检查问题（请用自己的话回答）
为什么 PDF 解析用 PyMuPDF 而 HTML 用标准库 `html.parser`？`PDFParseError` 与"空白页返回空 blocks"分别代表什么（失败 vs 空结果）？`type: ignore[import-untyped]` 为什么不改为全局 `ignore_missing_imports`？

---

## P02-11：实现 PDF/HTML 解析降级路由（parser router / fallback pattern） ✅

**产物**：`src/invest_research/tools/parser_router.py`（`ParseOutcome`/`DocumentParseError`/`parse_document`）、`tests/test_parser_router.py`（14 测试）、`tools/__init__.py`（导出）

### 3 个知识点

1. **Fallback pattern（降级模式）的本质是"精确的一次性回退"**：主解析器失败时，备用解析器**恰好调用一次**，绝不循环重试同一文件。实现上用 依赖注入的 `html_parser`/`pdf_parser` 可调用对象 + 测试 spy 计数，构造出"主成功→备用完全不调用""主失败→备用恰好一次"两类确定性证明。这与 docs/04 §5.1"PDF 主解析器一次、备用一次，不对同一损坏文件循环重试"完全一致。

2. **降级不是无条件双向，而是受控单向**：只允许 `PDF → HTML`（PDF 主解析抛 `PDFParseError` 且内容确为可解码文本时，把字节交给 HTML 解析器）。HTML 主路径失败**不**反向走 PDF——HTML 字符串不是 PDF，无条件双向没有收益且违背类型确定性。判断"能否降级"用 `_is_degradable_text`：严格 UTF-8 可解码 + 无 NUL 控制字节，防止把二进制/加密 PDF 的乱码当 HTML 解析。

3. **路由层用判别联合（discriminated union）而非统一模型**：`ParseOutcome(kind: "html"|"pdf", document: ParsedDocument|ParsedPDF, parser_name, degraded_from)` 让调用方按 `kind` 收窄类型，**不修改** P02-09 的 `ParsedDocument`/P02-10 的 `ParsedPDF` 契约；`degraded_from` 记录降级来源（审计/日志用）。路由只显式捕获 `PDFParseError`，未知异常向上传播，不静默吞错（对齐 .clinerules/02）。

### 检查问题（请用自己的话回答）
`parse_document` 用"依赖注入解析器 + spy 计数"如何证明"主成功时备用完全不调用"与"主失败时备用恰好一次"？为什么降级只做 `PDF → HTML` 单向而不做双向？NUL 控制字节在 `_is_degradable_text` 里起什么作用？

---

## P02-12：实现本地 `ArtifactStoreTool`（原子本地存储） ✅

**产物**：`src/invest_research/tools/artifact_store.py`（`ArtifactStore`/`ArtifactStoreRequest`/`ArtifactStoreTool`/`ArtifactRef`）、`tests/test_artifact_store.py`（27 测试）、`tools/__init__.py`（导出）

### 3 个知识点

1. **原子写（atomic write）= 同目录临时文件 + fsync + `os.replace`**：临时文件必须与目标**同一目录**（保证 `os.replace` 在同一文件系统内原子），写完 `flush()` + `os.fsync()` 强制落盘后才 `os.replace` 改名——成功前外界看不到目标文件，失败则清理临时文件，不留下半成品。这对应 docs/03 §6"文件先写临时名，checksum 成功后原子改名"。
2. **安全 key = 双重防路径穿越**：`_validate_artifact_key` 拒绝 `..` 段、绝对路径、反斜杠、空白与危险符号；`_resolve` 再把解析后的路径与存储根目录做 `is_relative_to` 校验。**校验在"生成即合法"层（弃错）+ 解析在"使用即合规"层（双重保险）** 与 P01-02 模型校验思路一致。
3. **判别请求（discriminated request）保持 P02-01 契约单一**：用 `operation: Literal["write","read","list"]` 单模型 + `model_validator` 约束各操作字段，而不是三个独立请求类——因为 P02-01 的 `RequestT` 是单个 `BaseModel` 泛型；单模型天然满足契约，`execute(request) -> ToolResult[ArtifactOperationResponse]` 类型干净。

### 检查问题（请用自己的话回答）
为什么临时文件必须与目标文件放在**同一目录**？`os.replace` 的"原子性"具体指什么？`_validate_artifact_key` 与 `_resolve` 的两道防线分别防什么？为什么 P02-12 用"判别请求单模型"而不是三个独立请求类？

---

## P02-13：实现财务 concept 映射配置（versioned mapping） ✅

**产物**：`src/invest_research/financial/__init__.py`、`src/invest_research/financial/concept_mapping.py`（`ConceptMappingEntry`/`ConceptMapping`/`load_concept_mapping`/`select_concept`）、`src/invest_research/financial/mappings/concepts_v1.json`、`tests/test_concept_mapping.py`（10 测试）

### 3 个知识点

1. **versioned mapping = 数据即配置 + 版本可追溯**：指标的 concept 候选表不硬编码在代码里，而是放进带 `version: concept_mapping_v1` 的 JSON 数据文件；Pydantic 在加载时强制 version 必填（缺 version 即失败）。这样：①换口径只改数据文件不改代码；②配合 P05-04 输入失效，schema/口径版本变化 → 下游指标必须重算。
2. **同义 concept 的优先级选择**：`select_concept` 按候选表顺序返回 `available_concepts` 中第一个命中的 concept——us-gaap 标准（如 `RevenueFromContractWithCustomerExcludingAssessedTax`）在前、旧标准别名/公司扩展在后；无匹配返回 `None`（禁止臆造，交给计算层 `not_computable`），而不是硬凑一个。
3. **配置非法要在加载期 fail-fast**：`ConceptMappingEntry` 用 Pydantic validator 拒绝"空 metric_name / 空候选 / 重复候选"；`ConceptMapping` 拒绝缺 version。配置错误在 CI/启动即暴露，而不是运行到某家公司财报时才发现映射缺陷。

### 检查问题（请用自己的话回答）
"versioned mapping" 为什么要把候选表放到带版本号的 JSON 数据文件而不是写死在代码里？`select_concept` 按优先级返回第一个命中，与"公司扩展 concept 与 us-gaap 标准共存"有什么关系？为什么无匹配必须返回 `None` 而不是随便挑一个？

---

## P02-14：实现可比期间选择器（pure functions） ✅

**产物**：`src/invest_research/financial/period_selector.py`（`PeriodPreference`/`select_facts_for_period`/`select_report_period`）、`tests/test_period_selector.py`（12 测试）

### 3 个知识点

1. **期间可比性的本质是"先过滤、再去重、再按偏好排序"**：`select_facts_for_period` 三步走——① 只保留 `concept` 匹配 + 期间型（start+end 均非空）+ `period_end <= as_of` 的 fact；② 按 `(start, end)` 分组，组内修订申报（form_type 以 `/A` 结尾）优先（同一期间去重）；③ 对代表期间按偏好 `min/max` 排序选最优。`as_of` 过滤是第一步，防止使用"未来/未结束"期间。
2. **三种偏好的排序键设计**：QUARTERLY = 最短优先（`(days, -end)` 取 min）；YTD = 最长优先（`(days, end)` 取 max）；ANNUAL = 最新 period_end 优先、长度其次接近 365 天（容 52/53 周财年）。这正好覆盖路线图"52/53 周、季度/YTD"三类场景。
3. **模型层与选择器的分工边界（真实踩坑）**：测试最初假设"缺 period_start 的期间型会被 Pydantic 拒绝"，实测发现 `FinancialFact` 的 XOR 校验只约束"期间与时点不能同时存在"，**允许只有 end 无 start 的对象**。因此选择器在 `duration_facts` 过滤里显式要求 `period_start is not None`——防御性编码不能依赖"模型一定帮我挡住了"。

### 检查问题（请用自己的话回答）
为什么选择器要先按 `period_end <= as_of` 过滤？"同期间去重 + 修订版优先"与 `10-K/A` 语义有什么关系？`FinancialFact` 模型允许"只有 end、缺 start"时，选择器为什么还要自己再挡一次？

---

## P02-15：实现 5 个利润与增长指标（Decimal / 版本化公式） ✅

**产物**：`src/invest_research/financial/metrics.py`（`FORMULA_VERSION` + 5 个纯函数）、`tests/test_profit_growth_metrics.py`（14 测试）、`src/invest_research/domain/models.py`（`MetricResult.value` 移除 `gt=0`）

### 3 个知识点

1. **负增长率/负利润率是合法业务**：测试直接暴露了领域模型缺陷——`MetricResult.value` 原设 `gt=0`，导致"收入下降 -20%"和"经营亏损 -10%"无法构造（Pydantic 拒绝）。PRD §8 明确要求增长率/利润率允许负值，故移除 `gt=0`；这验证了"契约测试应能发现领域模型的语义缺口"。
2. **零分母/缺失 → NOT_COMPUTABLE 而非臆造**：所有 5 个指标在分母为 0 或输入缺失时返回 `MetricStatus.NOT_COMPUTABLE`、`value=None`、`explanation` 记录原因——对齐 PRD "分母为零/口径不可比时返回 not_computable，禁止臆造数值"。
3. **增长率公式分母用 `abs`**：`(current - prior) / abs(prior)` 使上期为负时也能表达"亏损收窄/扭亏"（如 -50 → 100 为增长 300%），符合 PRD §8 的公式范围。

### 检查问题（请用自己的话回答）
为什么增长率公式分母要用 `abs`？零分母为什么返回 `NOT_COMPUTABLE` 而不是 `inf`？`MetricResult.value` 为什么不能设 `gt=0`？

---

## P02-16：实现 5 个资产负债/现金流指标（Decimal / 版本化公式） ✅

**产物**：`src/invest_research/financial/metrics_balance.py`（`FORMULA_VERSION_BALANCE` + 5 个纯函数）、`tests/test_balance_cashflow_metrics.py`（19 测试）

### 3 个知识点

1. **单位冲突校验是不可混算的防线**：比值/差额类指标（流动比率、FCF 等）若两个输入同时提供单位且不一致（如 USD vs EUR），返回 `NOT_COMPUTABLE`——因为"100(USD) - 40(EUR)"没有任何财务意义。实现用 `_normalize_unit`（去空白/小写/空串→None）+ `_units_conflict`（仅两者都有值且不同才判冲突）。
2. **"零分母/缺失 → NOT_COMPUTABLE"与"负值合法"两条规则并存**：负债/资产/收入为 0 时不可计算；但 ROA、经营现金流率、自由现金流**允许负值**（亏损、现金流为负是真实业务）——这点在 P02-15 已通过移除 `MetricResult.value.gt=0` 落地，本任务直接复用。
3. **ROA 用平均总资产 `(期初+期末)/2`**：时点型数据（总资产）取期初期末平均，比直接用期末更接近"该期间实际使用的资产规模"；平均资产为 0（期初+期末都 0）→ NOT_COMPUTABLE。

### 检查问题（请用自己的话回答）
为什么两个输入单位不一致时必须 `NOT_COMPUTABLE` 而不是任意选择一个单位换算？ROA 为什么用平均总资产而不是期末总资产？`_units_conflict` 在"其中一个单位缺失"时返回 False 的语义是什么？

---

## P02-17：实现 `GoogleSearchTool` provider interface（anti-corruption layer） ✅

**产物**：`src/invest_research/tools/google_search.py`（`SearchProvider`/`SearchQuery`/`SearchResult`/`SearchResponse`/`GoogleSearchTool`）、`tests/test_google_search.py`（10 测试）、`tools/__init__.py`（导出）

### 3 个知识点

1. **anti-corruption layer（防腐层）**：`SearchProvider` 抽象接口 + 统一 Pydantic 契约，业务代码只依赖接口、不依赖任何具体提供商（Serper/Google 等）的响应结构——将来换提供商只加一个 adapter，业务零改动。fake provider 零绑定即可通过 `isinstance(provider, SearchProvider)` 结构匹配（`__subclasshook__` 检查 MRO 是否有 `search`），同时抽象方法保证不能直接实例化。
2. **结果规范化：canonical URL 去重**：`GoogleSearchTool.execute` 复用 P02-07 `canonicalize_url`，以 canonical 为 key 保留首个结果并把保留项 url 替换为 canonical 形式——与 `sources.canonical_url` 唯一约束前置一致；无效 URL 被剔除。
3. **结构协议 + mypy 豁免的平衡**：`__subclasshook__` 需返回 `NotImplemented` 让 ABCMeta 继续默认判断，但 mypy 对 `NotImplemented` 推断为 Any → 报 `no-any-return`。用 `# type: ignore[no-any-return]` 按需豁免（与 P02-10 的 `ignore[import-untyped]` 同一原则：精准豁免而非全局关检查）。

### 检查问题（请用自己的话回答）
"anti-corruption layer" 解决什么问题？`__subclasshook__` 检查 MRO 是否含 `search` 与"鸭子类型"有什么关系？为什么去重后要把 url 替换为 canonical 形式？

**概念讲解记录（2026-08-12）**：
1. **鸭子类型（Duck Typing）**：不看血缘看长相——只要对象有 `search` 方法就可当 provider 用（`FakeSearchProvider` 不继承任何类）。"如果它走起来像鸭子、叫起来像鸭子，那它就是鸭子。"
2. **结构协议 & `__subclasshook__`**：`isinstance(fake, SearchProvider)` 默认只认继承；`__subclasshook__` 让我们自定义"是否子类"——沿 `subclass.__mro__` 家族谱找是否有 `search` 方法，有则返回 True。`abstractmethod` 保证接口本身不能被实例化（`SearchProvider()` 抛 TypeError）。最后 `return NotImplemented` 是"拿不准交还 Python 引擎"，不是布尔 False（避免误伤真正继承的子类）；mypy 对 `NotImplemented` 报 `no-any-return`，用 `# type: ignore[no-any-return]` 精准豁免（与 P02-10 同原则）。
3. **防腐层（Anti-Corruption Layer）**：在"我的系统"和"外部服务"之间加一堵翻译墙——接口 `SearchProvider` + 统一 Pydantic 契约 + 每供应商一个 adapter；业务只依赖接口，换供应商只加 adapter、业务零改动，外部结构变化不污染领域模型。
4. **canonical URL 替换**：先 `canonicalize_url` 归一（去 utm/大小写/默认端口/排序 query/去片段），以 canonical 为 key 去重保留首个；再把保留项的 url 替换为 canonical（`model_copy(update=...)`，因为 frozen 不可原地改）——否则下次查重/入库又会撞上规范化问题，数据库 `sources.canonical_url` UNIQUE 约束也无法生效。`canonicalize_url` 返回 None 表示 URL 无效（直接剔除）。

---

## P02-18：实现 Serper provider adapter（mocked contract test） ✅

**产物**：「src/invest_research/tools/serper_adapter.py」（``SerperConfig``/「SerperAdapter/SERPER_ENDPOINT」）、「tests/test_serper_adapter.py」（6 测试）、「tools/__init__.py」（导出）

### 3 个知识点

1. **SecretStr 认证脱敏：密钥只在请求头，不进日志/异常**：`SerperConfig.api_key` 用 Pydantic `SecretStr`，`str()/repr()` 输出不泄露明文；只在发请求的一刻随 `X-Api-Key` 头发送。这让"认证信息不在消息/异常中出现"成为结构性保证（对齐 P00-05 密钥不进日志）。
2. **adapter 只做"翻译"，错误交给上层**：SerperAdapter 实现 SearchProvider、把 SearchQuery 翻译成 Serper HTTP 请求、把 organic JSON 翻译成 SearchResult；仅 HTTP 异常直接抛出（由上层 GoogleSearchTool 统一归一为 ToolFailure）。"每一层只负责自己的担当"是防腐层的演练。
3. **日期解析用多格式尝试 + as_of 筛选**：Serper 的 date 是人类可读字符串（"Jun 1, 2025"），用多个 strptime 格式尝试解析；无法解析时回退 as_of（不造假、仅兜底），并按 as_of 过滤（与 P02-17 契约一致）。

### 检查问题
为什么 api_key 用 SecretStr 而不是普通 str？adapter 为什么 HTTP 错误直接抛出而不自己返回 ToolFailure？无法解析 Serper date 时回退 as_of"不造假、仅兜底"的含义是什么？

---

## P02-19：实现 `CitationVerifierTool`（claim/source/locator） ✅

**产物**：`src/invest_research/tools/citation_verifier.py`（`verify_claim`/`CitationVerifierTool`/`CitationCheckRequest`/`CitationCheckResult`/`CitationSourceRef`）、`tests/test_citation_verifier.py`（9 测试）、`tools/__init__.py`（导出）

### 3 个知识点

1. **纯函数 + 工具包装的两层设计**：核心校验逻辑在纯函数 `verify_claim`（返回 `(valid, failures)`，可单独单测）；`CitationVerifierTool` 只把它包装成 P02-01 的 `ToolResult`。同时构造可注入自定义 rules（依赖注入、可替换）——从"领域层提炼真实验证"到"工具层契约封装"的分工。
2. **数字引用的十进制比较**：claim 的 key_number（如 "245100000000"）必须能被 `source.facts` 中的某个值以 `Decimal` 匹配（"245100000000" 与 "245100000000.00" 视为一致），否则 `NUMBER_UNSUPPORTED`——对齐 PRD"关键数字可追溯到来源"。
3. **失败条目可测试**：`MISSING_SOURCE` / `INVALID_LOCATOR` / `NUMBER_UNSUPPORTED` 都是明确的 Pydantic 校验结果（code + message），可被断言、可被下游展示。

### 检查问题（请用自己的话回答）
为什么核心验证逻辑放在纯函数而不是工具类里？数字比较为什么用 Decimal 而不是字符串直接相等？为什么 `MISSING_SOURCE` 是"失败"而不是"不确定"？

---

## P03-01：封装可配置的 OpenAI-compatible LLM factory（供应商无关，默认阿里云百炼 qwen-max） ✅

**产物**：`src/invest_research/settings.py`（新增 `llm_provider`/`llm_temperature`/`llm_timeout`，三个模型默认 `qwen-max`）、`.env.example`（占位符）、`src/invest_research/agents/llm_factory.py`（`LLMConfig`/`LLMRole`/`OpenAICompatibleLLMFactory`/`FakeLLM`）、`src/invest_research/agents/__init__.py`、`tests/test_llm_factory.py`（14 测试）

### 3 个知识点
1. **供应商无关（provider-agnostic）设计**：把"协议（OpenAI-compatible）"与"供应商（阿里/DeepSeek/任意）"解耦——业务层只依赖 `LLMConfig` 的字段（base_url/api_key/model/timeout/temperature），切换供应商只改环境变量，不动 Agent/Task/Flow 代码。这正是架构 §8"解耦具体模型名"的落地。
2. **SecretStr 结构性防泄漏**：密钥字段类型是 `SecretStr`，`repr/str/model_dump` 自动掩码为 `**********`；明文只在真正构造 LLM 实例的瞬间存在局部变量。测试用"注入 builder + 检查 repr/str/model_dump_json"证明 key 不进日志、异常、快照。
3. **注入 builder + 惰性占位（builder injection + lazy placeholder）**：`factory.create(config, role, builder=...)` 通过传入 builder 决定"真实还是 fake"——测试注入 fake builder 零联网；真实构造用惰性占位（`_build_real_llm` 抛 NotImplementedError），避免提前引入 CrewAI/LiteLLM 重依赖。这与 P02 工具"注入 client、测试用 MockTransport"同一模式。

### 检查问题（请用自己的话回答）
`OpenAICompatibleLLMFactory` 为什么既能构造 fake（不联网）又能将来构造真实 LLM？"builder 注入"在这里扮演了什么角色？如果未来切到 DeepSeek，需要改哪些文件、哪些文件一定不用改？

---

## P03-02：写信息搜集 Agent 提示词 v1（版本化 prompt） ✅

**产物**：`src/invest_research/prompts/research_prompt_v1.md`（角色/目标/权威顺序/工具白名单/输入/输出契约/规则/禁止项）、`src/invest_research/prompts/loader.py`（`PromptName`/`load_prompt`/`prompt_sha256`）、`src/invest_research/prompts/__init__.py`、`tests/test_prompts.py`（6 测试）

### 3 个知识点
1. **版本化提示词（prompt as versioned asset）**：提示词是"会改变的运行时资产"，不是代码里的字符串常量。独立 `.md` 文件 + 版本号 + sha256 指纹 = 改提示词可追溯、可对比、可判定下游是否需要重算（P05-04）。
2. **最小权限白名单（least-privilege tool whitelist）**：Agent 只能看到列出给它的工具。信息搜集 Agent 就只能收集来源，看不到财务计算器——从提示词层面就杜绝它"顺手算个指标"（真正防住靠 P03-03 的 prompt + P03-06 的 Task 工具注入两层夹击）。
3. **prompt 是"岗位说明书"不是"聊天开场白"**：它约束的是 Agent 的职责边界、输入输出契约、行为规则与红线。这正是三份说明书构成完整团队的逻辑：一个只收料、一个只算数、一个只写稿。

### 检查问题（请用自己的话回答）
信息搜集 Agent 的提示词里同时存在"允许工具白名单"和"禁止事项"两个部分——如果某工具**不在白名单里**但**也没在禁止项里**，Agent 能不能用？为什么白名单本身就足以表达"最小权限"？P03-03 的财报分析 Agent 与它有哪两个关键区别（工具 / 输出 schema）？

---

## P03-03：写财报分析 Agent 提示词 v1（禁止 LLM 算术） ✅

**产物**：`src/invest_research/prompts/analysis_prompt_v1.md`（角色/目标/上游输入/工具白名单/输入/FinancialAnalysisPack 契约/6 条规则/6 条禁止项）、`tests/test_prompts.py`（新增 analysis 正向测试）

### 3 个知识点
1. **"看懂数字"与"算数"是两种能力（ADR-002 落地）**：财报分析 Agent 负责选事实、要求计算器算、解释趋势；但**运算必须委托给确定性代码**（FinancialCalculator）——LLM 不背"权威财务算术"，才能接受回归测试与复核。
2. **防止 LLM 算术是"提示词 + 工具白名单"双保险**：提示词说"不许算"是第一层；只在 Task 里注入 ArtifactReader/FinancialFactQuery/FinancialCalculator（P03-06 才做）是第二层——没有计算器之外的算数路径，结构上就杜绝心算。
3. **可追溯性从提示词层面就开始要求**：每个 MetricResult 必须带 formula_version / input fact ids / 期间（对齐 `MetricResult.inputs_json`）——让"指标 = 公式版本 + 输入事实 + 期间"变成 Agent 的规则，而不是事后补救，这正是 P03-13 质量门禁能验证"数字可追溯到事实"的前置条件。

### 检查问题（请用自己的话回答）
财报分析 Agent 的提示词同时强调"禁止 LLM 算术"和"冲突不猜测"。假如某公司财报里同时有 `RevenueFromContractWithCustomerExcludingAssessedTax` 与公司自定义的 `RevenueX`，Agent 应该怎么做？为什么"自己把两个加在一起"和"自己挑一个"都是被禁止的？（提示：想 FinancialFactQuery 与 concept mapping 的优先级、以及 AMBIGUOUS 语义）

---

## P03-04：写报告 Agent 提示词 v1（grounded generation） ✅

**产物**：`src/invest_research/prompts/writer_prompt_v1.md`（角色/目标/唯一输入/工具白名单/输入/ReportDraft 契约/6 条规则/6 条禁止项）、`tests/test_prompts.py`（新增 writer 正向测试、移除 F401）

### 3 个知识点
1. **接地生成（grounded generation）**：LLM 写报告时"只能引用给到的材料"。把"唯一输入 = ResearchPack + FinancialAnalysisPack"写进提示词，是从源头杜绝幻觉新数字/新事实——这是报告可信度的第一道闸门（后续 P03-13 质量门禁会机械校验 citation 是否存在）。
2. **三个 Agent 的"接力式信任"**：信息搜集只收料 → 财报分析只算数 → 报告撰写只用前两者的结果。每个 Agent 的提示词都在"收窄输入、约束输出"：搜集不能下结论、分析不能心算、撰写不能加料。这就是 Multi-Agent 职责边界的最小完整闭环。
3. **提示词资产管理已成型**：三份版本化 `.md` + 统一 `load_prompt(PromptName)` + sha256 快照——改任何一份提示词，hash 变化 → 未来 manifest（P03-14）能指出"这份报告用了哪个版本的说明书"。

### 检查问题（请用自己的话回答）
三份提示词（research / analysis / writer）在"工具白名单"上各不相同，这背后是同一个原则。请说明：为什么"信息搜集 Agent 不能碰 FinancialCalculator、财报分析 Agent 不能碰 GoogleSearch、报告撰写 Agent 只能读上游两个 pack"不是限制自由，而是工程上的必要约束？（提示：从 LLM 幻觉、可追溯性、下游信任三个角度回答）

---

## P03-05：用 fake LLM 构建 Research Task（CrewAI 1.6.1，不联网） ✅

**产物**：`pyproject.toml`/`uv.lock`（新增 `crewai>=1.0`，实际锁定 1.6.1）、`src/invest_research/agents/llm_factory.py`（`FakeLLM` 继承 `BaseLLM`）、`src/invest_research/agents/research_task.py`（`build_research_agent`/`build_research_task`/`build_research_pair`）、`tests/test_research_task.py`（6 测试）

### 3 个知识点
1. **BaseLLM 是 CrewAI 的"自定义大脑"接口**：要做一个框架认识的 fake/自定义 LLM，就继承 `crewai.BaseLLM` 实现 `call(messages, response_model=None)`、`supports_function_calling()`、`get_context_window_size()`。`Agent(llm=...)` 接受任何 BaseLLM 子类——这就是"测试替身与框架对接"的标准姿势。
2. **output_pydantic 是结构化输出的守门员**：`Task(..., output_pydantic=ResearchPack)` 传类（不是实例）；CrewAI 会把 LLM 输出解析成该 Pydantic 模型。fake 的 `call(response_model=...)` 提前演练了这条链路——"输出必须能解析为 ResearchPack"是本任务验收的核心。
3. **同一实例 vs 重复创建**：Task 必须持有所属 Agent 的**同一个对象**（`task.agent is agent`），pair 工厂通过参数复用来保证。这是多 Agent 编排里容易踩的坑：两个等效但不同的实例会让 Crew 行为不一致。

### 检查问题（请用自己的话回答）
`FakeLLM` 继承 `BaseLLM` 后多了一个 `call(messages, response_model=...)` 方法。为什么 CrewAI 的 `Agent.llm` 需要的是这种"带 response_model 的 call 方法"而不是一个简单的 `__call__(prompt) -> str`？（提示：想 `output_pydantic` 是怎么拿到结构化对象的）

---

## P03-06：用 fake LLM 构建 Analysis Task（工具最小权限白名单） ✅

**产物**：`src/invest_research/agents/analysis_task.py`（`@tool` 包 FinancialFactQuery/FinancialCalculator + `build_analysis_agent`/`build_analysis_task`/`build_analysis_pair`）、`tests/test_analysis_task.py`（8 测试）

### 3 个知识点

1. **`@tool("Name")` 装饰器把普通函数包装成 `crewai.tools.base_tool.BaseTool` 实例**：原逻辑不变，外层新增 name / description（来自 docstring）/ args_schema / `.run()` 等工具能力，从而能放进 `Agent(tools=[...])`。测试触发工具时用 `.run(...)` 而非直接调用（直接调用会报 `'Tool' object is not callable`）。
2. **最小权限白名单在 Task 层落地**：`_ANALYSIS_TOOLS = [financial_fact_query, financial_calculator]` 是硬闸门——Agent 拿不到的工具，LLM 连调用入口都没有（P03-02/03 的提示词只是文字约束）。测试断言 `agent.tools` 名字集合恰等于这两个，且不含搜索/下载等无关工具。
3. **确定性计算不许 LLM 碰（ADR-002 落地）**：FinancialCalculator 内部全走 `financial/` 确定性纯函数（Decimal、formula_version），指标名用 PRD §8 白名单强校验（未知指标→明确 not_computable）、零分母→NOT_COMPUTABLE。

### 检查问题（请用自己的话回答）
在 `agent.tools or []` 里，为什么需要 `or []` 而不是直接 `agent.tools`？（已实测：`Agent.model_fields['tools']` 的 annotation 是 `list[crewai.tools.base_tool.BaseTool] | None`，非必填。所以 `or []` 是给 mypy 的类型收窄：`None or []` → `[]`，让 `for t in agent.tools` 安全遍历。）

### 概念讲解记录（2026-08-13）三问详解
1. **`@tool` 做了什么**：装饰器。执行前是普通函数，执行后是 `BaseTool` 实例——"封装 + 暴露给 Agent"，不是替换逻辑。
2. **`Agent.tools` 的类型**：Pydantic 字段，`list[BaseTool] | None`（实测 `model_fields`），非必填、默认 None；`@tool` 返回值正是该类型，CrewAI 也兼容 LangChain `BaseTool`。
3. **为什么要 `or []`**：类型是 `| None`，mypy strict 对 `for t in agent.tools` 报"None 无 __iter__"；`or []` 短路成空列表，类型收窄，运行时行为不变（我们的 Agent 总配了工具）。

---

## P03-07：用 fake LLM 构建 Writer Task（grounded generation 工具白名单） ✅

**产物**：`src/invest_research/agents/writer_task.py`（`@tool` 包 ArtifactReader/CitationVerifier/TemplateGuide + `build_writer_agent`/`build_writer_task`/`build_writer_pair`）、`tests/test_writer_task.py`（8 测试）

### 3 个知识点

1. **协议适配层**：P02 的 Tool（`execute(request) -> ToolResult`，Protocol 形状）与 CrewAI 的 BaseTool（`run()`，具体类）是**两套协议**。`Agent.tools` 要求 `list[BaseTool]`，所以 P02-01 工具不能直接塞进去，需用 `@tool` 装饰（内部动态创建 BaseTool 子类实例）或手动继承 BaseTool——这就是 @tool 的"语法糖"本质。
2. **Writer 的"无中生有"被结构性禁止**：只给 Writer ArtifactReader + CitationVerifier + TemplateGuide 三种能力（读工件/验引用/查模板），**没有搜索、没有计算**——它连"找新数字"的入口都没有，只能组织上游 context 写稿（grounded generation 双保险，与提示词层叠加）。
3. **三 Agent Task 闭环达成**：research（搜资料）→ analysis（算指标）→ writer（写报告），各自工具白名单完全隔离：搜索/下载只在 research、取数/计算只在 analysis、读写/引用/模板只在 writer。三个 fake Task 均能经 response_model 实例化对应 pack。

### 检查问题（请用自己的话回答）
`CitationVerifier` 工具为什么用 `@tool("CitationVerifier")` 包装 P02 的 `verify_claim` 纯函数，而不是直接传给 `Agent(tools=[CitationVerifierTool()])`？（提示：`Agent.tools` 字段类型是 `list[BaseTool] | None`，而 P02-01 的 Tool 是 Protocol 形状非具体类）

### 概念讲解记录（2026-08-13）两套工具协议
1. **P02-01 Tool**：`typing.Protocol`，只要结构上有 `name` + `execute -> ToolResult` 就算（鸭子类型），无需继承；没有 `run()`。
2. **CrewAI BaseTool**：具体类，方法 `run()`；`Agent.tools` 注解 `list[BaseTool] | None` 只接受 BaseTool 或其子类实例。
3. **为什么不能混用**：Agent 执行器调 `tool.run(...)`，而 P02-01 工具只有 `.execute()`；类型上也不满足 `list[BaseTool]`。
4. **解法**：`@tool` 装饰器 = 动态创建一个继承 BaseTool 的类并实例化，把原函数挂进去；或手动 `class X(BaseTool)` 继承实现 `_run`。两者最终都是 BaseTool 实例。

---

## P03-08：组合三个 Task 为 sequential Crew（三 Agent 顺序流水线，kickoff 全链路） ✅

**产物**：`src/invest_research/agents/crew_factory.py`（`build_research_crew`：三 Agent 各自独立 + `analysis_task.context=[research_task]` + `writer_task.context=[research_task, analysis_task]` + `Process.sequential`）、`src/invest_research/agents/llm_factory.py`（`FakeLLM.invoke` 耗尽后循环复用 + `call` 无 response_model 返回 JSON 文本）、`tests/test_research_crew.py`（4 测试，含真实 kickoff）

### 3 个知识点

1. **Crew = 团队的"排班表"**：`Crew(agents=[...], tasks=[...], process=Process.sequential)` 是声明"谁按什么顺序干什么"；`kickoff()` 才真正执行，上游输出经 `context` 自动喂给下游。agent executor 会多次调 LLM（plan/thought 无 response_model、final 有 response_model）。
2. **FakeLLM 必须模拟真实调用方的分阶段输出形态**：无 `response_model` 时返回预置 pack 的 **JSON 文本**（CrewAI 对文本做 `.rstrip()`，直接给 BaseModel 会报 `'ResearchPack' object has no attribute 'rstrip'`）；有 `response_model` 才返回 BaseModel 实例；响应耗尽后**循环复用**（多次调用不能抛错中断）。
3. **kickoff 返回 CrewOutput**：`result.pydantic` 为最终结构化输出（本版无 `result.tasks` 属性，勿沿用旧文档写法）。

### 检查问题（请用自己的话回答）
为什么 `FakeLLM.call` 在**无 `response_model`** 时必须返回"预置 pack 的 JSON 文本"而不是直接返回 `ResearchPack` 实例？（提示：CrewAI agent executor 在 plan/thought 阶段对结果做了什么操作？这个操作为什么要求字符串？）

### 概念讲解记录（2026-08-13）两问详解
1. **为什么无 response_model 要返回文本**：CrewAI 把每轮 LLM 输出都当"对话文本"拼进历史（`prompt.rstrip()`）。Task 执行会多次调 LLM：plan/thought 阶段（无 response_model，只要字符串）→ final 阶段（有 response_model，才解析为 BaseModel）。fake 必须伪装真实 LLM 的分阶段输出形态。
2. **顺序执行一次循环 = 不如 LangChain？**：顺序本身不是 CrewAI 独有；优势在"以团队为中心的多 Agent 编排"——Agent 抽象（role/goal/backstory+llm+tools）、Task 契约（output_pydantic）、process 演进（sequential→hierarchical）、Crew 级共享、以及未来接入的 Flow 状态恢复/分支（ADR-001）。MVP 只用一部分，但为 Flow/guardrail 打基础。

---

## P03-09：给三个 Task 添加 schema guardrail（确定性校验 + 修复反馈 + 限定重试） ✅

**产物**：`src/invest_research/agents/guardrails.py`（`validate_against_schema`/`build_fix_prompt`/`run_with_guardrail`/`GuardrailOutcome`）、`tests/test_guardrails.py`（8 测试）

### 3 个知识点
1. **guardrail = 确定性的"输出质检员"**：它不是"审核 Agent"（会引入新不确定性），而是用 Pydantic `ValidationError` 机械检查 LLM 输出是否满足 schema；失败 → 把错误反馈给原 LLM 重试（自愈），但仍受**次数上限**约束（docs/04 `SCHEMA_INVALID` 可重试但有限）。
2. **"首次合法 / 修复后合法 / 始终非法"三段语义**：`run_with_guardrail` 的 `attempts_used` 精确反映"第几次成功或耗尽"——既是可测试的重试指纹，也对应 docs/04 §5.1"LLM schema 修复最多限定次数"。
3. **guardrail 管格式、LLM 管内容**：失败时把"哪里错了"（field/msg）作为 hint 喂回 producer，让 LLM 自己重产出正确输出；guardrail 不直接改输出（那样等于替 LLM 造假）。

### 检查问题（请用自己的话回答）
为什么 `run_with_guardrail` 用"修复指令作为 hint 传回给 producer"而不是让 guardrail 直接改输出？如果去掉 `max_attempts` 上限会有什么风险？

---

## P03-10：建立 typed Flow state（CrewAI Flow 状态模型，Pydantic） ✅

**产物**：`src/invest_research/flows/state.py`（`ResearchFlowState`）、`src/invest_research/flows/__init__.py`、`tests/test_flow_state.py`（6 测试）

### 3 个知识点
1. **Flow state = 跨步骤的"工作台/半成品箱"**：`Flow[Model]` 让 `self.state` 成为类型安全的共享内存——步骤 A 写入、步骤 B 读取，天然支持"上游产物喂下游"。
2. **字段对齐 docs/04 §2 步骤契约**：request/company_identity/research_pack/document_manifest/analysis_pack/report_draft/quality_report/run_manifest 分别对应 00-07 每步输出。
3. **非 frozen + 序列化恢复**：Flow state 运行期可改（`self.state.x = ...`），与 domain pack 的 frozen=True 形成对比；`model_dump_json/validate_json` 往返无损是断点续跑的地基。

### 检查问题（请用自己的话回答）
`ResearchFlowState` 为什么用 `frozen=False`（运行期可改），而 domain 的 `ResearchPack` 用 `frozen=True`（不可变）？两类对象在性质上有什么不同？

---

## P03-11：实现 00-03 Flow 步骤（请求→公司→ResearchPack→文档清单） ✅

**产物**：`src/invest_research/flows/research_flow.py`（`@start step00_receive_request` + `@listen step01_resolve_company`/`step02_run_research_agent`/`step03_collect_documents`）、`tests/test_research_flow.py`（4 测试）

### 3 个知识点
1. **`@start/@listen` = 流水线传动带**：上游方法完成 → 引擎自动找监听者触发；步骤间用 `self.state` 传递产物，无需手动连。
2. **fake 也要守领域契约**：`ResearchPack.sources` 是 `min_length=1`（P01-05 合法契约），fake 填 `sources=[]` 会 ValidationError——正确做法是给 1 条占位 Source，而不是破坏 schema。
3. **运行时标识 vs 业务状态**：CrewAI Flow 自动给 `self.state` 注入 `id`（StateWithId）；比较状态内容时按业务字段断言，不做整对象相等。

### 检查问题（请用自己的话回答）
为什么 `@listen(step01_resolve_company)` 里能直接读 `self.state.company_identity` 而不需要参数传入？`self.state` 在 Flow 里扮演的角色，与"函数 A 返回值传给函数 B"有什么本质区别？

---

## P03-12：接入 sequential Crew 形成 04-05 步骤（收纳 Agent 产物） ✅

**产物**：`research_flow.py` 追加 `@listen step04_run_analysis_agent`（→ analysis_pack）与 `@listen step05_run_writer_agent`（→ report_draft）、tests 新增 00-05 全链断言

### 3 个知识点
1. **Flow 收纳 Agent 产物**：Crew（P03-08）完成 02-05 三个 Agent 的顺序产出；Flow 的 step04/05 把这些 pack 写进 `self.state`，质量门禁/发布（P03-13/14）统一读取——"谁产出、谁收纳"分工清晰。
2. **fake 遵守 Analysis 契约**：`FinancialAnalysisPack.facts` 至少 1 条（min_length=1），fake 给一条占位收入事实。
3. **编排与协作分离**：Flow 负责"步骤编排 + 状态收纳"，Crew 负责"Agent 内部协作"；两条职责不冲突（ADR-001）。

### 检查问题（请用自己的话回答）
在 `ResearchFlow` 里，`step05_run_writer_agent` 为什么能直接读 `self.state.company_identity` 来构造报告标题，而不需要 step04 把 identity 传给它？`self.state` 与"函数参数层层传递"有何不同？

---

## P03-13：实现质量门禁 06（确定性硬门禁） ✅

**产物**：`src/invest_research/flows/quality.py`（`run_quality_gate` 纯函数）、`tests/test_flow_quality.py`（5 测试）

### 3 个知识点
1. **"审核 Agent" vs "确定性门禁"**：架构明确不新增第四个审核 Agent；用 `QualityReport` 的确定性布尔检查（pack 齐备、章节完整、引用非空、数字可追溯、无投资建议）做硬拦截。
2. **门禁输入 = Flow state**：`run_quality_gate(state)` 直接读 `state.research_pack/analysis_pack/report_draft`——Flow state 是所有步骤产物的统一容器，质量门禁是最后一个消费者。
3. **rejected 是受控拒绝而非失败**：`recommendation` 给发布层的信号（P03-14 决定 partial/failed），符合 docs/04"不通过阻止发布或标记 partial"。

### 检查问题（请用自己的话回答）
`run_quality_gate` 为什么直接读 `state` 的三个 pack 字段，而不是让调用方逐个传参？这体现了 Flow 里 `self.state` 扮演的什么角色？

---

## P03-14：实现发布和 RunManifest 07（可复现性） ✅

**产物**：`src/invest_research/flows/manifest.py`（`MANIFEST_VERSION`/`build_run_manifest`）、`tests/test_flow_manifest.py`（5 测试）

### 3 个知识点
1. **发布前置 = 质量门禁通过**：`build_run_manifest` 先检查 `state.quality_report.all_passed`；不通过直接返回 `status=rejected`——"硬门禁不通过不发布"变成结构保证。
2. **可复现指纹**：三份 pack 各自 `sha256(model_dump_json)` + 提示词 `prompt_sha256` + 模型名（`config.model_for`）——同输入必同 hash，报告能指出"用了哪版说明书、基于哪些材料"。
3. **manifest = 报告的"出生证明"**：一次记录模型、prompt 版本、pack checksum、公司/CIK/as-of、耗时，任何审计/复现/争论都有据可查。

### 检查问题（请用自己的话回答）
`build_run_manifest` 为什么要先检查 `state.quality_report.all_passed` 才生成"published" manifest，而不是无条件记录所有字段？

---

## P03-15：纯 fake 端到端测试（00-07 全链发布） ✅

**产物**：`tests/test_e2e_fake.py`（2 测试：完整 00-07 发布 + state 序列化往返）

### 3 个知识点
1. **端到端语义 = fake 提供能通过的输入**：fake writer 草案必须含 PRD §7 全部必需章节 + 引用键，质量门禁才通过、才能 published——fake 不"放水"，门禁是真拦截。
2. **纯 fake E2E = 零成本纵向切片**：不联网、不花钱就能验证"请求→三 Agent 顺序协作→质量门禁→RunManifest"整条链路可运行（M3 验收核心）。
3. **测试替身层次**：FakeLLM（替 Agent 大脑）→ fake pack（替 Agent 产物）→ fake draft（替 Writer 产出）——三层 fake 配合 Crew+Flow 全过程离线演练。

### 检查问题（请用自己的话回答）
为什么 fake 的 `ReportDraft` 必须包含 PRD §7 全部必需章节和引用键，端到端才能 `published`？如果把质量门禁的必需章节检查删掉来"让测试通过"，违背了项目的什么原则？

---

## P03-16 ✅：定义结构化质量问题与修订请求（Phase 3.5 起点）

**产物**：`src/invest_research/domain/quality.py`（`QualitySeverity`/`QualityAction`/`QualityRecommendation`/`QualityIssue`/`RevisionRequest`/`SupplementResearchRequest`）、`src/invest_research/domain/models.py`（`QualityReport.recommendation` 收紧为 `QualityRecommendation` 枚举）、`tests/test_quality_models.py`（12 测试）

### 3 个知识点

1. **最小必要契约 + 结合现有模型**：不在 `domain/models.py` 重复定义质量模型，新建 `domain/quality.py` 承载 Phase 3.5 的新类型；`QualityReport` 保留原位置，仅把 `recommendation` 从裸 `str` 收紧为 `QualityRecommendation`（枚举值 `published/rejected/…` 与旧字符串完全兼容，Pydantic 自动解析，门禁逻辑零改动）。
2. **结构化问题 = 让"反思"可路由、可审计**：`QualityIssue` 带 severity/stage/action/related_claim/citation_key，`RevisionRequest`/`SupplementResearchRequest` 分别承载"定向修订"与"补证"的输入与次数边界——这为 P03-17~21 的 ReflectionController 路由提供类型安全的数据载体。
3. **枚举边界交给 Pydantic**：非法 severity/action 值、`revision_number<1`、`attempt_number<0` 都在构造期被 `ValidationError` 拦截（fail-fast）；所有模型 `frozen=True` 不可变，保证契约稳定；`related_claim`/`citation_key` 可空以容纳"缺章节"这类不挂单 claim 的问题。

### 检查问题（请用自己的话回答）
为什么 `QualityReport.recommendation` 从 `str` 收紧为 `QualityRecommendation` 枚举后，`flows/quality.py` 和 `flows/manifest.py` 不需要改？（提示：Pydantic 对枚举字段的字符串自动解析 + 枚举值与旧字面量一致）

---

## P03-17~21 ✅：受控反思与修订闭环（Phase 3.5 全部完成）

**产物**：`flows/quality_classifier.py`（P03-17 结构化分类）、`agents/revision_task.py`（P03-18 定向修订 Task + REVISION_PROMPT_V1）、`flows/supplement.py`（P03-19 最多一次补证）、`flows/reflection.py`（P03-20 ReflectionController + 次数上限 + 审计历史）、`tests/test_quality_classifier.py`、`test_revision_task.py`、`test_supplement.py`、`test_reflection.py`、`test_reflection_e2e.py`（P03-21 六场景 fake E2E）

### 3 个知识点

1. **质量门禁从"布尔"到"结构化行动"**：`classify_state` 输出带 `QualityAction`/`QualitySeverity` 的 `QualityIssue[]`，`recommendation_from_issues` 确定给出 PUBLISH/PUBLISH_PARTIAL/REVISE/REJECT；门禁仍纯确定性、不新增第四 Agent。
2. **受控反思 = "路由 + 次数上限"分离**：`ReflectionController` 只做决策（按 QualityAction + 已用次数 →唯一动作），不直接调用 Agent；修订/补证各 ≤1 次，超限即 repeat_reject，杜绝无限循环；每次决策写入 `history`（action/计数/outcome）可审计。
3. **fake 闭环 E2E 用"确定性替换"演练反射**：修订用直接替换 report_draft、补证用 `apply_supplement` 追加来源——全程不联网、不再造第二个"LLM 大脑"，用最小可验证的确定性动作串起 8 类结局（发布/partial/修订后发布/补证后发布/拒绝/上限停止）。

### 检查问题（请用自己的话回答）
`ReflectionController.step` 只接收 `QualityAction` 和"已用次数"，为什么不接收 `QualityIssue` 列表？把它和 `classify_state` 分开各解决什么问题？

---

## P04-01：FastAPI app、health、readiness ✅

**产物**：`src/invest_research/api/__init__.py`、`health.py`、`app.py`；`tests/test_api_health.py`；Settings 新增 readiness 探测超时。

### 3 个知识点

1. **liveness vs readiness 是两种不同语义的探针**：`/health`（liveness）只回答"进程还活着吗"，回答"活着"不需要依赖任何外部资源，所以它绝不访问 DB/Redis，挂了就重启；`/readiness` 回答"进程能开始服务请求吗"，必须真实探测 PostgreSQL 和 Redis，任一不可用就返回 503 并从负载均衡摘除。二者分离后，依赖抖动不会导致误杀可用进程，进程僵死也不会误接流量。

2. **application factory 是"延迟接线"的关键**：`create_app()` 只有在调用时才创建 FastAPI 实例，且依赖（引擎、Redis 客户端、checker）通过构造参数注入。这保证了"模块导入不连接数据库/Redis"——测试注入 fake checker 时，app 全程不创建任何真实资源；默认情况下才由 `build_health_checker` 惰性创建带显式超时的探测资源，并在 lifespan 退出时释放。

3. **依赖注入（DI）让路由只认协议、不认实现**：`/readiness` 通过 `Depends(_get_health_checker)` 从 `app.state` 取出的只是 `HealthChecker` 协议；假的 checker 和真实 `DependencyHealthChecker` 都满足同一接口。这样路由不用改一行就能在"单元测试（fake）"和"本地 Docker（真实）"两种模式下切换，也把"探测结果 → HTTP 语义（200/503）"的转换留在 API 层，不掺入 Flow 业务逻辑。

### 检查问题（请用自己的话回答）
`/health` 与 `/readiness` 都在"进程活着"时可以返回 200，为什么生产环境仍然必须区分它们？如果只用一个端点同时回答"存活"和"就绪"，当数据库短暂抖动但进程健康时会发生什么？

**用户复述记录（2026-08-13）**：用户回答抓住了核心——health 与 ready 是两种不同的探针，ready 除进程存活外还会深查数据库等依赖 的状态；若只看 health（pass）而数据库抖动/不健康，生产环境（把流量导给该实例）就会出问题。表述有一处小口误："ready 是进程是否存活"，正确表述应为 **health = 我（进程）活着 / readiness = 我现在能不能干活（PostgreSQL 与 Redis 都可用）**。结论：回答满足题意，方向正确，建议口头复述时把"存活"与"就绪"两个词各归其位。

---

## P04-02：`POST /v1/research-jobs` 创建投研任务 ✅

**产物**：`src/invest_research/application/__init__.py`、`application/jobs.py`（`JobStore` 端口 + `CreateResearchJobService` 用例 + `CreatedJob`）；`api/jobs.py`（`CreateResearchJobResponse` DTO）；`api/app.py`（新增 `POST /v1/research-jobs`，注入 `job_store`）；`tests/test_api_create_job.py`（9 测试）。

### 3 个知识点

1. **async job API（异步任务接口设计）**：创建任务接口立即返回 `202 Accepted + job_id`，任务实际执行放在后台——这告诉调用方"请求已被受理、稍后完成"，而不是让 HTTP 请求一直阻塞到整份报告生成完。`202` 与 `200` 的关键区别是 `202` 表示"已受理、结果尚未就绪"，与任务状态 `pending` 呼应。这是 P04-06 Worker 消费、P04-03 轮询状态的前置语义。

2. **application 层的端口（Port）与用例（Use Case）分离**：`JobStore` 是 Protocol（端口，只声明 `create`），`CreateResearchJobService` 是用例（编排：生成 uuid → 委托存储 → 返回 pending）；`application` 不导入 SQLAlchemy/CrewAI/FastAPI——持久化实现将来由 `infrastructure` 提供。这让"API 层只做 HTTP 转换、业务逻辑在 application、存储可替换"成为结构保证，测试注入内存版 store 即可完全离线。

3. **请求体重用领域模型（生成即合法的第一道闸门）**：`POST /v1/research-jobs` 的请求体直接是 `domain.ResearchRequest`（空公司名/未来日期/非法语言/空表单全部由 Pydantic 校验），FastAPI 对非法请求自动返回 422，路由里不需要手写 if-else 判断。这正是 P01-02"模型校验 = 生成即合法"的落地：校验规则只写一次，API/CLI/未来所有入口共享。

### 检查问题（请用自己的话回答）
为什么创建任务用 `202 Accepted` 而不是 `200 OK`？`application/jobs.py` 里的 `JobStore` 为什么用 Protocol（端口）而不是直接 import `JobRepository`？如果直接在 API 路由里调用 SQLAlchemy，会违反哪条 `.clinerules` 依赖方向？

**用户复述记录 + 概念讲解（2026-08-13）**：

1. **为什么 202 而不是 200**：用户理解正确——202 = "已受理、结果未就绪"（对应任务 `pending`，还在准备中）；200 = 结果已 ready（即时状态）。补充：报告生成是重活（搜资料+算指标+写报告要几分钟），不可能在这一次 HTTP 请求里同步返回，所以先受理返回 job_id，之后用 P04-03 轮询；`/health`、`/readiness` 用 200 是因为它们返回的就是当前即时状态。
2. **为什么 JobStore 用 Protocol 而不是 import JobRepository**：JobRepository 是 infrastructure 里的具体实现类，直接在 application 里 import 它会：① 违反依赖方向铁律（api/infrastructure -> application -> domain）；② 测试无法注入 fake，必须真连数据库；③ 换存储实现要改上层代码。Protocol 只约定"结构上有 create 方法"（插座协议思维），生产由 infrastructure 提供真实实现、测试由 FakeJobStore 顶替——这就是依赖倒置（DIP）：高层定义抽象、低层实现抽象。
3. **在 API 路由里直接调用 SQLAlchemy 违反哪条**：违反 `.clinerules/02-engineering.md` 的依赖方向 `adapters/API/infrastructure -> application -> domain`——API 是最外层只能依赖 application；直接连库会让 API 跳过 application 层、混合"HTTP 转换+业务逻辑"职责，测试必须真连库，错误分类与脱敏无法统一。

**结论：三个问题均答对或方向正确，P04-02 检查问题已复述 ✅**

---

## P04-03：`GET /v1/research-jobs/{id}` 查询任务状态 ✅

**产物**：`application/jobs.py` 新增 `StepSnapshot`/`JobSnapshot`/`JobQueryStore`/`GetResearchJobService`；`api/jobs.py` 新增 `GetResearchJobResponse`（复用 JobSnapshot）；`api/app.py` 新增 `job_query_store` 注入与 `GET /v1/research-jobs/{job_id}`；`tests/test_api_get_job.py`（7 测试）。

### 3 个知识点

1. **查询 DTO（Data Transfer Object）= 只读快照，不是领域对象**：`JobSnapshot`/`StepSnapshot` 是"给 API 看的只读视图"，包含状态、当前步骤、错误码、错误信息、耗时；`duration_seconds` 由 `build()` 根据 `started_at/completed_at` 统一计算（未开始为 None）。它把"领域/存储内部形状"翻译成"对外稳定契约"，后续字段变化不影响客户端。

2. **查询端口 `JobQueryStore` 与创建端口 `JobStore` 分离**：读与写能力分开定义（CQRS 思想的简化），查询用例 `GetResearchJobService.get` 只是委托端口返回 `JobSnapshot | None`，由 API 决定 404（None）还是 200。这保持 application 层零基础设施依赖，测试用 fake store 完全离线。

3. **路径参数类型即校验**：`GET /v1/research-jobs/{job_id}` 把路径参数声明为 `uuid.UUID`，FastAPI 自动校验：非法 UUID 返回 422，合法 UUID 才进入路由；不存在返回 404。再次体现"类型/模型层挡掉格式错误（生成即合法），路由只处理业务语义"的分层思想。

### 检查问题（请用自己的话回答）
`JobSnapshot` 为什么要用 `build()` 统一计算 `duration_seconds`，而不是让调用方自己算？`JobQueryStore` 与 `JobStore` 为什么分开（而不是同一个 store 同时提供 create 和 get）？路径参数直接声明为 `uuid.UUID` 与声明为 `str` 再手动验证有什么不同？

**用户复述记录 + 概念讲解（2026-08-13）**：

1. **为什么用 build() 统一算 duration_seconds**：耗时计算是一条规则（未开始→None；开始→完成-开始，保留 3 位），只允许存在一处（DRY）。若调用方各自算会出现"有人用秒/有人用毫秒/四舍五入不一致"，同一任务不同页面耗时对不上。build() 把规则放进模型自己，谁生成快照都调 build()，结果必然一致——类比"毛利率口径只在财务部定义一次"。
2. **为什么读端口与写端口分开**：读关心"按条件返回快照"，写关心"持久化后可见"，关注点不同。分离带来：①接口隔离（创建用例只见 create、查询用例只见 get，不被无关方法耦合）；②实现可分离（将来读走缓存/只读副本、写走主库，互不污染）。JobRepository 一个类同时有 create/get/update_status，但 application 端口按用例拆开，每个用例只声明最小能力（依赖倒置 + 接口隔离组合）。
3. **uuid.UUID 声明 vs str 手动验证**：区别不在 UUID 的性质（唯一/随机），而在"谁来校验、何时校验"。声明 uuid.UUID：FastAPI 在进入路由前自动解析校验，非法 422 根本进不到函数，合法时参数已是 UUID 对象。声明 str：函数收到原始字符串，必须自己在函数体里 uuid.UUID(job_id)+try/except，校验散落易漏。再次体现"框架层挡格式错误（生成即合法），路由只处理业务语义"。

**结论：三个问题均已讲解，P04-03 检查问题已复述 ✅**

---

## P04-04：工件清单与安全下载接口 ✅

**产物**：`application/artifacts.py`（`InvalidArtifactKey` + `ArtifactInfo` + `ArtifactCatalogStore`/`ArtifactContentStore` 端口 + `GetJobArtifactsService`/`GetJobArtifactContentService` 用例）；`api/artifacts.py`（DTO）；`api/app.py`（新增 `artifact_catalog_store`/`artifact_content_store` 注入 + `GET /v1/research-jobs/{job_id}/artifacts` 清单 + `GET /v1/research-jobs/{job_id}/artifacts/{artifact_key:path}` 下载）；`tests/test_api_artifacts.py`（9 测试）。

### 3 个知识点

1. **路径穿越（Path Traversal）防护是"注册 + 校验 + 不暴露路径"三层**：下载接口只允许"该 job 已登记"的工件——端口 `ArtifactContentStore.read` 按 (job_id, key) 查登记表，不存在返回 None（404）；`artifact_key` 经独立校验（非空/无首尾空白/无反斜杠/无危险字符/非绝对路径/无 `..` 段，非法抛 `InvalidArtifactKey` → API 转 400）；端口不暴露磁盘路径，下载路径解析完全留在 infrastructure 实现里。这比"拼字符串找文件"安全得多。

2. **`{artifact_key:path}` 路由转换器**：artifact_key 可能含 `/`（如 `research/2025/pack.json`），用 `{artifact_key:path}` 让 FastAPI 匹配多段路径，而不是只匹配单段；下载用 `Response(content=..., media_type="application/octet-stream")` 直接返回原始字节流，不需要响应模型。

3. **读出/写分离在工件层同样成立**：`ArtifactCatalogStore`（只读登记清单）与 `ArtifactContentStore`（只读内容）是两个独立端口，各自只声明最小能力；application 层用 `InvalidArtifactKey` 把"key 不合法"（业务校验失败，400）与"key 不存在/不属于该 job"（404）区分开，语义清晰。

### 检查问题（请用自己的话回答）
为什么下载接口既要"按 (job_id, key) 查登记"又要"单独校验 artifact_key"，而不是只校验 key 或只查登记？`{artifact_key:path}` 与 `{artifact_key}` 的区别是什么？为什么用 `application/octet-stream` 返回字节而不是 JSON？

**概念讲解记录（2026-08-13，用户跳过回答直接进入 P04-05，先给答案供核对）**：
1. **查登记 = 授权，校验 key = 防路径穿越**：查登记只允许该 job 已登记工件（业务授权）；校验 key 是因为 URL 传入的 key 是攻击者可控的，必须先校验格式（`..`/反斜杠等）再查询，避免恶意 key 到达存储层。只查登记不校验 → 恶意 key 可能碰巧匹配存储；只校验不查登记 → 未登记文件也可能被读。两者缺一不可。
2. **`{artifact_key:path}` 匹配含 `/` 的多段 key**（如 `research/2025/pack.json`）；`{artifact_key}` 只匹配单段（不含 `/`）。
3. **`application/octet-stream`**：工件是任意二进制（JSON/Markdown/PDF），返回字节流告诉客户端"不透明字节，按文件处理"，不要尝试 JSON 解析。

> 复述状态：尚未由用户复述，待后续核对。

---

## P04-05：客户端 Idempotency-Key 幂等创建任务 ✅

**产物**：`application/idempotency.py`（`StoredJob` + `IdempotencyStore` 端口 + `IdempotencyConflict` + `CreateResearchJobIdempotentService`）；`api/app.py`（`idempotency_store` 注入 + `Idempotency-Key` 头读取 + 幂等分支）；`tests/test_api_idempotency.py`（4 测试）。

### 3 个知识点

1. **幂等语义 = 同 key 同请求复用、异请求 409**：幂等不是"所有请求都一样"，而是"同一客户端同一操作不会被重复执行"。实现：`IdempotencyStore` 存 `key → (job_id, status, request_fingerprint)`；同 key 且指纹一致 → 复用首次 job_id（200，不重复创建）；同 key 但指纹不一致 → 抛 `IdempotencyConflict`（409）。`request_fingerprint` 用 `request.model_dump_json()` 规范化指纹判断"是否同一请求"。
2. **状态码语义分层**：首次创建返回 **202**（已受理未就绪）；同 key 复用返回 **200**（已存在、直接给结果）——不要两者都用 202。`create()` 返回 `(StoredJob, created_now: bool)`，路由据此选状态码，把"是否首次"的判定留在用例层。
3. **幂等池与创建解耦**：`CreateResearchJobIdempotentService` 组合 `CreateResearchJobService`（真正创建）+ `IdempotencyStore`（幂等池），application 层零基础设施依赖；数据库的 `idempotency_key` UNIQUE 约束是并发兜底。未注入幂等池或未带 key 时，路由回退到普通 202 创建路径，向后兼容。

### 检查问题（请用自己的话回答）
为什么幂等"同 key 但请求体不同"要返回 409 而不是直接复用/覆盖？`request_fingerprint` 的作用是什么？为什么首次创建返回 202、复用返回 200（而不是统一 200 或统一 202）？数据库 `idempotency_key` UNIQUE 约束与幂等池各解决什么问题（并发兜底 vs 查询去重）？

---

## P04-UI-01：Streamlit 骨架与 typed API client ✅

**产物**：`src/invest_research/frontend/`（models/errors/client/config）、`frontend/Home.py` + 三个页面占位骨架、`tests/test_frontend_api_client.py`（12 测试）、`.env.example`（API_BASE_URL/API_TIMEOUT）

### 3 个知识点

1. **"前端即 HTTP 客户端"（架构 ADR-006 落地）**：Streamlit 不是独立业务层，只通过 HTTP 调用 FastAPI。前端 DTO（`frontend/models.py`）是**独立建模**的——不复用后端 `api/jobs.py` 的 DTO（前/后端是两个进程，只依赖 HTTP JSON 契约），但复用 `domain` 的纯枚举（JobStatus/StepStatus）与 `ResearchRequest` 请求体模型（它们零后端耦合）。这样 frontend 保持轻量、可单测，且依赖方向 `frontend -> domain` 不反向泄漏。

2. **显式超时 + typed 错误分类（4xx/5xx/网络/超时）**：`httpx.Timeout` 必须显式给全 connect/read/write/pool 四个参数（否则抛 ValueError）；`_request()` 统一把 4xx/5xx 分类为 `HttpStatusError`/`ApiNotFoundError`（保留 status_code 与 detail），超时归 `ApiTimeoutError`，连接失败归 `ApiNetworkError`。错误消息只透出后端 `detail`，不显示连接串/密钥/内部路径——对齐架构 §11.2 禁止项。

3. **readiness 的 503 语义**：后端依赖不可用时 `/readiness` 返回 503，但响应体**仍是合法 `ReadinessResponse`**（`ready=False`）。因此 client 对 readiness 把 503 视为"可解析的就绪状态"（`ok_statuses={503}`），交给 UI 展示"未就绪"而不是抛错——liveness（进程活）/ readiness（依赖可用）分开的语义在前端同样成立。

### 检查问题（请用自己的话回答）
为什么 `frontend/models.py` 不复用后端 `api/jobs.py` 的 DTO，却复用了 `domain` 的 `JobStatus` 枚举与 `ResearchRequest`？"前端只依赖 HTTP JSON 契约"和"复用纯 domain 模型"之间是什么边界？

---

## 待复述清单（完成复述后打勾）

------

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
- [x] P02-01 检查问题已复述
- [x] P02-02 检查问题已复述
- [ ] P02-03 检查问题已复述
- [x] P02-04 检查问题已复述
- [ ] P02-05 检查问题已复述
- [ ] P02-06 检查问题已复述
- [ ] P02-07 检查问题已复述
- [ ] P02-08 检查问题已复述
- [ ] P02-09 检查问题已复述
- [ ] P02-10 检查问题已复述
- [ ] P03-01 检查问题已复述
- [ ] P03-02 检查问题已复述
- [ ] P03-03 检查问题已复述
- [ ] P03-04 检查问题已复述
- [ ] P03-05 检查问题已复述
- [ ] P03-06 检查问题已复述
- [ ] P03-07 检查问题已复述
- [ ] P03-08 检查问题已复述
- [ ] P03-09 检查问题已复述
- [ ] P03-10 检查问题已复述
- [ ] P03-11 检查问题已复述
- [ ] P03-12 检查问题已复述
- [ ] P03-13 检查问题已复述
- [ ] P03-14 检查问题已复述
- [ ] P03-15 检查问题已复述
- [ ] P03-16 检查问题已复述
- [ ] P03-17~21 检查问题已复述
- [x] P04-01 检查问题已复述
- [x] P04-02 检查问题已复述
- [x] P04-03 检查问题已复述
- [ ] P04-04 检查问题已复述
- [ ] P04-05 检查问题已复述
- [ ] P04-UI-01 检查问题已复述

---

## P04-10A：生产依赖组装与真实 PostgreSQL Store ✅

**产物**：`infrastructure/wiring.py`、`infrastructure/db/application_stores.py`、
`infrastructure/queue/job_dispatcher.py`、`application/job_dispatcher.py`、
`migrations/versions/0005_idempotency_keys.py`、`cli.py`（P04-11）

### 3 个知识点

1. **Protocol 端口 + composition root（wiring）**：application 层只定义 `Protocol`
   （JobStore/JobQueryStore/IdempotencyStore/...），infrastructure 提供 SQLAlchemy 实现，
   由 `create_production_app()` 统一组装注入 `api.create_app`。测试注入 fake 即可完全离线；
   这是收口"生产组装"与"测试替身"的关键，避免测试和运行走不同代码路径。

2. **幂等池必须独立表 + DB UNIQUE 兜底**：`idempotency_keys` 表以 `key` 唯一约束兜底，
   并发同 key 由数据库拒绝而非进程内判断，杜绝"并发下同 key 生成两个有效任务"。
   `request_fingerprint` 用于判断同 key 是否同一请求（同请求复用、异请求 409）。

3. **Celery 队列必须用 `kombu.Queue` 对象**：`task_queues = ("research-jobs",)` 字符串元组
   在 `send_task` 构造 router 时会崩（`'str' object has no attribute 'name'`）；
   `task_queues = (Queue("research-jobs"),)` 才正确。这是真实联调才暴露的坑。

### 检查问题（请用自己的话回答）
为什么 `SqlIdempotencyStore.save` 遇到并发用「IntegrityError → 读回 → 抛冲突」而不是
「先 SELECT 防重复后 INSERT」？两者在并发竞态窗口上有什么本质差别？

---

## P04-10：Docker Compose 联调 + P04-UI-05 + P04-11 ✅

### 3 个知识点

1. **migrate 服务三连坑**：① alembic 原在 dev 依赖组，`uv sync --no-dev` 不装 → 须移入
   runtime；② src 布局下 `migrations/env.py import invest_research` 需 `PYTHONPATH=/app/src`；
   ③ env.py 只读 `alembic.ini` 的 `sqlalchemy.url`（localhost），必须支持
   `DATABASE_URL` 环境变量覆盖（12-factor），否则 Docker 内连不上 postgres 服务名。

2. **镜像内自包含迁移优于 bind mount 覆盖**：最终把最新 `migrations/` 与 `alembic.ini`
   打进镜像（Dockerfile COPY），compose 不再 bind mount——迁移文件随镜像版本走，
   杜绝"宿主机覆盖旧镜像"的隐含依赖。**验证要点**：下载 artifact 时必须确认对应
   commit（镜像内 env.py 是否含最新 override），避免误用旧快照。

3. **Worker 消费语义验证**：API 成功落库后经 `CeleryJobDispatcher` 投递 job_id，
   worker 用真实 `JobRepository`（pending→running→succeeded）+ `ResearchFlowRunner`
   （fake 全链）推进；轮询 `GET` 观察到 pending→succeeded 即为端到端验收通过。

### 检查问题（请用自己的话回答）
为什么 "Database 已提交成功但 Celery 消息投递失败" 这个窗口是 Phase 4 认可的已知限制，
而不是必须先做 transactional outbox？它计划在哪个阶段处理？

---

## P04-UI-06~10：任务列表 API、最近任务页、job_id 持久化、局部轮询、连贯导航 ✅

**产物**：`application/job_listing.py`、`infrastructure/db/application_stores.py`（SqlJobListStore）、
`api/app.py`（GET /v1/research-jobs）、`wiring.py`、`frontend/state.py`、`frontend/render.py`、
`frontend/Home.py`（任务中心）、三个页面（创建/状态/报告）、`tests/test_api_list_jobs.py`、
`tests/test_frontend_state.py`、`tests/test_ui_smoke_flow.py`（smoke 串联）

### 3 个知识点

1. **keyset（游标）分页 vs offset 分页**：offset 在大数据量下会有重复/遗漏（数据变更时），
   且越翻越慢。keyset 用「上一页最后一条的 (created_at, job_id) 作为游标」，
   配合 `created_at DESC, job_id DESC` 稳定排序，保证无重复、无遗漏。实现上
   `SqlJobListStore.list_jobs(before=(created_at, job_id))` 返回严格更早的行，
   API 层「多取一条判断是否有下一页」再裁剪，下一页游标由最后一条编码生成。

2. **URL 是任务上下文的持久化载体**：`st.query_params`（正式 API，非实验接口）把
   job_id 写进 URL，浏览器刷新/复制链接即可恢复；`session_state` 只是同会话内的
   回退缓存。恢复优先级 = URL > session > 手动输入，且一律先做 UUID 校验，
   非法值不调用 API。这也回答「多页面导航清理 query params」的坑：我们不做
   依赖偶然行为的页面跳转，而是统一入口 `load_job_id()` 显式解析。

3. **st.fragment(run_every=...) 实现局部轮询**：把「任务状态/耗时/步骤」放进
   fragment，`run_every=2` 只重跑该片段，页面标题与导航不重建，明显减少闪烁。
   终态（succeeded/partial/failed/cancelled）在 fragment 内直接停止自动轮询；
   网络失败只提示「下次轮询重试」，不抛异常中断整页。

### 检查问题（请用自己的话回答）
为什么 keyset 分页可以做到「无重复、无遗漏」，而 offset 分页在任务不断创建/状态变化时做不到？如果两条任务 created_at 完全相同，排序键为什么要加上 job_id？

---

## P05-01：实现通用 retry policy ✅

**产物**：`src/invest_research/infrastructure/retry.py`（RetryableError / compute_backoff_delay / RetryPolicy / build_retrying / retry_call）、`tests/test_retry_policy.py`；pyproject 增加 tenacity 依赖。

### 3 个知识点

1. **错误码白名单优先于「异常类型可见」**：只允许把 domain 层白名单错误码（RATE_LIMITED / NETWORK_TRANSIENT / UPSTREAM_5XX / SCHEMA_INVALID）包装成 `RetryableError`，构造时校验；`AuthError`、`InputInvalid` 等非白名单错误码根本无法被当成可重试异常抛出。这把「能不能重试」的决策收敛到 domain 层，避免调用方随意把业务错误划成可重试。

2. **可注入 sleep 让重试测试既快又确定**：tenacity `Retrying` 接受 `sleep=` 参数；先推进模拟时钟（每步 1s→2s→4s）并记录每次 sleep 时长，即可断言「尝试了 4 次」「等待序列是 [1.0, 2.0, 4.0]」，完全不需要真实等待（配合 docs/04 §5.1「测试不得依赖真实等待时间」）。

3. **`reraise=True` 保证「耗尽后抛出最后一次原始异常」**：默认 tenacity 在重试次数耗尽后抛 RetryError，业务侧会用 `except RetryableError` 而不是原始异常；`reraise=True` 直接抛原始异常（含 error_code 字段），让上层按错误码分支、不丢失语义。

### 检查问题（请用自己的话回答）
为什么 `RetryableError` 构造时要对 `error_code` 做「白名单校验」？如果把 `AUTH_ERROR`（key 无效）也包装成可重试错误，会发生什么不好的结果？

---

## P05-02：尊重 Retry-After 并动态降速 ✅

**产物**：`src/invest_research/infrastructure/retry.py`（新增 `parse_retry_after` / `RetryAfterProvider` / `ResponseHeadersRetryAfter` / `build_retrying_with_retry_after`）、`tests/test_retry_after.py`。

### 3 个知识点

1. **Retry-After 是上游给你的"精确等待指令"**：RFC 9110 规定 429 限流、503 服务不可用等响应可带 `Retry-After` 头，告诉客户端"过多久再试"。格式二选一：**秒数**（`Retry-After: 120`）最常用，或 **HTTP-date**（`Wed, 21 Oct 2015 07:28:00 GMT` 表示到那个时刻再试）。`parse_retry_after` 优先解析秒数、其次是 HTTP-date（用 `email.utils.parsedate_to_datetime`），非法/缺失/负秒数一律返回 None——响应头是"人写的"，绝不能因坏头导致程序崩溃。

2. **动态降速 = 把"我自己等的"换成"上游要求的"**：普通退避是"我猜等多久"（指数退避），Retry-After 是"服务器告诉我等多久"（精确、配合服务器状态）。`build_retrying_with_retry_after` 的 wait 策略：有合法 Retry-After → 按其秒数等待；无/非法 → 回退指数退避。这样收到 429 时不会按本地时钟僵硬重试，而是跟随服务器的节奏"动态降速"（docs/04 §5.1「优先尊重 Retry-After」）。

3. **注入 Provider 抽象让"等待策略"可独立测试**：`RetryAfterProvider` 是抽象（只声明 `get()`），`ResponseHeadersRetryAfter` 从响应头读取（大小写不敏感遍历），测试用可变 provider 模拟"有/无 Retry-After"两种场景。配合 FakeSleep 记录等待时长，断言"有头时等 10s、10s；无头时等 1s、2s"——完全不需要真实 sleep（与 P05-01 同模式）。

### 检查问题（请用自己的话回答）
为什么服务器给的 `Retry-After` 比客户端自己算的指数退避更"准"？当 `Retry-After` 是 HTTP-date 且该时刻已过去时，`parse_retry_after` 返回 0 是什么意思？为什么坏格式的 Retry-After 应该返回 None 而不是抛异常？

---

## P05-03：步骤 lease 与 stale recovery ✅

**产物**：`src/invest_research/application/recovery.py`（`StaleStepSnapshot` / `StepLeaseStore` 端口 / `RecoverySettings` / `RecoveryResult` / `StaleRecoveryService`）、`tests/test_recovery.py`。

### 3 个知识点

1. **at-least-once execution（至少一次语义）与 lease**：worker 开始处理步骤时把状态置为 `running` 并开始计时（`started_at`），只有"持有 lease"（未超过 `lease_seconds`）的 worker 才有权继续。若 worker 崩溃，其 `running` 步骤会一直停着；这就是"stale"（陈旧）。恢复任务扫描所有 `running` 且 `started_at + lease > now` 的步骤，把它们标记回 `failed_retryable` 以便再次入队——先标记不直接重跑，保证至少一次（不丢）但可能重复（靠幂等键去重）。

2. **条件更新避免"恢复任务"与"慢 worker"互相覆盖**：恢复时用 `mark_failed_retryable(job_id, step_name)` 且**仅当当前仍是 running 才成功**（乐观锁）。如果真实 worker 只是慢、并没崩溃，它可能已把步骤推进成 succeeded/failed——恢复任务的条件更新会失败（返回 False），不会把已完成的步骤错误地拉回可重试。这是"恢复安全性"的关键：宁可少恢复，不可误伤。

3. **协议注入让恢复逻辑可独立测试**：`StepLeaseStore` 是 Protocol（只声明 `list_running_steps` / `mark_failed_retryable`），`StaleRecoveryService` 只依赖这个抽象；测试用内存 fake store + 固定 `now`，覆盖"未超时/超时/自定义lease/混合/条件更新失败"五类场景，无需数据库、不依赖真实时钟。这与 P05-01/02 的模式一致：**逻辑在 application，存储经端口注入**。

### 检查问题（请用自己的话回答）
为什么恢复任务要把 stale 的 `running` 步骤改成 `failed_retryable` 而不是直接改成 `pending` 或直接重跑？"仅当当前是 running 才更新"（条件更新）如何防止恢复任务误伤一个只是运行得慢的真实 worker？

---

## P05-03A：Worker 启动自动 stale recovery 与恢复计数 ✅

**产物**：`application/recovery.py`（新增 `RecoveryCounter`）、`infrastructure/queue/recovery_bootstrap.py`（`run_startup_recovery`）、`tests/test_recovery_bootstrap.py`。

### 3 个知识点

1. **"启动即恢复"是 at-least-once 的启动语义**：宕机后重启的 worker，不等人手动跑恢复脚本，而是在启动流程里调用 `run_startup_recovery` 自动扫描超租约步骤并标记为可重试。这样"worker 崩溃 → 重启 → 自动恢复被中断的步骤"形成一个闭环，让断点续跑成为系统默认行为而非人工操作。

2. **恢复计数可观测（RecoveryCounter）**：`RecoveryCounter` 累计"启动以来恢复的步骤总数"并保留最近一次扫描结果。这是将来 Prometheus gauge（P05-06）与结构化日志的来源——恢复不是"静默进行"，运维能看到"这次启动恢复了几个 stale 步骤"，从而发现频繁崩溃模式。

3. **`now` 可注入（ClockNow）让启动恢复可测试**：`run_startup_recovery(service, counter, now=lambda: now)` 通过注入固定时钟，测试可以在毫秒级验证"启动时恢复了1个、无 stale 时0个、多次启动计数累计"——不依赖真实时间，与 P05-03 的 fake store + 固定 now 完全一致。

### 检查问题（请用自己的话回答）
为什么"worker 重启时自动恢复"比"等运维手动跑恢复脚本"更可靠？`RecoveryCounter` 的累计计数对运维/监控有什么价值（提示：结合频繁崩溃模式与 P05-06 Prometheus）？

---

## P05-03B：Transactional Outbox 与 pending 投递恢复 ✅

**产物**：outbox_events 表 + SqlOutboxStore + OutboxRelayService + relay bootstrap + tests/test_outbox.py

### 3 个知识点
1. **双写原子性**：Job 与事件同事务提交，避免「DB 成功但消息未发」gap。
2. **事件级乐观锁**：pending→claimed 条件更新防并发重复投递。
3. **有界重试**：dispatch 失败回拨 pending+1，达上限 failed；重启后 relay 恢复。

### 检查问题（请用自己的话回答）
为什么 outbox 事件必须与 Job 同事务写入，而不能在 dispatch 失败后单独补写？

---

## P05-05：结构化日志与敏感字段脱敏 ✅

**产物**：src/invest_research/infrastructure/observability/logging.py + tests/test_observability.py（5 测试）

### 3 个知识点
1. **脱敏 processor 在渲染前执行**：structlog 用自定义 processor 在 JSON 渲染前把 key/Authorization/Cookie 替换为 ***。
2. **敏感字段名规范化**：set_cookie 与 set-cookie 需统一（下划线转连字符）再查 SENSITIVE_KEYS。
3. **正则顺序影响结果**：先匹配完整 Bearer 值，再匹配 key=val，避免残留。

### 检查问题（请用自己的话回答）
为什么脱敏必须放在 JSON 渲染之前，而不是渲染之后？

---

## P05-04：输入 hash 与下游失效 ✅

**产物**：`src/invest_research/application/versioning.py`（`compute_input_hash` / `should_recompute` / `VersionDecision` / `VersioningService`）、`tests/test_versioning.py`。

### 3 个知识点

1. **输入 hash = 缓存失效的"指纹"**：`compute_input_hash` 把"阶段标识 + 请求输入 + 上游 hash + 公式版本 + 提示词 hash + schema 版本"合成一个 sha256。任一组件变化 hash 即变——这就是 docs/04 §6「提示词/模型/公式/schema 版本变化后输入 hash 改变，下游失效重算」的机械实现。`json.dumps(sort_keys=True)` 保证字典键顺序不影响结果（确定性）。

2. **失效判断 = "无缓存 → 重算；hash 不同 → 重算；schema 不匹配 → 重算"**：`should_recompute` 三档语义。特别地，schema 版本升级（current ≠ expected）即使 hash 相同也必须重算——因为旧 pack 的形状已经过期（对齐 P01-05 各 pack 的 version 字段）。

3. **VersioningService 是幂等/断点续跑的开关**：`recompute_decision` 同时算出当前 hash 和是否重算，供调用方决定"复用旧工件（False）"还是"强制重算（True）"。这是断点续跑（跳过已通过 checksum 的步骤）与"版本升级后强制重算"两股力量的统一入口。

### 检查问题（请用自己的话回答）
为什么"schema 版本不匹配"时即使 hash 相同也必须重算？`json.dumps(sort_keys=True)` 对 hash 的确定性起什么作用？

---

## P05-06：Prometheus 指标 ✅

**产物**：src/invest_research/infrastructure/observability/metrics.py + tests/test_metrics.py（3 测试）+/metrics 端点

### 3 个知识点
1. **Prometheus label 设计**：不添加 job_id/URL/公司名高基数 label，避免指标爆炸。
2. **generate_latest 输出**：/metrics 用 PlainTextResponse + CONTENT_TYPE_LATEST 暴露标准格式。
3. **Counter/Gauge 分工**：总数用 Counter（research_jobs_total），当前值用 Gauge（stale_running_steps）。

### 检查问题（请用自己的话回答）
为什么 Prometheus 不把 job_id 作为 label？高基数会带来什么问题？

---

## P05-07：OpenTelemetry Trace ✅

**产物**：`src/invest_research/infrastructure/observability/tracing.py`（`setup_tracing` / `get_tracer` / `trace_id_from_context`）、`tests/test_tracing.py`。commit `f830367`。

### 3 个知识点

1. **Trace 是"跨进程/跨步骤的胶水"**：单靠结构化日志（P05-05）只能看到"某个进程里这一秒发生了什么"，无法把 API 请求 → Worker 消费 → Flow 各步骤 → Tool 调用串成一条完整链路。`trace.set_tracer_provider(provider)` 建立全局 TracerProvider，所有模块用 `get_tracer(name)` 按名称取 tracer 创建 span，`trace_id` 成为贯穿全链路的同一条"案件编号"。
2. **资源语义化（SERVICE_NAME Resource）**：TracerProvider 用 `Resource.create({SERVICE_NAME: ...})` 打上服务名标签，所有 span 自动携带该资源属性。多服务部署时（API/Worker），按 service.name 就能区分 span 归属，而不必在每条 span 手工加标签。
3. **可注入导出器 + 与结构化日志关联**：`setup_tracing(exporter=...)` 默认 `SimpleSpanProcessor + ConsoleSpanExporter`（本地可观测），生产可传入 OTLP exporter（对象注入，不硬编码依赖）；`trace_id_from_context()` 读取当前 span context 的 trace_id（无则 None），供 P05-05 的日志 processor 把 `trace_id` 写进每条日志——日志与 trace 通过同一条 id 关联，是"排障时先看 trace、再按 trace_id 过滤日志"的桥梁。

### 检查问题（请用自己的话回答）
为什么 `setup_tracing` 要把 exporter 作为可注入参数，而不是在函数里直接创建 OTLP exporter？`trace_id_from_context()` 返回 `None` 的语义是什么（什么时候会没有当前 span）？

### 已知限制
- 当前未接入真实 OTLP Collector 与 Zipkin/Jaeger 后端；span 只输出到控制台/内存（完整 Collector 接入属于 P06-05）。
- Flow/工具层尚未普遍创建自定义 span，仅提供 tracer 获取能力；API→Worker 的传播还需在 HTTP 客户端注入 context 头。

---

## P05-08：Grafana 最小 Dashboard ✅

**产物**：`deploy/grafana/provisioning/dashboards/research.json`（RED/USE 面板）、`tests/test_grafana_dashboard.py`（3 测试）。commit `9e19f9e`、`3c543e5`（强制纳入 Git 跟踪）。

### 3 个知识点

1. **RED/USE 是 SRE 面板选指标的框架**：RED = Rate（速率）/Errors（错误）/Duration（耗时）；USE = Utilization/Saturation/Errors。面板取"成功率（research_jobs_total）→ Rate+Errors、P95 耗时（histogram_quantile(0.95)）→ Duration、重试（tool_retries_total）→ Saturation/Errors、质量门禁失败（quality_gate_failures_total）→ Errors"——每个面板的 PromQL 表达式都对应 P05-06 已暴露的一个指标，不臆造不存在的指标。
2. **Dashboard-as-code 可校验**：dashboard 是 JSON 文件，测试把它当"配置代码"验证：`title`/`uid` 固定、必须包含 4 类关键面板表达式、`refresh=30s`、时间窗口 `now-1h`。这样"面板没漏指标""表达式拼错"会在 CI 暴露，而不是部署后才在浏览器里看到空白面板。
3. **Git 跟踪范围是显式决策**：本项目根 `.gitignore` 是白名单模式，`deploy/` 默认不被跟踪；Dashboard 属于"交付产物"而非"运行时生成物"，通过强制添加（`git add -f`）纳入版本控制，并保持后续只修改已跟踪文件、不随意扩大 ignore 范围——"什么进 Git"是工程决策，不是 gitignore 的偶然结果。

### 检查问题（请用自己的话回答）
为什么 Dashboard 的 JSON 要写测试来校验，而不是"打开 Grafana 肉眼看一眼"？`histogram_quantile(0.95, ...)` 表达的是哪个指标的第几个百分位，它与"P95 耗时"的语义如何对应？

### 已知限制
- Dashboard 只在 Grafana provisioning 目录存在，未实际起 Grafana 容器验证渲染（真实 Grafana 部署属于 P06-06 完整 Compose profile）。
- 面板数量保持在最小集合，未覆盖全部指标（如恢复计数、outbox 投递延迟）；后续按需增补。

---

## P05-09：故障注入（timeout/429/5xx） ✅

**产物**：`tests/test_fault_injection.py`（状态码分类 + 429 重试恢复 + 5xx 耗尽放弃 + timeout 归类 + 最终状态矩阵）。commit `ddfacd4`。

### 3 个知识点

1. **故障注入 = 在"分类层"验证韧性，而非等真实故障**：测试用 `httpx.MockTransport` 构造 429/5xx/timeout 响应，验证 `classify_status_code`/`classify_http_exception` 的映射正确（429→RATE_LIMITED、5xx→UPSTREAM_5XX、timeout→NETWORK_TRANSIENT），并验证"429 重试后成功（calls==2）"与"5xx 达上限后放弃（calls==3）"两条路径——不联网、不真实 sleep，秒级确定。
2. **MockTransport 是 httpx 的"故障发生器"**：`httpx.MockTransport(handler)` 拦截所有请求，handler 按调用次数（`calls["n"]`）分派"第 1 次返回故障、后续返回成功"。这比 mock 整个 client 更真实：走真实请求/响应管线，又能精确制造各种故障时序。
3. **重试上限的"最终状态"才是验收**：故障注入的价值不是"重试总能成功"，而是"可恢复的（429/timeout）重试后成功、不可恢复/耗尽的（持续 5xx）按上限放弃且错误可分类"。这验证了 P05-01 白名单错误码 + P05-02 Retry-After 的"有界重试"闭环：任何情况下都不会无限重试。

### 检查问题（请用自己的话回答）
为什么"429 重试后成功"测试要断言 `calls["n"] == 2`（恰好两次），而不是只断言"最终成功"？（提示：验证了有界重试中的"有界"与"恰好一次降级"两面）

### 已知限制
- 本任务覆盖 HTTP 层 timeout/429/5xx 分类与有界重试，未覆盖"降级路由（PDF→HTML）""LLM schema guardrail/reflection 上限"等内部故障——这些属于 P05-10。
- `_policy` 辅助函数尚未真正绑定 tenacity Retrying 实例，仅演示分类与最终状态；真正的重试执行路径由 P05-01/02 的 retry 模块覆盖。

---

## P05-10：故障注入（坏 PDF/非法 LLM JSON） ✅

**产物**：`tests/test_fault_injection_pdf.py`（损坏 PDF/降级路由）、`tests/test_fault_injection_llm.py`（非法 LLM 输出/guardrail/reflection 上限）。**零生产代码改动，纯测试复用现有模块。**

### 3 个知识点

1. **"内部故障注入"与"外部故障注入"分层**：P05-09 注入 HTTP 层故障（timeout/429/5xx），P05-10 注入**系统内部**故障——损坏/空白 PDF、PDF 主解析失败、非法 LLM JSON、缺字段、Pydantic 验证失败、guardrail/reflection 达上限。分层验证"有界恢复"：外部网络故障走重试策略，内部解析/结构故障走降级/修复循环，两类各有上限、互不混淆。
2. **复用 ≠ 重写**：P05-10 不新造整条 Flow，而是直接对 `ParserRouter.parse_document`、`run_with_guardrail`、`ReflectionController.step` 注入 spy/bomb 测试替身，用断言"调用次数=1/0、attempts_used=max、repeat_reject"证明**降级恰好一次、guardrail 有限、反思不无限**。这正是"为测试不重新设计整条 Flow"的落地。
3. **失败语义分层可查询**：损坏 PDF → `DocumentParseError(failed_parsers, detail)`（分类可查）；空白 PDF → 空 blocks（不是虚假成功）；非法 LLM JSON → 可读错误列表（含字段路径）；guardrail 达上限 → `valid=False + errors` 保留；reflection 达上限 → `repeat_reject`。每个失败点都有结构化、可断言、可查询的结果，不吞错、不产生半成品。

### 检查问题（请用自己的话回答）
为什么"降级路由（PDF→HTML）"和"guardrail 修复循环"都强调**恰好一次/有上限**，而不是允许无限重试？这两类"有限恢复"与 P05-09 的 HTTP 重试在语义上有什么共同点（提示：都受 docs/04 的"重试次数上限"约束，都是把无限失败转化为可分类的终态）？

### 已知限制
- 测试注入的 PDF/HTML 解析器是替身，未用真实 PyMuPDF 解析损坏 PDF（真实解析器行为已在 P02-10 覆盖）。
- 未对"真实 LLM 反复输出非法 JSON"做集成级验证（guardrail 修复循环的行为是纯函数测试）。
- 未触发"降级成功但 HTML 解析结果为空"到 Flow 层面的产物判定（属于 P05-11/12A 范围）。

---

## P05-11：故障注入（数据库/本地工件写入失败） ✅

**产物**：`tests/test_fault_injection_db.py`（Job+Outbox 事务语义+连接失败）、`tests/test_fault_injection_artifact.py`（原子写各阶段故障）。**零生产代码改动。**

### 3 个知识点

1. **事务语义 = "要么两者都持久化，要么都不持久化"**：`SqlJobStore.create` 在同一 session 里同时 `add(ResearchJob)` 与 `add(OutboxEvent)`，一次 `commit()` 原子提交。测试注入"commit 抛错/连接断开"的双份 session 工厂，断言**没有一行 Job、也没有一行 Outbox 事件落库**——这就证明了"DB 与事件一致"：不可能出现"Job 成功但 Outbox 缺失"的半成品。
2. **原子写入 = "临时文件 + fsync + os.replace"的每一环都可失败且不留下半成品**：`ArtifactStore.write` 用同目录临时文件，写完 `flush()`+`os.fsync()` 后 `os.replace` 原子改名。测试用 monkeypatch 分别注入 `write`/`flush`/`fsync`/`os.replace`/`hashlib.sha256` 的失败，断言**异常传播（不误报成功）+ 临时文件被清理 + 目标文件不存在（或覆盖场景下旧内容保留）**——每个故障点都不产生内部可见的半成品。
3. **注入点必须匹配真实实现（真实调试教训）**：第一版把故障注入到 `Path.write_bytes`/`builtins.open`，但 `ArtifactStore` 实际用的是 `os.fdopen` + 文件对象方法——注入根本没命中，4 个测试"没有抛错"直接失败。修正为包装 `os.fdopen` 返回的 file 对象、patch `os.fsync`/`os.replace`/`hashlib.sha256` 后才真正命中。**教训：测试必须先读清生产代码的真实调用路径，注入点错误 = 测试假绿。**

### 检查问题（请用自己的话回答）
为什么"commit 失败"测试要同时断言 `_count_jobs == 0` 和 `_count_events == 0`（两个都是 0），而不是只断言其中一个？"覆盖场景下 os.replace 失败"为什么要求旧内容仍然保留（v1 不被破坏）？（提示：原子替换成功与否的分界在哪一步？）

### 已知限制
- 数据库测试用 SQLite 内存库验证事务写入语义，未连真实 PostgreSQL（真实 DB 集成由 P01-07/08 的 Testcontainers 覆盖）。
- 文件系统故障用 monkeypatch 注入，未模拟真实磁盘错误/断电（该粒度不适合单测）。
- Outbox relay 的投递失败与队列发布失败（非 Job 创建事务）已在 P05-03B 覆盖，本任务聚焦"创建事务"与"工件写入"。

---

## P05-12：SEC Fixture 录制与脱敏（真实录制完成 ✅）

**产物**：`src/invest_research/infrastructure/fixture.py`（`FixtureMeta`/`sanitize_response`/`build_meta`/`replay`）、`tests/test_fixture_sanitizer.py`（10 + 2 tests）、`scripts/record_sec_fixture.py`、`scripts/verify_recorded_fixture.py`、`tests/fixtures/sec_recorded_aapl.json`（真实录制）。

### 3 个知识点

1. **record/replay 的脱敏在"入库前"执行**：录制响应必须先经 `sanitize_response` 递归脱敏（敏感键→`***`、普通文本内 bearer/token 打码）再保存，而不是回放时才脱敏。这样 fixture 文件本身就不含敏感字段，任何人 checkout 也安全；测试另加一条守卫"已有 fixture 不含 Authorization/Cookie/API Key"。
2. **FixtureMeta = fixture 的"身份证"**：来源 URL、录制日期、schema 版本、内容 sha256 checksum 四元组让每个 fixture 可追溯（哪里录的、哪天录的、什么 schema、内容是否被改过）。这与 RunManifest（P03-14）的"可复现指纹"思想一致——数据资产也要版本化。
3. **离线回放 = 普通测试零成本且确定性**：`replay(fixture_path)` 纯本地 JSON 读取，不联网。CI 普通测试可安全调用，结果确定（同文件必同数据），是实现"CI 不访问真实 SEC"的最小手段——也符合 .clinerules"外部服务必须使用 mock/fixture"。

### 检查问题（请用自己的话回答）
为什么脱敏必须在"录制入库前"而不是"回放时"执行？如果 fixture 文件里已经存了真实 Authorization 头，回放时才脱敏，会有什么风险？

### 已知限制（真实录制已完成 ✅）
- **真实 SEC 网络录制已执行（2026-08-15）**：使用 `scripts/record_sec_fixture.py` 访问 SEC 官方 Company Tickers/Submissions/Company Facts，录制 AAPL（CIK 0000320193），选定真实历史截止日 `2025-10-31`（最近 10-K 的 filingDate，非动态 today）。
- 录制响应立即经 `sanitize_response` 脱敏并编 `FixtureMeta`（含 sha256），落盘为 `tests/fixtures/sec_recorded_aapl.json`。
- 守卫测试 `test_recorded_aapl_fixture_replays_offline` / `test_recorded_aapl_fixture_meta_and_no_secrets` 验证离线回放 + 无敏感字段/邮箱。
- P05-12 在路线图**标 ✅**（真实录制 + 脱敏 + 离线回放验收真实通过）。

---

## P05-12A：FLOW_MODE=fake/live 与生产 Flow wiring ✅

**产物**：`src/invest_research/infrastructure/flow_wiring.py`（`build_flow_runner`/`LiveResearchFlowRunner`/`FlowModeError`）、`tests/test_flow_wiring.py`（10 contract tests）、`settings.py` 新增 `flow_mode`、`worker.py` 新增 `_default_flow_runner`、`.env.example` 新增 `FLOW_MODE`。

### 3 个知识点

1. **Composition Root 集中按模式分支**：生产 wiring（`build_flow_runner`）是唯一按 `FLOW_MODE` 分支组装依赖的地方——fake 返回 `ResearchFlowRunner`（离线全链，不联网），live 返回 `LiveResearchFlowRunner`（持有配置但允许 run 前先校验 key）。业务/application 层只依赖 `FlowRunner` 端口，不感知模式，保持依赖方向 `infrastructure -> application -> domain`。
2. **fail-fast 用"配置校验"而非"运行时静默降级"**：live 模式下 `_ensure_live_api_key` 在返回 runner 之前就检查 `SECRET_API_KEY` 是否为空/空白，缺失即抛可读 `FlowModeError`。这避免把"缺 key"误当成"可以跑"——宁可启动即失败，也不允许带着坏配置悄悄执行真实模型调用。
3. **live wiring 契约可用"注入 fake LLM"离线验证**：`LiveResearchFlowRunner.assemble_crew` 接受三个 fake LLM 组装真实 Crew（`build_research_crew`），因此"真实三 Agent wiring 正确"可以在不联网、不付费、不发请求的前提下被测试覆盖；`run()` 显式抛 `NotImplementedError` 表示真实执行尚属 P05-13。

### 检查问题（请用自己的话回答）
为什么 `build_flow_runner`（composition root 分支）必须放在 infrastructure 层，而不是业务/application 层？"live 缺 key fail-fast"和"普通测试默认 fake"这两条约束，分别通过什么机制保证（一个靠构造时校验，一个靠默认值）？

### 已知限制（必须诚实记录）
- live 分支只完成 wiring 契约：`run()` 抛 `NotImplementedError`，真实受控 live run 属 P05-13，等待用户授权。
- `llm_api_key` 仍为 Settings 必需字段；fake 模式构造 Settings 时仍需占位 key（不触发任何请求）。
- worker 的 `_default_flow_runner` 只在 `FLOW_MODE=live` 时才读取 Settings 并触发 key 校验，fake 路径保持模块导入零 Settings 依赖。

---

## P05-12B：真实 LLM、Agent 工具和 LiveResearchFlowRunner 生产组装 ✅

**产物**：`agents/llm_factory.py`（`build_real_llm`/`AnyLLM`）；`agents/{research,analysis,writer,revision}_task.py`（统一 LLM 接口 + tools 注入）；
`agents/crew_factory.py`（`build_live_research_crew`）；`infrastructure/real_tools.py`（真实工具白名单）；
`infrastructure/flow_wiring.py`（`LiveResearchFlowRunner.run` 完整控制流）；`infrastructure/queue/worker.py`（live 工具组装 + Serper fail-fast）；
`settings.py`/`.env.example`/`compose.yml`（`SERPER_API_KEY`/`FLOW_MODE` 传递）；`tests/test_real_tools.py` + 更新 4 个既有测试文件。

### 3 个知识点

1. **CrewAI 1.6.1 的真实 LLM 构造契约**：`crewai.LLM(model, base_url, api_key, temperature, timeout)` 是 OpenAI-compatible 的官方入口，`issubclass(LLM, BaseLLM)=True`，构造阶段**不发起网络请求**（仅模型/provider 解析，实测 provider 自动识别为 `openai`）；API Key 用 `SecretStr.get_secret_value()` 只在构造那一刻解包，此后由 CrewAI 内部持有，绝不进入 repr/日志/异常。这回答了"真实 LLM builder 如何不猜测接口"——以当前安装版本源码/签名实测为准。
2. **统一 LLM 接口 = 联合类型而非抽象基类**：`AnyLLM = FakeLLM | BaseLLM`（`type alias`），三个 Agent 的 `fake: AnyLLM | None = None` 参数同时接受测试替身和真实 LLM；`fake=None` 时默认走 `build_real_llm`。这比让 Agent 继承抽象基类更贴合 CrewAI 生态（FakeLLM 本就是 BaseLLM 子类），且 mypy strict 可静态验证。删除"仅支持 fake"的 `NotImplementedError` 后，调用方无需区分模式。
3. **composition root 里把 P02 确定性工具包装为 CrewAI @tool**：真实工具（CompanyResolver/SEC/下载/解析/搜索）是 P02-01 的 `Tool` 协议对象（`execute(request) -> ToolResult`），不能直接挂到 CrewAI `Agent(tools=...)`；需要经 `@tool("Name")` 包装成"函数收原始参数 → 返回 JSON 字符串"。关键决策：**工具失败返回结构化 JSON（含 ErrorCode）而非抛异常**——CrewAI 会把异常当作工具调用失败，丢失统一错误语义；用 ToolFailure 的 ErrorCode 保留可重试分类。SEC Company Facts 结果受控（`as_of_date` 过滤 + 条数上限），防止把整份 XBRL 塞进 LLM context。
4. **live 模式 fail-fast 的完整闭环**：`flow_wiring.build_flow_runner` 校验 `LLM_API_KEY`；`worker._build_live_research_tools` 校验 `SERPER_API_KEY`（缺 key 直接抛 `FlowModeError`，禁止自动退回 fake）；`LiveResearchFlowRunner.run()` 的 `crew_factory` 可注入（默认真实 `build_live_research_crew`），因此"完整控制流（Crew→pack→质量门禁→受控反思→manifest→工件落盘）"可在离线用 fake crew 验证；任何 Crew 异常转 `LiveFlowExecutionError`，绝不偷偷降级 fake。
5. **Worker 按 FLOW_MODE 路由**：`_default_flow_runner()` 默认 `FLOW_MODE=fake`（模块导入零 Settings 依赖，保持 CI 离线）；`FLOW_MODE=live` 时才读 Settings、组装真实工具、fail-fast。`compose.yml` 的 `x-app-env` 已补 `FLOW_MODE`/`SERPER_API_KEY`/`SERPER_ENDPOINT`（默认 fake + 占位符，真实 key 只从运行环境注入，不进镜像/Git）。

### 检查问题（请用自己的话回答）
为什么"真实工具失败"要返回结构化 JSON（含 ErrorCode）而不是让 CrewAI 工具抛异常？如果工具直接抛异常，统一错误分类和可重试语义会丢失什么？（提示：对比 ToolFailure.error_code 与 CrewAI 对异常的处理）

### 已知限制（必须诚实记录）
- `LiveResearchFlowRunner.run()` 的真实**反思**（修订/补证的真实 LLM 重跑）仍以确定性占位记录审计（反思决策落进 manifest），真正触发真实 LLM 重跑属于 P05-13 live smoke。
- 本任务只在离线/CI 用 fake crew + 预置 pack 验证完整控制流；**真实模型调用未发生**（无真实 API Key），真实 E2E 等待 P05-13 受控授权。
- Serper 真实 Key 未配置（`.env.example` 只放占位符）；worker live 路径在缺 key 时会 fail-fast，属于预期行为。

---

## P05-13：真实服务 E2E smoke（实现完成 ✅ 离线契约；live 等待受控授权）

**产物**：`tests/test_live_e2e.py`（opt-in，默认 skip）、`scripts/run_live_e2e.py`（运行入口 + 前置自检）。

### 3 个知识点

1. **opt-in 测试 = 用环境变量做"保险丝"**：真实 E2E 用 `@pytest.mark.skipif(not _live_enabled(), ...)`，`_live_enabled()` 要求 `RUN_LIVE_E2E=1` 且 `FLOW_MODE=live` 同时成立；普通测试/CI 下测试**恒 skip**，绝不联网。这就是"真实服务调用必须有显式授权、默认禁"的结构保证。
2. **离线契约测试与 live 测试分层**：本文件同时含"永不 skip 的离线契约测试"（CIK/as_of/表单固定、FLOW_MODE 缺 key fail-fast）与"仅 live 触发的真实 E2E"——验收时能用 `pytest -v` 看到 `3 passed + 1 skipped` 明确区分，不被误判为"真实 E2E 已通过"。
3. **固定测试对象来自 P05-12 真实录制**：AAPL/CIK 0000320193/as_of 2025-10-31/10-K+10-Q/zh-CN 与 `sec_recorded_aapl.json` 一致——离线已有真实 SEC 契约基线，live 只是"放行真实调用"的最后一公里。

### 检查问题（请用自己的话回答）
为什么真实 E2E 必须同时满足 `RUN_LIVE_E2E=1` 和 `FLOW_MODE=live` 两个条件才触发，而不是只设一个？

### 已知限制（必须诚实记录）
- 项目根目录**无真实 `.env`**：live E2E 未执行，P05-13 **不标 ✅**，记录为"实现完成，等待受控 live run"。
- 运行说明（`scripts/run_live_e2e.py`）已写好真实 key 的填写/执行步骤，但未触发任何付费调用。

---

## P05-14 ✅：20 公司 × 5 场景 evals 数据集

**产物**：`evals/build_dataset.py`（确定性生成器）、`evals/dataset.json`（100 条）、`tests/test_evals_dataset.py`（10 校验测试）。

### 3 个知识点

1. **数据集也是代码（deterministic generator）**：20 家公司 × 5 场景不是手写 JSON，而是用 Python 生成器 `build_dataset()` + 固定 `_COMPANIES`/`_SCENARIOS` 元数据确定性产出——改公司/场景只需改元组，且每次运行结果一致（可复现）。
2. **固定 as_of 日期的纪律**：所有 as_of 是 `date(2025,10,31)`/`date(2024,6,30)` 写死的日期，**禁止 `date.today()`**——测试 `test_all_dates_fixed_no_today` 专门断言 `as_of != today`。历史截止场景（2024-06-30）专门用于验证"无未来数据"。
3. **校验器把"分布正确"做成机器可断言**：正好 100、case_id 唯一、CIK 10 位、20 公司各 5 场景、每个场景恰好 20 条、`expected_source_types` 含 sec_filing、无密钥——这些验收不再靠肉眼，10 个测试秒级跑完。

### 检查问题（请用自己的话回答）
为什么 `evals/dataset.json` 用"生成器产生"而不是手写，且 `as_of_date` 必须固定而不能用 `date.today()`？

### 已知限制
- 20 家公司 CIK 为真实 SEC 10 位数字，但 as_of 是统一固定历史日期（非各公司真实年报期）；真实 live 跑时以 runner 的 `_request_from_case` 直接用 as_of 为准。
- 本任务不调用模型；live 跑 100 条属 P06-10。

---

## P05-15 ✅：benchmark runner 与汇总

**产物**：`evals/benchmark_runner.py`（BenchmarkRunner/BenchmarkSummary/summarize/main）、`tests/test_benchmark_runner.py`（4 测试）。

### 3 个知识点

1. **三种模式 = 一种 Runner 参数化**：`BenchmarkMode.FAKE/FIXTURE/LIVE` 只影响 `_execute()` 的单条执行策略——fake 走 `ResearchFlowRunner` 全链、fixture 回放 `replay(sec_recorded_aapl.json)`、live 走 `build_flow_runner`；case 选择（--case/--limit/--resume/--only-failed）、保存原始 manifest、汇总统计全部复用。默认 `--mode fake` 保证开发/CI 零成本。
2. **断点续跑 = 按 run 目录查已存在 case**：`_run_exists(case_id)` 扫 `workdir/*/{case_id}.json` 判断是否已完成；`--resume` 跳过已完成、`--only-failed` 从所有历史 run 里挑 status=failed 的 case 重跑——"从上次断点继续"不需要额外状态文件，靠已保存的原始 manifest 本身。
3. **汇总用 `statistics.median` + 线性插值 P95**：P50 直接用中位数；P95 用 `_percentile(sorted, 0.95)` 的线性插值（`k=(n-1)*0.95`）得到更平滑的百分位；失败类别分布用 `Counter(error_code)`、质量门禁失败分布按 `recommendation` 聚合——每项都对齐 docs/04 §10 的汇总模板。

### 检查问题（请用自己的话回答）
为什么"只重跑失败项"（--only-failed）可以不用单独保存"上次失败清单"，而是扫描已保存的 run 目录就能实现？

### 已知限制
- `retry_recovery_rate` 在 fake/fixture 无失败时为 1.0（无失败即有界恢复），live 需真实重试数据才有实际意义。
- Token/外部 API 调用数当前为占位 0——live 模式接入真实 instrumentation 后回填（属 P06-10）。
- 正式 100 次 live benchmark 禁止在本任务执行（明确属 P06-10）。

---

## P05.5-1 ✅：性能记录（PerformanceRecorder + manifest.performance）

**产物**：`src/invest_research/infrastructure/performance.py`、`flows/manifest.py`（performance 字段）、`infrastructure/real_tools.py`（工具计时）、`tests/test_performance.py`。

### 3 个知识点

1. **可观测性只记录「计数与耗时」，绝不记录「内容」**：`PerformanceRecorder` 只累计 Agent 耗时、工具调用次数/耗时、token 计数（prompt/completion/total tokens）；密钥、Authorization 头、完整 Prompt、模型输入输出正文一律不落盘。token usage 取 `CrewOutput.token_usage`，不可得时写 `null` 而不是造假。
2. **Agent 耗时来自 CrewAI Task 的 start_time/end_time**：三 Agent 跑在同一个 `crew.kickoff()` 里，靠每个 `Task.start_time/end_time` 计算各自耗时；工具耗时在 real_tools 包装层用 context manager 计时——职责分离：Flow 采集 Agent 耗时、工具层自报工具耗时。
3. **性能数据并入 manifest 但保持可复现**：`build_run_manifest` 增加 `performance` 参数，只有计数/耗时，不含任何敏感内容，因此 `test_live_e2e` 的「工件不含密钥」断言依然成立。

### 检查问题（请用自己的话回答）
为什么 token usage「能安全获取时记录、否则写 null」，而不是在拿不到时写 0？

---

## P05.5-2 ✅：fast/deep 研究档位（RESEARCH_PROFILE + ResearchProfile）

**产物**：`settings.py`（`ResearchProfile` + `research_profile`）、三个 `*_task.py` 与 `crew_factory.py`（预算注入）、`tests/test_profile.py`。

### 3 个知识点

1. **预算集中到单一配置对象**：`ResearchProfile` 集中管理 research/analysis/writer 的 `max_iter`、`max_retry_limit`、`max_execution_time`（秒）、`max_rpm`，Agent 文件只读不写默认值——避免「每个 Agent 各写一份默认迭代」的散落。
2. **fast/deep 只是同一配置对象的两种预算**：fast=3/2/1 iter、retry=1、180s、rpm=60；deep=15/10/5、retry=2、600s、rpm=None（完整能力），默认 deep 保证向后兼容。切换只改 `RESEARCH_PROFILE` 一个环境变量。
3. **CrewAI RPMController 的非守护 Timer 泄漏**：`max_rpm` 非 None 时 CrewAI 1.6.1 启动 `threading.Timer(60.0, _reset_request_count)`（daemon=False 且循环自重建），测试里构造 fast Agent 后必须调 `agent._rpm_controller.stop_rpm_counter()`，否则 Python 卡在 `threading._shutdown` 不退出——这是「测试不退出」而非「测试失败」的经典陷阱。

### 检查问题（请用自己的话回答）
`max_execution_time` 用秒、`max_rpm` 用「请求/分钟」，两者分别限制的是「时间」和「频率」哪个维度的预算？

---

## P05.5-3 ✅：并行预取 + 每 Job 工具缓存

**产物**：`infrastructure/tool_cache.py`（ToolCallCache）、`real_tools.py`（ResearchToolkit + prefetch + 缓存包装）、`infrastructure/flow_wiring.py`、`queue/worker.py`、`tests/test_tool_cache.py`、`tests/test_prefetch.py`。

### 3 个知识点

1. **缓存键 = 工具名 + 规范化参数**：`json.dumps(params, sort_keys=True, default=str)` 让「参数顺序不同但值相同」命中同一键；日期经 `default=str` 与 ISO 字符串对齐。缓存只存成功结果（失败不缓存，允许底层重试/兜底）。
2. **并行只并「独立 I/O」**：公司解析（确定性）后用 `ThreadPoolExecutor(max_workers=2)` 并行 SEC submissions + Serper 搜索，各自先查缓存再决定是否取数；Analysis/Writer 依赖关系不变，不新增 Agent。prefetch 是 best-effort——失败由 Agent 工具兜底。
3. **Toolkit 共享避免重复构造**：`ResearchToolkit` 把 5 个 P02 工具集中为一份，CrewAI 包装层与 prefetch 复用同一实例；缓存存「最终 JSON 串」，命中直接返回，序列化逻辑（`_unpack`/`_serialize_search_result`）抽成共享 helper。

### 检查问题（请用自己的话回答）
为什么缓存要「只存成功结果、不存失败结果」？如果把失败也缓存，会对重试机制造成什么破坏？

---

## P05.5-4 ✅：三角色模型配置 fail-fast

**产物**：`agents/llm_factory.py`（模型名空白校验）、`tests/test_llm_factory.py`。

### 3 个知识点

1. **供应商/模型名绝不写死在逻辑里**：业务只表达 `LLMRole.RESEARCH/ANALYSIS/WRITER`，模型名从 `LLM_MODEL_*` 环境变量读，`LLMConfig.model_for(role)` 按角色返回——切供应商/模型只改 env，不改代码。
2. **fail-fast 在构造期而非运行期**：`field_validator` 对三个模型名 strip 后判空，空/纯空白抛 `ValidationError`——配置错误在 `LLMConfig` 构造瞬间暴露，而不是跑到一半「模型名空」才炸；live 缺 key 同样 fail-fast，绝不降级 fake。
3. **`extra="ignore"` 的静默陷阱**：`Settings` 的字段是 `llm_model_research`，测试里传 `model_research=` 会被 `extra="ignore"` 静默吞掉（不报错、用默认值）——断言「用错字段名」的测试必须用真实字段名，否则测了个寂寞。

### 检查问题（请用自己的话回答）
`Settings` 的 `extra="ignore"` 在你传错字段名（如 `model_research` 而非 `llm_model_research`）时会发生什么？这为什么让「配置错误 fail-fast」反而更危险？

---



## P05-13 ✅：真实服务 E2E smoke（fast live run 通过）

**产物**：真实运行产物在 `artifacts/AAPL_2025-10-31/`（07_manifest status=published，238s，token 62k，含 evidence.invocation_summary）。

### 3 个知识点

1. **验收 10 项是「管道连通性」检查，不是分析质量基准**：只验证 manifest published、报告非空、引用非空、SEC URL+locator、无未来数据、SEC/Serper/LLM 调用证据、工件齐全、无密钥泄漏——不验证财务数字正确。本次报告指标全是「不可计算」、营收是占位 0，依然通过——「链路通」与「数据好」是两件事。
2. **Agent 不调工具 ≠ 没有外部调用**：预取（prefetch）的真实 SEC/Serper 调用必须计入 evidence；若只统计 Agent 工具调用，Agent 直接用预取结果时 evidence 为空，验收会误判「没有真实调用」（本次正是踩了这个坑：requirement7 缺失 evidence）。
3. **快模式的时间构成**：238s ≈ 预取 4s + Research 37s + Analysis 155s + Writer 41s；Analysis 最慢是因为 LLM 往返轮数最多。工具结果体积 60→15 条后 token 从 30 万降到 6.2 万。

### 检查问题（请用自己的话回答）
为什么「报告里的财务指标全是 N/A、营收是占位 0」时验收还能通过？验收和「报告质量」的分界线在哪里？

### 已知限制
- Analysis Agent 仍拿不到真实 XBRL 事实（数据流缺口：ResearchPack 不带 facts、Analysis 白名单无 SEC 工具），报告如实标注「不可计算」——属分析质量限制，不影响 P05-13 验收。
- manifest.company 为 null（runner 未回填 state.company_identity），属展示细节，不影响验收。

---

## P06-01 ✅：用 Jinja2 固化 Markdown 报告模板

**产物**：`src/invest_research/reporting/`（`renderer.py`：ReportRenderInput / ReportRenderer / build_render_input + `templates/report.md.j2`）、`tests/test_report_renderer.py`（15 测试）、`tests/fixtures/golden_report.md`（golden 基准）、`flow_wiring.py` 增加 `08_report.md` 落盘、`pyproject.toml` 增加 `jinja2>=3.1.0`（uv.lock 同步）。

### 3 个知识点

1. **golden test 的本质是把「渲染输出」变成版本化契约**：固定输入（含固定 `generated_at`）→ 渲染结果逐字节等于已入库的 `golden_report.md`。任何模板/转义/字段变更都会让 golden 失败，从而强制人工确认「是否真的要改变报告外观」——报告结构从此可回归。
2. **模板骨架与 LLM 正文分离**：封面信息（公司/Ticker/CIK/截止日/生成时间）、来源清单与引用、结构化数据限制、固定免责声明由 Jinja2 确定性生成；Writer 初稿的 `ReportDraft.markdown` 原样嵌入（不篡改 grounded content）。即使 LLM 输出漂移，骨架章节与引用格式保持稳定。
3. **Jinja2 空白控制的两个坑**：`lstrip_blocks=True` 会吞掉列表项之间的换行（`{% endfor %}` 前的空白被剥离）；行尾内联 `{% endif %}` 后的换行会被 `trim_blocks` 吃掉。解决办法是把「来源行」的拼装移到 Python 侧（模板全局函数 `source_line`），模板里每行只输出纯文本——这是「模板只管结构、格式逻辑放代码」的实践。

### 检查问题（请用自己的话回答）
为什么「把来源列表项拼成一行文本」的逻辑放在 Python 侧（模板全局函数）而不是用模板内联 `{% if %}` 拼？`trim_blocks` / `lstrip_blocks` 对行尾换行的影响分别是什么？

### 已知限制
- 模板正文透传 LLM 初稿，未对初稿做章节拆分/重排（章节级确定性依赖质量门禁与 Writer 提示词）；
- 渲染输入不含引用键→来源的映射（citation_keys 只输出键名列表，来源清单来自 ResearchPack.sources），映射关系属后续任务。

---

## P06-02 ✅：Markdown→PDF 渲染（样式与视觉检查完成）

**产物**：`src/invest_research/reporting/pdf.py`（markdown-it-py 安全解析 + PyMuPDF Story 分页）、`tests/test_pdf_renderer.py`、`scripts/render_report_pdf.py`、`docs/p06-02-samples/`（sample_report.pdf 4 页 + 全部页 PNG，42 个唯一 SEC 来源链接）。

### 3 个知识点

1. **PyMuPDF 内置 CJK 字体免外部依赖**：`fontname="china-s"` 是 PyMuPDF 内置简体中文字体，无需下载/打包任何字体文件；`pymupdf.get_text_length(text, fontname="china-s", fontsize=12)` 可精确测量 CJK 宽度（每全角字符 = fontsize pt），据此做手动换行与链接矩形定位。
2. **Markdown 链接 → PDF link annotation 的两种实现**：① 模板把来源清单渲染成 `[title](url)`（Markdown 链接语法），PDF 渲染器解析行内链接段（`_split_segments` 正则）后逐段绘制并在对应矩形 `page.insert_link(kind=LINK_URI, ...)`——**注意 `get_links()` 在保存前为空、保存重开后才有**，测试要 reopen 后再断言。② 若来源是纯文本 URL 则不会生成链接（本次正是为此把模板改成链接语法）。
3. **手动换行与自动分页**：`insert_textbox` 自动换行但无法定位链接；改用 `insert_text` + `get_text_length` 逐段贪心换行（优先空格断行、CJK 无空格按字符断行），每行绘制前检查 `y + line_height > 页高 - 页脚区` 就 `doc.new_page()`——分页是"画之前判断"，保证不出现半行跨页。

### 检查问题（请用自己的话回答）
为什么 `page.get_links()` 在 `doc.save()` 之前是空的？这提醒测试断言链接时要按什么顺序操作？

### 已知限制
- HTML 输入被禁用，避免把未信任报告内容当作 HTML 渲染；
- 首个 H1 由模板统一管理，正文中的重复 H1 会被确定性去除。

---

## P06-03 ✅：本地工件生命周期清理（retention policy）

**产物**：`src/invest_research/infrastructure/artifact_lifecycle.py`（CleanupPolicy / CleanupStats / ArtifactCleanupService）、`tests/test_artifact_lifecycle.py`（13 测试，全部只使用临时目录）。

### 3 个知识点

1. **清理策略按"类别 × 年龄/数量"分解**：① 临时文件（`.tmp-*.part` 孤儿，ArtifactStore 原子写失败遗留）按年龄（超过 `temp_file_max_age_seconds` 才删，新鲜文件可能是进行中的写入）；② 任务工件目录按保留天数（mtime ≤ now - 30 天即过期），`.keep` 标记文件可人工保护；③ 基准运行目录按数量（只保留最近 N 个）。三种类别各自独立、可单独测试。
2. **dry_run 是清理服务的默认安全模式**：默认只统计不删除（`CleanupStats` 计数 + `bytes_freed`），真正删除要显式 `dry_run=False`——把"破坏性操作"变成"先看后删"，且测试可以在 dry_run 下断言统计、在删除模式下断言文件消失。
3. **mtime 是"最近修改时间"而非"创建时间"**：测试用 `os.utime` 把 mtime 改成任意过去时间来控制"年龄"，配合注入 `now`（服务构造参数）实现完全确定性——不依赖真实时钟，删除逻辑可在毫秒级测试里验证 30 天保留期。边界条件（恰好等于阈值）也纳入测试。

### 检查问题（请用自己的话回答）
为什么"新鲜临时文件不删"？如果删除了正在写入的 `.tmp-*.part`，ArtifactStore 的原子写会发生什么？

### 已知限制
- 清理服务目前是独立基础设施模块（含测试），**尚未接入 CLI/API/定时任务**——接入方式建议在 P06-06/07 本地部署时一并考虑（worker 启动或独立 cron/调度）；
- 基准运行目录的"保留最近 N 个"按目录 mtime 排序，同一 run 内部分完成（无 report.json）的运行也会计入保留名额，属可接受的简化。

---

## P06-04 ✅：生产配置与密钥管理（settings profiles + 密钥卫生）

**产物**：`src/invest_research/settings.py`（`is_placeholder_secret` + `_production_secrets_guard` model_validator）、`tests/test_secrets_config.py`（11 测试）、`.env.example` 生产档位说明、`docs/10-RUN-GUIDE.md §7 生产配置与密钥管理`。

### 3 个知识点

1. **占位符检测是"生产 fail-fast"的关键**：`is_placeholder_secret()` 用特征标记（`your-` / `placeholder` / `change-me` / `xxx` / 空串 / 恰好 `secret`）识别"仍是占位符"的密钥。`environment=production` 时在 `Settings` 的 `model_validator(mode="after")` 里逐个校验 LLM_API_KEY / SERPER_API_KEY / SEC_USER_AGENT_CONTACT——**配置错误在进程启动瞬间暴露**，而不是跑到一半才发现带占位符跑生产。
2. **profile 是"同一套字段、两套行为"**：`ENVIRONMENT=development` 允许占位符（本地演示/测试便利），`production` 严格拒绝——同一个 Settings 模型，用环境变量切换校验强度，不改代码。这是 12-factor 配置"环境决定行为"的实践。
3. **密钥卫生可以写成自动化测试**：扫描 `.env.example` 不含真实密钥样式（`sk-*`、32+ 位长串、AWS/GitHub PAT 正则）、`.gitignore` 忽略 `.env` 且放行 `.env.example`、`compose.yml` 用 `${VAR:-占位符}` 注入且无真实密钥——把"密钥不进镜像/Git/日志/manifest"从口头约定变成机器可断言的回归。

### 检查问题（请用自己的话回答）
为什么占位符校验放在 `model_validator(mode="after")` 而不是把 `llm_api_key` 的 validator 直接设为必真值？development/production 两种档位下，同一字段的校验规则为什么可以不同？

### 已知限制
- 占位符检测基于特征标记，理论上真实的短密钥若恰好含 `your-` 等字样会被误判（概率极低，且只影响启动报错、不泄露）；
- manifest/日志脱敏沿用 P05-05/P05.5 的既有实现，本任务只新增了配置层防线与仓库级测试，未改动运行时脱敏逻辑。

---

## P06-05 ✅：本地 OpenTelemetry 链路查看（collector/exporter 配置 + 离线测试）

**产物**：`infrastructure/observability/tracing.py`（OTLP 端点/批量导出/`span()` 助手）、四层打点（API middleware `api.request`、Celery task `worker.process`、`flow_wiring` `flow.run`、`real_tools._timed` `tool.*`）、`deploy/otel-collector.yaml`（本地 collector 配置）、`tests/test_tracing_local.py`（9 测试）、`.env.example` OTEL 变量、pyproject 增加 `opentelemetry-exporter-otlp-proto-http`。

### 3 个知识点

1. **trace 关联必须真正跨过消息边界**：API span 的 W3C `traceparent` 先写入 Transactional Outbox payload，relay 投递时放入 Celery headers，Worker 从 headers 提取 parent context 后再开 `worker.process`。只在同一进程里嵌套 span 不能证明跨进程链路正确。
2. **OTel SDK 的 Once 守卫坑**：`set_tracer_provider` 默认只允许设置一次（`_TRACER_PROVIDER_SET_ONCE` 内部 Once 对象），测试里多次 `setup_tracing` 会静默失败（只打 warning、provider 不换）。解法是在 `setup_tracing` 里重置 `_done` 守卫，得到"最后一次调用生效"语义——对测试隔离和进程内重配都是正确行为。
3. **no-op provider 是零成本的默认**：所有 span 打点都在真实代码路径里（middleware、task、runner、工具包装），但**未调用 `setup_tracing` 时 OTel 用 no-op provider**，span 创建零开销——所以普通测试不加任何 tracing 依赖也能跑，打点是"可选观测"而非"强制副作用"。

### 检查问题（请用自己的话回答）
为什么"工具 span 放在 `_timed` 计时上下文里"而不是在 8 个工具调用点各写一行？`ExitStack` 嵌套多个 context manager 的好处是什么？

### 已知限制
- 本地 collector 配置已提供（`deploy/otel-collector.yaml`），但**未实际启动 collector/导入 compose**（属 P06-06/07 本地部署范围，本任务只做离线配置与测试）；
- 队列消息头与 Outbox 持久化已接入；真实 Redis/Celery/collector 上的 trace 查询留待 P06-07 Docker smoke。

---

## P06-05A ✅：SEC Company Facts → Analysis 真实数据流

**产物**：`infrastructure/real_tools.py` 三路并行预取、`PrefetchResult.financial_facts_summary`、Analysis Task 的 `{financial_facts}` 硬约束、SEC filed-date 截断测试。

### 3 个知识点

1. **period_end 不等于数据可得日**：历史截止日验证必须看 SEC 条目的 `filed`，否则后来修订的旧期间数据会造成未来数据泄漏。
2. **先确定性选数，再让 Agent 解释**：系统用 `concepts_v1.json` 选择白名单 concept，每个指标只保留最近两个可比期，Agent 只能消费这份 JSON，不凭记忆填数。
3. **并行化只用于互不依赖的 I/O**：SEC submissions、Company Facts 和 Serper 在已知 CIK 后可并行；Analysis 仍依赖 Research，Writer 仍依赖 Analysis，不改变业务因果顺序。

### 检查问题

为什么只用 `period_end <= as_of_date` 过滤 Company Facts 仍可能引入未来信息？

---

## P06-06 ✅：完整本地 Docker Compose observability profile

**产物**：`compose.yml`（新增 observability profile：prometheus/grafana/otel-collector/jaeger）、`deploy/prometheus/prometheus.yml`、`deploy/grafana/provisioning/datasources/prometheus.yml`、`deploy/grafana/provisioning/dashboards/dashboard.yml`、`deploy/otel-collector.yaml`（更新：Jaeger 导出 + health_check）、`tests/test_compose_observability.py`（12 测试）。

### 3 个知识点

1. **compose profile 是"按需启动"的编排边界**：`profiles: ["observability"]` 让观测组件只有在 `docker compose --profile observability up -d` 时才启动——基础服务（postgres/redis/api/worker/streamlit）行为完全不变，本地日常开发不拉取几百 MB 的观测镜像。profile 是 Compose 的"可选服务组"，不是单独的 compose 文件。
2. **observability 的配置面是"数据文件"而非代码**：Prometheus 的 `scrape_configs`、Grafana 的 `datasources/dashboards provisioning`、OTel Collector 的 `exporters` 都是挂载进容器的只读 YAML/JSON——容器是固化版本镜像，配置随仓库版本化。Grafana datasource 的 `uid` 必须与 dashboard JSON 中引用的 `uid` 一致，才能让 dashboard 自动绑定数据源。
3. **健康检查"探测对象"要与服务类型匹配**：Postgres 用 `pg_isready`、API/Streamlit 用 HTTP 探活、Prometheus/Grafana/Jaeger 用 `wget` 探测自带 health 端点、OTel Collector 官方镜像是 distroless（无 shell）——healthcheck 用非 shell 的 `["CMD", "/otelcol", "--version"]` 探测进程存活，真实健康由配置里的 `health_check` extension（13133）提供。观测栈默认 `FLOW_MODE=fake`，绝不在本地启动时触碰真实 LLM/Serper。

### 检查问题（请用自己的话回答）
为什么"观测组件用 compose profile 隔离"比"直接加进默认启动列表"更适合本地开发？Grafana datasource 的 `uid` 在 provisioning 里起什么作用？

### 已知限制
- Jaeger 使用 all-in-one 内存存储（重启丢失链路），仅用于本地查看，不用于生产；
- 观测镜像体积较大（Prometheus/Grafana/Jaeger），慢速网络下首次 `pull` 耗时较长（本机已实测网络间歇性超时，需重试）；
- 完整业务 smoke（创建任务→Worker→报告下载→指标/链路查询）留待 P06-07。

---

## P06-06A：每任务 fast/deep 研究档位（前端 + 后端持久化 + Worker 路由）

**产物**：`domain/models.py` `ResearchProfileMode` + `ResearchRequest.research_profile`、`migrations/versions/0007_research_profile.py`、ORM/Store/DTO 持久化、`LiveResearchFlowRunner` 按任务档位动态切换（fast 关闭思考模式）、`frontend/pages/1_创建投研任务.py` 档位单选（UI 默认 fast 且显式传值）、`frontend/render.py` 档位徽章（⚡ 快速 / 🔬 深度）、列表/详情展示。

### 3 个知识点

1. **"UI 默认值"与"领域默认值"可以是两个不同决策**：领域模型 `ResearchRequest.research_profile` 默认 `deep` 是为了兼容旧 API 客户端（不传字段也不出错）；前端 UI 默认选 `fast` 是产品决策（推荐、低成本演示），且**显式把所选值写入请求体**——前端不依赖领域默认值，两侧语义独立、都清晰。
2. **档位是"预算"而非"能力开关"**：fast 只是更少的迭代/重试/思考模式预算（`ResearchProfile.for_mode`），不是降级 AI 质量保证——避免"fast=假结果"的误导。
3. **按任务档位动态覆盖，不改全局环境变量**：`run(request)` 里按 `request.research_profile` 解析当前预算（fast 关闭 `enable_thinking`），构造时 `profile` 只是回退默认——单 worker 串行 + 每 job 一个 runner 实例保证安全。

### 检查问题（请用自己的话回答）
为什么"前端 UI 默认 fast"和"领域模型默认 deep"不冲突？把两个默认值分开放置各自解决了什么问题？

### 已知限制
- 迁移 0007 已在本机 Docker PostgreSQL 真实执行 upgrade → downgrade → upgrade 通过；未在 CI 环境验证；
- 前端未做"档位变更需确认"的二次确认（低风险，留待后续 UX 任务）。

---

## P06-06B：实时步骤状态与最小前端进度

**产物**：`application/progress.py`（ProgressSink Protocol + 00-07 步骤常量）、`infrastructure/db/progress.py`（SqlProgressSink：短事务/幂等创建/合法状态转换/fail_all_running_steps）、`flows/research_flow.py`（fake Flow 步骤边界标记）、`flow_wiring.py`（live CrewAI TaskStartedEvent + Task 完成回调标记）、`worker.py`（mark_running 时初始化步骤、终态清空 current_step、失败收口 running）、`ExecutionRecorder` 改更新/补全（不重复插入）、`frontend/render.py` 当前阶段中文映射 + 步骤紧凑图标 + 旧任务兼容。

### 3 个知识点

1. **真实步骤边界与"假装有进度"的边界**：CrewAI 1.6.x 提供 TaskStartedEvent（task.py 开始 emit）和 Task 完成回调（同步调用）——能精确标记 Agent 步骤开始/完成；没有独立 documents Task，所以 `03_documents` 在 Research Task 完成后由 runner 标记（不提前 running）。**绝不解析日志文本假装进度**。
2. **短事务 + 尽力而为的进度写入**：SqlProgressSink 每个方法独立短事务；Flow/Worker 侧捕获 `StepRecordError` 只记脱敏日志——进度写入失败**不得把成功任务误报失败**。"进度是观测，不是业务结果"。
3. **幂等创建与状态机防护**：Worker mark_running 时 `initialize_steps` 幂等创建 00-07（复用 `uq_workflow_steps_job_step`）；`mark_step_running` 只允许 pending/failed_retryable → running；`mark_succeeded` 只允许 running → succeeded——重复 Celery 投递不重复插入、不越权迁移。ExecutionRecorder 从"插入"改"更新/补全"避免与实时进度冲突。

### 检查问题（请用自己的话回答）
为什么"进度写入失败不能把成功任务误报失败"？SqlProgressSink 的短事务 + 应用边界捕获异常各起了什么作用？

### 已知限制
- live 模式引导（公司解析 → prefetch → Crew kickoff → 门禁 → manifest）的步骤标记已接线，但**真实 live 全链未在本轮验证**（Docker smoke 只跑了 fake；真实 LLM/SEC/Serper 付费调用需用户明确授权）；
- live Crew 的 `03_documents` 由 runner 在 Research Task 后统一标记（Crew 内无独立 documents Task），粒度是"文档处理阶段完成"而非每个文档逐个标记；
- 测试期间曾误用 live 模式创建任务（.env 为 FLOW_MODE=live），已改用临时 `FLOW_MODE=fake` 覆盖；残留 running 任务（73ec29cf/e38d85f0）需用户清理。

---

## P06-06B 收口：步骤状态不变量 + 取消清理 + 前端中国时区 ✅

**产物**：`tests/test_progress_invariants.py`（10 条不变量）、`SqlProgressSink` 唯一 running 条件 + attempt_count 原子 +1 + 终态清空 current_step、`CancelStepCleanup` 端口 + `cancel_pending_steps`（running/pending→skipped）、`_RepoLoader` 请求加载后立即标记 00 成功、`tests/test_cancellation_cleanup.py`、前端中国时区 `format_cn_time`（Asia/Shanghai，简化格式 `26-08-16 18:21`）+ 列表/详情开始/结束/耗时 + `tests/test_frontend_cn_time.py`。

### 3 个知识点

1. **"唯一 running"不变量放在 SQL 条件里，而非应用层先查后改**；`current_step` 只在行更新成功后才设置。
2. **current_step 是"派生视图"而非独立状态**：步骤进入终态时用 `WHERE current_step = step_name` 条件更新，只清仍指向该步骤的指针。
3. **后端统一存 UTC，前端只做展示层时区转换**：`format_cn_time` 转 Asia/Shanghai 并简化格式，不改后端存储。

### 检查问题（请用自己的话回答）
为什么"唯一 running"必须放进 SQL 的 WHERE 条件而不是用"先 SELECT 再 UPDATE"实现？`format_cn_time` 为什么只做展示层转换而不改后端存储？

### 已知限制
- 已收口 2 个误触发孤儿任务（e38d85f0/73ec29cf → cancelled、current_step=null、无 running、pending 已 skipped）；未删除数据库行/工件；
- fake smoke（fast+deep）验证最多一个 running、current_step 与 running 一致、8 步终态合理、attempt_count≥1、无 SEC/Serper/模型调用；
- 模型配置仅复核：.env 精确模型名 Research/Analysis=qwen3.6-flash、Writer=qwen3.5-plus、thinking=false；容器已读取新配置；未硬编码/未改 .env/未做逐角色 thinking 与真实连接测试。

---

---

## P06-06C：接通 Prometheus 业务指标与 Jaeger 真实 trace 数据链 ✅

**产物**：OTLP endpoint 规范化 + 业务指标事件 + Worker 多进程指标端点 + Prometheus/Grafana 修复

### 3 个知识点

1. **prometheus_client 多进程模式（multi-process registry）**：Celery prefork 的每个子进程若各自直接创建 Counter/Gauge/Histogram，这些数据只落在进程内，Prometheus 采集时看不到。必须在任何 prometheus_client 导入前设置 `PROMETHEUS_MULTIPROC_DIR`，让每个子进程写独立 pid_*.db 文件；父进程的 HTTP server 用 `MultiProcessCollector` 按 label 汇总所有 .db 文件。这是"API 容器 /metrics 看不到 Worker 指标"的根因与解法。

2. **OTLP HTTP 导出端点必须含完整路径**：`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` 只是基础地址，`OTLPSpanExporter(endpoint=...)` 要求完整 URL 含 `/v1/traces`，否则 Collector 返回 404 Not Found。统一用 normalize 函数补路径，并正确修剪末尾斜杠、避免重复拼接。

3. **指标事件的语义防重复计数**：业务指标只能在确定性状态转换成功（如 SQL rowcount>0、乐观锁成功）后计数，而不是在"尝试执行"处计数。这样 at-least-once 投递与重复 Celery 消息不会让 Counter 明显多计。

### 检查问题（请用自己的话回答）
为什么 Worker 的 `PROMETHEUS_MULTIPROC_DIR` 环境变量必须在任何 `prometheus_client` 导入之前设置？如果 API 和 Worker 在同一个容器但不同进程，这套多进程机制还适用吗？

### 已知限制与风险
- Worker 独立 endpoints 与 API 分别被 Prometheus 采集，同名指标自动合并求和；但跨 target 的求和无法区分来源进程，需依赖 job label 区分。
- 当前工具失败已分类为可重试（`ToolError.is_retryable`）即计入 tool_retries_total，未来接入 tenacity 重试循环后应在 before_sleep 回调处计数，避免双重计数。
- 验证时宿主机 .env 含 FLOW_MODE=live 导致意外真实调用；Docker compose 验收必须用 `--env-file` 显式覆盖为 fake。

---

## P06-08：PostgreSQL 与工件备份恢复演练 ✅

**产物**：`scripts/backup_snapshot.py`、`scripts/restore_snapshot.py`、`docs/15-P06-08-BACKUP-RUNBOOK.md`、离线单测 ×10

### 3 个知识点

1. **逻辑备份（pg_dump custom）与工件快照必须"同一时间点"对齐**：数据库表通过 `job_id` 外键关联，全库 `pg_dump -Fc` 天然保证跨表一致性；工件目录按 `<job_id>/` 组织，与 `artifacts.storage_uri` 一一对应。备份时先导出 DB 再复制工件卷，manifest 记录每个文件的 sha256，恢复前先 `--verify` 校验 checksum 再写入。
2. **恢复演练的验证闭环必须以"外部可观察状态"为准**：DROP 该 job 行（级联删 steps/artifacts 元数据）+ 删容器工件后，API 必须返回 404（证明破坏真实生效）；恢复后用 API 查询 steps 数量/状态、artifacts 清单、下载 MD/PDF 并比对 sha256 与恢复前基线一致——而不是只看"表里有数据"。
3. **`docker compose exec/cp` 使用服务名而非容器名**：compose v2 中直接 `docker compose exec worker ...` 用 compose 服务名（`worker`/`postgres`）即可，硬编码 `agent-worker-1` 这类容器名会在 compose 重建后失效；脚本保持"服务名"作为唯一标识更健壮。另：`python scripts/restore_snapshot.py` 直接运行时 `sys.path` 不含仓库根，模块间导入需避免（脚本已改为自包含）。

### 检查问题（请用自己的话回答）
为什么"备份校验"必须在恢复前做？为什么只验证"数据库表行数恢复"不够，还必须通过 API 下载报告并比对 sha256？

### 已知限制与风险
- 备份为手动触发（无 cron 定时）；RPO 取决于备份频率，RTO 实测秒级（dump 52KB + 工件 1.7MB）。
- 全库 `pg_restore --clean` 会先 DROP 再 CREATE，只适合演练/灾难恢复场景；单 job 级恢复只覆盖工件，不覆盖 DB 行（需配 `--restore-db`）。
- `backup/` 目录已被 `.gitignore` 排除，快照内容不入库；恢复脚本 `--restore-artifacts` 只动目标 job，不影响其他 job。

---

## P06-09：Pack 交接收口 + 统一 schema 解析/错误分类 + GitHub Actions CI（✅ 已完成）

**产物**：`agents/pack_parsing.py`（统一边界解析）、`application/failure_classifier.py`（稳定错误分类）、`migrations/versions/0008_failure_stage.py`（research_jobs.failure_stage）、`.github/workflows/ci.yml`（独立 CI，不把 docker-image 当 CI）、`tests/test_pack_contracts.py`（离线契约测试 ×20）

### 3 个知识点

1. **"不只依赖提示词保证 schema" = 把解析做成确定性契约**：LLM 输出可以是 CrewAI TaskOutput（pydantic 已绑模型）、dict、JSON 文本、带 ```json 代码围栏的文本；统一 `parse_pack_output` 按固定顺序（pydantic → json_dict/exported → raw → str）读取，`_reject_extra_fields` 在 Pydantic 默认 `extra=ignore` 之外显式拒绝多余字段。解析侧（flow_wiring）与 Writer 的 ArtifactReader loader 共用同一读取顺序——保证"解析到的 pack"与"Writer 工具读到的 pack"完全一致，任务要求中 Writer 读上游真实 pack（非 readable 占位符）由此落地。
2. **失败必须映射为稳定 error_code + 脱敏消息 + failure_stage，且不能只靠异常类型猜**：`classify_failure` 按异常类型/消息关键词分类（TIMEOUT / RATE_LIMITED / NETWORK_TRANSIENT / UPSTREAM_5XX / AUTH_ERROR / SCHEMA_INVALID / INTERNAL_BUG 兜底），消息用 `sanitize_message` 截断 500 字符并隐藏 sk-/api_key/路径；`LiveFlowExecutionError` 携带 error_code/failure_stage 传播到 Worker，`mark_failed` 落库 `research_jobs.error_code/error_message/failure_stage`——任务"失败任务展示"与"历史 stale running 收口"都能定位到具体阶段。
3. **CI 是"可重复的质量门禁"，不是"构建镜像"**：独立 workflow 把 Ruff、mypy src、离线 pytest（`SKIP_DB_TESTS=1`，跳过 testcontainers 的 DB 测试）、迁移+DB 集成（GitHub Actions `services.postgres` 真实 PostgreSQL）拆成 4 个 job；全部 job `FLOW_MODE=fake` 禁止真实出网调用，不 resolve 任何 secrets；`concurrency.cancel-in-progress` + `timeout-minutes` 让失败按 job 名可直接定位。本地验证命令与 CI 完全一致（`ruff check src tests migrations scripts` + `mypy src` + `FLOW_MODE=fake SKIP_DB_TESTS=1 pytest` → 893 passed, 19 skipped）。

### 检查问题（请用自己的话回答）

为什么 Writer 侧必须用"可注入的 ArtifactReader loader"（从上游 Task 的 output 读 pack）而不是直接相信提示词"上游 pack 已在 context 里"？ArtifactReader 读到 content 与 not_found 两种结果分别给 Writer 什么指导？

### 已知限制与风险
- 本机完整 pytest 的 DB 集成测试（test_db_base/test_job_repository/test_migration_*.py）因 testcontainers 网络无法连 registry-1.docker.io 而报错，与 docs/16 既有记录一致；CI 的 migrations job 在 GitHub Actions 内置 PostgreSQL 上覆盖此部分（需 push 后在 Actions 内验证）。
- stale running Job 恢复在 worker 启动时执行（`_run_stale_job_recovery`），用 `research_jobs.status='running'` 全量扫描；worker 重启真实执行验证留待 CI/部署环境确认（离线测试覆盖逻辑分支）。
- 本任务按 .clinerules 只做 05 路线图中的一个 ID（P06-09）；P06-10（100 次基准）为下一候选任务，未开始。


---

## P06-09A：FinancialAnalysisPack 结果完整性状态（✅ 已完成）

**产物**：domain/models.py（AnalysisCompleteness StrEnum + schema_version=v2 + unavailable_reason + 跨字段 validator + v1 兼容读取）、prompts/analysis_prompt_v2.md / writer_prompt_v2.md、prompts/loader.py（v2 切换）、agents/analysis_task.py / writer_task.py（description 同步）、flows/quality_classifier.py（unavailable 含内容兜底）、tests/test_analysis_completeness.py。

### 3 个知识点

1. **业务语义用 StrEnum + 跨字段 validator 表达，而不是散落的 if**：complete 须有 facts/metrics、partial 须有 limitations、unavailable 须有 reason 且不得含数据；缺字段/多余字段/状态矛盾在 Pydantic 层统一收敛为 SCHEMA_INVALID。
2. **向后兼容读取 = 宽进严出**：model_validator(mode="before") 把缺 schema_version 且 version 以 _v1 结尾的输入标记为 v1 走宽松分支；新 schema_version=analysis_pack_v2 才严格校验——42 处 analysis_pack_v1 构造与历史工件全部兼容。
3. **unavailable 是合法业务结果，不是系统异常**：Writer 只允许说明数据不可用、禁止推断数字；质量门禁加 unavailable_with_content CRITICAL 兜底，双层保障不得伪造指标。

### 检查问题（请用自己的话回答）

为什么 completeness=partial 必须强制 limitations 非空？如果允许 partial 且无说明，下游 Writer 会怎样错误组织报告？

### 已知限制与风险
- 只升级 FinancialAnalysisPack；ResearchPack/ReportDraft 未加状态字段（任务范围约束）。
- 提示词切到 v2（analysis_prompt_v2 / writer_prompt_v2）；manifest 中 prompt hash 变化，触发既有版本化重算语义。
- 未重新生成历史工件。

---

## P06-09B：统一 PackBoundary（✅ 已完成）

**产物**：agents/pack_parsing.py（PackSourceKind、BoundaryError、identify_source_kind、extract_candidate、PackBoundary）、infrastructure/flow_wiring.py（_to_packed 接入）、tests/test_pack_boundary.py。

### 3 个知识点

1. **边界解析的关键是区分来源类别，而不只是解析格式**：identify_source_kind 分为 final_answer / tool_params / action_input / plain_text；Action Input 与工具参数直接拒为 NOT_A_PACK，解决把工具调用过程当最终答案的故障。
2. **分层校验顺序决定错误可定位性**：JSON/结构 → 多余字段 → schema → 语义跨字段四层返回 BoundaryError（error_code/stage/field/expected/actual/脱敏 detail）；value_error 标记 semantics 阶段不进入格式修复——业务事实错误禁止修复器发明/改写数据。
3. **有限修复 = 至多一次、只修结构、失败返回原始错误**：max_repairs=1 且仅 schema 阶段可修复；fixer 只拿到原始文本 + 结构化错误（无法调用 SEC/Serper/文件/计算器）；修复一次仍失败时返回最初的稳定错误分类。_sanitize 截断 + 去绝对路径 + 打码密钥字段。

### 检查问题（请用自己的话回答）

为什么 semantics 错误（业务跨字段矛盾）不允许走一次 LLM 修复，而 JSON_INVALID/EXTRA_FIELD 允许？允许修复器改字段结构，和允许修复器补数据的本质区别是什么？

### 已知限制与风险
- flow_wiring 的 _to_packed 以 max_repairs=0（确定性 parse）接入，未注入 LLM fixer；调用方可按需注入一次修复回调，框架已限制至多一次且不可调外部工具。
- PackBoundary 为单一入口；parse_pack_output/dump_task_output 保留（同一读取顺序），公共 API 未删。
- 未 push；阶段级 Checkpoint 记录为 deferred/后续可选升级，不实施。

---

## P06-09C：Prometheus + Grafana + Jaeger 可观测性增强（✅ 已完成）

**产物**：metrics.py/metrics_events.py 18 个新指标 + api/worker/flow_wiring/execution 接线 + logging.py（P05-05 脱敏 + P06-09C trace 关联）+ 7 组 Grafana dashboard + scripts/run_observability_smoke.py + deploy/compose.observability-smoke.yml + docs/18-P06-09C-VALIDATION.md。

### 3 个知识点

1. **prometheus_client 多进程模式的时序关键点**：`PROMETHEUS_MULTIPROC_DIR` 必须在任何 prometheus_client/metrics 导入**之前**设置。若 worker.py 里的 setdefault 晚于 metrics.py 的模块导入，prometheus_client 落单进程模式、Celery 子进程不写 pid_*.db，父进程 9101 的 MultiProcessCollector 就聚不到业务指标——本次 smoke 实测在 Prometheus 看到 pending/cancelled 却缺 succeeded/failed，正是这个根因；把 setdefault 移到 worker.py 模块最顶、所有 import 之前即修复。
2. **trace 与日志通过 trace_id/span_id 关联**：execution._log_job_event 在 job_flow_succeeded/job_flow_failed 事件上带 trace_id/span_id（从当前 OTel span context 读），Jaeger 链路与日志同 id 可 join；agent span 用 `get_tracer("agent").start_as_current_span("agent.{role}")` 而非独立 start_span，才真正成为 flow.run 的子 span（父 span 上下文必须作为 current 才自动继承）。
3. **Grafana dashboard 要可测、Histogram 必须用 _bucket**：把 dashboard JSON 写测试（7 Row 顺序、datasource uid、timezone、refresh、PromQL 指标名/`histogram_quantile`/无 job_id/company/error_message label），避免"肉眼看"；histogram_quantile 只能对 `_bucket` 序列计算，Prometheus 白名单 regex 必须显式列出 `_bucket/_sum/_count` 后缀，否则面板无数据。

### 检查问题（请用自己的话回答）

为什么"环境变量必须在 prometheus_client 导入前设置"是一个**进程/导入顺序**问题而非"A 代码写错"？如果 Worker 换成 threads 或 threads 模式（不 prefork），这个问题还会以同样方式出现吗（提示：MultiProcessCollector 针对 fork；单进程模式时指标落在进程内是可见的）？

### 已知限制与风险

- smoke 的"故障注入（schema 错误 / Pack 修复成功/失败）"在 fake flow 无注入路径，经 `--include-component-scenarios` 开关默认关闭；组件级场景由 tests/test_pack_contracts.py 单独覆盖。
- 取消场景存在竞态：任务可能在取消前已被 worker 消费完成，终态为 succeeded 而非 cancelled；smoke 只断言 DELETE 返回 200。
- Jaeger 为 all-in-one 内存存储，重启丢失链路（仅本地查看）。
- 本机未跑全量 pytest（与 live Docker testcontainers 冲突超时）；CI 覆盖（SKIP_DB_TESTS=1 893 passed 基线）。
- 未 push 任一 P06-09C commit；Checkpoint 记录 deferred；P06-10（100 次基准）未开始。

## P06-10A: benchmark framework & calibration (2026-08-18)
- Learning: e2e HTTP benchmark vs in-process; denominator isolation; binary PDF download
- Question: why not claim workflow_success_rate as live_agent_success_rate?
- Limits: prometheus snapshot not queried yet; control scenarios only cancel

## P06-11A: LLM Token、延迟与调用链可观测性准确性收口（✅ 已完成 2026-08-18）

**任务目标**：修复 Prometheus/Grafana 的 LLM 指标不准确问题（Crew 汇总 usage 三倍计数、duration 未 observe、失败次数缺失、dashboard 缺 Token 总量/分位数），并补齐 Jaeger 真实 LLM 调用 span。

**修改文件**：
- `infrastructure/observability/llm_call_observer.py`（新增）：LlmCallObserver 订阅 CrewAI 官方 LLMCallStarted/Completed/Failed 事件，在每次真实模型调用边界写 llm_requests_total / llm_request_duration_seconds / llm_tokens_total / llm_usage_missing_total 与 `llm.request` span；role 由 Agent.role 可读名映射为 research/analysis/writer；token 只从事件 `usage` 正式字段提取（input/output/cached_input）。
- `infrastructure/flow_wiring.py`：kickoff 时订阅 LLM 事件；`_record_agent_metrics` 删除把 CrewOutput.token_usage 复制给三个角色的三倍计数逻辑（保留 Agent 耗时指标）。
- `deploy/grafana/provisioning/dashboards/research.json`：LLM Row 增加 Token 1h/24h 总量、调用成功/失败 1h/24h、P50/P95/P99 延迟、usage missing 1h、provider/model/role 筛选变量；全部 PromQL 使用 rate/increase，无数据时 Grafana 显示 No data（不伪造 0）。
- `tests/test_llm_observability.py`（新增 20 用例）+ `tests/test_grafana_dashboard.py`（P06-11A 校验已含）。

### 3 个知识点

1. **CrewAI 1.6.1 有官方 LLM 事件总线（非 monkey patch）**：`crewai.llm.LLM` 在每次真实 模型调用边界经 `crewai_event_bus` emit `LLMCallStartedEvent` / `LLMCallCompletedEvent` / `LLMCallFailedEvent`；事件 `LLMEventBase.__init__` 把 `from_agent` 转换为 `agent_role` （Agent.role 可读名），再映射回内部稳定 role。这是比任务级 token_usage 汇总更精确的 “每次真实调用”入口。
2. **事件字段必须对照安装版本源码验证**：实际 `LLMCallCompletedEvent` 的 `usage` 是正式 字段（`usage: dict | None`），`LLMCallFailedEvent` 有 `model` 字段，而**都没有** `duration` 字段；不要依赖 `get_extra()`（BaseEvent 默认忽略 extra）。耗时用 Started→Completed/Failed 的本地 `time.monotonic()` 配对实测。
3. **准确计数不等于把汇总拆分**：修复方案不是“把 Crew 汇总 usage 平均/复制到三个角色”，而是订阅每次真实调用事件，让 usage/token/耗时天然落在发起该调用的 Agent 角色上——一次调用只在 Completed 或 Failed 中记录一次，从根本上消除三倍计数。

### 检查问题（请用自己的话回答）

为什么说“把 CrewOutput.token_usage 复制给三个角色”是语义错误而不只是数值错误？提示：一次 research task 内可能发生多次真实模型调用（plan/tool 循环/final answer），Crew 汇总 usage 无法告诉我们是哪次调用、哪个角色消耗了多少。

### 已知限制与风险

- 事件总线只覆盖 CrewAI 1.6.1 官方 emit 点；若 CrewAI 内部某些调用路径不 emit （如缓存命中/内部重试），该次调用不计入（保守不伪造）。
- Failed 事件若 `model` 字段缺失，回退最近一次 Started 的 model 配对；完全无 Started 配对时丢弃（不猜测模型名）。
- duration 用本地实测，不依赖事件字段；同 (role, model) 串行调用冲突时取最近一次。
- 未执行真实 DeepSeek/Qwen 付费调用（遵守限制）；未 push；未运行 100 次基准；未执行 docker compose down -v。

### 下一任务建议

- 按 docs/12 末尾给出的 Docker 重建与一次 fast live 验收步骤验证真实调用下的指标/span 准确性后，再评估 P06-12（README 演示）或 Checkpoint 升级。

## P06-11B: 修复 DeepSeek 与 CrewAI 结构化输出不兼容（✅ 已完成 2026-08-18）

**任务目标**：修复 DeepSeek 普通 Chat Completion 在 CrewAI output_pydantic 输出转换阶段触发 `beta.chat.completions.parse(response_model=...)` 发送不支持的 response_format 导致 HTTP 400（"This response_format type is unavailable now"）。不修改金融业务含义、不放宽 Pack 必填字段、不触碰工具调用能力。

**根因**：CrewAI 1.6.1 的 `task.py:_export_output`（L768）中 `output_pydantic` 与 `output_json` 都进入同一个 `convert_to_model`；当 Agent 最终文本无法直接通过 Pydantic 校验时进入 `Converter.to_pydantic`（`converter.py`），在 `llm.supports_function_calling()` 时调用 `llm.call(..., response_model=self.model)`，触发 OpenAI SDK `beta.chat.completions.parse` 并发送 json_schema 类型的 response_format。DeepSeek 普通 Chat Completion 不支持该 response_format → 请求阶段 400。这不是 Pack 字段设计问题，也不是 max_iter / 网络重试能解决的。

**修改文件**：
- `agents/llm_factory.py`：新增 `StructuredOutputMode`（NATIVE_PYDANTIC / JSON_TEXT_LOCAL_VALIDATION）与 `structured_output_mode(config)`，按显式 `LLM_VENDOR` 集中决策（qwen→原生，deepseek/generic→JSON 文本 + 本地校验），不根据 base_url 猜测。
- `agents/analysis_task.py`、`agents/writer_task.py`：JSON 文本路径下不绑定 `output_pydantic`（也不改用 `output_json`——CrewAI 1.6.1 中二者同一 Converter 路径）；提示词追加"最终答案只能是一个 JSON object、不要 Markdown 围栏、不要解释文字、工具参数不能作为最终答案"；Crew 完成由现有 `PackBoundary` 本地解析。
- `domain/errors.py`：新增稳定错误码 `STRUCTURED_OUTPUT_UNSUPPORTED`（非重试）。
- `application/failure_classifier.py`：识别 response_format/json_schema 拒绝，优先于迭代耗尽分类。
- `infrastructure/flow_wiring.py`：kickoff 异常优先分类为 `STRUCTURED_OUTPUT_UNSUPPORTED`；同一次执行同时迭代耗尽时保留根因并记录前置信息。
- `tests/test_p06_11b_deepseek_structured_output.py`（新增 27 用例）。

### 3 个知识点

1. **CrewAI 的 output_json 与 output_pydantic 进入同一条远程转换路径**：`task.py` 中二者都是 `convert_to_model(result, output_pydantic, output_json, ...)`；不要以为换 `output_json` 就能避开 beta parse。对不支持 response_format 的供应商，唯一安全路径是不绑定任何 output_*，让 Agent 返回 JSON 文本，再由本地 Pydantic/PackBoundary 校验。
2. **"供应商能力"必须用显式标识决策，而不是 base_url 猜测**：同一 OpenAI-compatible base_url 可能同时被不同网关使用；用 `LLMConfig.vendor`（来自 `LLM_VENDOR` env）集中决定结构化输出能力，保证可测试、可审计。
3. **错误分类要保留最接近失败根因的一层**：response_format 400 发生在请求阶段，是比"迭代耗尽/网络瞬态"更直接的根因；即使同一次执行同时出现迭代耗尽，也应分类为 `STRUCTURED_OUTPUT_UNSUPPORTED`（不可重试）并在日志记录前置迭代信息。

### 检查问题（请用自己的话回答）

为什么在 CrewAI 1.6.1 中，即使把 Task 的 `output_json` 换成 `output_pydantic`（或反过来），DeepSeek 依然可能在输出转换阶段发出带 json_schema response_format 的请求？提示：两个参数最终进入哪一个函数，判断依据是什么？

### 已知限制与风险

- DeepSeek 输出质量仍依赖提示词约束（JSON object 无围栏/无解释）；本地 PackBoundary 会拒绝非法结构，但不会修复语义缺失。
- 未执行真实 DeepSeek/Qwen 付费调用（遵守限制）；真实行为差异需通过受控 live smoke 验证。
- `structured_output_mode` 是静态能力声明；若某供应商未来支持 response_format，需显式更新该函数（不允许静默放宽）。
- 未 push；未启动 Docker；未运行 100 次基准；未进入下一任务。

### 下一任务建议

- 在受控 live 环境下用 `LLM_VENDOR=deepseek` 跑一次 fast 任务，验证 Analysis/Writer 的 JSON 文本输出能被本地 PackBoundary 正常解析；再评估 P06-12（README 演示）或 Checkpoint 升级。
