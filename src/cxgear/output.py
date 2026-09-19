"""Codex exec's public JSONL event projection; never emits internal messages."""
import json
import re
import sys


def snake(value):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def item_event(item):
    kind = item.get("type")
    result = {"id": item["id"]}
    status = snake(item.get("status", "completed"))
    if kind == "agentMessage":
        return {**result, "type": "agent_message", "text": item.get("text", "")}
    if kind == "commandExecution":
        return {**result, "type": "command_execution", "command": item.get("command", ""), "aggregated_output": item.get("aggregatedOutput") or "", "exit_code": item.get("exitCode"), "status": status}
    if kind == "fileChange":
        changes = [{"path": c["path"], "kind": (c.get("kind") or {}).get("type", "update") if isinstance(c.get("kind"), dict) else c.get("kind", "update")} for c in item.get("changes", [])]
        return {**result, "type": "file_change", "changes": changes, "status": status}
    if kind == "mcpToolCall":
        return {**result, "type": "mcp_tool_call", "server": item.get("server", ""), "tool": item.get("tool", ""), "arguments": item.get("arguments", {}), "result": item.get("result"), "error": item.get("error"), "status": status}
    if kind == "webSearch":
        return {**result, "type": "web_search", "query": item.get("query", ""), "action": item.get("action", {})}
    if kind == "collabAgentToolCall":
        return {**result, "type": "collab_tool_call", "tool": snake(item.get("tool", "")), "sender_thread_id": item.get("senderThreadId", ""), "receiver_thread_ids": item.get("receiverThreadIds", []), "prompt": item.get("prompt"), "agents_states": item.get("agentsStates", {}), "status": status}
    return None


class Output:
    def __init__(self, json_mode):
        self.json = json_mode
        self.last_message = ""
        self.usage = {}

    def emit(self, kind, **data):
        if self.json:
            print(json.dumps({"type": kind, **data}, ensure_ascii=False), flush=True)
        elif kind == "thread.started":
            print("session id: " + data["thread_id"], file=sys.stderr, flush=True)
        elif kind == "item.completed" and data["item"]["type"] == "agent_message":
            print(data["item"]["text"], flush=True)
        elif kind in ("turn.failed", "error"):
            print(data.get("message") or data.get("error", {}).get("message", "Codex turn failed"), file=sys.stderr, flush=True)

    def observe(self, message):
        method, params = message.get("method"), message.get("params", {})
        if method in ("item/started", "item/completed"):
            item = item_event(params["item"])
            if item:
                if method == "item/completed" and item["type"] == "agent_message":
                    self.last_message = item["text"]
                self.emit(method.replace("/", "."), item=item)
        elif method == "thread/tokenUsage/updated":
            self.usage = params.get("tokenUsage", {}).get("total", {})
        elif method == "turn/plan/updated":
            self.emit("item.updated", item={"id": "plan", "type": "todo_list", "items": [{"text": p["step"], "completed": p["status"] == "completed"} for p in params.get("plan", [])]})
        elif method == "error":
            self.emit("error", message=params.get("error", {}).get("message", "Codex error"))

    def completed(self):
        fields = {"inputTokens": "input_tokens", "cachedInputTokens": "cached_input_tokens", "cacheWriteInputTokens": "cache_write_input_tokens", "outputTokens": "output_tokens", "reasoningOutputTokens": "reasoning_output_tokens"}
        # Native exec reports the thread's cumulative total, including resume.
        usage = {v: self.usage.get(k, 0) for k, v in fields.items()}
        self.emit("turn.completed", usage=usage)
