# SPDX-License-Identifier: MIT
"""
Run an agent as a subprocess of the Claude command, and journal what the call cost.

A driver names its kinds of call. `Caller` holds the agent, the answer shape and the turn budget a kind of call takes.
`converge_prose` settles the prose between a critic and a comparator, and it reaches those agents through here.

The agent reads its message over standard input. The command writes a JSON line per event. The init line lists the tools
the agent holds. An agent lacking a tool `write_agent_prompts.TOOLS` names stops the run. A record of the call joins the
journal, and `began` tells the runs apart.
"""

import asyncio
import dataclasses
import json
import os
import signal
import tempfile
import time

from collections.abc import Callable
from typing import Any

import gate
import settling_state
import write_agent_prompts

# The [settings](https://code.claude.com/docs/en/cli-reference) an agent call loads.
_SETTING_SOURCES = "project,local"

# The [environment](https://code.claude.com/docs/en/env-vars) an agent call adds. The command otherwise puts the user's
# instruction file, the memory index and the git instructions into the first message. That text sits past the cached
# prefix, and a call writes it to the cache again. https://platform.claude.com/docs/en/build-with-claude/prompt-caching
# says how the cache treats such a change. `write_agent_prompts` writes the rules an agent needs into the agent
# definition.
_AGENT_ENVIRONMENT = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
}

# The fields of a call record a run adds up, beside the count of calls and the count of dead calls.
_A_TOTAL = ("items", "messageBytes", "proseBytes", "answerBytes", "seconds", "cost")

# The token counts a run adds up. An entry pairs the name a total takes with the name the command gives that count.
_A_TOKEN = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cacheRead": "cache_read_input_tokens",
    "cacheWrite": "cache_creation_input_tokens",
}

# The statuses after which a run stops and a later run continues from the state. `converge_prose` and `converge_claims`
# keep that state. https://platform.claude.com/docs/en/api/errors lists the statuses.
_A_WALL = (429, 529)

# The seconds a call may take.
_A_CALL_BUDGET = 1800.0

# The bytes a line of the command's output may hold. The command writes a whole message on a line.
_A_LINE_LIMIT = 1 << 26

# The name of the journal taking a record per call. A run appends to this journal. The field `run` of a record holds
# `caller.began`, and that field tells the runs apart.
_SPENT = "calls.jsonl"

# The status a driver leaves where a later run continues from the stop.
RESUMABLE = 3

# The status a driver leaves where a person investigates the stop.
FAILED = 1


@dataclasses.dataclass
class Caller:
    """
    The state a driver's calls share.

    `agents` maps a kind of call to the agent it runs as. `schemas` maps a kind to the answer shape it asks for. `turns`
    maps a kind to the turns an agent of that kind may take. `children` holds the subprocesses in flight.
    """

    into: str  # the directory taking the journal of calls.
    model: str  # the model the agents run. The empty string leaves the default in place.
    began: str  # the clock this run started on. A record of a call names that clock. The runs then stay apart.
    agents: dict[str, str]
    schemas: dict[str, Any]
    turns: dict[str, int]
    calls: int = 0
    children: set[Any] = dataclasses.field(default_factory=set)
    is_killing: bool = False  # whether the run killed its agents. `converge_prose` drops the result of a killed call.
    stopped: str = ""  # the reason the run stopped. The empty string says the run is still going.
    is_resumable: bool = False  # whether the driver exits with `RESUMABLE` rather than `FAILED`.


def try_stopping(caller: Caller, why: str, is_resumable: bool) -> bool:
    """Stop the run for `why`. This answers false where the run already stopped. A driver logs a true answer."""
    if caller.stopped:
        return False
    caller.stopped = why
    caller.is_resumable = is_resumable
    return True


def _journal(caller: Caller) -> str:
    """
    The file taking a record per call. A run leaves the records of an earlier run in place, and `began` names a run.
    """
    return os.path.join(caller.into, _SPENT)


