import { reactive, watch } from 'vue'

export type UserRole = 'VALIDATOR' | 'SUPERVISOR'

export interface User {
  user_id: string
  role: UserRole
  display_name: string
}

const STORAGE_KEY = 'workbench.user'

const DEMO_USERS: User[] = [
  { user_id: 'CRM-USR-4821', role: 'VALIDATOR',  display_name: 'Layla Al-Saud  · Validator' },
  { user_id: 'CRM-USR-4955', role: 'VALIDATOR',  display_name: 'Khalid Hassan · Validator' },
  { user_id: 'CRM-USR-1093', role: 'SUPERVISOR', display_name: 'Faisal Otaibi · Supervisor' },
  { user_id: 'CRM-USR-1102', role: 'SUPERVISOR', display_name: 'Noura Al-Qahtani · Supervisor' },
]

function loadInitial(): User {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw) as User
  } catch (_) { /* ignore */ }
  return DEMO_USERS[0]
}

export const userStore = reactive({
  current: loadInitial() as User,
  options: DEMO_USERS,
  switchTo(u: User) {
    this.current = u
  },
})

watch(
  () => userStore.current,
  (u) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(u))
    } catch (_) { /* ignore */ }
  },
  { deep: true },
)
