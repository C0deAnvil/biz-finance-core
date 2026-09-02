<template>
  <el-button v-bind="$attrs" :disabled="disabled || countdown > 0" @click="handleClick">
    <slot />{{ countdown > 0 ? `（${countdown}s）` : '' }}
  </el-button>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import ElButton from 'element-plus/es/components/button/index.mjs'
import { onBeforeUnmount, ref } from 'vue'

const props = defineProps({ disabled: { type: Boolean, default: false }, delay: { type: Number, default: 3 } })
const emit = defineEmits(['click'])
const countdown = ref(Math.max(0, Number(props.delay || 0)))
let timer = null

if (countdown.value > 0) {
  timer = window.setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) {
      window.clearInterval(timer)
      timer = null
    }
  }, 1000)
}

function handleClick(event) {
  if (countdown.value > 0 || props.disabled) return
  emit('click', event)
}

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>
