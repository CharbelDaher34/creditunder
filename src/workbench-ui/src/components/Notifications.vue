<script setup lang="ts">
import { ref } from 'vue'

interface Toast {
  id: number
  message: string
}

const toasts = ref<Toast[]>([])
let nextId = 0

const addToast = (message: string) => {
  const id = nextId++
  toasts.value.push({ id, message })
  
  // Auto remove after 5 seconds
  setTimeout(() => {
    removeToast(id)
  }, 5000)
}

const removeToast = (id: number) => {
  toasts.value = toasts.value.filter(t => t.id !== id)
}

defineExpose({
  addToast
})
</script>

<template>
  <div class="toast-container">
    <TransitionGroup name="toast-list">
      <div v-for="toast in toasts" :key="toast.id" class="toast">
        <div class="toast-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z" stroke="var(--success)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M9 12L11 14L15 10" stroke="var(--success)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="toast-content">{{ toast.message }}</div>
        <button class="toast-close" @click="removeToast(toast.id)">&times;</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-list-enter-active,
.toast-list-leave-active {
  transition: all 0.3s ease;
}
.toast-list-enter-from {
  opacity: 0;
  transform: translateX(50px);
}
.toast-list-leave-to {
  opacity: 0;
  transform: translateX(50px);
}
.toast-icon {
  display: flex;
  align-items: center;
}
.toast-content {
  flex: 1;
  font-size: 0.875rem;
}
.toast-close {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.25rem;
  cursor: pointer;
}
.toast-close:hover {
  color: white;
}
</style>
