## ADDED Requirements

### Requirement: Harness 记录可审计的变更和验证证据
项目 SHALL 建立 Harness，分别记录架构边界、变更台账、验证命令、语料场景和每轮报告。`AGENTS.md` SHALL 只保存长期项目边界和强制验证要求，算法迭代、阈值调整和运行证据 SHALL 记录在 Harness，远程视频及大体积证据 SHALL NOT 提交到 Git。

#### Scenario: 完成一轮算法阈值调整
- **WHEN** 开发者根据校准集调整动态检测阈值并重新运行
- **THEN** 变更台账记录调整原因、前后配置、算法版本、关联报告和验证结论，而 `AGENTS.md` 不增加逐轮流水记录

### Requirement: 每轮自动发现并冻结全部课程 P 视频
Harness SHALL 从配置的 `/course/` 根地址递归发现同源目录中 basename 包含 `PPT` 且扩展名为 `.mp4` 的全部文件，规范化并去重 URL，并 SHALL 在运行前冻结本轮 inventory。实现 SHALL NOT 把提案阶段观察到的视频数量写成固定常量。

#### Scenario: 课程目录新增 P 视频
- **WHEN** 下一轮验收重新扫描时发现新的符合规则的视频
- **THEN** 新视频自动进入新一轮冻结 inventory 和测试分母，而已完成历史报告保持不变

#### Scenario: 目录中存在非 P 视频或跨域链接
- **WHEN** 发现器遇到文件名不含 `PPT` 的视频、非 `.mp4` 文件、越出允许路径前缀或不同主机的链接
- **THEN** 发现器不把该资源加入 P 视频 inventory，并记录过滤统计

### Requirement: Inventory 包含资源身份和处理状态
每个 inventory 条目 SHALL 记录规范化 URL、解码后的课程/文件名、可获得的 `Content-Length` 和 `Last-Modified`、duration、codec、fps、分辨率，以及发现、探测、处理、证据和复核状态。无法访问或处理失败的条目 SHALL 保留明确中文原因，不得从分母中静默移除。

#### Scenario: 某个远程视频无法解码
- **WHEN** 视频已被发现但探测或解码失败
- **THEN** inventory 和中文报告保留该 URL、失败阶段和原因，并把整轮状态标记为未完成或有阻塞

### Requirement: 全量运行支持限流、检查点和最终端到端复跑
Harness SHALL 对冻结 inventory 执行有界并发、超时和有限重试，并 SHALL 按视频保存检查点以支持中断恢复。所有课程 MP4 SHALL 直接从 URL 流式探测和解码，不得落盘原始视频、完整副本、MP4 预览或其他可还原完整视频的文件。开发迭代 MAY 使用 Git 忽略的小型元数据和特征缓存，但最终验收 SHALL 对冻结语料使用冻结算法和配置完成一次完整端到端运行。

#### Scenario: 全量运行中途终止
- **WHEN** runner 在部分视频完成后被中断并以同一 inventory 恢复
- **THEN** 资源指纹未变化的已完成项可跳过，未完成项继续处理，报告仍能区分缓存结果与最终端到端结果

#### Scenario: 处理远程课程视频
- **WHEN** runner 探测、检测或提取证据帧
- **THEN** 工具直接消费远程 URL 且工作目录中不产生 `.mp4`、完整视频副本或视频预览文件

### Requirement: 每个候选区间都有可回看的证据
Harness SHALL 为每个疑似动态区间生成起点、中点、终点附近的带时间戳静态证据帧和联系表。报告 SHALL 把证据关联到原始 URL 和具体时间，证据文件 SHALL 存放在 Git 忽略目录；Harness SHALL NOT 生成 MP4、GIF 或其他连续视频预览文件。

#### Scenario: 算法输出疑似视频播放区间
- **WHEN** 某视频产生一个动态区间候选
- **THEN** 复核者能从报告打开该区间证据并按 URL 与时间范围回到原视频复查

