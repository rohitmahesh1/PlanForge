const REF_PATTERN = /\$\{([^}]+)\}/g;

function executePlan(plan, context = {}, host = {}, limits = {}) {
  const steps = plan?.steps;
  const maxSteps = Number(limits?.max_steps ?? 8);
  const maxToolCalls = Number(limits?.max_tool_calls ?? maxSteps);

  if (!Array.isArray(steps)) {
    return errorResult("Plan must contain a steps array");
  }
  if (steps.length > maxSteps) {
    return errorResult(`Plan exceeds max_steps=${maxSteps}`);
  }
  if (!host || typeof host.tool !== "function") {
    return errorResult("Host tool bridge is unavailable");
  }

  const state = { context: cloneJson(context) };
  const trace = [];
  const opIds = [];
  let lastResult = null;
  let toolCalls = 0;

  for (let index = 0; index < steps.length; index += 1) {
    const rawStep = steps[index];
    if (!isPlainObject(rawStep)) {
      return errorResult(`Step ${index + 1} must be an object`, trace, opIds);
    }

    const stepId = String(rawStep.id || `step_${index + 1}`);
    const condition = rawStep.if;
    if (condition !== undefined && !evalCondition(condition, state)) {
      trace.push({
        step_id: stepId,
        kind: "condition",
        skipped: true,
      });
      continue;
    }

    if (Object.prototype.hasOwnProperty.call(rawStep, "return")) {
      const returned = resolveValue(rawStep.return, state);
      state[stepId] = returned;
      trace.push({
        step_id: stepId,
        kind: "return",
        result: returned,
        skipped: false,
      });
      return {
        status: "ok",
        trace,
        op_ids: opIds,
        result: returned,
      };
    }

    const toolName = rawStep.tool;
    if (typeof toolName !== "string" || !toolName.trim()) {
      return errorResult(`Step ${stepId} is missing a tool`, trace, opIds);
    }

    const args = resolveValue(rawStep.args ?? {}, state);
    if (!isPlainObject(args)) {
      return errorResult(`Step ${stepId} args must resolve to an object`, trace, opIds);
    }

    toolCalls += 1;
    if (toolCalls > maxToolCalls) {
      return errorResult(`Plan exceeds max_tool_calls=${maxToolCalls}`, trace, opIds);
    }

    try {
      const result = host.tool(toolName, args);
      state[stepId] = result;
      lastResult = result;
      collectOpIds(result, opIds);
      trace.push({
        step_id: stepId,
        kind: "tool",
        tool: toolName,
        args,
        result,
        skipped: false,
      });
    } catch (error) {
      trace.push({
        step_id: stepId,
        kind: "tool",
        tool: toolName,
        args,
        error: errorMessage(error),
        skipped: false,
      });
      return errorResult(errorMessage(error), trace, opIds);
    }
  }

  const finalResult = Object.prototype.hasOwnProperty.call(plan, "return")
    ? resolveValue(plan.return, state)
    : lastResult;
  return {
    status: "ok",
    trace,
    op_ids: opIds,
    result: finalResult,
  };
}

function resolveValue(value, state) {
  if (Array.isArray(value)) {
    return value.map((item) => resolveValue(item, state));
  }

  if (isPlainObject(value)) {
    if (Object.keys(value).length === 1 && typeof value.$ref === "string") {
      return lookupRef(value.$ref, state);
    }
    const resolved = {};
    for (const [key, item] of Object.entries(value)) {
      resolved[key] = resolveValue(item, state);
    }
    return resolved;
  }

  if (typeof value === "string") {
    if (value.startsWith("$") && !value.includes("${")) {
      return lookupRef(value, state);
    }
    if (value.includes("${")) {
      return value.replace(REF_PATTERN, (_, rawRef) => {
        const resolved = lookupRef(rawRef, state);
        if (typeof resolved === "object" && resolved !== null) {
          throw new Error("Cannot interpolate structured values into a string");
        }
        return resolved == null ? "" : String(resolved);
      });
    }
  }

  return value;
}

function lookupRef(rawRef, state) {
  const ref = rawRef.startsWith("$") ? rawRef.slice(1) : rawRef;
  if (!ref) {
    throw new Error("Empty reference");
  }

  let current = state;
  for (const part of ref.split(".")) {
    if (part === "length") {
      current = current.length;
      continue;
    }

    if (Array.isArray(current)) {
      const index = Number(part);
      if (!Number.isInteger(index) || index < 0 || index >= current.length) {
        throw new Error(`Invalid list reference segment: ${part}`);
      }
      current = current[index];
      continue;
    }

    if (isPlainObject(current)) {
      if (!Object.prototype.hasOwnProperty.call(current, part)) {
        throw new Error(`Unknown reference segment: ${part}`);
      }
      current = current[part];
      continue;
    }

    throw new Error(`Cannot dereference ${part} from ${typeof current}`);
  }

  return current;
}

function evalCondition(expr, state) {
  if (typeof expr === "boolean") {
    return expr;
  }
  if (!isPlainObject(expr)) {
    return Boolean(resolveValue(expr, state));
  }
  if (Object.prototype.hasOwnProperty.call(expr, "not")) {
    return !evalCondition(expr.not, state);
  }
  if (Object.prototype.hasOwnProperty.call(expr, "exists")) {
    try {
      return resolveValue(expr.exists, state) != null;
    } catch {
      return false;
    }
  }
  if (Object.prototype.hasOwnProperty.call(expr, "equals")) {
    const [left, right] = expr.equals;
    return resolveValue(left, state) === resolveValue(right, state);
  }
  if (Object.prototype.hasOwnProperty.call(expr, "len_equals")) {
    const [left, right] = expr.len_equals;
    return resolveValue(left, state).length === Number(resolveValue(right, state));
  }
  if (Object.prototype.hasOwnProperty.call(expr, "len_gte")) {
    const [left, right] = expr.len_gte;
    return resolveValue(left, state).length >= Number(resolveValue(right, state));
  }
  if (Object.prototype.hasOwnProperty.call(expr, "len_lte")) {
    const [left, right] = expr.len_lte;
    return resolveValue(left, state).length <= Number(resolveValue(right, state));
  }
  if (Array.isArray(expr.all)) {
    return expr.all.every((item) => evalCondition(item, state));
  }
  if (Array.isArray(expr.any)) {
    return expr.any.some((item) => evalCondition(item, state));
  }
  throw new Error(`Unsupported condition: ${JSON.stringify(expr)}`);
}

function collectOpIds(result, sink) {
  if (!isPlainObject(result)) {
    return;
  }
  if (typeof result.op_id === "string") {
    sink.push(result.op_id);
  }
  if (Array.isArray(result.op_ids)) {
    for (const opId of result.op_ids) {
      if (typeof opId === "string") {
        sink.push(opId);
      }
    }
  }
}

function errorResult(error, trace = [], opIds = []) {
  return {
    status: "error",
    trace,
    op_ids: opIds,
    error: String(error),
  };
}

function errorMessage(error) {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return String(error);
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

globalThis.executePlan = executePlan;

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    executePlan,
  };
}
