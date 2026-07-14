export const env = {
  apiBaseUrl: import.meta.env.VITE_API_URL || "/api",
  mode: import.meta.env.MODE,
  isDev: import.meta.env.DEV,
  isProd: import.meta.env.PROD,
} as const;
