import "@mantine/core/styles.css";
import "../styles.css";
import "./cloud.css";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { parseriumTheme } from "../theme";
import { CloudApp } from "./CloudApp";

const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5000 }, mutations: { retry: false } } });
const root = document.getElementById("root");
if (!root) throw new Error("Application root is missing.");
createRoot(root).render(<StrictMode><QueryClientProvider client={client}><MantineProvider theme={parseriumTheme} forceColorScheme="light"><CloudApp /></MantineProvider></QueryClientProvider></StrictMode>);
