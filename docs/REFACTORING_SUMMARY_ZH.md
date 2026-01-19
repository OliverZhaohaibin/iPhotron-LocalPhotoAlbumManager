# iPhotron 架构重构方案 - 执行摘要

> **快速参考版本** | 完整文档请查阅: [ARCHITECTURE_ANALYSIS_AND_REFACTORING.md](./ARCHITECTURE_ANALYSIS_AND_REFACTORING.md)

## 📊 项目现状

### 代码规模
- **代码行数:** 约49,000行
- **Python文件:** 218个
- **技术栈:** Python 3.12+, PySide6 (Qt6), SQLite

### 架构优势 ✅
1. **清晰分层:** 后端(app.py)与GUI(facade.py)完全解耦
2. **全局数据库:** 统一SQLite索引，单一写入网关保证一致性
3. **信号槽通信:** Qt机制解耦组件依赖

## ⚠️ 主要问题

### 1. 控制器激增 🔴 (严重)
- **现状:** 43个控制器，职责重叠
- **影响:** MainController初始化15+子控制器，成为上帝对象
- **后果:** 难以测试、重构风险高、认知负担重

### 2. AssetListModel职责过载 🔴 (严重)
- **问题:** 混合数据加载、缓存、状态、适配、UI呈现
- **代码量:** 构造函数80+行，总计~400行
- **影响:** 违反单一职责原则，维护困难

### 3. 路径处理复杂 🔴 (严重)
- **问题:** library-relative与album-relative路径混用
- **风险:** 路径计算错误、资产查询失败、跨相册移动出错

### 4. 性能瓶颈 ⚡
| 操作 | 当前性能 | 问题 |
|------|---------|------|
| 扫描10万文件 | 15分钟 | 单线程遍历、串行元数据提取 |
| 打开50K相册 | 8秒阻塞UI | 同步全量加载到内存 |
| 缩略图首次加载 | 200ms/张 | 无缓存、重复FFmpeg调用 |
| 内存占用 | 5-10GB | 缩略图无限期缓存 |

## 🎯 目标架构

### 设计模式升级
```
当前 (MVC) → 目标 (MVVM + DDD)

Controllers (43个) → Coordinators (15个) + ViewModels
app.py (God Object) → Use Cases + Domain Services
AssetRepository (Concrete) → IAssetRepository (Interface) + SQLiteAssetRepository
直接引用通信 → EventBus (发布-订阅)
无DI → Dependency Injection Container
```

### 核心改进

#### 1. MVVM模式
```
View (纯展示) ←→ ViewModel (展示逻辑) ←→ UseCase (业务逻辑) ←→ Repository (数据访问)
```

**优势:**
- ViewModel可独立测试（无需Qt）
- 视图与业务逻辑解耦
- 支持多视图绑定

#### 2. Use Case封装业务逻辑
```python
class OpenAlbumUseCase:
    def execute(self, request: OpenAlbumRequest) -> OpenAlbumResponse:
        # 1. 验证输入
        # 2. 加载相册
        # 3. 可选自动扫描
        # 4. 加载资产
        # 5. 发布事件
        # 6. 返回响应
```

**优势:**
- 单一职责，易于测试
- 业务逻辑集中管理
- 支持事务和回滚

#### 3. 仓储接口分离
```python
# 领域层定义接口
class IAssetRepository(ABC):
    @abstractmethod
    def find_by_query(self, query: AssetQuery) -> List[Asset]: ...

# 基础设施层实现
class SQLiteAssetRepository(IAssetRepository):
    def find_by_query(self, query: AssetQuery) -> List[Asset]:
        # SQLite具体实现
```

**优势:**
- 领域层不依赖具体数据库
- 可轻松切换存储后端
- 测试时使用内存仓储

#### 4. 事件总线解耦
```python
# 发布者
scan_use_case.execute(request)
event_bus.publish(AlbumScannedEvent(...))

# 订阅者
class ThumbnailPreloader:
    def __init__(self, event_bus):
        event_bus.subscribe(AlbumScannedEvent, self.on_album_scanned)
```

**优势:**
- 发布者不知道订阅者
- 易于添加新功能
- 支持异步处理

## 🗺️ 重构路线图

### 时间线（5-6个月）

| 阶段 | 目标 | 周数 | 优先级 |
|------|------|------|--------|
| **Phase 1** | 基础设施现代化 | 2-3周 | P0 |
| **Phase 2** | 仓储层重构 | 3-4周 | P1 |
| **Phase 3** | 应用层重构 | 4-5周 | P1 |
| **Phase 4** | GUI层MVVM迁移 | 5-6周 | P2 |
| **Phase 5** | 性能优化 | 3-4周 | P2 |
| **Phase 6** | 测试与文档 | 2-3周 | P3 |

### Phase 1: 基础设施 (立即开始)

