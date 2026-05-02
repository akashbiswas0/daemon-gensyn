import { rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const sourceRoot = path.resolve(__dirname, "..");
const runtimeRoot = process.env.NODE_NEXUS_RUNTIME_DIR
  ? path.resolve(process.env.NODE_NEXUS_RUNTIME_DIR)
  : sourceRoot;

function resolveArtifactPath(artifactPath) {
  return path.isAbsolute(artifactPath)
    ? artifactPath
    : path.resolve(runtimeRoot, artifactPath);
}

export function applyArtifactRetention({ screenshots }, env = process.env) {
  if (String(env.ARTIFACT_RETENTION ?? "keep").trim().toLowerCase() !== "delete_screenshots") {
    return;
  }

  for (const screenshot of screenshots ?? []) {
    rmSync(resolveArtifactPath(screenshot), { force: true });
  }
}
