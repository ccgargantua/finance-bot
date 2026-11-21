import logging
from dataclasses import dataclass

import anthropic
from anthropic import AsyncAnthropic

from finance_bot.approval import Approver
from finance_bot.config import MAX_TOKENS, MAX_TOOL_ITERATIONS, Config
from finance_bot.conversation import Conversation
from finance_bot.tools.registry import TOOL_SCHEMAS, dispatch

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class TurnResult:
    text: str
    ok: bool


def extract_text(content) -> str:
    return "\n".join(b.text for b in content if b.type == "text").strip()


async def call_model(client: AsyncAnthropic, cfg: Config, conv: Conversation):
    async with client.beta.messages.stream(
        model=cfg.model,
        max_tokens=MAX_TOKENS,
        system=conv.system,
        tools=TOOL_SCHEMAS,
        messages=conv.messages,
        thinking={"type": "adaptive"},
        output_config={"effort": cfg.effort},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    ) as stream:
        return await stream.get_final_message()


async def run_tools(cfg: Config, approver: Approver, content) -> dict:
    results = []
    for block in content:
        if block.type != "tool_use":
            continue
        output = await dispatch(cfg, approver, block.name, block.input)
        log.info("tool name=%s id=%s is_error=%s", block.name, block.id, output.is_error)
        result = {"type": "tool_result", "tool_use_id": block.id, "content": output.text}
        if output.is_error:
            result["is_error"] = True
        results.append(result)
    return {"role": "user", "content": results}


async def run_turn(client: AsyncAnthropic, cfg: Config, approver: Approver, conv: Conversation, user_text: str) -> TurnResult:
    """Appends to conv; on any failure truncates conv back to its state before the turn (the tail was never replayed, so thinking blocks stay valid)."""
    mark = conv.mark()
    conv.append({"role": "user", "content": user_text})
    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            response = await call_model(client, cfg, conv)
            log.info("response stop_reason=%s model=%s request_id=%s", response.stop_reason, response.model, getattr(response, "_request_id", None))
            if response.stop_reason == "refusal":
                conv.rollback_to(mark)
                return TurnResult("The model declined this request.", False)
            if response.stop_reason == "max_tokens":
                conv.rollback_to(mark)
                return TurnResult("Reply cut off (max_tokens reached); this exchange was dropped from history. Try a narrower request.", False)
            conv.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "tool_use":
                conv.append(await run_tools(cfg, approver, response.content))
                continue
            if response.stop_reason == "pause_turn":
                continue
            return TurnResult(extract_text(response.content) or "(empty reply)", True)
        return TurnResult("Stopped: tool iteration limit reached. Tell me to continue if needed.", True)
    except anthropic.APIError as e:
        conv.rollback_to(mark)
        log.error("anthropic error type=%s request_id=%s msg=%s", type(e).__name__, getattr(e, "request_id", None), e)
        return TurnResult(f"API error ({type(e).__name__}); this exchange was dropped from history. Try again.", False)
    except BaseException:
        conv.rollback_to(mark)
        raise