def _records_of(caller: Caller) -> list[dict[str, Any]]:
    """The records written by the run that `caller.began` names. A record of an earlier run stays out."""
    return [one for one in settling_state.lines_read(_journal(caller)) if one.get("run") == caller.began]


def _kept(
    caller: Caller, streamed: list[dict[str, Any]], reply: dict[str, Any], folder: str = "dead"
) -> dict[str, Any]:
    """
    Write the messages of a call to a file in `folder` under `caller.into`. Answer with `reply` and a `transcript`
    naming that file. A streamed `session_id` names the file. A call streaming no `session_id` takes a file name from
    `caller.began` and `caller.calls`. `_called` passes `--no-session-persistence`. The command then keeps no
    transcript. https://code.claude.com/docs/en/cli-reference documents `--no-session-persistence`.

    A dead call writes into `dead/`. A call that `spoken` keeps writes into `_KEPT`, and that folder holds the newest
    `_MOST_KEPT` files.
    """
    session = next((one["session_id"] for one in streamed if "session_id" in one), f"{caller.began}-{caller.calls}")
    held = os.path.join(caller.into, folder)
    os.makedirs(held, exist_ok=True)
    path = os.path.join(held, f"{session}.jsonl")
    settling_state.rewritten(path, streamed)
    if folder == _KEPT:
        for one in gate.files_by_age(held)[:-_MOST_KEPT]:
            one.unlink()
    return {**reply, "transcript": path}


# A seed's reply holds this field. `_replied` sets it once the seed has written the cached prefix.
_SEEDED = "is_seeded"

# The folder under a run's directory holding the transcripts of the calls `spoken` keeps.
_KEPT = "kept"

# The most transcripts `_KEPT` holds. A run deletes the oldest past this.
_MOST_KEPT = 200


def _spent(kind: str, message: str, counts: tuple[int, int], reply: dict[str, Any], seconds: float) -> dict[str, Any]:
    """
    The record of a call. `counts` holds the items of the batch and the bytes of prose they hold.

    `usage` holds the command's own `usage` record under the names the command gave its fields. `seconds` covers the
    subprocess. `durationMs` holds the time the command reported for itself. `subtype` says how the agent's run ended,
    and `session` holds the id the command gave that run. `transcript` names the file `_kept` wrote for a dead call or
    for a call `spoken` keeps. https://code.claude.com/docs/en/agent-sdk/agent-loop lists the subtypes.
    """
    usage = reply.get("usage")
    return {
        "kind": kind,
        "items": counts[0],
        "messageBytes": len(message.encode()),
        "proseBytes": counts[1],
        "answerBytes": len(json.dumps(reply.get("structured_output") or "").encode()),
        "seconds": seconds,
        "durationMs": reply.get("duration_ms"),
        "apiMs": reply.get("duration_api_ms"),
        "turns": reply.get("num_turns"),
        "cost": reply.get("total_cost_usd"),
        "isError": bool(reply.get("is_error")),
        "status": reply.get("api_error_status"),
        "subtype": reply.get("subtype"),
        "session": reply.get("session_id"),
        "transcript": reply.get("transcript"),
        "usage": usage if isinstance(usage, dict) else {},
    }


