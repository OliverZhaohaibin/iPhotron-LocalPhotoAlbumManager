# Refactor Evaluation 3 (Post-Convergence Updates)

## 结论摘要

本轮调整继续收敛于新架构目标，重点把**增量索引缓存逻辑从旧 facade 抽离**并
统一进入 `cache/index_store` 作用域，避免新旧架构交叉依赖。同时完成了
**过时测试替换**与**新测试补齐**，使测试基准统一基于 `global_index.db`
并覆盖新增的缓存工具逻辑。

---

## 本轮收敛重点

### ✅ 1. Legacy 依赖进一步收敛
- 增量索引缓存逻辑迁移到 `cache/index_store/index_cache.py`，作为新架构的共享基础设施。
- 扫描 Worker 移除对 `app.py` 的直接依赖，降低旧 Facade 侵入。
- 增量缓存现在自动剥离 album 前缀，确保全局 DB 下仍支持局部扫描的缓存命中。

### ✅ 2. 无用代码清理
- 旧 `app.py` 内的缓存/路径工具函数移除，避免重复实现与跨层耦合。
- 相关调用统一转向新的 `index_cache` 模块。

### ✅ 3. 过时测试清理与新测试补齐
- 移除旧 `index.jsonl` 断言逻辑，所有测试统一验证 `global_index.db`。
- 修正 Application Use Case 测试，使其显式注入 metadata/thumbnail provider。
- 新增针对 `index_cache` 的专用单测，覆盖 album 路径解析与增量缓存键规范化。

---

## 当前完成度判断

**总体完成度：约 92%（持续收尾阶段）**

- **已完成**：Repository 路径收敛、GUI 主入口服务化、性能基准、测试补齐。
- **新增改进**：缓存与扫描逻辑进一步从旧 Facade 解耦。

---

## 后续建议

1. 持续替换残留 AppFacade 直接调用，避免新旧架构交错。
2. 逐步将 GUI 层扫描/导入的逻辑迁移到 Application Use Case。
3. 保持新增测试覆盖，确保 global_index.db 成为唯一基准。
