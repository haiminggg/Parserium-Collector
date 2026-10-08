import "@mantine/core/styles.css";
import "../styles.css";
import { MantineProvider } from "@mantine/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { parseriumTheme } from "../theme";
import { PreviewApp } from "./PreviewApp";

const root = document.getElementById("root");
if (!root) throw new Error("Application root is missing.");
createRoot(root).render(
  <StrictMode><MantineProvider theme={parseriumTheme} forceColorScheme="light"><PreviewApp /></MantineProvider></StrictMode>,
);