async def _replied(
    caller: Caller,
    kind: str,
    run: Any,
    streamed: list[dict[str, Any]] | None = None,
    is_a_seed: bool = False,
) -> dict[str, Any]:
    """
    The result line the agent process `run` writes, or a dead reply naming the fault. `streamed` takes the message lines
    as they arrive.

    The init line lists the tools the agent holds. An agent lacking a tool `write_agent_prompts.TOOLS` names stops the
    run, and the run kills its agents. A command writing a result line and no init line stops the run too.

    `is_a_seed` stops the call at its first assistant message. The request behind that message has written the cached
    prefix, as https://platform.claude.com/docs/en/build-with-claude/prompt-caching says. The reply says so.
    """
    agent = caller.agents[kind]
    wanted = write_agent_prompts.TOOLS[f"{agent}.md"]
    aside = asyncio.create_task(run.stderr.read())
    is_checked = False
    result: dict[str, Any] = {}
    async for line in run.stdout:
        text = line.decode(errors="replace").strip()
        try:
            held = json.loads(text)
        except json.JSONDecodeError:  # not-a-failure: The error reply below names the line the command wrote.
            run.kill()
            await run.wait()
            return {"is_error": True, "result": f"the command wrote no JSON: {text[:200]}"}
        if streamed is not None:
            streamed.append(held)
        if held.get("type") == "system" and held.get("subtype") == "init":
            missing = [tool for tool in wanted if tool not in held.get("tools", [])]
            if missing:
                why = f"the agent {agent} lacks {', '.join(missing)}. Its MCP servers: {held.get('mcp_servers')}"
                try_stopping(caller, why, False)
                killed(caller)
                await run.wait()
                return {"is_error": True, "result": why}
            is_checked = True
        elif held.get("type") == "assistant" and is_a_seed:
            run.kill()
            await run.wait()
            return {_SEEDED: True, "result": "the first answer of the seed wrote the cached prefix"}
        elif held.get("type") == "result":
            result = held
    await run.wait()
    said = (await aside).decode(errors="replace").strip()
    if not result:
        return {"is_error": True, "result": said or f"the command wrote no answer and left with {run.returncode}"}
    if not is_checked:
        why = f"the command wrote no init line for the agent {agent}. The tools of that agent went unchecked"
        try_stopping(caller, why, False)
        killed(caller)
        return {"is_error": True, "result": why}
    return result


