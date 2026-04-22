# 🛠️ VAF 脚本目录

> 本目录包含 VAF 框架的所有 Python 工具脚本，按职责分类组织。

## 📁 目录结构

```
scripts/
├── init/           # 🚀 初始化工具
├── knowledge/      # 🧠 K 阶段：知识库构建
├── vibe_flow/      # � 流程控制
├── prd/            # 📄 PRD 处理
├── vlm/            # 🖼️ VLM 图片解析
├── validation/     # ✅ 验证工具
├── git/            # 🔧 Git 工具
├── infra/          # 🛠️ 基础设施
├── cleanup/        # 🧹 清理工具
├── release/        # 📦 发版工具
├── tests/          # 🧪 测试代码
└── validation/     # 🔍 验证工具（含接口完整性验证）
```

## 📋 分类说明

### 🚀 init/ - 初始化工具

| 脚本 | 用途 |
|:---|:---|
| `vibe_init_hub.py` | Hub 一键初始化（创建需求目录 + .vibe 资产 + Git 提交） |
| `vibe_init_hub_feature.py` | Hub 需求目录初始化（创建 features/ + Git 分支） |
| `vibe_init_hub_workspace.py` | Hub 工作区初始化（拷贝 .vibe 资产 + workspace.json） |
| `vibe_init_service.py` | Service 一键初始化（创建需求目录 + .vibe 资产 + Git 提交） |
| `vibe_init_service_feature.py` | Service 需求目录初始化（创建 docs/features/ + manifest） |
| `vibe_init_service_workspace.py` | Service 工作区初始化（从 Hub 拷贝 .vibe 资产） |
| `knowledge_init.py` | 知识库初始化（拷贝 K 阶段 Prompt/模板/规则/脚本到目标项目） |

### 🧠 knowledge/ - 知识库构建

| 脚本 | 用途 |
|:---|:---|
| `K01_A_behavior_discovery_scanner.py` | 行为发现扫描器 - 扫描 Java 项目入口点（Dubbo/REST/MQ/Job） |
| `K01_B_external_dependency_scanner.py` | 外部依赖扫描器 - 扫描 Dubbo 服务依赖元数据 |
| `K01_C_call_chain_tracer.py` | 调用链追踪器 - 从入口追踪完整调用链，构建类级别拓扑图 |
| `K01_D_sql_table_extractor.py` | SQL 表结构提取器 - 从 DDL 提取表名/注释/字段 |
| `K01_E_table_ddl_standalone.py` | Zeus DDL 导出工具 - 从 Zeus 控制台批量导出表 DDL |
| `k03_parallel.py` | K03 并行执行脚本 - 基于 API 并行调用 K03-A/B/C |
| `toon_encoder.py` | TOON 编码器 - JSON 转 TOON 格式（v1.3 规范） |
| `toon_decoder.py` | TOON 解码器 - TOON 转 JSON 格式（v1.3 规范） |

#### knowledge/tmf/ - TMF 框架专属扫描器

| 脚本 | 用途 |
|:---|:---|
| `scan_bp.py` | TMF BP 层元数据扫描器 - 提取 Partner/Scenario/Extension 注解 |
| `scan_cp.py` | TMF CP 层元数据扫描器 - 提取 Domain/Aggregate/Ability/Step 等 |

### � vibe_flow/ - 流程控制

| 脚本 | 用途 |
|:---|:---|
| `vibe_startup.py` | 一键启动脚本 - 目录校验 + Git 同步 + 需求检测 + 菜单渲染 |
| `vibe_detect_feature.py` | Feature 上下文检测 - 自动检测/选择当前工作区的 feature |
| `vibe_render_menu.py` | 菜单渲染脚本 - 读取状态文件生成 ASCII 进度菜单 |
| `vibe_render_menu_frontend.py` | 前端菜单渲染（精简版）- 支持 P/F 前端阶段 |
| `vibe_unlock_checker.py` | 阶段解锁检查 - 检查 P/S/T 阶段的解锁状态和依赖 |
| `vibe_update_status.py` | 状态更新脚本 - 原子化更新阶段状态（start/pending/approve） |
| `vibe_update_status_frontend.py` | 前端状态更新 - 独立的前端 P/F 阶段状态写入 |
| `vibe_validate_choice.py` | 用户输入校验 - 校验用户选择的阶段编号是否有效 |
| `vibe_validate_output.py` | 产物校验脚本 - 校验各阶段产出物的存在性和完整性 |
| `vibe_git_commit.py` | 阶段 Git 提交 - P/S/T 阶段完成后的 Git 提交推送 |
| `vibe_git_flow.py` | Git 提交流程状态机 - 阶段放行后的交互式 Git 提交流程 |
| `vibe_helpers.py` | 通用工具函数 - 版本号/JSON 读写/路径查找/工作区检测 |

