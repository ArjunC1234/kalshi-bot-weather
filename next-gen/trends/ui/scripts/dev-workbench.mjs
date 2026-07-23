import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const uiRoot = path.resolve(scriptDir, "..");
const nextGenRoot = path.resolve(uiRoot, "../..");
const isWindows = process.platform === "win32";
const pythonCommand = process.env.PYTHON || "python";
const npmCommand = isWindows ? "npm.cmd" : "npm";
const children = [];
let shuttingDown = false;

function childEnv() {
  if (!isWindows) return process.env;
  const env = {};
  let pathValue = "";
  for (const [key, value] of Object.entries(process.env)) {
    if (key.toLowerCase() === "path") {
      pathValue ||= value ?? "";
    } else {
      env[key] = value;
    }
  }
  env.Path = pathValue;
  return env;
}

function isListening(port, host = "127.0.0.1") {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
    socket.setTimeout(750, () => {
      socket.destroy();
      resolve(false);
    });
  });
}

async function getJson(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function tableCountTotal(item) {
  const counts = item?.table_counts;
  if (!counts || typeof counts !== "object") return 0;
  return Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
}

async function warnIfStaleControlBackend() {
  const payload = await getJson("http://127.0.0.1:8775/control/api/reports");
  const reports = Array.isArray(payload?.reports) ? payload.reports : [];
  if (!reports.length) return;
  const checked = reports.slice(0, 12);
  if (checked.every((report) => tableCountTotal(report) === 0)) {
    console.warn("[control] existing backend is returning 0 rows for every report.");
    console.warn("[control] stop the stale Python process on port 8775, then rerun npm.cmd run dev:workbench.");
    if (isWindows) {
      console.warn("[control] find it with: netstat -ano | findstr \":8775\"");
      console.warn("[control] stop it with: taskkill /PID <PID> /F");
    }
  }
}

async function waitForPort(label, port, timeoutMs = 10_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await isListening(port)) {
      console.log(`[${label}] ready on http://127.0.0.1:${port}`);
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  console.error(`[${label}] did not start listening on port ${port} within ${Math.round(timeoutMs / 1000)}s`);
  return false;
}

function prefixOutput(label, stream) {
  let pending = "";
  stream.on("data", (chunk) => {
    pending += chunk.toString();
    const lines = pending.split(/\r?\n/);
    pending = lines.pop() ?? "";
    for (const line of lines) {
      if (line) console.log(`[${label}] ${line}`);
    }
  });
  stream.on("end", () => {
    if (pending) console.log(`[${label}] ${pending}`);
  });
}

function start(label, command, args, cwd) {
  const useShell = isWindows && /\.cmd$/i.test(command);
  let child;
  try {
    child = spawn(command, args, {
      cwd,
      env: childEnv(),
      shell: useShell,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    console.error(`[${label}] failed to start ${command}: ${error.message}`);
    shutdown(1);
    return null;
  }
  children.push(child);
  prefixOutput(label, child.stdout);
  prefixOutput(label, child.stderr);
  child.on("exit", (code, signal) => {
    if (!shuttingDown) {
      console.error(`[${label}] exited with ${signal ?? code}`);
      shutdown(code || 1);
    }
  });
  return child;
}

function shutdown(code = 0) {
  shuttingDown = true;
  for (const child of children) {
    if (!child.killed) child.kill();
  }
  process.exitCode = code;
}

process.once("SIGINT", () => shutdown(130));
process.once("SIGTERM", () => shutdown(143));

if (await isListening(8765)) {
  console.log("[trends] already listening on http://127.0.0.1:8765/api");
} else {
  start(
    "trends",
    pythonCommand,
    ["-m", "trends.cli", "serve", "--port", "8765", "--no-open"],
    nextGenRoot,
  );
  waitForPort("trends", 8765);
}

if (await isListening(8775)) {
  console.log("[control] already listening on http://127.0.0.1:8775/control/api");
  await warnIfStaleControlBackend();
} else {
  start(
    "control",
    pythonCommand,
    ["-m", "control.cli", "serve-workbench", "--port", "8775"],
    nextGenRoot,
  );
  waitForPort("control", 8775);
}

if (await isListening(5173)) {
  console.log("[ui] already listening on http://127.0.0.1:5173");
} else {
  start("ui", npmCommand, ["run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"], uiRoot);
  waitForPort("ui", 5173);
}
