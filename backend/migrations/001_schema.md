# 001 初始表结构

应用首次启动以 SQLAlchemy 模型创建表与唯一约束。现有数据表只补建缺失表，升级到修改过字段的版本需要显式迁移。

## 升级：抗干扰检查点（MySQL 8）

已建库的环境在升级后需为 `runs` 增加可空 JSON 列，再重启 backend / worker：

```sql
ALTER TABLE runs
  ADD COLUMN interference_checkpoint JSON NULL AFTER result;
```

全新环境由 `Base.metadata.create_all` 自动建出该列，无需手动执行。

## 表说明

- `users` 账号角色和加盐密码摘要。
- `datasets` 不可变记录批次，以名称和 SHA-256 去重。
- `runs` 记录参数、数据批次、运行租约、分析结果、导出文件及复核决定。
  - `interference_checkpoint`（JSON，可空）保存抗干扰复核的分批检查点（档级、偏移、已判定组合数）；中断后续跑剩余组合，完成后清空。
- `schedules` 保存定时计划和下一到期时间。
- `deliveries` 保存站内分发回执，每次运行最多一个回执。
- `audit_events` 记录登录、数据入库、运行、复核和报表分发动作。
