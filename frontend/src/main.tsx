import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { ToastProvider } from "./components/ui/Toast";
import "./index.css";
import { AuthProvider } from "./lib/auth-context";
import { InvestigationProvider } from "./lib/investigation-context";
import { queryClient } from "./lib/queryClient";
import { ThemeProvider } from "./lib/theme-context";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ThemeProvider>
          <ToastProvider>
            <AuthProvider>
              <InvestigationProvider>
                <App />
              </InvestigationProvider>
            </AuthProvider>
          </ToastProvider>
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
