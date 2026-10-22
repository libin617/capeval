import axios from 'axios'

export const http = axios.create({ baseURL: '/api', timeout: 30000 })

// ---- 身份切换(M6,PRD 7.3 MVP):X-Acting-User 请求头,本地持久化 ----
export const CURRENT_USER_KEY = 'capeval_acting_user'

export function getCurrentUser(): string {
  return localStorage.getItem(CURRENT_USER_KEY) || 'admin'
}

export function setCurrentUser(username: string): void {
  localStorage.setItem(CURRENT_USER_KEY, username)
}

http.interceptors.request.use((config) => {
  config.headers['X-Acting-User'] = getCurrentUser()
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const detail = err.response?.data?.detail
    err.message = typeof detail === 'string' ? detail : JSON.stringify(detail ?? err.message)
    return Promise.reject(err)
  },
)
