# iPhoton 重构文档总览

> **版本**: v1.0  
> **日期**: 2026-02-14  
> **状态**: 规划阶段

## 📖 文档导航

本文档集全面分析了 iPhoton 项目的现有架构问题，并参考业界优秀开源相册工程与大型 Python GUI 项目的最佳实践，提出了系统性的重构方案。

### 文档结构

| 编号 | 文档 | 内容概述 |
|------|------|---------|
| **01** | [现有架构分析](./01-current-architecture-analysis.md) | 当前架构全景、层次结构、核心问题诊断（含 Mermaid 图） |
| **02** | [行业对标分析](./02-industry-benchmarks.md) | 开源相册（digiKam、Shotwell）与大型 Python GUI 工程（Calibre、Anki、Frescobaldi）对标 |
| **03** | [目标架构设计](./03-target-architecture.md) | 目标 MVVM + Clean Architecture 设计（含 Mermaid 图）、优势对比 |
| **04** | [重构路线图](./04-refactoring-roadmap.md) | 5 阶段分步实施计划、里程碑、风险管理 |
| **05** | [阶段一：基础设施层](./05-phase1-infrastructure.md) | DI 容器增强、EventBus 重建、连接池优化 |
| **06** | [阶段二：领域与应用层](./06-phase2-domain-application.md) | 领域模型统一、Use Case 补全、Service 层整合 |
| **07** | [阶段三：GUI MVVM 重构](./07-phase3-gui-mvvm.md) | Coordinator 精简、ViewModel 拆分、View 解耦 |
| **08** | [阶段四：性能优化](./08-phase4-performance.md) | 并行扫描、多级缓存、GPU 加速、内存治理 |
| **09** | [阶段五：测试与 CI/CD](./09-phase5-testing-ci.md) | 测试体系建设、CI/CD 流水线、质量门禁 |

### 阅读顺序建议

```
01 (了解现状) → 02 (对标学习) → 03 (理解目标) → 04 (掌握路径) → 05~09 (分阶段实施)
```

## 📊 项目现状速览

```
代码规模:  ~49,000 行 Python 代码，218 个文件
技术栈:    Python 3.12+ / PySide6 (Qt6) / SQLite / OpenGL 3.3+
架构模式:  正在从 MVC 向 MVVM + DDD 迁移（双架构并存）
核心问题:  God Object、双重模型、EventBus 未启用、DI 不完整
```

## 🎯 重构核心目标

1. **消除双架构并存** — 完成从 Legacy Facade → Clean Architecture 的迁移
2. **解耦 GUI 与业务逻辑** — Qt 依赖不再渗透到 Service/Domain 层
3. **统一数据模型** — 消除 `models/` 与 `domain/models/` 的重复
4. **启用事件驱动** — EventBus 替代 Qt Signal 进行跨层通信
5. **完善 DI 容器** — 支持生命周期管理、循环依赖检测、惰性初始化
6. **建立质量门禁** — CI/CD + 测试覆盖率 ≥ 80%

## 📋 与已有文档的关系

本文档集是对 `docs/finished/referactor/` 中已有分析的 **进一步细化和实施指导**：
- 已有文档提供了宏观架构分析和总体方向
- 本文档集增加了 **行业对标**、**具体代码级问题定位**、**分阶段实施细节** 和 **可执行的重构步骤**
