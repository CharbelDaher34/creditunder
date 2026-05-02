<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { userStore, type User } from '../userStore'

const open = ref(false)
const dropdownRef = ref<HTMLElement | null>(null)

const initials = computed(() => {
  const parts = userStore.current.display_name.split(' ').filter(Boolean)
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase()
})

const roleChipClass = computed(() =>
  userStore.current.role === 'SUPERVISOR' ? 'chip-brand' : 'chip-info',
)

function pick(u: User) {
  userStore.switchTo(u)
  open.value = false
}

function onDocClick(e: MouseEvent) {
  if (dropdownRef.value && !dropdownRef.value.contains(e.target as Node)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('mousedown', onDocClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocClick))
</script>

<template>
  <div class="user-switcher" ref="dropdownRef">
    <button class="user-btn" @click="open = !open" :aria-expanded="open">
      <span class="avatar">{{ initials }}</span>
      <span class="user-text">
        <span class="user-name truncate">{{ userStore.current.display_name }}</span>
        <span class="user-meta">
          <span class="chip chip-dot" :class="roleChipClass">{{ userStore.current.role }}</span>
          <span class="user-id mono">{{ userStore.current.user_id }}</span>
        </span>
      </span>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" :class="{ rot: open }"><path d="M6 9l6 6 6-6"/></svg>
    </button>

    <div v-if="open" class="dropdown panel">
      <div class="dropdown-header">Switch identity (demo)</div>
      <button
        v-for="u in userStore.options"
        :key="u.user_id"
        class="dropdown-item"
        :class="{ 'is-active': u.user_id === userStore.current.user_id }"
        @click="pick(u)"
      >
        <span class="avatar small">{{ ((u.display_name.split(' ')[0]?.[0] ?? '') + (u.display_name.split(' ')[1]?.[0] ?? '')).toUpperCase() }}</span>
        <span class="dropdown-text">
          <span class="dropdown-name">{{ u.display_name }}</span>
          <span class="dropdown-sub mono">{{ u.user_id }}</span>
        </span>
        <span class="chip" :class="u.role === 'SUPERVISOR' ? 'chip-brand' : 'chip-info'">{{ u.role }}</span>
      </button>
      <div class="dropdown-footnote muted">
        Real deployments resolve identity via the bank's IDP. This selector exists only for the local demo.
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-switcher { position: relative; }

.user-btn {
  display: flex; align-items: center; gap: 10px;
  padding: 6px 10px 6px 6px;
  border-radius: 999px;
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--border);
  color: var(--text-1);
  cursor: pointer;
  transition: background .15s ease, border-color .15s ease;
}
.user-btn:hover { background: rgba(255,255,255,0.07); border-color: var(--border-strong); }
.user-btn .rot { transform: rotate(180deg); transition: transform .2s ease; }

.avatar {
  width: 32px; height: 32px;
  border-radius: 999px;
  display: grid; place-items: center;
  background: linear-gradient(135deg, #6366f1, #38bdf8);
  color: white;
  font-weight: 600;
  font-size: 12px;
}
.avatar.small { width: 28px; height: 28px; font-size: 11px; }

.user-text { display: flex; flex-direction: column; align-items: flex-start; line-height: 1.2; max-width: 200px; }
.user-name { font-size: 13px; font-weight: 500; }
.user-meta { display: flex; align-items: center; gap: 6px; margin-top: 2px; }
.user-id { font-size: 11px; color: var(--text-3); }

.dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 320px;
  padding: 8px;
  z-index: 50;
}
.dropdown-header {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-3);
  padding: 8px 10px 4px;
}
.dropdown-item {
  width: 100%;
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px;
  background: transparent;
  color: var(--text-1);
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  cursor: pointer;
  text-align: left;
  font-family: inherit;
}
.dropdown-item:hover { background: rgba(255,255,255,0.04); border-color: var(--border); }
.dropdown-item.is-active { background: var(--brand-soft); border-color: rgba(99,102,241,0.35); }
.dropdown-text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.dropdown-name { font-size: 13px; font-weight: 500; }
.dropdown-sub { font-size: 11px; color: var(--text-3); }

.dropdown-footnote {
  font-size: 11px;
  padding: 8px 10px 4px;
  border-top: 1px solid var(--border);
  margin-top: 4px;
}

@media (max-width: 720px) {
  .user-text { display: none; }
  .user-btn { padding: 4px; }
}
</style>