### 📄 prd/ - PRD 处理

| 脚本 | 用途 |
|:---|:---|
| `prd_standardize.py` | PRD 标准化 - 目录映射 + 图片路径替换 |
| `merge_prd_assets.py` | PRD 资源合并 - 将图片解析结果合并到 PRD |
| `copy_sections.py` | 章节复制 - 按映射表将源 PRD 章节搬运到目标模板 |
| `validate_prd_engineering.py` | PRD 工程化验证 - 校验标准化 PRD 结构是否符合模板 |

### 🖼️ vlm/ - VLM 图片解析

| 脚本 | 用途 |
|:---|:---|
| `run_pipeline.py` | VLM 流水线入口 - 编排飞书下载 + 图片解析 + 合并流程 |
| `process_assets_with_vlm.py` | VLM 统一入口 - 路由到 DashScope/MiFy 后端 |
| `process_assets_with_qwen.py` | Qwen VLM 处理 - 使用 Qwen3-VL-Plus 批量解析图片 |
| `mify_image_analyze.py` | MiFy VLM 处理 - 使用 MiFy Workflow 并发解析图片 |

### ✅ validation/ - 验证工具

| 脚本 | 用途 |
|:---|:---|
| `health_check.py` | 环境健康检查 - 验证 Git/Python/larkkit/DashScope 等依赖 |
| `mermaid_validator.py` | Mermaid 语法验证 - 静态校验 + LLM 自修复 |
| `validate_doc_consistency.py` | 文档一致性验证 - 检查 docs/ 与 core/ 权威来源的一致性 |
| `validate_knowledge_reading.py` | 知识库读取质检 - 验证 AI 是否完整读取知识库文件 |

### 🔧 git/ - Git 工具

| 脚本 | 用途 |
|:---|:---|
| `branch_doctor.py` | 分支诊断工具 - 诊断 Git 分支规范问题，支持交互式清理 |

### 🛠️ infra/ - 基础设施

| 脚本 | 用途 |
|:---|:---|
| `vaf_config.py` | VAF 统一配置管理 - 从 ~/.vaf/config 读取敏感配置 |
| `fetch_feishu_doc.py` | 飞书文档下载器 - 使用 larkkit 下载飞书文档（带重试） |
| `db_inspector.py` | 数据库检查器 - 安全获取 MySQL 表结构和样本（只读） |
| `diagnose_network.py` | 网络诊断工具 - 诊断 DashScope API 网络连接问题 |
| `generate_code_index.py` | 代码索引生成 - 扫描代码仓库提取类/方法信息供 S01 使用 |

### 🧹 cleanup/ - 清理工具

| 脚本 | 用途 |
|:---|:---|
| `cleanup_p02.py` | P02 产物清理 - 清理 P02 阶段的 source_export/knowledge_repos 等 |

### 📦 release/ - 发版工具

| 脚本 | 用途 |
|:---|:---|
| `vaf_release.py` | VAF 自动化发版 - 基于 Conventional Commits 推断版本号 + 生成 changelog + 打 tag |

### 🧪 tests/ - 测试代码

| 脚本 | 用途 |
|:---|:---|
| `test_mermaid_coverage.py` | Mermaid 校验函数代码覆盖率测试 |
| `test_mermaid_validation.py` | Mermaid 语法校验测试套件（20+ 场景） |

### 📍 根目录脚本

| 脚本 | 用途 |
|:---|:---|

---

## 🔧 使用说明

### 常用入口脚本

```bash
# Hub 初始化
python tools/scripts/init/vibe_init_hub.py --feature <需求名>

# Service 初始化
python tools/scripts/init/vibe_init_service.py --feature <需求名> --hub-path <Hub路径>

# 知识库初始化
python tools/scripts/init/knowledge_init.py <目标项目路径>

# 环境检查
python tools/scripts/validation/health_check.py

# VLM 流水线
python tools/scripts/vlm/run_pipeline.py <飞书URL> <输出目录>

# 自动化发版
python tools/scripts/release/vaf_release.py [--dry-run]
```

---

> 📅 更新时间: 2026-01-04