async def _called(
    caller: Caller,
    kind: str,
    message: str,
    environment: dict[str, str],
    is_kept: bool = False,
    is_a_seed: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """
    The answer the agent for `kind` gives `message`, and the reply the command wrapped it in.

    The answer is `None` where the call died. A call past its budget dies. This function then kills its subprocess. A
    call that answers keeps its transcript in `_KEPT` where `is_kept` holds. A call `is_a_seed` marks answers `None`
    once `_replied` stops it.
    """
    named = [
        "claude",
        "-p",
        "--agent",
        caller.agents[kind],
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(caller.schemas[kind]),
        "--permission-prompts",
        "none",
        "--max-turns",
        str(caller.turns[kind]),
        "--no-session-persistence",
        "--setting-sources",
        _SETTING_SOURCES,
    ]
    if caller.model:
        named += ["--model", caller.model]
    # The prompt reaches the agent as a file on its standard input.
    with tempfile.TemporaryFile() as prompt:
        prompt.write(message.encode())
        prompt.seek(0)
        run = await asyncio.create_subprocess_exec(
            *named,
            cwd=gate.TREE,
            env={**os.environ, **_AGENT_ENVIRONMENT, **environment},
            stdin=prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_A_LINE_LIMIT,
        )
    caller.children.add(run)
    streamed: list[dict[str, Any]] = []
    try:
        held = await asyncio.wait_for(_replied(caller, kind, run, streamed, is_a_seed), _A_CALL_BUDGET)
    except TimeoutError:  # failure-is-reported: `_kept` writes the transcript into `dead/`.
        run.kill()
        await run.wait()
        return None, _kept(
            caller, streamed, {"is_error": True, "result": f"the call ran past {_A_CALL_BUDGET} seconds"}
        )
    finally:
        caller.children.discard(run)
    if held.get("subtype") == "error_max_turns":
        return None, _kept(caller, streamed, {**held, "result": f"the agent ran past {caller.turns[kind]} turns"})
    if held.get("is_error"):
        return None, _kept(caller, streamed, held)
    if held.get(_SEEDED):
        return None, held
    answer = held.get("structured_output")
    if not isinstance(answer, dict):
        return None, _kept(
            caller, streamed, {**held, "is_error": True, "result": "the answer made no StructuredOutput call"}
        )
    return answer, (_kept(caller, streamed, held, _KEPT) if is_kept else held)


async def spoken(
    caller: Caller,
    kind: str,
    message: str,
    counts: tuple[int, int] = (0, 0),
    environment: dict[str, str] | None = None,
    is_kept: bool = False,
    is_a_seed: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """
    Answer with what a call said. The record of that call joins the journal of calls.

    A seed call holds no batch. The record of such a call counts no item and no prose. `environment` names what this
    call adds to the agent's environment. `is_kept` keeps the transcript of a call that answers, and the reply names
    that transcript. `is_a_seed` stops the call once it has written the cached prefix.
    """
    began = time.monotonic()
    caller.calls += 1
    said, reply = await _called(caller, kind, message, environment or {}, is_kept, is_a_seed)
    record = {"run": caller.began, **_spent(kind, message, counts, reply, time.monotonic() - began)}
    settling_state.appended(_journal(caller), [record])
    return said, reply


# The subtype of a reply whose agent spent its StructuredOutput retries.
_RETRIES_SPENT = "error_max_structured_output_retries"


def did_the_answers_run_out(reply: dict[str, Any]) -> bool:
    """
    Whether the agent of `reply` spent its StructuredOutput retries.

    Public. `converge_claims` files such a fragment as unsettled rather than stopping the run.
    """
    return reply.get("subtype") == _RETRIES_SPENT


def last_refusal(reply: dict[str, Any]) -> str:
    """
    The text of the last tool result the transcript of `reply` holds as an error. `_kept` names that transcript. A reply
    naming no transcript raises. So does a transcript holding no such result.

    Public. `converge_claims` files that text beside the fragment the hook kept refusing.
    """
    with open(str(reply["transcript"]), encoding="utf-8") as handle:
        streamed = [json.loads(line) for line in handle if line.strip()]
    for held in reversed(streamed):
        content = held.get("message", {}).get("content") if isinstance(held.get("message"), dict) else None
        for part in reversed(content if isinstance(content, list) else []):
            if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("is_error"):
                return str(part.get("content"))
    raise ValueError(f"{reply['transcript']} holds no refused answer")


def does_the_reply_stop_the_run(reply: dict[str, Any]) -> bool:
    """
    Whether the reply stops the run.

    An error reply with a status in `_A_WALL` stops the run. This answers false for an error with another status. The
    caller decides what such an error does.
    """
    return bool(reply.get("is_error")) and reply.get("api_error_status") in _A_WALL


def killed(caller: Caller) -> None:
    """
    Kill the agents still running. The run loses their work. `generator/converge_prose.py` drops the result of a killed
    call.
    """
    caller.is_killing = True
    for run in list(caller.children):
        try:
            run.kill()
        except ProcessLookupError:  # not-a-failure: The agent process has already exited.
            pass


# The signals that stop a run. A terminal sends `SIGINT` and `SIGHUP`, and `kill` sends `SIGTERM`.
_STOP_SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT")


def stops_on_signals(stop: Callable[[str], None]) -> None:
    """
    Install a handler for the signals `_STOP_SIGNALS` names. The handler calls `stop` with a line naming the signal.

    Public. `converge_prose` and `converge_claims` call this inside their event loop.
    """
    loop = asyncio.get_running_loop()
    for named in _STOP_SIGNALS:
        loop.add_signal_handler(getattr(signal, named), stop, f"the run took {named}")


def summed(caller: Caller) -> dict[str, Any]:
    """
    The totals of a run's calls, a record per kind of call.

    The journal of calls holds the calls these totals add up. A dead call joins the count of dead calls, and its own
    totals stay in the sum. The journal keeps the records of an earlier run, and `began` leaves those records out.
    """
    blank = {"calls": 0, "dead": 0, **{name: 0 for name in _A_TOTAL}, **{name: 0 for name in _A_TOKEN}}
    held: dict[str, Any] = {}
    for got in _records_of(caller):
        into = held.setdefault(got["kind"], dict(blank))
        into["calls"] += 1
        into["dead"] += int(got["isError"])
        for name in _A_TOTAL:
            into[name] += got[name] or 0
        for name, said in _A_TOKEN.items():
            into[name] += got["usage"].get(said) or 0
    return held