**任务:**
1. ✅ 实现DI容器
2. ✅ 创建事件总线
3. ✅ 添加数据库连接池
4. ✅ 统一错误处理

**交付物:**
- 依赖注入容器原型
- 事件总线POC
- 连接池实现

### Phase 2-4: 核心重构 (3个月)

**关键任务:**
- 定义仓储接口（IAssetRepository, IAlbumRepository）
- 提取Use Cases（OpenAlbum, ScanAlbum, PairLivePhotos）
- 迁移到MVVM（创建ViewModels替代Controllers）
- 简化控制器（43个→15个核心Coordinators）

### Phase 5: 性能优化 (3-4周)

**优化目标:**

| 指标 | 当前 | 目标 | 提升 |
|------|------|------|------|
| 扫描10K文件 | 85秒 | <30秒 | 65% ↓ |
| 打开50K相册 | 8秒 | <2秒 | 75% ↓ |
| 缩略图加载 | 200ms | <100ms | 50% ↓ |
| 内存占用 | 5-10GB | <2GB | 70% ↓ |

**实施方案:**
1. 并行扫描（多线程文件发现 + 批量元数据提取）
2. 多级缓存（内存LRU + 磁盘持久化）
3. 异步分页加载（首屏100条，按需加载）
4. 渐进式预览（低分辨率即时 + 高质量延迟）

## 🎬 实施策略

### 1. 风险缓解

#### 功能开关 (Feature Flags)
```python
if feature_flags.is_enabled(Feature.NEW_MVVM_ARCHITECTURE):
    model = AlbumViewModel(album_service)  # 新架构
else:
    model = AssetListModel(facade)  # 旧架构（回退）
```

#### 金丝雀发布
```
Week 1-2: 内部测试（开发团队）
Week 3-4: Alpha测试（5-10位早期用户）
Week 5-6: Beta测试（50-100位用户）
Week 7+:  正式发布（全量用户）
```

#### 数据库安全
```python
class SafeDatabaseMigrator:
    def migrate(self, target_version):
        backup_path = self.create_backup()  # 自动备份
        try:
            self.apply_migrations()
            self.validate()
        except Exception:
            self.rollback(backup_path)  # 自动回滚
```

### 2. 渐进式迁移

**适配器模式:**
```python
class LegacyAssetRepositoryAdapter(IAssetRepository):
    """包装旧实现为新接口"""
    def __init__(self, legacy_repo: AssetRepository):
        self._legacy = legacy_repo
```

**并行运行:**
- 新代码使用IAssetRepository接口
- 旧代码继续使用AssetRepository
- DI容器配置适配器桥接

**逐步替换:**
1. Week 1-2: 创建接口和适配器
2. Week 3: 迁移ScanAlbumUseCase
3. Week 4: 迁移GUI加载逻辑
4. Week 5: 移除适配器

## 📈 成功指标

### 代码质量
| 指标 | 当前 | 目标 | 改进 |
|------|------|------|------|
| 控制器数量 | 43 | <15 | 65% ↓ |
| 平均依赖数 | 7.2 | <4 | 44% ↓ |
| 代码重复率 | 18% | <10% | 44% ↓ |
| 测试覆盖率 | 65% | >80% | 23% ↑ |

### 可维护性
| 指标 | 当前 | 目标 |
|------|------|------|
| 新功能开发 | 2-3周 | <1周 |
| Bug修复时间 | 3-5天 | <2天 |
| 新人上手 | 2-3周 | <1周 |
| 代码评审 | 4-6小时 | <2小时 |

## ✅ 下一步行动

### 立即行动 (本周)
- [ ] 团队评审本文档，达成共识
- [ ] 创建重构任务看板（Jira/GitHub Projects）
- [ ] 设置性能基准测试环境
- [ ] 准备数据库备份策略

### 短期目标 (2周内)
- [ ] 实现DI容器原型
- [ ] 创建事件总线POC
- [ ] 编写第一个Use Case测试
- [ ] 设置功能开关系统

### 中期目标 (3个月)
- [ ] 完成仓储层重构
- [ ] 迁移核心业务逻辑到Use Cases
- [ ] 发布Alpha版本内部测试
- [ ] 完成性能基准测试

## 📚 相关文档

- **完整架构文档:** [ARCHITECTURE_ANALYSIS_AND_REFACTORING.md](./ARCHITECTURE_ANALYSIS_AND_REFACTORING.md)
- **迁移指南:** 详见完整文档 Phase 2-4 章节
- **API设计:** 详见完整文档"目标架构设计"章节
- **性能优化:** 详见完整文档"性能瓶颈分析"章节

## 🤝 参与贡献

如需讨论或提出建议，请：
1. 创建GitHub Issue标记为`architecture`
2. 在团队会议上提出
3. 联系架构团队成员

---

**文档版本:** 1.0  
**最后更新:** 2026-01-19  
**维护者:** Architecture Team
