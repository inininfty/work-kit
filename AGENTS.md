# AGENTS.md — 这个工作区的规矩

> 本文件是**壳**的一部分，随壳升级。你的经验不写在这里，写 `data/`。
> 规则要改，改本文件并同步 `kit.json` 的版本；否则下次升级会被覆盖。

## 一句话

这是一个「把任何事变成可验收推进」的工作壳：**账本记事实，闸门查声明的条件，你填经验。**
当前壳版本 **0.4.3**（与 `VERSION`、`kit.json` 同步）。

## 安装：交给 DSH，一次启动

用户说安装时读取 `START_HERE.md`；在目标项目直属 work-kit 中运行 `bin/start.py`。
入口自动配置、接入根规则、跑检查并显示状态；退出 0 且 READY 才报告装好。
退出 3 只问一次只读候选，确认全部后重跑加 `--confirm-readonly`；不猜边界。
自动寻找 Python 3.11+；缺依赖/权限时给具体缺项，不让用户手填 JSON，不擅自下载或改系统。
重复启动保留配置与数据；已有安装使用升级流程，不覆盖。详见 `START_HERE.md`。

## 日常命令（手不用碰 JSON）

```bash
python bin/kit.py status
python bin/kit.py task new "把它写清楚" --next "第一步"
python bin/kit.py task list --open
python bin/kit.py change new T-001 --files a.py --modified b.md --budget 100
python bin/kit.py change approve C-001
python bin/kit.py log C-001 --created a.py --modified b.md --complete
```

`log` 核实真实文件并生成哈希回执，再用同一可恢复事务写修改、清单和任务状态。
`task done` 必须带 `--change` 和文件证据，例如 `--evidence file:result.txt`；哈希不证明内容正确。
清单还没批准时 `log` 会被拒：先 `change approve`，再记修改记录。

## 删除：先 plan，再进回收站

```bash
python bin/kit.py remove a.txt b/          # 只打印计划，不动磁盘
python bin/kit.py remove a.txt b/ --yes    # 移进回收站
python bin/kit.py trash                    # 看回收站
python bin/kit.py restore 20261025-101500  # 放回并校验哈希
```

工作区根、工作区外、只读根、壳及其祖先、`.git`、链接/联接点、重叠目标**整单拒收**。
先保存 manifest，复制并核验全部文件和空目录，再移除源；恢复先预检全部冲突和哈希，原备份保留。

**两步确认（R14）**：`remove` 不带 `--yes` 时打印每一项的**绝对路径、类型、大小/文件数、合计**，
以及影响与恢复方式。把这张计划原样给用户，等他明确回「删除」或「确认」，再加 `--yes`。
覆盖 / 清空文件之前同理：先复制到 `.tmp/backup/<stamp>/`，没有备份不许截断。

## 升级：一条命令

```bash
python bin/kit.py update --source <新版本目录>   # 第一次：记住上游
python bin/kit.py update                          # 以后每次
```

它依次做：升级壳 → 重写规则指针 → 跑闸门。任何一步失败就停，不会继续往下走。

## 先判适用范围

普通答疑、相关只读检索和局部计算直接处理，不建工程任务、不审批、不跑工程检查；只读与工具自身安全要求仍有效。
保存/导出只增加文件操作登记、批准、路径/内容/格式检查；明确保存指令可作为该范围授权，不逐步重问，不自动增加领域审核。
修改项目、规则、配置、安装或发布进入下列工程流程；同题追问不反复建单，无关工程错误不阻塞普通讨论。

## 工程开工前（四步，缺一步就别动手）

1. 读 `config/kit.config.json`：工作区在哪、哪些目录只读。
   **没有这个文件就先跑 `bin/init.py`，不要猜路径。**
2. 用 `python bin/kit.py task new "标题"` 建任务；没有 id 不许开工。
3. 用 `python bin/kit.py change new T-001 --files ...` 出清单，等用户批准。
4. 跑 `python bin/check.py all`，全 pass 才动手。

## 推进方式：按需增加步骤

- 默认当前 Agent 在已批准范围内推进到交付；实质歧义先核验，仍影响目标、范围、权限、正确性或验收才问；不猜授权，不重复求批。
- 按不确定性、影响范围、可逆性和依赖选择步骤；讨论/计划/小样按实质歧义/多步依赖/关键假设启用，不确定性解除后恢复直接执行；分工需独立子任务、收益及授权。
- 长项目分段验收并在现有账本留恢复信息，不因周期长自动增加 Agent；先定位再读片段，同版本同范围有效证据按需复用。
- 验收用适当测试、diff、回读或真实运行；小样不代替正式结果。领域核验另按明确请求和适用协议判断；工程/保存登记、批准、必要检查及删除确认保留。
- 只提炼值得复用的经验，不强制每次加规则或检查；详细触发条件及行为验证边界见 `README.md`。

## 四条硬规矩

1. **清单外不写。** 任何新建/修改的文件，必须已经出现在一条已批准的清单里。
2. **结论带锚点。** 事实必须指回 `文件#L行`、页码、或命令输出。指不回去就写「推测」。
3. **只说跑过的。** 没跑过不写「已验证」；只跑过一次写「观察到」。
4. **破坏性操作先给计划、等一句确认。** 删除 / 覆盖 / 清空之前，先把「绝对路径 + 影响 + 是否可逆 + 恢复方式」
   写成计划给用户；确认词是「删除」或「确认」，不是「可以吧」「差不多」。

## 目录分工

| 路径 | 谁的 | 升级时 |
|---|---|---|
| `bin/` `core/` `schemas/` `AGENTS.md` `README.md` `kit.json` `install.cmd` | 壳 | 会被覆盖 |
| `data/` | 你的 | 永不触碰 |
| `config/` | 本机 | 永不触碰，也不进发布包 |

遇到未完成事务先 `python bin/kit.py recover`；恢复不改数学结论。

## 状态在哪

```
python bin/check.py status
```

按需生成，不常驻上下文。**不要在任何文件里抄状态**——抄了就会过期。

## 出错之后

先归类，再动手：

| 这条经验的性质 | 放哪 |
|---|---|
| 会改变下次的选择流程 | 本文件（并升版本） |
| 只是某条路线的细节 | `data/notes/` |
| 需要机器守住 | 加进 `bin/check.py` |
| 只是「发生过什么」的教训 | `data/negative-examples.json` |

## 经验怎么填

`data/negative-examples.json` —— 一条踩过的坑：

```json
{"id":"N-001","scenario":"当时在做什么","lesson":"下次怎么做","domain":"code"}
```

`data/patterns.json` —— 一条稳定有效的做法：

```json
{"id":"P-001","scenario":"什么场景","action":"怎么做","why_it_works":"为什么有效","confidence":"high"}
```

不设条数配额。用 `python bin/kit.py recall 关键词` 检索带 ID 的原始记录；命中不自动晋升规则。

## 按需读

| 文件 | 什么时候 |
|---|---|
| `README.md` | 第一次装、要发布时 |
| `schemas/*.json` | 不确定某行该写什么字段 |
