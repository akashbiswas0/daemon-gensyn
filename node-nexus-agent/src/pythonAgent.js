import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { logStep, truncate } from "./logging.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const sourceRoot = path.resolve(__dirname, "..");
const runtimeRoot = process.env.NODE_NEXUS_RUNTIME_DIR
  ? path.resolve(process.env.NODE_NEXUS_RUNTIME_DIR)
  : sourceRoot;
const agentPath = path.join(sourceRoot, "python-agent", "agent.py");
const defaultVenvPython =
  process.platform === "win32"
    ? path.join(runtimeRoot, "python-agent", "venv", "Scripts", "python.exe")
    : path.join(runtimeRoot, "python-agent", "venv", "bin", "python3");
const venvPython = process.env.NODE_NEXUS_PYTHON_BIN || defaultVenvPython;

function parseInfoValue(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) {
    return "";
  }

  if (
    trimmed.startsWith("[") ||
    trimmed.startsWith("{") ||
    trimmed === "true" ||
    trimmed === "false" ||
    trimmed === "null"
  ) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return value;
    }
  }

  return value;
}

export function parsePythonInfo(stdout) {
  return Object.fromEntries(
    stdout
      .split(/\r?\n/)
      .filter((line) => line.startsWith("INFO|"))
      .map((line) => line.slice("INFO|".length))
      .map((entry) => {
        const separatorIndex = entry.indexOf("=");
        if (separatorIndex === -1) {
          return [entry, true];
        }

        return [
          entry.slice(0, separatorIndex),
          parseInfoValue(entry.slice(separatorIndex + 1))
        ];
      })
  );
}

export function parsePythonError(stdout) {
  return stdout
    .split(/\r?\n/)
    .find((line) => line.startsWith("ERROR|"))
    ?.slice("ERROR|".length)
    .trim();
}

function requirePythonAgent(requestId) {
  if (existsSync(venvPython)) {
    return;
  }

  logStep(requestId, "python-agent", "fail", {
    reason: "missing-venv",
    venvPython
  });

  throw new Error(
    `Python virtual environment not found at ${venvPython}. Run npm run setup first.`
  );
}

export function runPythonAgent({ url, task, requestId }) {
  return new Promise((resolve, reject) => {
    try {
      requirePythonAgent(requestId);
    } catch (error) {
      reject(error);
      return;
    }

    logStep(requestId, "python-agent", "start", {
      agentPath,
      timeoutMs: 10 * 60 * 1000
    });

    execFile(
      venvPython,
      [agentPath, url, task, "--request-id", requestId],
      {
        cwd: runtimeRoot,
        env: process.env,
        timeout: 10 * 60 * 1000,
        maxBuffer: 1024 * 1024 * 10
      },
      (error, stdout, stderr) => {
        if (error) {
          const pythonError = parsePythonError(stdout);
          const details = [stderr.trim(), stdout.trim()].filter(Boolean).join("\n");
          logStep(requestId, "python-agent", "fail", {
            error: truncate(pythonError || details || error.message),
            exitCode: error.code ?? null,
            signal: error.signal ?? null
          });
          reject(new Error(pythonError || details || error.message));
          return;
        }

        const successLine = stdout
          .split(/\r?\n/)
          .find((line) => line.startsWith("SUCCESS|"));

        if (!successLine) {
          logStep(requestId, "python-agent", "fail", {
            reason: "missing-success-marker",
            stdout: truncate(stdout)
          });
          reject(new Error(`Python agent did not return SUCCESS marker. stdout: ${stdout}`));
          return;
        }

        const reportPath = successLine.slice("SUCCESS|".length).trim();
        const info = parsePythonInfo(stdout);
        logStep(requestId, "python-agent", "success", {
          reportPath,
          ...info,
          stderr: stderr.trim() ? truncate(stderr.trim()) : undefined
        });

        resolve({
          reportPath: info.reportPath || reportPath,
          artifactDir: info.artifactDir,
          screenshots: Array.isArray(info.screenshots) ? info.screenshots : [],
          finalUrl: info.finalUrl,
          info,
          stdout,
          stderr
        });
      }
    );
  });
}