### Requirement: 复核区分确认、误报与不确定
Harness SHALL 保留算法原始输出，并 SHALL 允许复核者为候选独立标注 `CONFIRMED_VIDEO`、`CONFIRMED_SCROLL`、`FALSE_POSITIVE` 或 `UNCERTAIN`。AI MAY 进行初审，但证据不足的结果 MUST 标记为 `UNCERTAIN`，不得自动写成确认真值。

#### Scenario: AI 无法确认动态内容类型
- **WHEN** 证据不足以判断候选是视频播放、持续滚动还是普通动画
- **THEN** 该候选标为 `UNCERTAIN` 并进入人工待复核清单，不计为已确认正确

### Requirement: 复核流程主动检查漏报
Harness SHALL 除候选复核外检查算法未报告的时间范围，至少覆盖固定时间网格、长时间未切片区间、切片异常密集区间以及变化分数高但未达到动态阈值的区间，并 SHALL 为疑似漏报生成证据和复核记录。

#### Scenario: 动态播放未被算法输出
- **WHEN** 漏报检查在非候选区域发现持续播放视频或连续滚动证据
- **THEN** Harness 记录漏报区间及证据，将其纳入真值和召回率统计，并阻止本轮被标记为无已知漏报

### Requirement: 校准集和保留验证集隔离调参
Harness SHALL 将实施前已有真值的样本固定到校准集，并对其余规范化课程 URL 使用稳定哈希进行课程级确定性 70/30 划分；同一课程 SHALL NOT 跨越校准集和保留验证集。inventory SHALL 记录划分原因且其指纹 SHALL 包含最终 split。保留集标签和指标在算法及配置冻结前 SHALL NOT 用于阈值调优。

#### Scenario: 已知真值样本的哈希原本落入保留集
- **WHEN** 用户已提供真值的 URL 按默认稳定哈希会得到 `HOLDOUT`
- **THEN** Harness 将其固定为 `CALIBRATION`、记录 `split_reason=KNOWN_TRUTH`，其余未见样本仍按稳定哈希划分

#### Scenario: 保留验证集首次验收失败
- **WHEN** 冻结配置在保留验证集上未达到门槛
- **THEN** Harness 保存失败结果；后续修复作为有新版本和新运行标识的一轮记录，不得把反复查看后的同一保留集继续描述为未见数据

### Requirement: 每轮产生机器可读结果和中文报告
Harness SHALL 为每轮生成 JSON、CSV 和中文 Markdown。中文明细 SHALL 至少包含课程、视频 URL、时长、疑似开始/结束、类型、置信度、证据位置、复核结论、备注、算法版本和配置摘要，并 SHALL 单列不可访问、处理失败和未复核项目。

#### Scenario: 用户复查某个疑似区间
- **WHEN** 用户打开某轮中文报告
- **THEN** 用户能定位课程、原视频 URL、开始结束时间、证据、算法判断和复核结论，无需读取程序日志

### Requirement: 完成声明绑定冻结语料和质量门槛
Harness 只有在冻结语料发现覆盖率为 100%、所有可访问视频处理完成、候选和漏报检查复核覆盖率为 100%、segment precision/recall 均不低于 95%、p95 边界误差不超过 20 秒且没有已知误报或漏报时，才 SHALL 将本轮标记为通过。任何不可访问、处理失败或 `UNCERTAIN` 条目 SHALL 使报告标记为未完成或有阻塞。

#### Scenario: 全部当前样本没有已知错误
- **WHEN** 某一冻结 inventory 满足全部质量门槛且复核没有发现误报或漏报
- **THEN** 报告可以声明“当前冻结语料无已知误报、漏报”，并同时列出 `run_id`、语料快照、算法版本和配置，但不得宣称对未来未知课程绝对百分之百准确

#### Scenario: 有一个视频暂时不可访问
- **WHEN** 冻结 inventory 中至少一个视频因网络原因未完成处理
- **THEN** 报告列出该视频并标记本轮未完成，不得把其从分母删除后宣称全量通过
