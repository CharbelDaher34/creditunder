<script setup lang="ts">
import { ref } from 'vue'

type Tone = 'info' | 'success' | 'warning' | 'danger'

interface Toast {
  id: number
  message: string
  tone: Tone
}

const toasts = ref<Toast[]>([])
let nextId = 0

function addToast(message: string, tone: Tone = 'info') {
  const id = nextId++
  toasts.value.push({ id, message, tone })
  setTimeout(() => removeToast(id), 5000)
}

function removeToast(id: number) {
  toasts.value = toasts.value.filter(t => t.id !== id)
}

function iconFor(tone: Tone) {
  if (tone === 'success') return '✓'
  if (tone === 'warning') return '!'
  if (tone === 'danger')  return '✕'
  return 'ⓘ'
}

defineExpose({ addToast })
</script>

<template>
  <div class="toast-stack">
    <TransitionGroup name="toast-fly">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="toast"
        :class="`toast-${t.tone}`"
        role="status"
      >
        <span class="toast-icon">{{ iconFor(t.tone) }}</span>
        <span class="toast-msg">{{ t.message }}</span>
        <button class="x" aria-label="Dismiss" @click="removeToast(t.id)">×</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-icon {
  width: 22px; height: 22px;
  border-radius: 999px;
  display: grid; place-items: center;
  font-weight: 700;
  background: var(--brand-soft);
  color: #c7d2fe;
}
.toast-success .toast-icon { background: var(--success-soft); color: var(--success); }
.toast-warning .toast-icon { background: var(--warning-soft); color: var(--warning); }
.toast-danger  .toast-icon { background: var(--danger-soft);  color: var(--danger); }

.toast-msg { flex: 1; line-height: 1.4; }

.toast-fly-enter-active,
.toast-fly-leave-active { transition: all .25s cubic-bezier(.16,1,.3,1); }
.toast-fly-enter-from { opacity: 0; transform: translateX(40px); }
.toast-fly-leave-to   { opacity: 0; transform: translateX(40px); }
</style>
