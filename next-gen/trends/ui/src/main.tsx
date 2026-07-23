import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { WorkbenchApp } from "./WorkbenchApp";
import "./styles.css";

document.documentElement.dataset.theme = "trends";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WorkbenchApp />
  </StrictMode>,
);
