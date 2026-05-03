// Tiny module containing only the mock-mode flag check.
// Splitting it out from mock.ts (which is ~800 lines of fixtures + handlers)
// lets vite tree-shake the heavy module out of prod bundles via dynamic
// import in apiFetch / useSSE.

export function isMockEnabled(): boolean {
  if (typeof import.meta === 'undefined') return false
  if (import.meta.env.PROD) return false
  if (import.meta.env.VITE_API_REAL === '1') return false
  return true
}
