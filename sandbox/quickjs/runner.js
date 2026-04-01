const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const {
  newQuickJSAsyncWASMModule,
  RELEASE_ASYNC,
  shouldInterruptAfterDeadline,
} = require("quickjs-emscripten");

let activeSessionId = null;
let callCounter = 0;
const pendingCalls = new Map();
let quickJSModulePromise = null;

const PLAN_RUNTIME_SOURCE = fs.readFileSync(
  path.join(__dirname, "plan_runtime.js"),
  "utf8",
);

const EXECUTION_SOURCE = `
JSON.stringify(
  executePlan(
    JSON.parse(__planJson),
    JSON.parse(__contextJson),
    {
      tool(name, args) {
        return JSON.parse(__hostTool(name, JSON.stringify(args)))
      },
    },
    JSON.parse(__limitsJson),
  ),
)
`;

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function sendAndExit(message, code) {
  process.stdout.write(`${JSON.stringify(message)}\n`, () => {
    process.exit(code);
  });
}

function errorMessage(error) {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return String(error);
}

async function hostTool(toolName, args) {
  const callId = `call_${++callCounter}`;
  return await new Promise((resolve, reject) => {
    pendingCalls.set(callId, { resolve, reject });
    send({
      type: "tool_call",
      session_id: activeSessionId,
      call_id: callId,
      tool: toolName,
      args,
    });
  });
}

async function getQuickJSModule() {
  if (!quickJSModulePromise) {
    quickJSModulePromise = newQuickJSAsyncWASMModule(RELEASE_ASYNC);
  }
  return await quickJSModulePromise;
}

function setGlobalJson(context, name, value) {
  const handle = context.newString(JSON.stringify(value ?? {}));
  try {
    context.setProp(context.global, name, handle);
  } finally {
    handle.dispose();
  }
}

async function runPlanInQuickJS(message, timeoutMs) {
  const QuickJS = await getQuickJSModule();
  const context = QuickJS.newContext();
  const memoryLimitBytes = Number(process.env.SANDBOX_MEMORY_LIMIT_BYTES ?? 16 * 1024 * 1024);

  if (memoryLimitBytes > 0) {
    context.runtime.setMemoryLimit(memoryLimitBytes);
  }
  context.runtime.setInterruptHandler(
    shouldInterruptAfterDeadline(Date.now() + timeoutMs),
  );

  const hostToolHandle = context.newAsyncifiedFunction(
    "__hostTool",
    async (toolNameHandle, argsJsonHandle) => {
      const toolName = context.getString(toolNameHandle);
      const argsJson = context.getString(argsJsonHandle);
      const args = argsJson ? JSON.parse(argsJson) : {};
      const result = await hostTool(toolName, args);
      return context.newString(JSON.stringify(result));
    },
  );

  try {
    context.setProp(context.global, "__hostTool", hostToolHandle);
    setGlobalJson(context, "__planJson", message.plan || {});
    setGlobalJson(context, "__contextJson", message.context || {});
    setGlobalJson(context, "__limitsJson", message.limits || {});

    const loadResult = await context.evalCodeAsync(PLAN_RUNTIME_SOURCE, "plan_runtime.js");
    context.unwrapResult(loadResult).dispose();

    const executionResult = await context.evalCodeAsync(
      EXECUTION_SOURCE,
      "plan_exec.js",
    );
    const payloadHandle = context.unwrapResult(executionResult);
    try {
      return JSON.parse(context.getString(payloadHandle));
    } finally {
      payloadHandle.dispose();
    }
  } finally {
    hostToolHandle.dispose();
    context.dispose();
  }
}

async function handleRunPlan(message) {
  if (activeSessionId) {
    sendAndExit(
      {
        type: "error",
        session_id: message.session_id,
        error: "Sandbox runner is already handling a session",
      },
      1,
    );
    return;
  }

  activeSessionId = String(message.session_id || "");
  const limits = message.limits || {};
  const timeoutMs = Number(limits.timeout_ms ?? 4000);
  const timeout = setTimeout(() => {
    sendAndExit(
      {
        type: "error",
        session_id: activeSessionId,
        error: `Sandbox runner timed out after ${timeoutMs}ms`,
      },
      1,
    );
  }, timeoutMs);

  try {
    const result = await runPlanInQuickJS(message, timeoutMs);
    clearTimeout(timeout);
    sendAndExit(
      {
        type: "done",
        session_id: activeSessionId,
        payload: result,
      },
      result.status === "ok" ? 0 : 1,
    );
  } catch (error) {
    clearTimeout(timeout);
    sendAndExit(
      {
        type: "error",
        session_id: activeSessionId,
        error: errorMessage(error),
      },
      1,
    );
  }
}

function handleToolResult(message) {
  const pending = pendingCalls.get(message.call_id);
  if (!pending) {
    return;
  }
  pendingCalls.delete(message.call_id);
  if (message.ok) {
    pending.resolve(message.result);
    return;
  }
  pending.reject(new Error(String(message.error || "Tool call failed")));
}

rl.on("line", (line) => {
  if (!line.trim()) {
    return;
  }

  let message;
  try {
    message = JSON.parse(line);
  } catch (error) {
    sendAndExit(
      {
        type: "error",
        session_id: activeSessionId,
        error: `Invalid JSON input: ${errorMessage(error)}`,
      },
      1,
    );
    return;
  }

  if (message.type === "run_plan") {
    void handleRunPlan(message);
    return;
  }

  if (message.type === "tool_result") {
    handleToolResult(message);
    return;
  }

  sendAndExit(
    {
      type: "error",
      session_id: activeSessionId,
      error: `Unsupported message type: ${String(message.type)}`,
    },
    1,
  );
});

rl.on("close", () => {
  if (activeSessionId && pendingCalls.size > 0) {
    for (const pending of pendingCalls.values()) {
      pending.reject(new Error("Sandbox runner input closed"));
    }
  }
});
