import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { ApiError } from './lib/api'
import './index.css'

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    // A session can end while the page is open (expired, revoked, signed
    // out in another tab). Any query answering 401 re-asks who is signed
    // in, and the app shows the sign-in page.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        void queryClient.invalidateQueries({ queryKey: ['me'] })
      }
    },
  }),
  defaultOptions: {
    queries: {
      // Retrying a 4xx gets the same answer: only server and network errors.
      retry: (failures, error) => failures < 3 && !(error instanceof ApiError && error.status < 500),
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
