import { createRouter, createWebHistory } from 'vue-router'
import { clearAuthSession, getAuthToken, verifyAuthSession } from '../lib/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: () => import('../components/Chat.vue') },
    { path: '/templates', name: 'templates', component: () => import('../components/TemplateCommunity.vue') },
    { path: '/login', name: 'login', component: () => import('../components/Login.vue') }
  ]
})

router.beforeEach(async (to) => {
  if (to.path === '/login' && to.query.state && (to.query.code || to.query.error)) return true
  const token = getAuthToken()
  if (to.path !== '/login' && !token) {
    return '/login'
  }
  if (!token) {
    return true
  }

  const valid = await verifyAuthSession()
  if (!valid) {
    clearAuthSession()
    return to.path === '/login' ? true : '/login'
  }
  if (to.path === '/login') {
    return '/'
  }
  return true
})

export default router
