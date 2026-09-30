import "@mantine/core/styles.css";
import "./styles.css";

import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { parseriumTheme } from "./theme";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("Application root is missing.");
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5_000,
    },
  },
});

createRoot(rootElement).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <MantineProvider theme={parseriumTheme} forceColorScheme="light">
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </MantineProvider>
    </MotionConfig>
  </StrictMode>,
);
