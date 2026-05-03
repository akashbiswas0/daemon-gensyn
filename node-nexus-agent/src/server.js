import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import dotenv from "dotenv";
import express from "express";

import { applyArtifactRetention } from "./artifacts.js";
import { logEvent, logStep, truncate } from "./logging.js";
import { runPythonAgent } from "./pythonAgent.js";
import {
  uploadReportToZeroGStorage,
  validateZeroGStorageConfig
} from "./zeroGStorage.js";

dotenv.config();

const __filename = fileURLToPath(import.meta.url);

export function createApp() {
  const app = express();

  app.use(express.json({ limit: "1mb" }));

  app.use((request, response, next) => {
    const requestId = crypto.randomUUID();
    const startedAt = Date.now();
    request.requestId = requestId;

    logEvent({
      requestId,
      event: "request",
      status: "received",
      details: {
        method: request.method,
        path: request.path,
        ip: request.ip
      }
    });

    response.on("finish", () => {
      logEvent({
        requestId,
        event: "request",
        status: response.statusCode >= 400 ? "fail" : "success",
        details: {
          method: request.method,
          path: request.path,
          statusCode: response.statusCode,
          durationMs: Date.now() - startedAt
        }
      });
    });

    next();
  });

  app.get("/health", (request, response) => {
    logStep(request.requestId, "health", "success");
    response.json({
      ok: true,
      service: "pookie-node-orchestrator"
    });
  });

  app.post("/mcp/execute", async (request, response) => {
    const requestId = request.requestId;
    const { url, task } = request.body ?? {};

    logStep(requestId, "parse-request", "start", {
      hasUrl: typeof url === "string",
      hasTask: typeof task === "string",
      taskLength: typeof task === "string" ? task.length : 0
    });

    if (typeof url !== "string" || typeof task !== "string") {
      logStep(requestId, "parse-request", "fail", {
        reason: "invalid-body"
      });
      response.status(400).json({
        ok: false,
        error: "Expected JSON body with string fields: url, task"
      });
      return;
    }

    logStep(requestId, "parse-request", "success", {
      url,
      taskLength: task.length
    });

    const storageValidation = validateZeroGStorageConfig();
    if (!storageValidation.ok) {
      logStep(requestId, "0g-storage-config", "fail", {
        missing: storageValidation.missing
      });
      response.status(500).json({
        ok: false,
        error: `Missing 0G Storage configuration: ${storageValidation.missing.join(", ")}`
      });
      return;
    }

    logStep(requestId, "0g-storage-config", "success", {
      storageRpcUrl: storageValidation.config.storageRpcUrl,
      storageIndexerRpc: storageValidation.config.storageIndexerRpc
    });

    try {
      const agentResult = await runPythonAgent({ url, task, requestId });

      logStep(requestId, "0g-upload", "start", {
        reportPath: agentResult.reportPath
      });
      const upload = await uploadReportToZeroGStorage(agentResult.reportPath);
      logStep(requestId, "0g-upload", "success", {
        reportHash: upload.reportHash,
        reportUri: upload.reportUri,
        txHash: upload.txHash
      });
      applyArtifactRetention({
        screenshots: agentResult.screenshots
      });

      logStep(requestId, "response", "success", {
        reportHash: upload.reportHash,
        reportUri: upload.reportUri,
        reportPath: agentResult.reportPath
      });
      response.json({
        ok: true,
        reportHash: upload.reportHash,
        reportUri: upload.reportUri,
        txHash: upload.txHash,
        reportPath: agentResult.reportPath,
        artifactDir: agentResult.artifactDir,
        screenshots: agentResult.screenshots
      });
    } catch (error) {
      logStep(requestId, "response", "fail", {
        error: truncate(error instanceof Error ? error.message : String(error))
      });
      response.status(500).json({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  });

  return app;
}

export function startServer({ port = Number(process.env.PORT ?? 8080) } = {}) {
  const app = createApp();

  return app.listen(port, () => {
    console.log(`Pookie Node orchestrator listening on http://localhost:${port}`);
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  startServer();
}
