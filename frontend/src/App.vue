<template>
  <router-view />
</template>

<style>
.module-notification {
  border-left: 4px solid var(--module-accent, #409eff);
  border-radius: 12px;
  padding-left: 16px;
  box-shadow: 0 10px 28px rgba(15, 92, 144, 0.12);
}

.module-notification .el-notification__title {
  font-weight: 700;
  color: #303133;
}

.module-notification .el-notification__content {
  color: #606266;
}

.module-notification--sales { --module-accent: #67c23a; }
.module-notification--purchase { --module-accent: #e6a23c; }
.module-notification--inventory { --module-accent: #409eff; }
.module-notification--production { --module-accent: #f56c6c; }
.module-notification--quality { --module-accent: #909399; }
.module-notification--finance { --module-accent: #b88230; }
.module-notification--admin { --module-accent: #7d54ff; }
.module-notification--dashboard { --module-accent: #606266; }
</style>
