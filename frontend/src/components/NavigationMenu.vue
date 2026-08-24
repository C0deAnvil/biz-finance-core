<template>
  <el-scrollbar class="navigation-scroll">
    <el-menu
      :default-active="route.path"
      :default-openeds="defaultOpeneds"
      router
      unique-opened
      class="menu"
      background-color="transparent"
      text-color="#e8f7ff"
      active-text-color="#ffffff"
      @select="handleSelect"
    >
      <template v-for="item in menuItems" :key="item.path">
        <el-sub-menu v-if="item.children?.length" :index="item.path">
          <template #title>
            <span>{{ item.title }}</span>
          </template>
          <el-menu-item
            v-for="child in item.children"
            :key="child.path"
            :index="child.path"
            @mouseenter="prefetch(child.path)"
          >
            <span>{{ child.title }}</span>
          </el-menu-item>
        </el-sub-menu>
        <el-menu-item
          v-else
          :index="item.path"
          @mouseenter="prefetch(item.path)"
        >
          <span>{{ item.title }}</span>
        </el-menu-item>
      </template>
    </el-menu>
  </el-scrollbar>
</template>

<script setup>
import 'element-plus/es/components/menu/style/css'
import 'element-plus/es/components/scrollbar/style/css'

import ElMenu, { ElMenuItem, ElSubMenu } from 'element-plus/es/components/menu/index.mjs'
import ElScrollbar from 'element-plus/es/components/scrollbar/index.mjs'

import { useRoute } from 'vue-router'

defineProps({
  menuItems: { type: Array, default: () => [] },
  defaultOpeneds: { type: Array, default: () => [] }
})

const emit = defineEmits(['prefetch', 'select'])
const route = useRoute()

function prefetch(path) {
  emit('prefetch', path)
}

function handleSelect(index) {
  emit('select', index)
}
</script>

<style scoped>
.navigation-scroll {
  flex: 1;
  min-height: 0;
}

.menu {
  border-right: none;
  font-size: 15.5px;
}

:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  height: 50px;
  line-height: 50px;
  font-size: 15.5px;
  border-radius: 10px;
  margin: 4px 10px;
}

:deep(.el-menu-item:hover),
:deep(.el-sub-menu__title:hover) {
  background: rgba(255, 255, 255, 0.12) !important;
}

:deep(.el-menu-item.is-active) {
  background: rgba(255, 255, 255, 0.18) !important;
  box-shadow: inset 4px 0 0 #ffffff;
}
</style>
